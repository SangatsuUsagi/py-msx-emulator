from dataclasses import dataclass, field


@dataclass
class Memory:
    rom: bytes
    ram: bytearray
    cartridge: bytes | None
    slot_register: int = 0

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        # Cartridge region checked before ROM: 0x4000-0xBFFF is slot 1/2
        if 0x4000 <= addr <= 0xBFFF:
            if self.cartridge is not None:
                offset = addr - 0x4000
                return self.cartridge[offset] if offset < len(self.cartridge) else 0xFF
            return 0xFF
        if addr <= 0x3FFF:
            return self.rom[addr] if addr < len(self.rom) else 0xFF
        if addr >= 0xC000:
            return self.ram[(addr - 0xC000) & 0x3FFF]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        if addr <= 0x3FFF:
            return  # ROM page 0 is read-only
        if 0x4000 <= addr <= 0xBFFF:
            return  # cartridge ROM is read-only
        if addr >= 0xC000:
            self.ram[(addr - 0xC000) & 0x3FFF] = value

    def read_port_a8(self) -> int:
        return self.slot_register & 0xFF

    def write_port_a8(self, value: int) -> None:
        self.slot_register = value & 0xFF
