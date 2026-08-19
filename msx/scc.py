from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict, cast

from msx.psg import SAMPLE_RATE, SAMPLES_PER_FRAME

SCC_CLOCK: int = 3_579_545  # Hz — full MSX CPU clock
SCC_SCALE: int = 6          # per-channel amplitude scale factor
NUM_CHANNELS: int = 5
WAVE_BANKS: int = 5
WAVE_LEN: int = 32          # bytes per waveform bank, and steps per playback cycle

# PORT-NOTE: _waves (WAVE_BANKS banks of WAVE_LEN bytes) and
#   _freq/_vol/_phase_cnt/_phase_idx (NUM_CHANNELS entries each) are
#   fixed-length for the object's whole lifetime -- constructed once and only
#   ever indexed, never resized or appended to.
# Rust equivalent: fixed-size arrays ([[u8; 32]; 5], [u16; 5], etc.), not
#   Vec/Vec<Vec<_>> -- the same convention msx/mapper.py's fixed-length-list
#   notes document for their own fields.
# C++ equivalent: std::array<std::array<uint8_t,32>,5>, std::array<uint16_t,5>,
#   etc.
# Kept as-is here because: no fixed-size array type is idiomatic in plain
#   Python; the fields are still constant-size in practice, so a plain list
#   costs nothing extra here versus a hypothetical fixed-array wrapper.

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
    # 5 waveform banks, one per channel. In Compatible mode (the default) a
    # write to bank 3 (channel 4) is mirrored into bank 4 as well, since
    # channel 5 has no independent waveform of its own on real Compatible-mode
    # hardware; Plus mode writes bank 4 directly, no mirroring. See write().
    _waves: list[list[int]] = field(
        default_factory=lambda: [[0] * WAVE_LEN for _ in range(WAVE_BANKS)],
        init=False, repr=False
    )
    # Frequency registers: 12-bit per channel (0–4095).
    _freq: list[int] = field(default_factory=lambda: [0] * NUM_CHANNELS, init=False, repr=False)
    # Volume registers: 4-bit per channel (0–15).
    _vol: list[int] = field(default_factory=lambda: [0] * NUM_CHANNELS, init=False, repr=False)
    # Channel enable: bit N = channel N+1 (bits 0–4).
    _enable: int = field(default=0, init=False, repr=False)
    # Synthesis state.
    _phase_cnt: list[int] = field(
        default_factory=lambda: [0] * NUM_CHANNELS, init=False, repr=False
    )
    _phase_idx: list[int] = field(
        default_factory=lambda: [0] * NUM_CHANNELS, init=False, repr=False
    )
    _clk_frac: int = field(default=0, init=False, repr=False)
    # Compatible mode (default): wave region 0x00-0x7F (banks 0-3), freq/vol/
    # enable at 0x80-0x9F. Plus mode: wave region 0x00-0x9F (banks 0-4, all
    # independent), freq/vol/enable at 0xA0-0xBF. Not part of SccState — a
    # cartridge mapper (e.g. SCCICart) re-derives it from its own saved mode
    # register on restore(), same as reset() re-derives it below.
    _plus_mode: bool = field(default=False, init=False, repr=False)
    # Chip identity, fixed for the instance's whole lifetime (never toggled by
    # set_mode() or reset() — unlike real hardware, this codebase has no
    # separate chip class per variant, so this flag stands in for "which
    # physical chip is this"). False (default): a real Konami 051649 —
    # the only chip KonamiSCCMapper ever carries. True: a Konami/SCC-I
    # 052539 (msx/mapper.py:SCCICart's chip). See read() for the one
    # Compatible-mode behaviour this changes.
    is_052539: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ I/O

    def set_mode(self, plus: bool) -> None:
        """Select Plus mode (5 independent waveform banks) or Compatible mode
        (channel 5 mirrors channel 4's bank). Does not alter register
        contents, only how read()/write() decode the address offset."""
        self._plus_mode = plus

    def read(self, addr: int) -> int:
        """Return the register byte at the given offset from 0x9800
        (Compatible mode) or 0xB800 (Plus mode)."""
        addr = addr & 0xFF
        wave_limit = 0xA0 if self._plus_mode else 0x80
        if addr < wave_limit:
            # Waveform banks: 32 bytes each, one bank per channel.
            bank = addr >> 5
            byte = addr & 0x1F
            return self._waves[bank][byte] & 0xFF
        if self.is_052539 and not self._plus_mode and 0xA0 <= addr < 0xC0:
            # 052539 Compatible-mode quirk: channel 5's waveform bank is
            # separately readable here even though it is only writable via
            # channel 4's mirrored write (see write()) — real 051649 hardware
            # has no such storage and reads 0xFF through this whole range
            # instead (openMSX SCC::peekMem, Mode::Compatible vs Mode::Real).
            return self._waves[4][addr & 0x1F] & 0xFF
        # The frequency/volume/enable block, the deformation register, and any
        # no-function gap are all write-only on real hardware and read back as
        # 0xFF. Reading the deformation range is a harmless no-op here;
        # rotation / frequency-mode emulation is intentionally omitted.
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        """Write value to the register at the given offset from 0x9800
        (Compatible mode) or 0xB800 (Plus mode)."""
        addr = addr & 0xFF
        value = value & 0xFF
        wave_limit = 0xA0 if self._plus_mode else 0x80
        if addr < wave_limit:
            bank = addr >> 5
            byte = addr & 0x1F
            self._waves[bank][byte] = value
            if not self._plus_mode and bank == 3:
                # Compatible mode only: channel 5 has no independent waveform
                # of its own, so it mirrors whatever channel 4 was just
                # written (openMSX SCC::writeWave).
                self._waves[4][byte] = value
            return
        freq_vol_limit = 0xC0 if self._plus_mode else 0xA0
        if addr < freq_vol_limit:
            # Frequency/volume/enable block, mirrored twice within its 32-byte
            # span: only the low 4 bits of the offset are decoded.
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
        # The deformation register and any no-function gap are a safe no-op:
        # they do not alter waveform/frequency/volume/enable state.

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register and synthesis state (matches field
        defaults). Also forces Compatible mode, matching openMSX SCC::reset()
        (a cartridge mapper re-selects Plus mode afterward if its own mode
        register calls for it)."""
        self._waves = [[0] * WAVE_LEN for _ in range(WAVE_BANKS)]
        self._freq = [0] * NUM_CHANNELS
        self._vol = [0] * NUM_CHANNELS
        self._enable = 0
        self._phase_cnt = [0] * NUM_CHANNELS
        self._phase_idx = [0] * NUM_CHANNELS
        self._clk_frac = 0
        self._plus_mode = False

    # ------------------------------------------------------------ save-state
    # PORT-LIBRARY-NOTE: see msx/opll.py's snapshot()/restore() for the
    #   canonical note on this explicit-TypedDict-field save-state boundary
    #   pattern (shared by PSG/SCC/OPLL/FmPac) and its Rust serde /
    #   C++ nlohmann::json crate candidates.

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
        waves = [list(w) for w in typed_state["_waves"]]
        if len(waves) != WAVE_BANKS or any(len(bank) != WAVE_LEN for bank in waves):
            raise ValueError(
                f"SccState._waves must be {WAVE_BANKS} banks of {WAVE_LEN} bytes each"
            )
        self._waves = waves
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
        period = [max(1, freq[ch] + 1) for ch in range(NUM_CHANNELS)]
        volsc = [vol[ch] * SCC_SCALE for ch in range(NUM_CHANNELS)]
        en = [(enable >> ch) & 1 for ch in range(NUM_CHANNELS)]
        # One bank per channel (bank == channel index): in Compatible mode
        # bank 4 already mirrors bank 3's content via write()'s mirroring, so
        # no mode branch is needed here.
        wave = [waves[ch] for ch in range(NUM_CHANNELS)]
        wave_mask = WAVE_LEN - 1  # hoisted out of the hot loop below
        # --- bind phase state to locals (lists mutated in place; scalar clk written back) ---
        pc = self._phase_cnt
        pi = self._phase_idx
        clk = self._clk_frac

        for i in range(n):
            clk += SCC_CLOCK
            ticks = clk // SAMPLE_RATE
            clk %= SAMPLE_RATE

            sample = 0
            for ch in range(NUM_CHANNELS):
                steps, pc[ch] = divmod(pc[ch] + ticks, period[ch])
                idx = (pi[ch] + steps) & wave_mask  # wraps within one waveform
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
