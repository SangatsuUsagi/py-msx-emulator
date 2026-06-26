from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from msx.scc import SCC

_PAGE_8K = 8192
_PAGE_16K = 16384


class Mapper(Protocol):
    def read(self, addr: int) -> int: ...
    def write(self, addr: int, value: int) -> None: ...


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
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)

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
            self._banks[reg] = value % self._num_pages()


@dataclass
class Ascii16Mapper:
    """ASCII16 mapper: two 16 KB windows at 0x4000 and 0x8000.

    Control registers at 0x6000–0x6FFF (window 0) and 0x7000–0x7FFF (window 1).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1], repr=False)

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
            self._banks[window] = value % self._num_pages()


@dataclass
class KonamiMapper:
    """Konami (Konami4) mapper: four 8 KB windows.

    Window 0 (0x4000–0x5FFF) is permanently fixed to page 0.
    Windows 1–3 are switched by writing the page index to an address
    within the window itself (0x6000–0x7FFF, 0x8000–0x9FFF, 0xA000–0xBFFF).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)

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
            self._banks[1] = value & self._bank_mask()
        elif 0x8000 <= addr < 0xA000:
            self._banks[2] = value & self._bank_mask()
        elif 0xA000 <= addr < 0xC000:
            self._banks[3] = value & self._bank_mask()
        # Writes to 0x4000–0x5FFF are ignored; window 0 is fixed to page 0.


@dataclass
class MajutsushiMapper(KonamiMapper):
    """Konami mapper + DAC for Hai no Majutsushi.

    Writes to 0x5000–0x5FFF are routed to the DAC (8-bit PCM).
    All other behaviour is identical to KonamiMapper.
    """

    _dac_value: int = field(default=0, init=False, repr=False)

    def write(self, addr: int, value: int) -> None:
        if 0x5000 <= addr < 0x6000:
            self._dac_value = value
        else:
            super().write(addr, value)

    @property
    def dac_value(self) -> int:
        return self._dac_value


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
            self._banks[0] = value % self._num_pages()
        elif 0x7000 <= addr < 0x7800:
            self._banks[1] = value % self._num_pages()
        elif 0x9000 <= addr < 0x9800:
            # Window 2 bank register: 0x3F enables SCC mode; any other value disables it.
            if value == 0x3F:
                self._scc_mode = True
            else:
                self._scc_mode = False
                self._banks[2] = value % self._num_pages()
        elif 0xB000 <= addr < 0xB800:
            self._banks[3] = value % self._num_pages()
        # Writes outside the four register zones are ignored.
