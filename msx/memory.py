from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger
    from msx.mapper import Mapper
    from msx.ram_mapper import RamMapper

from msx.mapper import FlatMapper


@dataclass(slots=True)
class Memory:
    rom: bytes
    ram: bytearray
    _mapper: "Mapper" = field(repr=False)
    _mapper2: "Mapper" = field(default_factory=lambda: FlatMapper(None), repr=False)
    # Default: page0+1=slot0(BIOS), page1+2=slot1(cart), page3=slot3(RAM)
    # 0b11_01_01_00 = 0xD4
    slot_register: int = 0xD4
    _logger: DebugLogger | None = field(default=None, repr=False)
    extrom: bytes | None = field(default=None, repr=False)
    ram_mapper: "RamMapper | None" = field(default=None, repr=False)
    # MSX slot 3 secondary slot register: bits 7:6=page3, 5:4=page2, 3:2=page1, 1:0=page0
    sub_slot_reg: int = 0x00
    sub_slot_enabled: bool = False  # True only for MSX2; enables 0xFFFF intercept
    sub0_rom: bytes | None = field(default=None, repr=False)
    sub1_rom: bytes | None = field(default=None, repr=False)
    rom_name: str = ""
    sub0_rom_name: str = ""
    _rom_len: int = field(init=False, repr=False, default=0)
    _extrom_len: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        self._rom_len = len(self.rom)
        self._extrom_len = len(self.extrom) if self.extrom is not None else 0

    def _slot(self, addr: int) -> int:
        page = (addr >> 14) & 0x03
        return (self.slot_register >> (page * 2)) & 0x03

    def _page3_is_slot3(self) -> bool:
        return self.sub_slot_enabled and ((self.slot_register >> 6) & 0x03) == 3

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        slot = (self.slot_register >> ((addr >> 14) * 2)) & 0x03  # page 0-3 → slot 0-3
        if slot == 0:
            if self.extrom is not None and 0x8000 <= addr <= 0xBFFF:
                off = addr - 0x8000
                return self.extrom[off] if off < self._extrom_len else 0xFF
            return self.rom[addr] if addr < self._rom_len else 0xFF
        if slot == 1:
            return self._mapper.read(addr)
        if slot == 2:
            return self._mapper2.read(addr)
        # slot 3
        # Secondary slot register intercept at 0xFFFF (only when page 3 = slot 3)
        if addr == 0xFFFF and self._page3_is_slot3():
            return (~self.sub_slot_reg) & 0xFF
        page = (addr >> 14) & 0x03
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        if sub == 0 and self.sub0_rom is not None:
            if addr <= 0x3FFF:
                return self.sub0_rom[addr] if addr < len(self.sub0_rom) else 0xFF
            return 0xFF  # sub0_rom present but addr out of its page-0 range
        elif sub == 1:
            return 0xFF
        # sub-slots 0 (no sub0_rom, backward compat), 2, and 3 → RAM mapper
        if self.ram_mapper is not None:
            return self.ram_mapper.read(addr)
        # MSX1: 32 KB RAM at 0x8000-0xFFFF only
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
        # slot 3
        # Secondary slot register intercept at 0xFFFF (only when page 3 = slot 3)
        if addr == 0xFFFF and self._page3_is_slot3():
            self.sub_slot_reg = value & 0xFF
            return
        page = (addr >> 14) & 0x03
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        if sub == 1:
            return  # reserved, ignore
        if sub == 0 and self.sub0_rom is not None:
            return  # sub0_rom is read-only
        # sub-slots 0 (fallback), 2, and 3 → RAM mapper
        if self.ram_mapper is not None:
            self.ram_mapper.write(addr, value)
            return
        self.ram[addr - 0x8000] = value

    def read_port_a8(self) -> int:
        return self.slot_register & 0xFF

    def write_port_a8(self, value: int) -> None:
        old = self.slot_register
        self.slot_register = value & 0xFF
        if self._logger is not None:
            self._logger.on_slot_register_write(old, self.slot_register, pc=0)
