from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.diagnostics.logger import DebugLogger
    from msx.fdc.interface import FloppyDisk
    from msx.mapper import Mapper
    from msx.ram_mapper import RamMapper

from msx.mapper import FlatMapper


@dataclass(slots=True)
class Memory:
    rom: bytes
    ram: bytearray
    _mapper: Mapper = field(repr=False)
    _mapper2: Mapper = field(default_factory=lambda: FlatMapper(None), repr=False)
    # Default: page0+1=slot0(BIOS), page1+2=slot1(cart), page3=slot3(RAM)
    # 0b11_01_01_00 = 0xD4
    slot_register: int = 0xD4
    _logger: DebugLogger | None = field(default=None, repr=False)
    extrom: bytes | None = field(default=None, repr=False)
    ram_mapper: RamMapper | None = field(default=None, repr=False)
    # MSX slot 3 secondary slot register: bits 7:6=page3, 5:4=page2, 3:2=page1, 1:0=page0
    sub_slot_reg: int = 0x00
    sub_slot_enabled: bool = False  # True only for MSX2; enables 0xFFFF intercept
    sub0_rom: bytes | None = field(default=None, repr=False)
    # Data-driven MSX2 slot-3 layout: when set, slot 3 hosts a flat (non-mapper)
    # RAM in this sub-slot only, the SUB ROM in sub-slot 0 page 0, and open bus
    # everywhere else. None keeps the legacy mapper / MSX1 flat-top behaviour.
    flat_ram_subslot: int | None = field(default=None)
    # Memory-mapped floppy interface in slot 3 sub-slot 0 page 1 (0x4000-0x7FFF);
    # the concrete FloppyDisk base exposes read_mem(addr)/write_mem(addr, value).
    # None when the machine has no FDC.
    fdc: FloppyDisk | None = field(default=None, repr=False)
    rom_name: str = ""
    sub0_rom_name: str = ""
    _rom_len: int = field(init=False, repr=False, default=0)
    _extrom_len: int = field(init=False, repr=False, default=0)
    # Per-page (16 KB) resolved routing cache: index 0-3, rebuilt whenever
    # the key (see _cache_key()) no longer matches — slot_register/
    # sub_slot_reg changing, or ram_mapper/sub0_rom/fdc/flat_ram_subslot
    # being reassigned to a different object (identity, not content: RAM
    # content changes don't affect which handler routes a page). extrom
    # isn't in the key: _read_rom() reads self.extrom live, unconditionally
    # correct regardless of when it's assigned.
    _page_cache_key: tuple[int, int, int, int, int, int | None] | None = field(
        init=False, repr=False, default=None
    )
    _page_read: list[Callable[[int], int]] = field(init=False, repr=False, default_factory=list)
    _page_write: list[Callable[[int, int], None]] = field(
        init=False, repr=False, default_factory=list
    )

    def __post_init__(self) -> None:
        self._rom_len = len(self.rom)
        self._extrom_len = len(self.extrom) if self.extrom is not None else 0
        # Slot 3's RAM strategy is exactly one of: MSX1 flat (both None),
        # legacy MSX2 banked (ram_mapper set), or data-driven flat sub-slot
        # (flat_ram_subslot set) — allium/slots.allium's
        # SlotThreeStrategyIsExclusive invariant.
        if self.ram_mapper is not None and self.flat_ram_subslot is not None:
            raise ValueError(
                "Memory: ram_mapper and flat_ram_subslot are mutually "
                "exclusive slot-3 RAM strategies"
            )
        if self.fdc is not None and self.flat_ram_subslot is None:
            raise ValueError(
                "Memory: fdc requires flat_ram_subslot (only the "
                "data-driven slot-3 layout hosts an FDC)"
            )
        self._rebuild_page_cache(self._cache_key())

    # -- terminal (leaf) read/write operations -------------------------------
    # Mechanical extraction of read()/write()'s former inline branches; each
    # keeps exactly the logic it had inline. Selected once per page by
    # _rebuild_page_cache() instead of re-decoded on every access.

    def _read_rom(self, addr: int) -> int:
        if self.extrom is not None and 0x8000 <= addr <= 0xBFFF:
            off = addr - 0x8000
            return self.extrom[off] if off < self._extrom_len else 0xFF
        return self.rom[addr] if addr < self._rom_len else 0xFF

    def _write_noop(self, addr: int, value: int) -> None:
        return

    def _read_open_bus(self, addr: int) -> int:
        return 0xFF

    def _read_sub0_rom(self, addr: int) -> int:
        assert self.sub0_rom is not None
        return self.sub0_rom[addr] if addr < len(self.sub0_rom) else 0xFF

    def _read_flat_ram(self, addr: int) -> int:
        ram = self.ram
        return ram[addr] if addr < len(ram) else 0xFF

    def _write_flat_ram(self, addr: int, value: int) -> None:
        ram = self.ram
        if addr < len(ram):
            ram[addr] = value

    def _read_msx1_flat_ram(self, addr: int) -> int:
        # MSX1: flat RAM sits at the top of the address space (32 KB → base
        # 0x8000). An access to a page selected to slot 3 without a RAM mapper
        # can fall below that base (negative index); return open-bus 0xFF.
        off = addr - (0x10000 - len(self.ram))
        return self.ram[off] if 0 <= off < len(self.ram) else 0xFF

    def _write_msx1_flat_ram(self, addr: int, value: int) -> None:
        off = addr - (0x10000 - len(self.ram))
        if 0 <= off < len(self.ram):
            self.ram[off] = value

    def _read_intercept_or(self, addr: int, leaf: Callable[[int], int]) -> int:
        if addr == 0xFFFF:
            return (~self.sub_slot_reg) & 0xFF
        return leaf(addr)

    def _write_intercept_or(
        self, addr: int, value: int, leaf: Callable[[int, int], None]
    ) -> None:
        if addr == 0xFFFF:
            self.sub_slot_reg = value & 0xFF
            return
        leaf(addr, value)

    # -- page-cache resolution ------------------------------------------------

    def _resolve_slot3_read_leaf(self, page: int) -> Callable[[int], int]:
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        flat_sub = self.flat_ram_subslot
        if flat_sub is not None:
            # Data-driven MSX2 slot-3 (e.g. HB-F1XD): SUB ROM in sub-slot 0
            # page 0, memory-mapped FDC in sub-slot 0 page 1, flat 64 KB RAM
            # (offset == address) in `flat_sub`, else open bus.
            if sub == 0:
                if page == 0 and self.sub0_rom is not None:
                    return self._read_sub0_rom
                if page == 1 and self.fdc is not None:
                    return self.fdc.read_mem
                return self._read_open_bus
            if sub == flat_sub:
                return self._read_flat_ram
            return self._read_open_bus
        # Legacy sub-slot dispatch:
        #   0: extension ROM in page 0 (if present), else main RAM
        #   1: reserved / unmapped -> open bus
        #   2, 3: main RAM
        if sub == 0:
            if self.sub0_rom is not None:
                if page == 0:
                    return self._read_sub0_rom
                return self._read_open_bus  # sub0_rom present, addr out of page-0 range
        elif sub == 1:
            return self._read_open_bus
        # sub == 2, sub == 3, or sub == 0 without a sub0_rom -> RAM
        if self.ram_mapper is not None:
            return self.ram_mapper.read
        return self._read_msx1_flat_ram

    def _resolve_slot3_write_leaf(self, page: int) -> Callable[[int, int], None]:
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        flat_sub = self.flat_ram_subslot
        if flat_sub is not None:
            # Data-driven MSX2 slot-3 write (see _resolve_slot3_read_leaf).
            if sub == 0:
                if page == 1 and self.fdc is not None:
                    return self.fdc.write_mem
                return self._write_noop  # SUB ROM (page 0) / open-bus pages
            if sub == flat_sub:
                return self._write_flat_ram
            return self._write_noop  # empty sub-slots ignore writes
        if sub == 1:
            return self._write_noop  # reserved, ignore
        if sub == 0 and self.sub0_rom is not None:
            return self._write_noop  # sub0_rom is read-only
        # sub-slots 0 (fallback), 2, and 3 → RAM mapper
        if self.ram_mapper is not None:
            return self.ram_mapper.write
        return self._write_msx1_flat_ram

    def _resolve_page_read(self, page: int) -> Callable[[int], int]:
        slot = (self.slot_register >> (page * 2)) & 0x03
        if slot == 0:
            return self._read_rom
        if slot == 1:
            return self._mapper.read
        if slot == 2:
            return self._mapper2.read
        # slot 3
        leaf = self._resolve_slot3_read_leaf(page)
        # Secondary slot register intercept at 0xFFFF only ever applies to
        # page 3 (0xC000-0xFFFF), and only when sub_slot_enabled (MSX2).
        if page == 3 and self.sub_slot_enabled:
            return partial(self._read_intercept_or, leaf=leaf)
        return leaf

    def _resolve_page_write(self, page: int) -> Callable[[int, int], None]:
        slot = (self.slot_register >> (page * 2)) & 0x03
        if slot == 0:
            return self._write_noop  # BIOS ROM is read-only
        if slot == 1:
            return self._mapper.write
        if slot == 2:
            return self._mapper2.write
        # slot 3
        leaf = self._resolve_slot3_write_leaf(page)
        if page == 3 and self.sub_slot_enabled:
            return partial(self._write_intercept_or, leaf=leaf)
        return leaf

    def _cache_key(self) -> tuple[int, int, int, int, int, int | None]:
        return (
            self.slot_register,
            self.sub_slot_reg,
            id(self.ram_mapper),
            id(self.sub0_rom),
            id(self.fdc),
            self.flat_ram_subslot,
        )

    def _rebuild_page_cache(self, key: tuple[int, int, int, int, int, int | None]) -> None:
        self._page_read = [self._resolve_page_read(page) for page in range(4)]
        self._page_write = [self._resolve_page_write(page) for page in range(4)]
        self._page_cache_key = key

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        key = self._cache_key()
        if self._page_cache_key != key:
            self._rebuild_page_cache(key)
        return self._page_read[addr >> 14](addr)

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        key = self._cache_key()
        if self._page_cache_key != key:
            self._rebuild_page_cache(key)
        self._page_write[addr >> 14](addr, value)

    def main_ram_range(self) -> tuple[int, int]:
        """Conventional main-RAM address window, for stack-sanity checks.

        MSX1 flat RAM sits at the top of the address space, so the window is
        derived from the RAM size. Mapper-backed (MSX2) RAM can appear in any
        page, so the whole address space is treated as valid RAM.
        """
        if self.ram_mapper is not None:
            return (0x0000, 0xFFFF)
        low = max(0, 0x10000 - len(self.ram))
        return (low, 0xFFFF)
