from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger
    from msx.mapper import Mapper

from msx.mapper import FlatMapper


@dataclass
class Memory:
    rom: bytes
    ram: bytearray
    _mapper: "Mapper" = field(repr=False)
    _mapper2: "Mapper" = field(default_factory=lambda: FlatMapper(None), repr=False)
    # Default: page0+1=slot0(BIOS), page1+2=slot1(cart), page3=slot3(RAM)
    # 0b11_01_01_00 = 0xD4
    slot_register: int = 0xD4
    _logger: DebugLogger | None = field(default=None, repr=False)

    def _slot(self, addr: int) -> int:
        page = (addr >> 14) & 0x03
        return (self.slot_register >> (page * 2)) & 0x03

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        slot = (self.slot_register >> ((addr >> 14) * 2)) & 0x03  # page 0-3 → slot 0-3
        if slot == 0:
            return self.rom[addr] if addr < len(self.rom) else 0xFF
        if slot == 1:
            return self._mapper.read(addr)
        if slot == 2:
            return self._mapper2.read(addr)
        # slot 3: 32 KB RAM mapped to 0x8000-0xFFFF; addr - 0x8000 gives the array index.
        # Pages 0/1 selecting slot 3 are not a standard MSX1 use case and are not supported.
        return self.ram[addr - 0x8000]

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        slot = (self.slot_register >> ((addr >> 14) * 2)) & 0x03  # page 0-3 → slot 0-3
        if slot == 0:
            return  # BIOS ROM is read-only
        if slot == 1:
            self._mapper.write(addr, value)
            return
        if slot == 2:
            self._mapper2.write(addr, value)
            return
        # slot 3: RAM
        self.ram[addr - 0x8000] = value

    def read_port_a8(self) -> int:
        return self.slot_register & 0xFF

    def write_port_a8(self, value: int) -> None:
        old = self.slot_register
        self.slot_register = value & 0xFF
        if self._logger is not None:
            self._logger.on_slot_register_write(old, self.slot_register, pc=0)
