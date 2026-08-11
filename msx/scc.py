from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict, cast

from msx.psg import SAMPLE_RATE, SAMPLES_PER_FRAME

SCC_CLOCK: int = 3_579_545  # Hz — full MSX CPU clock
SCC_SCALE: int = 6          # per-channel amplitude scale factor

__all__ = ["SCC", "SCC_CLOCK", "SAMPLES_PER_FRAME", "SccState"]


class SccState(TypedDict):
    """Save-state schema for SCC.snapshot()/SCC.restore().

    Covers all of SCC's persistent state (waveform banks, frequency/volume/
    enable registers, and synthesis phase state) -- unlike PSG, SCC has no
    separate register-file/synth split, so one method pair covers it all.
    """

    _waves: list[list[int]]
    _freq: list[int]
    _vol: list[int]
    _enable: int
    _phase_cnt: list[int]
    _phase_idx: list[int]
    _clk_frac: int


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
        # Offsets 0x80-0xFF (frequency/volume/enable block at 0x80-0x9F,
        # mirrored twice; the deformation register at 0xE0-0xFF; and the
        # no-function gap between) are all write-only on real hardware and
        # read back as 0xFF. Reading the deformation range is a harmless
        # no-op here; rotation / frequency-mode emulation is intentionally
        # omitted.
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
        if addr < 0xA0:
            # Frequency/volume/enable block, mirrored at 0x80-0x8F and
            # 0x90-0x9F: only the low 4 bits of the offset are decoded.
            reg = addr & 0x0F
            if reg <= 0x09:
                ch = reg >> 1
                if (reg & 1) == 0:
                    self._freq[ch] = (self._freq[ch] & 0xF00) | value
                else:
                    self._freq[ch] = (self._freq[ch] & 0x0FF) | ((value & 0x0F) << 8)
            elif reg <= 0x0E:
                self._vol[reg - 0x0A] = value & 0x0F
            else:
                self._enable = value & 0x1F
            return
        # Offsets 0xA0-0xFF (incl. the deformation register at 0xE0-0xFF) are
        # a safe no-op: they do not alter waveform/frequency/volume/enable
        # state.

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register and synthesis state (matches field defaults)."""
        self._waves = [[0] * 32 for _ in range(4)]
        self._freq = [0] * 5
        self._vol = [0] * 5
        self._enable = 0
        self._phase_cnt = [0] * 5
        self._phase_idx = [0] * 5
        self._clk_frac = 0

    # ------------------------------------------------------------ save-state

    def snapshot(self) -> SccState:
        """Capture full chip state for save-state (paired with restore)."""
        return {
            "_waves": [list(w) for w in self._waves],
            "_freq": list(self._freq),
            "_vol": list(self._vol),
            "_enable": self._enable,
            "_phase_cnt": list(self._phase_cnt),
            "_phase_idx": list(self._phase_idx),
            "_clk_frac": self._clk_frac,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Restore chip state produced by snapshot()."""
        typed_state = cast(SccState, state)
        self._waves = [list(w) for w in typed_state["_waves"]]
        self._freq = list(typed_state["_freq"])
        self._vol = list(typed_state["_vol"])
        self._enable = int(typed_state["_enable"])
        self._phase_cnt = list(typed_state["_phase_cnt"])
        self._phase_idx = list(typed_state["_phase_idx"])
        self._clk_frac = int(typed_state["_clk_frac"])

    # -------------------------------------------------------- sample generation

    def generate_samples(self, n: int) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples.

        Hot path (audio callback, 735 samples/frame): the registers are constant
        across a buffer (the CPU only writes SCC registers between buffers), so
        the per-channel period / volume / enable / waveform bank are precomputed
        once, and the phase state is bound to locals — mirroring PSG.generate_
        samples. Phase still advances for every channel each sample (even
        disabled ones); only the accumulation is gated by enable. Behaviour is
        identical to the straightforward per-sample form.
        """
        out = bytearray(n * 2)
        freq = self._freq
        vol = self._vol
        waves = self._waves
        enable = self._enable
        # --- per-channel buffer-constant precompute ---
        period = [max(1, freq[ch] + 1) for ch in range(5)]
        volsc = [vol[ch] * SCC_SCALE for ch in range(5)]
        en = [(enable >> ch) & 1 for ch in range(5)]
        wave = [waves[ch if ch < 3 else 3] for ch in range(5)]  # ch 4/5 share bank 3
        # --- bind phase state to locals (lists mutated in place; scalar clk written back) ---
        pc = self._phase_cnt
        pi = self._phase_idx
        clk = self._clk_frac

        for i in range(n):
            clk += SCC_CLOCK
            ticks = clk // SAMPLE_RATE
            clk %= SAMPLE_RATE

            sample = 0
            for ch in range(5):
                steps, pc[ch] = divmod(pc[ch] + ticks, period[ch])
                idx = (pi[ch] + steps) & 31  # 32-entry wave, & 31 == % 32
                pi[ch] = idx
                if en[ch]:
                    raw = wave[ch][idx]
                    signed = raw if raw < 128 else raw - 256  # unsigned byte -> signed 8-bit
                    sample += signed * volsc[ch]

            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768

            # Signed 16-bit little-endian; masking yields two's-complement bytes.
            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        self._clk_frac = clk
        return out
