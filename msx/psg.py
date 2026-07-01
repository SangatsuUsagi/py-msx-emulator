from __future__ import annotations

from dataclasses import dataclass, field

from msx.input import InputState

_REG_IO_PORT_A = 14

PSG_CLOCK: int = 223_722      # AY-3-8910: 3,579,545 Hz / 16
SAMPLE_RATE: int = 44_100
SAMPLES_PER_FRAME: int = 735  # 44100 // 60

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
            self.regs[self.latch] = value
            if self.latch == 13:
                self._reset_envelope()

    def read_port(self, port: int) -> int:
        if port == 0xA2:
            if self.latch == _REG_IO_PORT_A and self._input is not None:
                # PSG register 15 bit 6 = JOY_SELECT: 0→Joy1 dirs, 1→Joy2 dirs on bits 0-3
                joy_select = (self.regs[15] >> 6) & 1
                joy1 = self._input.joy1
                joy2 = self._input.joy2
                dir_bits = (joy1 if joy_select == 0 else joy2) & 0x0F
                trig_bits = (joy1 & 0x30) | ((joy2 & 0x30) << 2)
                return dir_bits | trig_bits
            return self.regs[self.latch]
        return 0xFF

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
        # 32-step counter with a 16-level (>>1) output: 2 steps per level, so a
        # step advances every period*8 ticks to keep the per-level rate (and full
        # ramp duration) identical to a 16-level period*16 model.
        self._env_cnt = self._env_period() * 8

    # ---------------------------------------------------------- tone channels

    def _step_tone(self, ch: int, ticks: int) -> None:
        fine = self.regs[ch * 2]
        coarse = self.regs[ch * 2 + 1] & 0x0F
        period = max(1, (coarse << 8) | fine)
        self._tone_cnt[ch] -= ticks
        while self._tone_cnt[ch] <= 0:
            self._tone_cnt[ch] += period
            self._tone_out[ch] ^= 1

    # --------------------------------------------------------------- noise

    def _step_noise(self, ticks: int) -> None:
        period = max(1, self.regs[6] & 0x1F)
        self._noise_cnt -= ticks
        while self._noise_cnt <= 0:
            self._noise_cnt += period
            # 17-bit LFSR, polynomial x^17 + x^14 + 1 (feedback from bits 0 and 3)
            feedback = (self._lfsr ^ (self._lfsr >> 3)) & 1
            self._lfsr = ((self._lfsr >> 1) | (feedback << 16)) & 0x1FFFF

    # ------------------------------------------------------------ envelope

    def _step_envelope(self, ticks: int) -> None:
        if self._env_holding:
            return
        step_ticks = self._env_period() * 8  # one step per period×8 PSG ticks
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
                if self._env_alternate and (self._env_step & 0x20):
                    self._env_attack ^= 0x1F
                self._env_step &= 0x1F

    # --------------------------------------------------------------- mixer

    def _mix_channel(self, ch: int) -> int:
        r7 = self.regs[7]
        tone_en = not ((r7 >> ch) & 1)
        noise_en = not ((r7 >> (ch + 3)) & 1)
        if not tone_en and not noise_en:
            return 1  # both disabled → constant high (volume sets amplitude)
        tone_bit = self._tone_out[ch] if tone_en else 0
        noise_bit = (self._lfsr & 1) if noise_en else 0
        return tone_bit | noise_bit

    # ---------------------------------------------------- sample generation

    def generate_samples(self, n: int) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples."""
        out = bytearray(n * 2)
        regs = self.regs  # local binding for the hot per-sample loop
        for i in range(n):
            # Advance PSG clock by one sample's worth of ticks (integer arithmetic).
            self._clk_frac += PSG_CLOCK
            ticks = self._clk_frac // SAMPLE_RATE
            self._clk_frac %= SAMPLE_RATE

            for ch in range(3):
                self._step_tone(ch, ticks)
            self._step_noise(ticks)
            self._step_envelope(ticks)

            # Sum channel amplitudes.
            env_amp = _VOL_TABLE[self._env_output_level()]
            sample = 0
            for ch in range(3):
                if self._mix_channel(ch):
                    vol_reg = regs[8 + ch]
                    if vol_reg & 0x10:
                        sample += env_amp
                    else:
                        sample += _VOL_TABLE[vol_reg & 0x0F]

            if sample > 32767:
                sample = 32767

            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        return out
