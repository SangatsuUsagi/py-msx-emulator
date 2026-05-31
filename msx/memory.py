from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Memory:
    rom: bytes
    ram: bytearray
    cartridge: bytes | None
    slot_register: int = 0

    def _slot(self, addr: int) -> int:
        page = (addr >> 14) & 0x03
        return (self.slot_register >> (page * 2)) & 0x03

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        slot = self._slot(addr)
        if slot == 0:
            return self.rom[addr] if addr < len(self.rom) else 0xFF
        if slot == 1:
            if self.cartridge is not None:
                offset = addr - 0x4000
                return self.cartridge[offset] if 0 <= offset < len(self.cartridge) else 0xFF
            return 0xFF
        if slot == 2:
            return 0xFF
        # slot 3: RAM — page-local offset
        return self.ram[addr & 0x3FFF]

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        slot = self._slot(addr)
        if slot == 0:
            return  # BIOS ROM is read-only
        if slot == 1:
            return  # cartridge ROM is read-only
        if slot == 2:
            return  # open bus, ignore
        # slot 3: RAM
        self.ram[addr & 0x3FFF] = value

    def read_port_a8(self) -> int:
        return self.slot_register & 0xFF

    def write_port_a8(self, value: int) -> None:
        self.slot_register = value & 0xFF
