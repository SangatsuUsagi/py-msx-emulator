"""Tests for AY-3-8910 PSG synthesiser: tone, noise, envelope, mixer, generate_samples."""
import struct

from msx.psg import PSG, SAMPLES_PER_FRAME

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_tone_period(psg: PSG, ch: int, period: int) -> None:
    """Write fine (R0+ch*2) and coarse (R1+ch*2) registers for the given period."""
    psg.regs[ch * 2] = period & 0xFF
    psg.regs[ch * 2 + 1] = (period >> 8) & 0x0F


def _samples_as_int16(buf: bytearray) -> list[int]:
    n = len(buf) // 2
    return list(struct.unpack_from(f"<{n}h", buf))


# ---------------------------------------------------------------------------
# generate_samples — basic output contract
# ---------------------------------------------------------------------------

def test_generate_samples_byte_count() -> None:
    psg = PSG()
    assert len(psg.generate_samples(735)) == 1470


def test_generate_samples_silence_all_volumes_zero() -> None:
    psg = PSG()
    # R7=0 enables all tone/noise, but volumes (R8–R10) are all 0 by default.
    psg.regs[7] = 0x00
    buf = psg.generate_samples(100)
    assert all(b == 0 for b in buf)


def test_generate_samples_returns_bytearray() -> None:
    psg = PSG()
    assert isinstance(psg.generate_samples(10), bytearray)


def test_generate_samples_per_frame_constant() -> None:
    assert SAMPLES_PER_FRAME == 735


# ---------------------------------------------------------------------------
# Tone channel
# ---------------------------------------------------------------------------

def test_tone_period_zero_clamped_to_one() -> None:
    """Period=0 must be treated as 1 — no divide-by-zero, maximum frequency."""
    psg = PSG()
    psg.regs[0] = 0  # R0 fine = 0
    psg.regs[1] = 0  # R1 coarse = 0  → period = 0, clamped to 1
    psg.regs[7] = 0x3E   # tone A enabled, noise disabled, channels B/C disabled
    psg.regs[8] = 0x0F   # channel A max volume
    # Should not raise and should produce non-zero output.
    buf = psg.generate_samples(10)
    assert len(buf) == 20


def test_tone_produces_nonzero_output() -> None:
    """With a non-zero volume and tone enabled, output must have non-zero samples."""
    psg = PSG()
    _set_tone_period(psg, 0, 254)  # ≈ 440 Hz (A4) at PSG_CLOCK=223722
    psg.regs[7] = 0x3E  # tone A enabled only
    psg.regs[8] = 0x0F  # channel A max volume
    buf = psg.generate_samples(SAMPLES_PER_FRAME)
    samples = _samples_as_int16(buf)
    assert any(s > 0 for s in samples)


def test_tone_channel_b_independent() -> None:
    """Channel B with volume 0 does not affect channel A output."""
    psg_a = PSG()
    _set_tone_period(psg_a, 0, 100)
    psg_a.regs[7] = 0x3E   # only tone A
    psg_a.regs[8] = 0x0F

    psg_ab = PSG()
    _set_tone_period(psg_ab, 0, 100)
    _set_tone_period(psg_ab, 1, 100)
    psg_ab.regs[7] = 0x3C  # tone A and B enabled
    psg_ab.regs[8] = 0x0F  # A max volume
    psg_ab.regs[9] = 0x00  # B volume = 0

    assert psg_a.generate_samples(50) == psg_ab.generate_samples(50)


def test_tone_state_advances_across_calls() -> None:
    """Synthesiser state persists between generate_samples calls."""
    psg = PSG()
    _set_tone_period(psg, 0, 10)
    psg.regs[7] = 0x3E
    psg.regs[8] = 0x0F
    cnt_before = psg._tone_cnt[0]
    psg.generate_samples(1)
    cnt_after = psg._tone_cnt[0]
    # Counter must have changed.
    assert cnt_before != cnt_after or psg._tone_out[0] != 0


# ---------------------------------------------------------------------------
# Noise channel
# ---------------------------------------------------------------------------

def test_noise_lfsr_never_zero() -> None:
    """LFSR must never reach 0 — the stuck-zero state is forbidden."""
    psg = PSG()
    psg.regs[6] = 1  # fastest noise
    for _ in range(10000):
        psg._step_noise(1)
        assert psg._lfsr != 0


def test_noise_lfsr_advances_each_period() -> None:
    psg = PSG()
    psg.regs[6] = 8  # period = 8
    # Force counter to exact period start so timing is predictable.
    psg._noise_cnt = 8
    initial_lfsr = psg._lfsr
    # Step 7 ticks — counter goes 8→1, no advance yet.
    psg._step_noise(7)
    assert psg._lfsr == initial_lfsr
    # Step 1 more — counter hits 0, LFSR advances.
    psg._step_noise(1)
    assert psg._lfsr != initial_lfsr


def test_noise_period_zero_clamped() -> None:
    psg = PSG()
    psg.regs[6] = 0
    psg._step_noise(1)  # must not raise


def test_noise_reload_is_2x_np() -> None:
    # Datasheet fN = PSG_CLOCK/(2*NP): after a shift the counter reloads to 2*NP,
    # so the LFSR clock is half the previous (buggy 1*NP) rate.
    psg = PSG()
    psg.regs[6] = 8  # NP = 8
    psg._noise_cnt = 1
    psg._step_noise(1)          # counter hits 0 → shift + reload
    assert psg._noise_cnt == 8 * 2  # reloaded with period * 2


# ---------------------------------------------------------------------------
# Envelope generator
# ---------------------------------------------------------------------------

def _run_envelope(shape: int, n_steps: int) -> list[int]:
    """Run the envelope for n_steps level changes at period=1, return level history.

    Uses the 32-step attack/alternate/hold model. Datasheet-correct rate: one
    counter step advances every `period` PSG ticks (period=1 → every tick), and
    two counter steps make one of the 16 output levels (level = step>>1). So
    _step_envelope(2) advances one output level. Attack bit direction
    (AY-3-8910): attack=1 ramps up, attack=0 ramps down.
    """
    psg = PSG()
    psg.regs[11] = 1   # period = 1
    psg.regs[12] = 0
    psg.regs[13] = shape
    psg._reset_envelope()

    levels: list[int] = [psg._env_output_level()]
    for _ in range(n_steps):
        psg._step_envelope(2)  # one output level per call (2 counter steps)
        levels.append(psg._env_output_level())
    return levels


def test_envelope_shape_08_decay_repeat() -> None:
    """Shape 0x08 (attack=0, continue=1): decays 15→0 then repeats."""
    levels = _run_envelope(0x08, 20)
    assert levels[0] == 15
    assert levels[15] == 0
    assert levels[16] == 15  # wrap-around: repeats from the top


def test_envelope_shape_0c_attack_repeat() -> None:
    """Shape 0x0C (attack=1, continue=1): rises 0→15 then repeats."""
    levels = _run_envelope(0x0C, 20)
    assert levels[0] == 0
    assert levels[15] == 15
    assert levels[16] == 0  # wrap-around: repeats from the bottom


def test_envelope_shape_09_decay_hold_0() -> None:
    """Shape 0x09 (attack=0, continue=1, hold=1): decays 15→0 then holds at 0."""
    levels = _run_envelope(0x09, 20)
    assert levels[0] == 15
    assert levels[15] == 0
    assert levels[16] == 0
    assert levels[20] == 0


def test_envelope_shape_0d_attack_hold_15() -> None:
    """Shape 0x0D (attack=1, continue=1, hold=1): rises 0→15 then holds at 15."""
    levels = _run_envelope(0x0D, 20)
    assert levels[0] == 0
    assert levels[15] == 15
    assert levels[16] == 15
    assert levels[20] == 15


def test_envelope_shape_0a_triangle_down_first() -> None:
    """Shape 0x0A (attack=0, continue=1, alternate=1): decays then reverses up."""
    levels = _run_envelope(0x0A, 35)
    assert levels[0] == 15
    assert levels[15] == 0     # reached the bottom
    assert levels[17] == 1     # reversed: now ascending
    assert levels[31] == 15    # reached the top
    assert levels[33] == 14    # reversed again: descending


def test_envelope_shape_00_single_decay_hold_0() -> None:
    """Shapes 0x00-0x07 (continue=0): single decay then hold at 0."""
    levels = _run_envelope(0x00, 20)
    assert levels[0] == 15
    assert levels[16] == 0
    assert levels[20] == 0


def test_envelope_advances_one_step_per_ep_ticks() -> None:
    # One 32-step count per EP PSG-clock ticks (no *8 / *16 multiplier).
    psg = PSG()
    psg.regs[11] = 3   # EP low
    psg.regs[12] = 0   # EP = 3
    psg.regs[13] = 0x08  # decay, repeating
    psg._reset_envelope()
    assert psg._env_cnt == 3      # reload = EP, not EP*8
    start = psg._env_step
    psg._step_envelope(3)         # exactly EP ticks → one counter step
    assert psg._env_step == start - 1


def test_envelope_reset_on_r13_write() -> None:
    """Writing R13 restarts the envelope from the beginning (step = 0x1F)."""
    psg = PSG()
    psg.regs[11] = 1
    psg.regs[12] = 0
    psg.regs[13] = 0x0C  # attack repeat (rises from 0)
    psg._reset_envelope()
    # Advance a few steps away from the start.
    psg._step_envelope(48)
    assert psg._env_output_level() > 0
    # Write R13 → restart at the beginning of the attack ramp.
    psg.write_port(0xA0, 13)
    psg.write_port(0xA1, 0x0C)
    assert psg._env_step == 0x1F
    assert psg._env_output_level() == 0


# ---------------------------------------------------------------------------
# Mixer
# ---------------------------------------------------------------------------

def test_mixer_both_disabled_returns_1() -> None:
    psg = PSG()
    psg.regs[7] = 0xFF  # all disabled
    assert psg._mix_channel(0) == 1
    assert psg._mix_channel(1) == 1
    assert psg._mix_channel(2) == 1


def test_mixer_tone_only_returns_tone_bit() -> None:
    psg = PSG()
    psg.regs[7] = 0x3E  # tone A enabled, noise A disabled
    psg._tone_out[0] = 1
    assert psg._mix_channel(0) == 1
    psg._tone_out[0] = 0
    assert psg._mix_channel(0) == 0


def test_mixer_tone_and_noise_and() -> None:
    # AY-3-8910 mixer ANDs tone and noise when both are enabled: a 0 from either
    # generator forces the channel low (previously OR; datasheet corrected).
    psg = PSG()
    psg.regs[7] = 0x36  # tone A and noise A both enabled
    psg._tone_out[0] = 0
    psg._lfsr = 0x10001  # bit 0 = 1
    assert psg._mix_channel(0) == 0  # tone 0 AND noise 1 = 0
    psg._tone_out[0] = 1
    assert psg._mix_channel(0) == 1  # tone 1 AND noise 1 = 1
    psg._lfsr = 0x10000  # bit 0 = 0
    assert psg._mix_channel(0) == 0  # tone 1 AND noise 0 = 0


# ---------------------------------------------------------------------------
# Envelope flag in volume register
# ---------------------------------------------------------------------------

def test_envelope_flag_uses_envelope_level() -> None:
    """R8 bit 4 = 1 → channel A amplitude uses envelope level, not volume bits."""
    psg = PSG()
    _set_tone_period(psg, 0, 10)
    psg.regs[7] = 0x3E   # tone A enabled
    psg.regs[8] = 0x10   # envelope mode (bit 4 set)
    psg.regs[11] = 0xFF
    psg.regs[12] = 0xFF  # very slow envelope → level stays near 15
    psg.regs[13] = 0x0D  # attack then hold at 15
    psg._reset_envelope()
    psg._env_attack = 0x1F
    psg._env_step = 0x00      # (0x00 ^ 0x1F) >> 1 == 15
    psg._env_holding = True   # freeze at level 15

    buf = psg.generate_samples(100)
    samples = _samples_as_int16(buf)
    # Some samples must be non-zero (envelope level = 15 → amplitude from table)
    assert any(s > 0 for s in samples)


# ---------------------------------------------------------------------------
# Integration: 440 Hz tone on channel A
# ---------------------------------------------------------------------------

def test_440hz_tone_nonzero_and_transitions() -> None:
    """440 Hz square wave: output alternates between 0 and a positive value."""
    psg = PSG()
    _set_tone_period(psg, 0, 254)   # ≈ A4 at PSG_CLOCK=223722 Hz
    psg.regs[7] = 0x3E              # tone A enabled, noise disabled
    psg.regs[8] = 0x0F              # max volume (no envelope)
    buf = psg.generate_samples(SAMPLES_PER_FRAME)
    samples = _samples_as_int16(buf)
    # There must be both zero and non-zero samples (square wave transitions).
    assert any(s == 0 for s in samples)
    assert any(s > 0 for s in samples)
