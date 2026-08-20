from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.diagnostics.logger import DebugLogger
    from msx.fdc.interface import FloppyDisk
    from msx.mapper import Mapper
    from msx.ram_mapper import RamMapper

from msx.mapper import FlatMapper


@dataclass(slots=True)
class Memory:
    """MSX address-space slot/sub-slot decode and read/write dispatch.

    Resolves the current `slot_register`/`sub_slot_reg` into a per-page
    (16 KB) dispatch cache (`_page_read`/`_page_write`) rather than
    re-deriving the routing decision on every access; see
    `openspec/changes/archive/2026-08-09-memory-dispatch-cache/design.md`
    for the full design history, including two benchmark-driven revisions.
    """

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
    # RAM length and MSX1 flat-RAM base offset, precomputed like _rom_len/
    # _extrom_len above (ram is assumed construction-time-fixed, same as rom).
    _ram_len: int = field(init=False, repr=False, default=0)
    _msx1_ram_base: int = field(init=False, repr=False, default=0)
    # Per-page (16 KB) resolved routing cache: index 0-3, rebuilt whenever one
    # of the 8 set_*() methods below invalidates it (event-driven: a single
    # bool check per access, no per-access recomputation of a comparison
    # key). extrom has no setter and needs none: _read_rom() reads
    # self.extrom live, unconditionally correct regardless of when it's
    # assigned.
    #
    # PORT-NOTE: _page_read/_page_write are a Callable dispatch table (bound
    #   methods/closures over `self`), rebuilt on cache invalidation and
    #   indexed on every memory access.
    # Rust equivalent: not a literal Box<dyn Fn>/Vec<Box<dyn Fn>> translation
    #   (that would pay heap-alloc + vtable cost on every rebuild that
    #   CPython's bound methods don't) — port
    #   _resolve_page_read_leaf/_resolve_page_write_leaf/_resolve_slot3_*_leaf
    #   directly as a match/switch over a small per-page enum tag, dispatched
    #   inline instead.
    # C++ equivalent: same — a switch over a per-page enum tag rather than a
    #   std::function table, to avoid the equivalent allocation/vtable cost.
    # Kept as-is here because: rebuild is rare (only on a setter-triggered
    #   invalidation) and read/write is hot (every CPU memory access);
    #   CPython's bound-method list-index is already close to the cheapest
    #   per-access dispatch Python offers.
    _page_cache_valid: bool = field(init=False, repr=False, default=False)
    _page_read: list[Callable[[int], int]] = field(init=False, repr=False, default_factory=list)
    _page_write: list[Callable[[int, int], None]] = field(
        init=False, repr=False, default_factory=list
    )
    # Precomputed alongside _page_read/_page_write: whether the 0xFFFF
    # secondary-slot-register intercept can fire under the current
    # slot_register/sub_slot_enabled (see _rebuild_page_cache).
    _page3_intercept_active: bool = field(init=False, repr=False, default=False)

    def _validate_slot3_strategy(self) -> None:
        """Enforce SlotThreeStrategyIsExclusive: ram_mapper/flat_ram_subslot
        are mutually exclusive slot-3 RAM strategies, and fdc requires
        flat_ram_subslot. Called from __post_init__ (once, at the end of
        construction) and from set_ram_mapper/set_fdc/set_flat_ram_subslot
        (so a post-construction reassignment can't silently desync the
        routing cache from this invariant) -- every caller runs after the
        dataclass __init__ has finished, so all three fields always exist
        by the time this runs.
        """
        ram_mapper = self.ram_mapper
        flat_ram_subslot = self.flat_ram_subslot
        fdc = self.fdc
        if ram_mapper is not None and flat_ram_subslot is not None:
            raise ValueError(
                "Memory: ram_mapper and flat_ram_subslot are mutually "
                "exclusive slot-3 RAM strategies"
            )
        if fdc is not None and flat_ram_subslot is None:
            raise ValueError(
                "Memory: fdc requires flat_ram_subslot (only the "
                "data-driven slot-3 layout hosts an FDC)"
            )

    # Explicit setters for the 8 fields that affect page routing. Callers
    # outside this class must use these, not direct assignment -- this class
    # has no __setattr__ hook (removed by the memory-explicit-setters
    # OpenSpec change) to catch a stray `mem.field = value` the way it used
    # to, so a direct assignment here silently leaves the routing cache
    # stale instead of raising or invalidating anything. A regression test
    # (tests/test_memory_setter_discipline.py) statically scans for this
    # mistake.

    def set_slot_register(self, value: int) -> None:
        self.slot_register = value
        self._page_cache_valid = False

    def set_sub_slot_reg(self, value: int) -> None:
        self.sub_slot_reg = value
        self._page_cache_valid = False

    def set_ram_mapper(self, value: "RamMapper | None") -> None:
        self.ram_mapper = value
        self._page_cache_valid = False
        self._validate_slot3_strategy()

    def set_sub0_rom(self, value: bytes | None) -> None:
        self.sub0_rom = value
        self._page_cache_valid = False

    def set_fdc(self, value: "FloppyDisk | None") -> None:
        self.fdc = value
        self._page_cache_valid = False
        self._validate_slot3_strategy()

    def set_flat_ram_subslot(self, value: int | None) -> None:
        self.flat_ram_subslot = value
        self._page_cache_valid = False
        self._validate_slot3_strategy()

    def set_mapper(self, value: "Mapper") -> None:
        self._mapper = value
        self._page_cache_valid = False

    def set_mapper2(self, value: "Mapper") -> None:
        self._mapper2 = value
        self._page_cache_valid = False

    def __post_init__(self) -> None:
        self._rom_len = len(self.rom)
        self._extrom_len = len(self.extrom) if self.extrom is not None else 0
        self._ram_len = len(self.ram)
        self._msx1_ram_base = 0x10000 - self._ram_len
        self._validate_slot3_strategy()
        self._rebuild_page_cache()

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
        pass

    def _read_open_bus(self, addr: int) -> int:
        return 0xFF

    def _read_sub0_rom(self, addr: int) -> int:
        assert self.sub0_rom is not None
        return self.sub0_rom[addr] if addr < len(self.sub0_rom) else 0xFF

    def _read_flat_ram(self, addr: int) -> int:
        return self.ram[addr] if addr < self._ram_len else 0xFF

    def _write_flat_ram(self, addr: int, value: int) -> None:
        if addr < self._ram_len:
            self.ram[addr] = value

    def _read_msx1_flat_ram(self, addr: int) -> int:
        # MSX1: flat RAM sits at the top of the address space (32 KB → base
        # 0x8000). An access to a page selected to slot 3 without a RAM mapper
        # can fall below that base (negative index); return open-bus 0xFF.
        off = addr - self._msx1_ram_base
        return self.ram[off] if 0 <= off < self._ram_len else 0xFF

    def _write_msx1_flat_ram(self, addr: int, value: int) -> None:
        off = addr - self._msx1_ram_base
        if 0 <= off < self._ram_len:
            self.ram[off] = value

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

    def _resolve_page_read_leaf(self, page: int) -> Callable[[int], int]:
        slot = (self.slot_register >> (page * 2)) & 0x03
        if slot == 0:
            return self._read_rom
        if slot == 1:
            return self._mapper.read
        if slot == 2:
            return self._mapper2.read
        return self._resolve_slot3_read_leaf(page)

    def _resolve_page_write_leaf(self, page: int) -> Callable[[int, int], None]:
        slot = (self.slot_register >> (page * 2)) & 0x03
        if slot == 0:
            return self._write_noop  # BIOS ROM is read-only
        if slot == 1:
            return self._mapper.write
        if slot == 2:
            return self._mapper2.write
        return self._resolve_slot3_write_leaf(page)

    def _rebuild_page_cache(self) -> None:
        self._page_read = [self._resolve_page_read_leaf(page) for page in range(4)]
        self._page_write = [self._resolve_page_write_leaf(page) for page in range(4)]
        # Secondary slot register intercept at 0xFFFF only ever applies to
        # page 3 (0xC000-0xFFFF), and only when sub_slot_enabled (MSX2) and
        # page 3's primary slot is 3. Precomputed here so read()/write() pay
        # only a cheap bool check, not a wrapper call, on the common
        # (non-0xFFFF) page-3 path.
        page3_slot = (self.slot_register >> 6) & 0x03
        self._page3_intercept_active = self.sub_slot_enabled and page3_slot == 3
        self._page_cache_valid = True

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        if not self._page_cache_valid:
            self._rebuild_page_cache()
        page = addr >> 14
        # 0xFFFF secondary-slot-register intercept, inlined (not wrapped) for
        # perf — see _rebuild_page_cache.
        if page == 3 and addr == 0xFFFF and self._page3_intercept_active:
            return (~self.sub_slot_reg) & 0xFF
        return self._page_read[page](addr)

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        if not self._page_cache_valid:
            self._rebuild_page_cache()
        page = addr >> 14
        # 0xFFFF secondary-slot-register intercept, inlined (not wrapped) for
        # perf — see _rebuild_page_cache.
        if page == 3 and addr == 0xFFFF and self._page3_intercept_active:
            self.set_sub_slot_reg(value & 0xFF)
            return
        self._page_write[page](addr, value)

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

    # ------------------------------------------------------------- debug REPL
    #
    # The three methods below back the debugger's `sl`/`st` slot-inspector
    # commands (msx/debugger/prompt.py). They exist so that debugger code
    # never needs to reflect on Memory's private fields (_mapper/_mapper2)
    # from outside this class -- see openspec/changes/archive/
    # *-debugger-slot-mapper-interface.

    def debug_slot_content(
        self, primary: int, secondary: int | None, page: int | None
    ) -> str:
        """Human-readable description of what a slot/sub-slot contains."""
        if primary == 0:
            name = self.rom_name or "ROM"
            return f"ROM {name}" if name != "ROM" else "ROM"
        if primary in (1, 2):
            mapper = self._mapper if primary == 1 else self._mapper2
            if isinstance(mapper, FlatMapper) and mapper.cartridge is None:
                return "Cartridge (empty)"
            cls = type(mapper).__name__.replace("Mapper", "")
            return f"Cartridge {cls}"
        if primary == 3:
            if secondary == 0:
                name = self.sub0_rom_name or "ROM"
                return f"ROM {name}" if name != "ROM" else "ROM"
            if secondary == 1:
                return "empty"
            if secondary in (2, 3):
                if self.ram_mapper is not None:
                    return "RAM (mapper:standard)"
                return "RAM"
            return "empty"
        return "empty"

    def debug_slot_bank(
        self, primary: int, secondary: int | None, page: int | None
    ) -> str:
        """Bank-register display string for the `sl` active-slot view."""
        if primary == 3 and secondary in (2, 3) and page is not None:
            rm = self.ram_mapper
            if rm is not None:
                return f"seg={rm.banks[page]}"
        if primary in (1, 2) and page is not None:
            mapper = self._mapper if primary == 1 else self._mapper2
            info = mapper.debug_bank_info(page)
            if info is not None:
                return info
        return "-"

    def debug_slot_size_kb(self, primary: int, secondary: int | None) -> str:
        """Size string (e.g. "32KB") for the `st` tree view, or "" for none."""
        if primary == 0:
            n = len(self.rom)
            return f"{n // 1024}KB" if n else ""
        if primary == 3 and secondary == 0:
            sub = self.sub0_rom
            n = len(sub) if sub is not None else 0
            return f"{n // 1024}KB" if n else ""
        if primary == 3 and secondary in (2, 3):
            rm = self.ram_mapper
            if rm is not None:
                return "128KB"
            n = len(self.ram)
            return f"{n // 1024}KB" if n else ""
        return ""
