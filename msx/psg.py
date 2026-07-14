from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from msx.input import InputState

_REG_IO_PORT_A = 14

PSG_CLOCK: int = 223_722      # AY-3-8910: 3,579,545 Hz / 16
SAMPLE_RATE: int = 44_100
SAMPLES_PER_FRAME: int = 735  # 44100 // 60
# Upper bound on recorded register writes per audio buffer. Far above any real
# frame's write count; caps memory if generate_samples is never called (headless).
_MAX_EVENTS: int = 4096

# Generator-state snapshot for sub-frame replay rewind: tone counters, tone
# outputs, noise counter, LFSR, envelope counter/step/attack, three envelope
# flags, and the clock fraction.
_GenState = tuple[
    list[int], list[int], int, int, int, int, int, bool, bool, bool, int
]

# AY-3-8910 quasi-logarithmic amplitude table (level 0–15 → 16-bit amplitude).
# Each consecutive step is approximately √2 (≈3 dB).  Three channels at max
# sum to 36 861; SDL2 output is clamped to [0, 32 767].
_VOL_TABLE: tuple[int, ...] = (
    0,    85,   121,   171,   241,   336,   473,   692,
    1023, 1447, 2072,  2900,  4111,  5800,  8296, 12287,
)


@dataclass
class PSG:
    regs: list[int] = field(default_factory=lambda: [0] * 16)
    latch: int = 0
    _input: InputState | None = field(default=None, repr=False)

    # Sub-frame audio: register writes are timestamped (cycle, reg, value) via
    # _get_cycle so generate_samples can place them at their in-frame sample
    # positions (reproduces software PCM). _regs_base / _gen_base snapshot the
    # register + generator state at the frame's first write, for replay rewind.
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    _events: list[tuple[int, int, int]] = field(default_factory=list, init=False, repr=False)
    _regs_base: list[int] = field(default_factory=list, init=False, repr=False)
    _gen_base: _GenState | None = field(default=None, init=False, repr=False)

    # --- synthesiser state (not part of __init__) ---
    _tone_cnt: list[int] = field(default_factory=lambda: [1, 1, 1], init=False, repr=False)
    _tone_out: list[int] = field(default_factory=lambda: [0, 0, 0], init=False, repr=False)
    _noise_cnt: int = field(default=1, init=False, repr=False)
    _lfsr: int = field(default=1, init=False, repr=False)   # 17-bit; must never be 0
    # Envelope: 32-step down-counter + attack/alternate/hold model (openMSX/MAME).
    _env_cnt: int = field(default=1, init=False, repr=False)
    _env_step: int = field(default=0x1F, init=False, repr=False)   # 5-bit counter 0-31
    _env_attack: int = field(default=0, init=False, repr=False)    # 0x00 or 0x1F
    _env_alternate: bool = field(default=False, init=False, repr=False)
    _env_hold_flag: bool = field(default=False, init=False, repr=False)
    _env_holding: bool = field(default=False, init=False, repr=False)
    _clk_frac: int = field(default=0, init=False, repr=False)

    # ------------------------------------------------------------------ ports

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA0:
            self.latch = value & 0x0F
        elif port == 0xA1:
            reg = self.latch
            if len(self._events) < _MAX_EVENTS:
                if not self._events:
                    # Frame's first write: snapshot state to rewind to at replay.
                    self._regs_base = self.regs[:]
                    self._gen_base = self._snapshot_gen()
                cyc = self._get_cycle() if self._get_cycle is not None else 0
                self._events.append((cyc, reg, value))
            self.regs[reg] = value
            if reg == 13:
                self._reset_envelope()

    def read_port(self, port: int) -> int:
        if port == 0xA2:
            if self.latch == _REG_IO_PORT_A and self._input is not None:
                # PORT A returns the *selected* joystick port's 6 signals on
                # bits 0-5 (dir 0-3, triggers 4-5). JOY_SELECT is PSG register
                # 15 bit 6: 0→Joy1, 1→Joy2. Bits 6-7 are not joystick lines
                # (pulled high here).
                joy_select = (self.regs[15] >> 6) & 1
                sel = self._input.joy1 if joy_select == 0 else self._input.joy2
                return (sel & 0x3F) | 0xC0
            return self.regs[self.latch]
        return 0xFF

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register and synthesiser state (matches field defaults)."""
        self.regs = [0] * 16
        self.latch = 0
        self._tone_cnt = [1, 1, 1]
        self._tone_out = [0, 0, 0]
        self._noise_cnt = 1
        self._lfsr = 1
        self._env_cnt = 1
        self._env_step = 0x1F
        self._env_attack = 0
        self._env_alternate = False
        self._env_hold_flag = False
        self._env_holding = False
        self._clk_frac = 0
        self._events = []
        self._gen_base = None

    # --------------------------------------------------------- envelope reset

    def _env_period(self) -> int:
        return max(1, (self.regs[12] << 8) | self.regs[11])

    def _env_output_level(self) -> int:
        """Current 4-bit envelope level (0-15) from the step/attack model."""
        return (self._env_step ^ self._env_attack) >> 1

    def _reset_envelope(self) -> None:
        shape = self.regs[13] & 0x0F
        # attack bit (0x04) picks the ramp direction; step always starts at 0x1F.
        self._env_attack = 0x1F if (shape & 0x04) else 0
        if (shape & 0x08) == 0:
            # continue = 0: single-shot; alternate mirrors the attack bit.
            self._env_hold_flag = True
            self._env_alternate = self._env_attack != 0
        else:
            self._env_hold_flag = bool(shape & 0x01)
            self._env_alternate = bool(shape & 0x02)
        self._env_step = 0x1F
        self._env_holding = False
        # AY-3-8910: one full 32-step ramp = 256*EP/fMaster, so a single step
        # takes (256*EP/fMaster)/32 = EP/(fMaster/8) = EP PSG-clock ticks.  One
        # step therefore advances every `period` ticks (no *8 / *16 multiplier).
        self._env_cnt = self._env_period()

    # --------------------------------------------------------------- noise

    def _step_noise(self, ticks: int) -> None:
        period = max(1, self.regs[6] & 0x1F)
        self._noise_cnt -= ticks
        while self._noise_cnt <= 0:
            # Datasheet fN = fMaster/(16*NP) = PSG_CLOCK/(2*NP): the LFSR shifts
            # once every 2*NP PSG-clock ticks (single-period reload ran it 2x fast).
            self._noise_cnt += period * 2
            # 17-bit LFSR, polynomial x^17 + x^14 + 1 (feedback from bits 0 and 3)
            feedback = (self._lfsr ^ (self._lfsr >> 3)) & 1
            self._lfsr = ((self._lfsr >> 1) | (feedback << 16)) & 0x1FFFF

    # ------------------------------------------------------------ envelope

    def _step_envelope(self, ticks: int) -> None:
        if self._env_holding:
            return
        step_ticks = self._env_period()  # one 32-step count per EP PSG ticks
        self._env_cnt -= ticks
        while self._env_cnt <= 0:
            self._env_cnt += step_ticks
            self._env_step -= 1
            if self._env_step < 0:
                if self._env_hold_flag:
                    # Freeze at the boundary; alternate flips the final direction.
                    if self._env_alternate:
                        self._env_attack ^= 0x1F
                    self._env_holding = True
                    self._env_step = 0
                    break
                # Repeating shape: reload counter; alternate reverses the ramp
                # immediately (no dwell). (_env_step & 0x20) is the underflow bit.
                # Portability note: _env_step went negative here (Python ints are
                # arbitrary precision, so -1 & 0x20 == 0x20). Rust/C++ unsigned
                # types underflow instead — a port must use a signed type (i32)
                # or an explicit wrap so bit 5 still flags the -1 boundary.
                if self._env_alternate and (self._env_step & 0x20):
                    self._env_attack ^= 0x1F
                self._env_step &= 0x1F

    # --------------------------------------------------------------- mixer

    def _mix_channel(self, ch: int) -> int:
        r7 = self.regs[7]
        tone_en = not ((r7 >> ch) & 1)
        noise_en = not ((r7 >> (ch + 3)) & 1)
        # AY-3-8910: channel output is tone AND noise; a disabled generator
        # contributes a constant 1, so it does not gate the enabled one.  Both
        # disabled → 1 & 1 = 1 (constant high, volume sets amplitude).
        tone_bit = self._tone_out[ch] if tone_en else 1
        noise_bit = (self._lfsr & 1) if noise_en else 1
        return tone_bit & noise_bit

    # ---------------------------------------------------- sample generation

    def _render(self, out: bytearray, lo: int, hi: int) -> None:
        """Synthesise samples [lo, hi) into `out` from the current registers.

        The registers are constant across the range (one segment), so all
        periods / mixer enables / volumes are precomputed once and the tone/
        noise/envelope generators are inlined with their state hoisted to locals
        and written back after the loop. generate_samples() calls this once per
        constant-register segment (once for the whole buffer when there are no
        mid-frame writes). Behaviour matches the _step_* methods kept above.
        """
        regs = self.regs

        # --- precompute buffer-constant register-derived values ---
        tp0 = max(1, ((regs[1] & 0x0F) << 8) | regs[0])
        tp1 = max(1, ((regs[3] & 0x0F) << 8) | regs[2])
        tp2 = max(1, ((regs[5] & 0x0F) << 8) | regs[4])
        np2 = max(1, regs[6] & 0x1F) * 2          # noise reload = 2 * NP
        ep = max(1, (regs[12] << 8) | regs[11])   # envelope: one step per EP ticks
        r7 = regs[7]
        tone_en0 = not (r7 & 0x01)
        tone_en1 = not (r7 & 0x02)
        tone_en2 = not (r7 & 0x04)
        noise_en0 = not (r7 & 0x08)
        noise_en1 = not (r7 & 0x10)
        noise_en2 = not (r7 & 0x20)
        vr0, vr1, vr2 = regs[8], regs[9], regs[10]
        va0 = _VOL_TABLE[vr0 & 0x0F]
        va1 = _VOL_TABLE[vr1 & 0x0F]
        va2 = _VOL_TABLE[vr2 & 0x0F]
        env0 = bool(vr0 & 0x10)
        env1 = bool(vr1 & 0x10)
        env2 = bool(vr2 & 0x10)

        # --- hoist generator state to locals ---
        tc0, tc1, tc2 = self._tone_cnt
        to0, to1, to2 = self._tone_out
        nc = self._noise_cnt
        lfsr = self._lfsr
        env_cnt = self._env_cnt
        env_step = self._env_step
        env_attack = self._env_attack
        env_alternate = self._env_alternate
        env_hold_flag = self._env_hold_flag
        env_holding = self._env_holding
        clk_frac = self._clk_frac

        for i in range(lo, hi):
            # Advance PSG clock by one sample's worth of ticks (integer arithmetic).
            clk_frac += PSG_CLOCK
            ticks = clk_frac // SAMPLE_RATE
            clk_frac %= SAMPLE_RATE

            # tone channels (inline _step_tone)
            tc0 -= ticks
            while tc0 <= 0:
                tc0 += tp0
                to0 ^= 1
            tc1 -= ticks
            while tc1 <= 0:
                tc1 += tp1
                to1 ^= 1
            tc2 -= ticks
            while tc2 <= 0:
                tc2 += tp2
                to2 ^= 1

            # noise (inline _step_noise): 17-bit LFSR, taps at bits 0 and 3
            nc -= ticks
            while nc <= 0:
                nc += np2
                feedback = (lfsr ^ (lfsr >> 3)) & 1
                lfsr = ((lfsr >> 1) | (feedback << 16)) & 0x1FFFF

            # envelope (inline _step_envelope)
            if not env_holding:
                env_cnt -= ticks
                while env_cnt <= 0:
                    env_cnt += ep
                    env_step -= 1
                    if env_step < 0:
                        if env_hold_flag:
                            if env_alternate:
                                env_attack ^= 0x1F
                            env_holding = True
                            env_step = 0
                            break
                        if env_alternate and (env_step & 0x20):
                            env_attack ^= 0x1F
                        env_step &= 0x1F

            env_amp = _VOL_TABLE[(env_step ^ env_attack) >> 1]
            noise_bit = lfsr & 1

            # mixer: channel output = tone AND noise (disabled generator → 1)
            sample = 0
            if (to0 if tone_en0 else 1) & (noise_bit if noise_en0 else 1):
                sample += env_amp if env0 else va0
            if (to1 if tone_en1 else 1) & (noise_bit if noise_en1 else 1):
                sample += env_amp if env1 else va1
            if (to2 if tone_en2 else 1) & (noise_bit if noise_en2 else 1):
                sample += env_amp if env2 else va2

            if sample > 32767:
                sample = 32767

            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        # --- write generator state back ---
        self._tone_cnt[0], self._tone_cnt[1], self._tone_cnt[2] = tc0, tc1, tc2
        self._tone_out[0], self._tone_out[1], self._tone_out[2] = to0, to1, to2
        self._noise_cnt = nc
        self._lfsr = lfsr
        self._env_cnt = env_cnt
        self._env_step = env_step
        self._env_attack = env_attack
        self._env_alternate = env_alternate
        self._env_hold_flag = env_hold_flag
        self._env_holding = env_holding
        self._clk_frac = clk_frac

    def generate_samples(
        self, n: int, frame_start: int = 0, frame_end: int = 0
    ) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples.

        Register writes recorded during the frame (write_port) are applied at
        their sub-frame sample positions, so software PCM played through rapid
        volume-register writes is reproduced. With no recorded writes — or no
        frame window (frame_end <= frame_start) — the whole buffer is one
        constant-register segment (the fast path, unchanged behaviour).
        """
        out = bytearray(n * 2)
        events = self._events
        if not events or frame_end <= frame_start or self._gen_base is None:
            self._events = []
            self._render(out, 0, n)
            return out
        # Sub-frame replay: rewind to the frame-start register + generator state,
        # then render segments split at each write's computed sample position.
        final = self.regs[:]
        self.regs[:] = self._regs_base
        self._restore_gen(self._gen_base)
        span = frame_end - frame_start
        lo = 0
        for cyc, reg, val in events:
            pos = (cyc - frame_start) * n // span
            if pos < 0:
                pos = 0
            elif pos > n:
                pos = n
            if pos > lo:
                self._render(out, lo, pos)
                lo = pos
            self.regs[reg] = val
            if reg == 13:
                self._reset_envelope()
        if lo < n:
            self._render(out, lo, n)
        # Restore the true final register state (also covers any capped events).
        self.regs[:] = final
        self._events = []
        self._gen_base = None
        return out

    def _snapshot_gen(self) -> _GenState:
        """Capture generator state to rewind to for sub-frame replay."""
        return (
            self._tone_cnt[:], self._tone_out[:], self._noise_cnt, self._lfsr,
            self._env_cnt, self._env_step, self._env_attack, self._env_alternate,
            self._env_hold_flag, self._env_holding, self._clk_frac,
        )

    def _restore_gen(self, s: _GenState) -> None:
        (tc, to, self._noise_cnt, self._lfsr, self._env_cnt, self._env_step,
         self._env_attack, self._env_alternate, self._env_hold_flag,
         self._env_holding, self._clk_frac) = s
        self._tone_cnt[:] = tc
        self._tone_out[:] = to
