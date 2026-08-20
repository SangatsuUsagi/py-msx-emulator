from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.diagnostics.logger import DebugLogger
    from msx.fdc.interface import FloppyDisk
    from msx.mapper import Mapper
    from msx.ram_mapper import RamMapper

from msx.mapper import FlatMapper

# Fields whose reassignment invalidates the page-routing cache (see
# Memory.__setattr__). slot_register/sub_slot_reg drive routing directly;
# ram_mapper/sub0_rom/fdc/flat_ram_subslot/_mapper/_mapper2 are assumed
# construction-time-fixed by the routing decision, but a *reassignment* (not
# content mutation, e.g. a test replacing mem.sub0_rom after construction)
# must still be observed.
_CACHE_INVALIDATING_FIELDS = frozenset(
    {
        "slot_register",
        "sub_slot_reg",
        "ram_mapper",
        "sub0_rom",
        "fdc",
        "flat_ram_subslot",
        "_mapper",
        "_mapper2",
    }
)

# Subset of _CACHE_INVALIDATING_FIELDS that also participate in the
# SlotThreeStrategyIsExclusive invariant (see _validate_slot3_strategy):
# reassigning any of these must re-check the invariant, not just invalidate
# the cache, or a reassignment could silently desync it from the routing
# decision the cache actually makes.
_SLOT3_STRATEGY_FIELDS = frozenset({"ram_mapper", "flat_ram_subslot", "fdc"})


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
    # Per-page (16 KB) resolved routing cache: index 0-3, rebuilt whenever
    # __setattr__ observes a write to one of _CACHE_INVALIDATING_FIELDS
    # (event-driven: a single bool check per access, no per-access
    # recomputation of a comparison key). extrom isn't in that set:
    # _read_rom() reads self.extrom live, unconditionally correct regardless
    # of when it's assigned.
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
    # Kept as-is here because: rebuild is rare (only on
    #   _CACHE_INVALIDATING_FIELDS writes) and read/write is hot (every CPU
    #   memory access); CPython's bound-method list-index is already close to
    #   the cheapest per-access dispatch Python offers.
    _page_cache_valid: bool = field(init=False, repr=False, default=False)
    _page_read: list[Callable[[int], int]] = field(init=False, repr=False, default_factory=list)
    _page_write: list[Callable[[int, int], None]] = field(
        init=False, repr=False, default_factory=list
    )
    # Precomputed alongside _page_read/_page_write: whether the 0xFFFF
    # secondary-slot-register intercept can fire under the current
    # slot_register/sub_slot_enabled (see _rebuild_page_cache).
    _page3_intercept_active: bool = field(init=False, repr=False, default=False)

    def __setattr__(self, name: str, value: object) -> None:
        """Invalidate the page-routing cache on writes to _CACHE_INVALIDATING_FIELDS.

        PORT-NOTE: intercepting plain attribute assignment has no static-
          language equivalent.
        Rust equivalent: explicit setters (e.g. `set_slot_register`) that call
          the equivalent of `invalidate_cache()` directly, instead of relying
          on assignment interception -- Rust has no operator-overload hook for
          plain field assignment on a struct.
        C++ equivalent: same -- explicit setter methods, since C++ has no
          portable equivalent of intercepting `obj.field = value` on a plain
          struct either (short of wrapping every field in a property-like
          accessor class, which this design avoids).
        Kept as-is here because: port target/shape not decided yet, and this
          is exercised as public API well beyond this file (msx/ppi.py,
          msx/machine.py, msx/state.py all do `mem.field = value`) -- the
          setters would need to replace every one of those call sites, not
          just this file, so it's tracked as a separate large-scope item
          rather than a mechanical refactor here.
        """
        object.__setattr__(self, name, value)
        if name in _CACHE_INVALIDATING_FIELDS:
            object.__setattr__(self, "_page_cache_valid", False)
        if name in _SLOT3_STRATEGY_FIELDS:
            self._validate_slot3_strategy()

    def _validate_slot3_strategy(self) -> None:
        """Enforce SlotThreeStrategyIsExclusive: ram_mapper/flat_ram_subslot
        are mutually exclusive slot-3 RAM strategies, and fdc requires
        flat_ram_subslot. Re-run whenever a _SLOT3_STRATEGY_FIELDS member is
        assigned (not just at construction) so a post-construction
        reassignment can't silently desync the routing cache from this
        invariant the way a bare cache-invalidation check wouldn't catch.

        Reads via getattr(..., None): __setattr__ (which calls this) also
        fires while the dataclass-generated __init__ is still assigning
        fields one at a time, before every _SLOT3_STRATEGY_FIELDS member
        exists yet on this slots instance — a not-yet-assigned field reads
        as None here (harmless: __post_init__ re-validates once everything
        is set, which is the authoritative check for construction).
        """
        ram_mapper = getattr(self, "ram_mapper", None)
        flat_ram_subslot = getattr(self, "flat_ram_subslot", None)
        fdc = getattr(self, "fdc", None)
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

    # Explicit setters for the fields __setattr__ above currently intercepts.
    # Callers outside this class should use these, not direct assignment --
    # see openspec/changes/memory-explicit-setters (__setattr__ is removed
    # once every call site is migrated to call these instead).

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
            self.sub_slot_reg = value & 0xFF
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
