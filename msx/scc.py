from __future__ import annotations

from dataclasses import dataclass, field

from msx.psg import SAMPLE_RATE, SAMPLES_PER_FRAME

SCC_CLOCK: int = 3_579_545  # Hz — full MSX CPU clock
SCC_SCALE: int = 6          # per-channel amplitude scale factor

__all__ = ["SCC", "SCC_CLOCK", "SAMPLES_PER_FRAME"]


@dataclass
class SCC:
    # 4 waveform banks: channels 1-3 independent, channel 4+5 share bank 3.
    _waves: list[list[int]] = field(
        default_factory=lambda: [[0] * 32 for _ in range(4)], init=False, repr=False
    )
    # Frequency registers: 12-bit per channel (0–4095).
    _freq: list[int] = field(default_factory=lambda: [0] * 5, init=False, repr=False)
    # Volume registers: 4-bit per channel (0–15).
    _vol: list[int] = field(default_factory=lambda: [0] * 5, init=False, repr=False)
    # Channel enable: bit N = channel N+1 (bits 0–4).
    _enable: int = field(default=0, init=False, repr=False)
    # Synthesis state.
    _phase_cnt: list[int] = field(default_factory=lambda: [0] * 5, init=False, repr=False)
    _phase_idx: list[int] = field(default_factory=lambda: [0] * 5, init=False, repr=False)
    _clk_frac: int = field(default=0, init=False, repr=False)

    # ------------------------------------------------------------------ I/O

    def read(self, addr: int) -> int:
        """Return the register byte at the given offset from 0x9800."""
        addr = addr & 0xFF
        if addr < 0x80:
            # Waveform banks: 4 × 32 bytes at offsets 0x00, 0x20, 0x40, 0x60.
            bank = addr >> 5        # 0–3
            byte = addr & 0x1F      # 0–31
            return self._waves[bank][byte] & 0xFF
        if addr <= 0x89:
            # Frequency registers: pairs (low, high) for channels 1–5.
            ch = (addr - 0x80) >> 1
            if (addr & 1) == 0:
                return self._freq[ch] & 0xFF
            else:
                return (self._freq[ch] >> 8) & 0x0F
        if addr <= 0x8E:
            return self._vol[addr - 0x8A] & 0x0F
        if addr == 0x8F:
            return self._enable & 0x1F
        # Offsets 0x90-0xFF (incl. the deformation register at 0xE0-0xFF) read
        # back as 0xFF. Reading the deformation range is a harmless no-op here;
        # rotation / frequency-mode emulation is intentionally omitted.
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        """Write value to the register at the given offset from 0x9800."""
        addr = addr & 0xFF
        value = value & 0xFF
        if addr < 0x80:
            bank = addr >> 5
            byte = addr & 0x1F
            self._waves[bank][byte] = value
            return
        if addr <= 0x89:
            ch = (addr - 0x80) >> 1
            if (addr & 1) == 0:
                self._freq[ch] = (self._freq[ch] & 0xF00) | value
            else:
                self._freq[ch] = (self._freq[ch] & 0x0FF) | ((value & 0x0F) << 8)
            return
        if addr <= 0x8E:
            self._vol[addr - 0x8A] = value & 0x0F
            return
        if addr == 0x8F:
            self._enable = value & 0x1F
            return
        # Offsets 0x90-0xFF (incl. the deformation register at 0xE0-0xFF) are a
        # safe no-op: they do not alter waveform/frequency/volume/enable state.

    # -------------------------------------------------------- sample generation

    def generate_samples(self, n: int) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples."""
        out = bytearray(n * 2)
        for i in range(n):
            self._clk_frac += SCC_CLOCK
            ticks = self._clk_frac // SAMPLE_RATE
            self._clk_frac %= SAMPLE_RATE

            # Single pass over the 5 channels: advance the phase (divmod instead
            # of a subtract-loop) and accumulate the sample in one go.
            sample = 0
            for ch in range(5):
                period = max(1, self._freq[ch] + 1)
                steps, self._phase_cnt[ch] = divmod(self._phase_cnt[ch] + ticks, period)
                idx = (self._phase_idx[ch] + steps) & 31  # 32-entry wave, & 31 == % 32
                self._phase_idx[ch] = idx
                if not (self._enable >> ch) & 1:
                    continue
                wave_bank = min(ch, 3)  # ch 4 and 5 (idx 3,4) both use bank 3
                raw = self._waves[wave_bank][idx]
                # Convert unsigned byte to signed 8-bit.
                signed = raw if raw < 128 else raw - 256
                sample += signed * self._vol[ch] * SCC_SCALE

            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768

            # Signed 16-bit little-endian; masking yields two's-complement bytes.
            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        return out
