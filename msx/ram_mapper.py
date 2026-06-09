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
        banks: Four bank-register values (one per 16 KB page). Initial
            values [3, 2, 1, 0] match the MSX2 BIOS default: the top of
            physical RAM appears in the lowest logical page.
    """

    ram: bytearray = field(default_factory=lambda: bytearray(_RAM_SIZE))
    banks: list[int] = field(default_factory=lambda: [3, 2, 1, 0])

    def _phys(self, addr: int) -> int:
        page = (addr & 0xFFFF) >> 14
        bank = self.banks[page] & 0x07
        return bank * _BANK_SIZE + (addr & 0x3FFF)

    def read(self, addr: int) -> int:
        return self.ram[self._phys(addr)]

    def write(self, addr: int, value: int) -> None:
        self.ram[self._phys(addr)] = value & 0xFF

    def read_port(self, port: int) -> int:
        """Return bank register for the page corresponding to port 0xFC–0xFF."""
        return self.banks[(port - 0xFC) & 0x03] & 0x07

    def write_port(self, port: int, value: int) -> None:
        """Set bank register for the page corresponding to port 0xFC–0xFF."""
        self.banks[(port - 0xFC) & 0x03] = value & 0x07
