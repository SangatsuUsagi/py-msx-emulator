"""YM2413 (OPLL) tests: register decode, melody FM synthesis, and ADSR shape.

The chip is a faithful port of emu2413 v1.5.9 (see msx/opll.py). These tests
check register/behaviour correctness (key-on produces sound, key-off decays to
silence, preset vs. user tone selection, note pitch) and lock the ported
instrument ROM and exp/log-sin tables to emu2413's reference values.
"""
from __future__ import annotations

from array import array

import pytest

from msx.opll import (
    _CLOCK,
    _DAMPER_RATE,
    _DEFAULT_INST,
    _EG_MAX,
    _EG_MUTE,
    _EXP_TABLE,
    _FULLSIN_TABLE,
    _PERC_RELEASE_RATE,
    _RELEASE,
    _SUS_RELEASE_RATE,
    SAMPLE_RATE,
    Opll,
    note_frequency,
)

_LOUD_INSTRUMENT_VOL = 0x10  # instrument=1 (violin), vol=0 (loudest)


def _key_on(o: Opll, ch: int, fnum: int = 0x150, block: int = 3) -> None:
    o.write_reg(0x30 + ch, _LOUD_INSTRUMENT_VOL)
    o.write_reg(0x10 + ch, fnum & 0xFF)
    o.write_reg(0x20 + ch, ((fnum >> 8) & 0x01) | (block << 1) | 0x10)


def _key_off(o: Opll, ch: int, fnum: int = 0x150, block: int = 3) -> None:
    o.write_reg(0x10 + ch, fnum & 0xFF)
    o.write_reg(0x20 + ch, ((fnum >> 8) & 0x01) | (block << 1))  # KON bit clear


def _max_abs(buf: bytes) -> int:
    arr = array("h")
    arr.frombytes(buf)
    return max(abs(x) for x in arr) if len(arr) else 0


# ---------------------------------------------------------------------------
# Register file (inherited from Phase 1, re-checked here for the synthesis path)
# ---------------------------------------------------------------------------

def test_register_write_via_latch_and_data() -> None:
    o = Opll()
    o.write_addr(0x30)
    o.write_data(0x1F)
    assert o.read_reg(0x30) == 0x1F


def test_chip_is_write_only() -> None:
    o = Opll()
    assert o.read() == 0xFF


def test_reset_clears_registers_and_silences() -> None:
    o = Opll()
    _key_on(o, 0)
    o.reset()
    assert o.read_reg(0x20) == 0
    assert o.read_reg(0x30) == 0
    buf = o.generate_samples(100)
    assert _max_abs(buf) == 0


# ---------------------------------------------------------------------------
# Buffer format
# ---------------------------------------------------------------------------

def test_generate_samples_buffer_format() -> None:
    o = Opll()
    buf = o.generate_samples(64)
    assert isinstance(buf, bytearray)
    assert len(buf) == 64 * 2


# ---------------------------------------------------------------------------
# Melody synthesis: key-on / key-off
# ---------------------------------------------------------------------------

def test_key_on_produces_nonzero_pcm() -> None:
    o = Opll()
    _key_on(o, 0)
    # Run a few buffers so the attack ramp has risen off zero.
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_silent_channel_produces_no_pcm() -> None:
    o = Opll()
    buf = o.generate_samples(735)
    assert _max_abs(buf) == 0


def test_key_off_decays_toward_silence() -> None:
    o = Opll()
    _key_on(o, 0)
    for _ in range(10):  # let the envelope reach a steady sustain
        o.generate_samples(735)
    _key_off(o, 0)
    for _ in range(60):  # long enough for release to complete
        buf = o.generate_samples(735)
    assert _max_abs(buf) == 0


# ---------------------------------------------------------------------------
# Preset vs. user tone selection
# ---------------------------------------------------------------------------

def test_preset_instrument_selected() -> None:
    o = Opll()
    o.write_reg(0x30, 0x18)  # instrument=1 (violin), vol=8 (mid-range, audible)
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_user_tone_selected() -> None:
    o = Opll()
    # User tone: instrument index 0, programmed via registers 0x00-0x07.
    o.write_reg(0x00, 0x61)  # mod multi + EG type/KR
    o.write_reg(0x01, 0x61)  # car multi + EG type/KR
    o.write_reg(0x02, 0x00)  # mod TL = 0 (loudest modulation)
    o.write_reg(0x03, 0x00)
    o.write_reg(0x04, 0xF0)  # mod AR/DR
    o.write_reg(0x05, 0xF0)  # car AR/DR
    o.write_reg(0x06, 0x00)
    o.write_reg(0x07, 0x00)
    o.write_reg(0x30, 0x00)  # instrument=0 (user), vol=0
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


# ---------------------------------------------------------------------------
# Multi-channel
# ---------------------------------------------------------------------------

def test_multiple_channels_sound_together() -> None:
    o = Opll()
    for ch in range(9):
        _key_on(o, ch, fnum=0x100 + ch * 20, block=3)
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


@pytest.mark.parametrize("sample_rate", [SAMPLE_RATE])
def test_sample_rate_matches_psg(sample_rate: int) -> None:
    assert sample_rate == 44_100


# ---------------------------------------------------------------------------
# Note frequency accuracy (standard YM2413 Fnum/Block formula)
# ---------------------------------------------------------------------------

def test_a4_frequency_matches_reference() -> None:
    # A4 = 440 Hz is the commonly documented reference: Fnum 290 at block 4.
    assert note_frequency(290, 4) == pytest.approx(440.0, abs=0.5)


def test_octave_doubles_frequency() -> None:
    base = note_frequency(300, 3)
    octave_up = note_frequency(300, 4)
    assert octave_up == pytest.approx(base * 2.0, rel=1e-9)


# ---------------------------------------------------------------------------
# Ported-data locks: instrument ROM and generated tables must match emu2413.
# ---------------------------------------------------------------------------

def test_instrument_rom_matches_emu2413() -> None:
    # emu2413 v1.5.9 default_inst[0] (YM2413 set): 16 melody + 3 rhythm patches.
    emu = (
        (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
        (0x71, 0x61, 0x1E, 0x17, 0xD0, 0x78, 0x00, 0x17),
        (0x13, 0x41, 0x1A, 0x0D, 0xD8, 0xF7, 0x23, 0x13),
        (0x13, 0x01, 0x99, 0x00, 0xF2, 0xC4, 0x21, 0x23),
        (0x11, 0x61, 0x0E, 0x07, 0x8D, 0x64, 0x70, 0x27),
        (0x32, 0x21, 0x1E, 0x06, 0xE1, 0x76, 0x01, 0x28),
        (0x31, 0x22, 0x16, 0x05, 0xE0, 0x71, 0x00, 0x18),
        (0x21, 0x61, 0x1D, 0x07, 0x82, 0x81, 0x11, 0x07),
        (0x33, 0x21, 0x2D, 0x13, 0xB0, 0x70, 0x00, 0x07),
        (0x61, 0x61, 0x1B, 0x06, 0x64, 0x65, 0x10, 0x17),
        (0x41, 0x61, 0x0B, 0x18, 0x85, 0xF0, 0x81, 0x07),
        (0x33, 0x01, 0x83, 0x11, 0xEA, 0xEF, 0x10, 0x04),
        (0x17, 0xC1, 0x24, 0x07, 0xF8, 0xF8, 0x22, 0x12),
        (0x61, 0x50, 0x0C, 0x05, 0xD2, 0xF5, 0x40, 0x42),
        (0x01, 0x01, 0x55, 0x03, 0xE9, 0x90, 0x03, 0x02),
        (0x41, 0x41, 0x89, 0x03, 0xF1, 0xE4, 0xC0, 0x13),
        (0x01, 0x01, 0x18, 0x0F, 0xDF, 0xF8, 0x6A, 0x6D),
        (0x01, 0x01, 0x00, 0x00, 0xC8, 0xD8, 0xA7, 0x68),
        (0x05, 0x01, 0x00, 0x00, 0xF8, 0xAA, 0x59, 0x55),
    )
    assert _DEFAULT_INST == emu


def test_exp_table_matches_emu2413_formula() -> None:
    # exp_table[x] = round((2^(x/256) - 1) * 1024)
    assert _EXP_TABLE[0] == 0
    assert _EXP_TABLE[1] == 3
    assert _EXP_TABLE[255] == 1018
    for x in (5, 64, 128, 200):
        assert _EXP_TABLE[x] == int((2 ** (x / 256.0) - 1) * 1024 + 0.5)


def test_fullsin_table_matches_emu2413_formula() -> None:
    # First quarter is -log2(sin(...)) * 256; the rest is mirrored/sign-extended.
    assert _FULLSIN_TABLE[0] == 2137
    assert _FULLSIN_TABLE[255] == 0
    # Second quarter mirrors the first (makeSinTable).
    assert _FULLSIN_TABLE[256] == _FULLSIN_TABLE[255]
    assert _FULLSIN_TABLE[511] == _FULLSIN_TABLE[0]
    # Negative half carries the 0x8000 sign bit.
    assert _FULLSIN_TABLE[512] == (0x8000 | _FULLSIN_TABLE[0])


def test_clarinet_pitch_is_accurate() -> None:
    # A harmonically simple preset lets a coarse autocorrelation recover the
    # fundamental; the emu2413 phase generator must land on the note pitch.
    o = Opll()
    o.write_reg(0x30, 0x50)  # clarinet, vol 0
    fnum, block = 290, 4
    o.write_reg(0x10, fnum & 0xFF)
    o.write_reg(0x20, ((fnum >> 8) & 1) | (block << 1) | 0x10)
    for _ in range(8):
        o.generate_samples(735)
    buf = bytearray()
    for _ in range(6):
        buf += o.generate_samples(735)
    arr = array("h")
    arr.frombytes(bytes(buf))
    samples = list(arr)

    sr = 44100
    best_lag, best = 0, -1e18
    for lag in range(int(sr / 2000), int(sr / 100)):
        s = sum(samples[i] * samples[i + lag] for i in range(len(samples) - lag))
        if s > best:
            best, best_lag = s, lag
    measured = sr / best_lag
    assert measured == pytest.approx(note_frequency(fnum, block), rel=0.03)


# ---------------------------------------------------------------------------
# allium/ym2413-core.allium: WriteAddress / WriteData -- the two-step latch
# protocol itself, the register-mirror fold, and the >=0x40 no-op. The tests
# above all drive registers via write_reg (a latch-bypassing convenience --
# see the spec's Excludes); these confirm the latch path itself, which
# write_reg never exercises.
# ---------------------------------------------------------------------------

def test_write_addr_then_write_data_matches_write_reg() -> None:
    via_latch = Opll()
    via_latch.write_addr(0x30)
    via_latch.write_data(0x1F)

    via_write_reg = Opll()
    via_write_reg.write_reg(0x30, 0x1F)

    assert via_latch.read_reg(0x30) == via_write_reg.read_reg(0x30) == 0x1F


@pytest.mark.parametrize(
    ("mirror_register", "canonical_register"),
    [(0x19, 0x10), (0x1F, 0x16), (0x29, 0x20), (0x2F, 0x26), (0x39, 0x30), (0x3F, 0x36)],
)
def test_mirror_register_folds_to_canonical_register(
    mirror_register: int, canonical_register: int
) -> None:
    o = Opll()
    o.write_addr(mirror_register)
    o.write_data(0x55)
    assert o.read_reg(canonical_register) == 0x55
    assert o.read_reg(mirror_register) == 0  # the mirror address itself is never stored to


def test_register_at_or_above_0x40_write_is_ignored() -> None:
    o = Opll()
    o.write_addr(0x40)
    o.write_data(0xFF)
    # Every real register must be untouched -- spot-check a representative
    # spread rather than all 64.
    for reg in (0x00, 0x0E, 0x10, 0x20, 0x30):
        assert o.read_reg(reg) == 0


def test_set_test_mode_stores_raw_bits() -> None:
    o = Opll()
    o.write_reg(0x0F, 0xFF)
    assert o._test_flag == 0xFF
    assert o.read_reg(0x0F) == 0xFF


def test_register_writes_only_affect_their_own_target() -> None:
    o = Opll()
    o.write_reg(0x10, 0x55)  # channel 0's F-number low byte
    assert o.read_reg(0x11) == 0  # channel 1's is untouched
    o.write_reg(0x0F, 0x01)  # test-mode register
    assert (o.read_reg(0x0E) & 0x20) == 0  # rhythm-enable bit untouched


# ---------------------------------------------------------------------------
# allium/ym2413-core.allium config: fixed rate/level constants used by the
# envelope generator (damper_rate, sustain_release_rate,
# percussive_key_off_rate, envelope_mute_level) and the chip clock.
# ---------------------------------------------------------------------------

def test_register_write_masks_out_of_range_value_to_eight_bits() -> None:
    # allium/ym2413-core.allium invariants ChannelFieldsBounded /
    # UserTonePatchFieldsBounded: every decoded field derives from a byte
    # nibble/bit extraction, so it can never leave its declared range
    # regardless of what's written -- mirrors allium/psg.allium's own
    # register-masking regression test.
    o = Opll()
    o.write_reg(0x30, 0x1FF)  # out-of-range int; write_reg masks with & 0xFF
    assert o.read_reg(0x30) == 0xFF
    o.write_reg(0x02, 0x1FF)
    assert o.read_reg(0x02) == 0xFF


def test_envelope_and_clock_constants_match_spec() -> None:
    assert _CLOCK == 3_579_545
    assert _EG_MUTE == 127
    assert _EG_MAX == 123
    assert _DAMPER_RATE == 12
    assert _SUS_RELEASE_RATE == 5
    assert _PERC_RELEASE_RATE == 7


# ---------------------------------------------------------------------------
# allium/ym2413-core.allium ProgramUserTonePatch: register 0x03's waveform
# select and modulator feedback, which the existing user-tone test above
# leaves at their all-zero defaults and never differentiates.
# ---------------------------------------------------------------------------

def _program_basic_user_tone(o: Opll, *, waveform_bits: int = 0x00, feedback: int = 0) -> None:
    o.write_reg(0x00, 0x21)  # mod: multi=1, EG-TYP=sustained (holds, easy to compare)
    o.write_reg(0x01, 0x21)  # car: same
    o.write_reg(0x02, 0x00)  # mod TL = 0 (loudest modulation)
    o.write_reg(0x03, waveform_bits | feedback)
    o.write_reg(0x04, 0xF8)  # AR=15, DR=8
    o.write_reg(0x05, 0xF8)
    o.write_reg(0x06, 0x40)  # SL=4, RR=0
    o.write_reg(0x07, 0x40)
    o.write_reg(0x30, 0x00)  # instrument=0 (user), vol=0
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)  # block=3, KON=1


def test_user_tone_waveform_select_changes_output() -> None:
    full_sine = Opll()
    _program_basic_user_tone(full_sine, waveform_bits=0x00)  # WS=0 for both operators
    half_sine = Opll()
    _program_basic_user_tone(half_sine, waveform_bits=0x18)  # WS=1 for both operators (bits 3-4)

    buf_full = bytearray()
    buf_half = bytearray()
    for _ in range(6):
        buf_full += full_sine.generate_samples(735)
        buf_half += half_sine.generate_samples(735)

    assert bytes(buf_full) != bytes(buf_half)


def test_user_tone_feedback_changes_output() -> None:
    no_feedback = Opll()
    _program_basic_user_tone(no_feedback, feedback=0)
    max_feedback = Opll()
    _program_basic_user_tone(max_feedback, feedback=7)

    buf_none = bytearray()
    buf_max = bytearray()
    for _ in range(6):
        buf_none += no_feedback.generate_samples(735)
        buf_max += max_feedback.generate_samples(735)

    assert bytes(buf_none) != bytes(buf_max)


# ---------------------------------------------------------------------------
# allium/ym2413-core.allium: sustained_tone vs. percussive envelope shape
# (ProgramUserTonePatch's EG-TYP), the channel sustain ("damper") option
# slowing Release (SetPitchBlockKeyOnSustain), and the modulator's envelope
# freezing rather than releasing on key-off (KeyOff).
# ---------------------------------------------------------------------------

def _carrier_eg_out_after(*, sustained_tone: bool, frames: int) -> int:
    o = Opll()
    eg_bit = 0x20 if sustained_tone else 0x00
    o.write_reg(0x00, 0x01 | eg_bit)
    o.write_reg(0x01, 0x01 | eg_bit)
    o.write_reg(0x02, 0x00)
    o.write_reg(0x03, 0x00)
    o.write_reg(0x04, 0xF8)  # AR=15 (instant attack), DR=8 (reaches SL quickly)
    o.write_reg(0x05, 0xF8)
    o.write_reg(0x06, 0x20)  # SL=2 (reached quickly), RR=0
    o.write_reg(0x07, 0x28)  # SL=2, RR=8 (moderate, only matters while percussive)
    o.write_reg(0x30, 0x00)
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)
    for _ in range(frames):
        o.generate_samples(735)
    return o._slot[1].eg_out  # carrier's envelope level -- higher means quieter


def test_sustained_tone_holds_while_percussive_keeps_decaying() -> None:
    early_sustained = _carrier_eg_out_after(sustained_tone=True, frames=3)
    late_sustained = _carrier_eg_out_after(sustained_tone=True, frames=50)
    early_percussive = _carrier_eg_out_after(sustained_tone=False, frames=3)
    late_percussive = _carrier_eg_out_after(sustained_tone=False, frames=50)

    # Sustained tone: Decay hands off to Sustain, which holds -- the level
    # barely moves between an early and a much later checkpoint.
    assert abs(late_sustained - early_sustained) <= 3
    # Percussive: Sustain keeps decaying at release_rate for as long as the
    # key is held -- the level (quieter = higher) keeps climbing.
    assert late_percussive > early_percussive + 5


def test_channel_sustain_option_slows_release() -> None:
    def frames_to_silence(sustain_bit: bool) -> int:
        o = Opll()
        o.write_reg(0x30, 0x10)  # violin (instrument 1), vol 0
        o.write_reg(0x10, 0x50)
        sustain_mask = 0x20 if sustain_bit else 0x00
        o.write_reg(0x20, 0x16 | sustain_mask)  # block=3, KON=1
        for _ in range(10):
            o.generate_samples(735)
        o.write_reg(0x20, 0x06 | sustain_mask)  # KON cleared, sustain option preserved
        for frames in range(1, 200):
            buf = o.generate_samples(735)
            if _max_abs(buf) == 0:
                return frames
        return 200

    without_sustain = frames_to_silence(False)
    with_sustain = frames_to_silence(True)
    assert with_sustain > without_sustain


def test_modulator_envelope_does_not_release_on_key_off() -> None:
    o = Opll()
    _key_on(o, 0)
    for _ in range(10):
        o.generate_samples(735)
    _key_off(o, 0)
    o.generate_samples(735)
    # Channel 0: modulator is slot 0, carrier is slot 1 (mod(ch)=slot[ch*2],
    # car(ch)=slot[ch*2+1]).
    assert o._slot[1].eg_state == _RELEASE  # carrier releases on key-off
    assert o._slot[0].eg_state != _RELEASE  # modulator's envelope simply freezes


# ---------------------------------------------------------------------------
# allium/ym2413-core.allium: noise LFSR is 24-bit (taps at bits 23 and 9),
# advanced 18 times per native tick (14+2+2 across _update_output's three
# _update_noise calls) -- not the PSG's 17-bit/1-shift shape. Regression
# guard for the allium width/rate fix.
# ---------------------------------------------------------------------------

def test_noise_lfsr_is_24_bit_not_truncated_to_17() -> None:
    o = Opll()
    o._noise = 1 << 20  # bit 20 set -- outside a 17-bit register (max bit 16)
    o._update_output()
    # bit 0 is clear throughout (a lone bit walking right never triggers the
    # XOR feedback until it reaches bit 0, which takes 20 shifts, more than
    # this tick's 18), so this is a plain 18-bit right-shift: 1<<20 -> 1<<2.
    # If the register were (wrongly) masked to 17 bits somewhere, bit 20
    # would already have been dropped before this point instead of arriving.
    assert o._noise == 1 << 2


def test_noise_lfsr_advances_18_shifts_per_native_tick() -> None:
    o = Opll()
    o._noise = 1  # bit 0 set: every shift XORs 0x800200 first
    o._update_output()
    # 18 applications of "if bit0: xor 0x800200; shift right 1", starting
    # from 1, computed independently here as the expected value.
    expected = 1
    for _ in range(18):
        if expected & 1:
            expected ^= 0x800200
        expected >>= 1
    assert o._noise == expected
