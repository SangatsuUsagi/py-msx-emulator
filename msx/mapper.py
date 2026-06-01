from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

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
        if self.cartridge is None:
            return 0xFF
        offset = addr - 0x4000
        if 0 <= offset < len(self.cartridge):
            return self.cartridge[offset]
        return 0xFF

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
    """Konami (Konami4/SCC) mapper: four 8 KB windows.

    Window 0 (0x4000–0x5FFF) is permanently fixed to page 0.
    Windows 1–3 are switched by writing the page index to an address
    within the window itself (0x6000–0x7FFF, 0x8000–0x9FFF, 0xA000–0xBFFF).
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
        if 0x6000 <= addr < 0x8000:
            self._banks[1] = value % self._num_pages()
        elif 0x8000 <= addr < 0xA000:
            self._banks[2] = value % self._num_pages()
        elif 0xA000 <= addr < 0xC000:
            self._banks[3] = value % self._num_pages()
        # Writes to 0x4000–0x5FFF are ignored; window 0 is fixed to page 0.
