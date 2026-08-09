"""MSX2 system RAM mapper (8 × 16 KB banks, 128 KB total).

Ports 0xFC–0xFF select which 16 KB bank is visible in each of the four
16 KB pages of the Z80 address space when slot 3 is active.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_BANK_SIZE = 0x4000  # 16 KB
_NUM_BANKS = 8
_RAM_SIZE = _NUM_BANKS * _BANK_SIZE  # 131072 bytes


@dataclass
class RamMapper:
    """128 KB banked RAM for MSX2.

    Attributes:
        ram: 131072-byte backing store.
        banks: Four bank-register values (one per 16 KB page). Power-on/reset
            value is [0, 0, 0, 0] -- all pages start aliased to bank 0
            (openMSX MSXMemoryMapperBase::reset(): "Most mappers initialize
            to segment 0 for all pages"). It is the MSX2 BIOS boot routine,
            not the hardware, that subsequently writes [3, 2, 1, 0] (top of
            physical RAM in the lowest logical page) via ports 0xFC-0xFF.
    """

    ram: bytearray = field(default_factory=lambda: bytearray(_RAM_SIZE))
    banks: list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    def reset(self) -> None:
        """Restore power-on/reset bank state. RAM contents are retained,
        matching Machine.reset()'s "Memory/VRAM contents are retained"."""
        self.banks[:] = [0, 0, 0, 0]

    def _phys(self, addr: int) -> int:
        page = (addr & 0xFFFF) >> 14
        bank = self.banks[page] & 0x07
        return bank * _BANK_SIZE + (addr & 0x3FFF)

    def read(self, addr: int) -> int:
        return self.ram[self._phys(addr)]

    def write(self, addr: int, value: int) -> None:
        self.ram[self._phys(addr)] = value & 0xFF

    def read_port(self, port: int) -> int:
        """Return bank register for the page corresponding to port 0xFC–0xFF.

        Only the low 3 bits are meaningful (8 x 16KB banks); the unused high
        bits read back as 1, not 0 (openMSX MSXMemoryMapperBase::peekIO,
        `registers[page] | ~bankMask` — real hardware sets them, it doesn't
        clear them).
        """
        return (self.banks[(port - 0xFC) & 0x03] & 0x07) | 0xF8

    def write_port(self, port: int, value: int) -> None:
        """Set bank register for the page corresponding to port 0xFC–0xFF."""
        self.banks[(port - 0xFC) & 0x03] = value & 0x07
