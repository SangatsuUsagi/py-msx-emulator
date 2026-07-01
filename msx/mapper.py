from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from msx.mapper_tracer import MapperTracer
    from msx.scc import SCC

_PAGE_8K = 8192
_PAGE_16K = 16384


class Mapper(Protocol):
    def read(self, addr: int) -> int: ...
    def write(self, addr: int, value: int) -> None: ...


def _trace_bank(mapper: object, window: int, old: int, new: int, addr: int) -> None:
    """Notify an injected MapperTracer of a bank change. No-op without a tracer."""
    tracer = getattr(mapper, "_tracer", None)
    if tracer is None or old == new:
        return
    get_pc = mapper._get_pc  # type: ignore[attr-defined]
    get_cycle = mapper._get_cycle  # type: ignore[attr-defined]
    get_frame = mapper._get_frame  # type: ignore[attr-defined]
    tracer.bank_change(
        window, old, new, addr,
        get_pc() if get_pc else 0,
        get_cycle() if get_cycle else 0,
        get_frame() if get_frame else 0,
    )


@dataclass
class FlatMapper:
    """Flat (non-bank-switching) cartridge mapper. Reproduces the original behaviour."""

    cartridge: bytes | None

    def read(self, addr: int) -> int:
        if not self.cartridge:
            return 0xFF
        # Mirror the ROM across the full cartridge region (e.g., 8 KB ROM repeats in 32 KB space).
        offset = (addr - 0x4000) % len(self.cartridge)
        return self.cartridge[offset]

    def write(self, addr: int, value: int) -> None:
        pass


@dataclass
class Ascii8Mapper:
    """ASCII8 mapper: four 8 KB windows at 0x4000/0x6000/0x8000/0xA000.

    Control registers written to 0x6000–0x7FFF select which ROM page each
    window shows.  The 2 KB sub-ranges (determined by bits 12:11 of the write
    address) map to windows 0–3 respectively.

    Power-on state: all four windows select bank 0 (matches real ASCII8 hardware
    and openMSX, which reset all segment registers to 0). Same rationale as
    Ascii16Mapper: a cartridge INIT may rely on the upper windows mirroring
    bank 0 before it switches banks itself.
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0, 0, 0], repr=False)
    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def read(self, addr: int) -> int:
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            # Bits 12:11 of address select register 0–3
            reg = (addr >> 11) & 0x03
            new = value % self._num_pages()
            old = self._banks[reg]
            self._banks[reg] = new
            _trace_bank(self, reg, old, new, addr)


@dataclass
class Ascii16Mapper:
    """ASCII16 mapper: two 16 KB windows at 0x4000 and 0x8000.

    Control registers at 0x6000–0x6FFF (window 0) and 0x7000–0x7FFF (window 1).

    Power-on state: both windows select bank 0 (matches real ASCII16 hardware
    and openMSX, which reset all segment registers to 0). Some games rely on the
    0x8000 window mirroring bank 0 at startup — e.g. their cartridge INIT does
    `JP 8031h` into bank-0 code visible through the second window.
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0], repr=False)
    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_16K)

    def read(self, addr: int) -> int:
        if addr < 0x8000:
            window, base = 0, 0x4000
        else:
            window, base = 1, 0x8000
        page_offset = self._banks[window] * _PAGE_16K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            # Bit 12 selects window 0 (0x6xxx) or window 1 (0x7xxx)
            window = (addr >> 12) & 0x01
            new = value % self._num_pages()
            old = self._banks[window]
            self._banks[window] = new
            _trace_bank(self, window, old, new, addr)


@dataclass
class KonamiMapper:
    """Konami (Konami4) mapper: four 8 KB windows.

    Window 0 (0x4000–0x5FFF) is permanently fixed to page 0.
    Windows 1–3 are switched by writing the page index to an address
    within the window itself (0x6000–0x7FFF, 0x8000–0x9FFF, 0xA000–0xBFFF).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)

    def _bank_mask(self) -> int:
        # Konami4 hardware: 5-bit bank register → 32 pages (256 KB) max.
        # Use power-of-2 bitmask capped at 31, matching OpenMSX setBlockMask(31).
        # Avoids modulo aliasing when ROM > 256 KB (upper half is padding/inaccessible).
        pages = min(max(1, len(self.rom) // _PAGE_8K), 32)
        m = 1
        while m < pages:
            m <<= 1
        return m - 1

    def read(self, addr: int) -> int:
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr < 0x8000:
            window = 1
        elif 0x8000 <= addr < 0xA000:
            window = 2
        elif 0xA000 <= addr < 0xC000:
            window = 3
        else:
            # Writes to 0x4000–0x5FFF are ignored; window 0 is fixed to page 0.
            return
        new = value & self._bank_mask()
        old = self._banks[window]
        self._banks[window] = new
        _trace_bank(self, window, old, new, addr)


@dataclass
class MajutsushiMapper(KonamiMapper):
    """Konami mapper + DAC for Hai no Majutsushi.

    Writes to 0x5000–0x5FFF are routed to the DAC (8-bit unsigned PCM).
    All other behaviour is identical to KonamiMapper.

    DAC writes are timestamped via _get_cycle callback so generate_samples()
    can reproduce sub-frame timing (same role as openMSX's BlipBuffer delta).
    """

    _last_dac: int = field(default=0x80, init=False, repr=False)
    _dac_events: list[tuple[int, int]] = field(default_factory=list, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)

    def write(self, addr: int, value: int) -> None:
        if 0x5000 <= addr < 0x6000:
            cycle = self._get_cycle() if self._get_cycle else 0
            self._dac_events.append((cycle, value & 0xFF))
        else:
            super().write(addr, value)

    def generate_samples(self, n: int, frame_start: int = 0, frame_end: int = 0) -> bytearray:
        """Return n signed 16-bit LE mono PCM samples from this frame's DAC events.

        frame_start / frame_end: machine.cycle_count at the frame boundaries.
        DAC events recorded during the frame are mapped to sample positions
        proportionally, matching openMSX DACSound8U delta-at-time behaviour.
        Conversion: (uint8 value - 0x80) * 256 → int16.
        """
        events = self._dac_events
        self._dac_events = []

        cycles = frame_end - frame_start if frame_end > frame_start else 1
        out = bytearray(n * 2)
        value = self._last_dac
        ev_idx = 0

        for i in range(n):
            threshold = frame_start + i * cycles // n
            while ev_idx < len(events) and events[ev_idx][0] <= threshold:
                value = events[ev_idx][1]
                ev_idx += 1
            sample = (value - 0x80) * 256
            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        if ev_idx < len(events):
            value = events[-1][1]
        self._last_dac = value
        return out


@dataclass
class KonamiSCCMapper:
    """Konami SCC mapper: same 8 KB bank switching as KonamiMapper, extended
    with SCC mode.

    When the window-2 bank register is set to 0x3F, the address range
    0x9800–0x9FFF is redirected to SCC registers instead of ROM.

    All four windows are switchable. Each bank register occupies only the
    low 2 KB of its window's register zone:
        bank 0 (0x4000): 0x5000–0x57FF
        bank 1 (0x6000): 0x7000–0x77FF
        bank 2 (0x8000): 0x9000–0x97FF  (0x3F enables SCC)
        bank 3 (0xA000): 0xB000–0xB7FF
    Decoding the whole window would wrongly treat ordinary writes (e.g. a
    BIOS RAM test hitting 0xBF00) as bank switches.
    """

    rom: bytes
    scc: "SCC"
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _scc_mode: bool = field(default=False, init=False, repr=False)
    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def read(self, addr: int) -> int:
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            return self.scc.read(addr - 0x9800)
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        # SCC register writes take priority over bank-register writes.
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            self.scc.write(addr - 0x9800, value)
            return
        if 0x5000 <= addr < 0x5800:
            new = value % self._num_pages()
            old = self._banks[0]
            self._banks[0] = new
            _trace_bank(self, 0, old, new, addr)
        elif 0x7000 <= addr < 0x7800:
            new = value % self._num_pages()
            old = self._banks[1]
            self._banks[1] = new
            _trace_bank(self, 1, old, new, addr)
        elif 0x9000 <= addr < 0x9800:
            # Window 2 bank register: 0x3F enables SCC mode; any other value disables it.
            if value == 0x3F:
                self._scc_mode = True
            else:
                self._scc_mode = False
                new = value % self._num_pages()
                old = self._banks[2]
                self._banks[2] = new
                _trace_bank(self, 2, old, new, addr)
        elif 0xB000 <= addr < 0xB800:
            new = value % self._num_pages()
            old = self._banks[3]
            self._banks[3] = new
            _trace_bank(self, 3, old, new, addr)
        # Writes outside the four register zones are ignored.
