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
    _env_cnt: int = field(default=1, init=False, repr=False)
    _env_level: int = field(default=15, init=False, repr=False)
    _env_dir: int = field(default=-1, init=False, repr=False)  # +1 or -1
    _env_hold: bool = field(default=False, init=False, repr=False)
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
                return self._input.joystick
            return self.regs[self.latch]
        return 0xFF

    # --------------------------------------------------------- envelope reset

    def _reset_envelope(self) -> None:
        shape = self.regs[13] & 0x0F
        # Shapes 4-11 begin with an attack (count up); all others begin with decay.
        if 4 <= shape <= 11:
            self._env_level = 0
            self._env_dir = 1
        else:
            self._env_level = 15
            self._env_dir = -1
        self._env_hold = False
        period = max(1, (self.regs[12] << 8) | self.regs[11])
        self._env_cnt = period * 16

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
        if self._env_hold:
            return
        period = max(1, (self.regs[12] << 8) | self.regs[11])
        step_ticks = period * 16  # one level change per period×16 PSG ticks
        self._env_cnt -= ticks
        while self._env_cnt <= 0:
            self._env_cnt += step_ticks
            self._env_level += self._env_dir
            if self._env_level < 0 or self._env_level > 15:
                self._envelope_boundary()
                if self._env_hold:
                    break

    def _envelope_boundary(self) -> None:
        shape = self.regs[13] & 0x0F
        if shape < 8:
            # Single-shot: hold at 0 regardless of attack/decay direction.
            self._env_level = 0
            self._env_hold = True
        elif shape == 8:    # //// attack repeat
            self._env_level = 0
        elif shape == 9:    # /^^^ attack, hold at 15
            self._env_level = 15
            self._env_hold = True
        elif shape == 10:   # /\/\ triangle
            self._env_dir = -self._env_dir
            self._env_level = 0 if self._env_dir == 1 else 15
        elif shape == 11:   # /^^^ (same as 9)
            self._env_level = 15
            self._env_hold = True
        elif shape == 12:   # \\\\ decay repeat
            self._env_level = 15
        elif shape == 13:   # \___ decay, hold at 0
            self._env_level = 0
            self._env_hold = True
        elif shape == 14:   # \/\/ inverted triangle
            self._env_dir = -self._env_dir
            self._env_level = 0 if self._env_dir == 1 else 15
        else:               # shape == 15: \^^^ decay, hold at 15
            self._env_level = 15
            self._env_hold = True

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
            sample = 0
            for ch in range(3):
                if self._mix_channel(ch):
                    vol_reg = self.regs[8 + ch]
                    if vol_reg & 0x10:
                        sample += _VOL_TABLE[self._env_level]
                    else:
                        sample += _VOL_TABLE[vol_reg & 0x0F]

            if sample > 32767:
                sample = 32767

            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        return out
