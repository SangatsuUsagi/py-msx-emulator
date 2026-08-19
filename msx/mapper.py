from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from msx.mapper_tracer import MapperTracer
    from msx.scc import SCC

_PAGE_8K = 8192
_PAGE_16K = 16384
# Flat-mirror span for every banked mapper: 4 windows * 8 KB == 2 windows *
# 16 KB == 32768 bytes (0x4000-0xBFFF). Hoisted so read()'s hot-path bounds
# check does a plain int compare instead of a per-call global-lookup multiply.
_WINDOW_BYTES = 4 * _PAGE_8K

# Portability note: every mapper's `addr - base` bounds check in this module
# (e.g. FixedPageMapper.read, GameMaster2Mapper.read, _read_out_of_window on the
# banked mappers) relies
# on Python's arbitrary-precision int subtraction to produce a negative value
# when `addr` falls below `base`, which the subsequent `0 <=` comparison then
# rejects. `addr` is not pre-clamped to a mapper's own window before `read()`
# is called (a BIOS/slot scan can probe any address in cartridge space), so
# `addr < base` is a real, expected case. A Rust/C++ port using fixed-width
# unsigned integers must not translate this subtraction directly -- it would
# underflow-wrap instead of going negative. Cast to a signed intermediate
# before subtracting, or compare `addr >= base` before computing the offset.

# R-Type (Irem) bank register masks (openMSX RomRType).
_RTYPE_HI_BIT = 0x10   # when set, only the low 3 bits of the mask apply
_RTYPE_MASK_HI = 0x17
_RTYPE_MASK = 0x1F
# Fixed window's ROM block (openMSX RomRType.cc reset(): setRom(1, 0x17)).
# Hardcoded, not derived from ROM size: real R-Type cartridges wire two
# physical ROM chips with asymmetric addressing (see RTypeMapper's own
# docstring), and 0x17 is one of two content-identical "last page" blocks
# (0x0F and 0x17) regardless of which physical dump size is loaded.
_RTYPE_FIXED_BLOCK = 0x17


class Mapper(Protocol):
    def read(self, addr: int) -> int: ...
    def write(self, addr: int, value: int) -> None: ...
    # Mapping (read-only, covariant), not dict (invariant): a snapshot is
    # only ever read to serialise, never mutated in place, and a covariant
    # return type lets an implementer narrow to its own TypedDict schema
    # (e.g. FmPacState in msx/fmpac.py) without breaking Protocol
    # conformance -- dict[str, object] blocks that narrowing even in return
    # position, since dict's mutating methods make it invariant.
    def snapshot(self) -> Mapping[str, object]: ...
    def restore(self, state: dict[str, object]) -> None: ...


@dataclass
class _BankTracing:
    """Shared bank-change tracing state for the bank-switching mappers.

    Consolidated into one base so every mapper carries the same four hook
    fields instead of redeclaring them, and so _trace_bank can access them
    with static typing rather than getattr. The loader injects the callbacks
    after construction (all init=False).
    """

    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)


def _trace_bank(mapper: _BankTracing, window: int, old: int, new: int, addr: int) -> None:
    """Notify an injected MapperTracer of a bank change. No-op without a tracer."""
    tracer = mapper._tracer
    if tracer is None or old == new:
        return
    get_pc = mapper._get_pc
    get_cycle = mapper._get_cycle
    get_frame = mapper._get_frame
    tracer.bank_change(
        window, old, new, addr,
        get_pc() if get_pc else 0,
        get_cycle() if get_cycle else 0,
        get_frame() if get_frame else 0,
    )


class NoStateMapperState(TypedDict):
    """Save-state schema for _NoStateMapperMixin -- always empty."""


class _NoStateMapperMixin:
    """Shared no-op write/snapshot/restore for mappers with no persisted state
    (no bank registers, no SRAM)."""

    def write(self, addr: int, value: int) -> None:
        pass

    def snapshot(self) -> NoStateMapperState:
        return {}

    def restore(self, state: dict[str, object]) -> None:
        pass


@dataclass
class FlatMapper(_NoStateMapperMixin):
    """Flat (non-bank-switching) cartridge mapper. Reproduces the original behaviour."""

    cartridge: bytes | None

    def read(self, addr: int) -> int:
        if not self.cartridge:
            return 0xFF
        # Mirror the ROM across the full cartridge region (e.g., 8 KB ROM repeats in 32 KB space).
        offset = (addr - 0x4000) % len(self.cartridge)
        return self.cartridge[offset]


@dataclass
class FixedPageMapper(_NoStateMapperMixin):
    """Non-bank-switched cartridge mapper: ROM appears only at a fixed base address;
    every other page in the cartridge region reads as 0xFF (open bus). Reproduces
    openMSX RomPageNN / RomPlain(mirrored=False) for the Page2 / 0x4000 / 0x8000
    ROM database mapper types.
    """

    rom: bytes
    base: int
    # Cached at construction so the hot read() path does a plain int compare
    # instead of a per-call len() lookup (same rationale as _WINDOW_BYTES above).
    _rom_len: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rom_len = len(self.rom)

    def read(self, addr: int) -> int:
        offset = addr - self.base
        if 0 <= offset < self._rom_len:
            return self.rom[offset]
        return 0xFF


class Ascii8MapperState(TypedDict):
    """Save-state schema for Ascii8Mapper.snapshot()/restore()."""

    banks: list[int]


@dataclass
class Ascii8Mapper(_BankTracing):
    """ASCII8 mapper: four 8 KB windows at 0x4000/0x6000/0x8000/0xA000.

    Control registers written to 0x6000–0x7FFF select which ROM page each
    window shows.  The 2 KB sub-ranges (determined by bits 12:11 of the write
    address) map to windows 0–3 respectively.

    Power-on state: all four windows select bank 0 (matches real ASCII8 hardware
    and openMSX, which reset all segment registers to 0). Same rationale as
    Ascii16Mapper: a cartridge INIT may rely on the upper windows mirroring
    bank 0 before it switches banks itself.

    No mirroring outside 0x4000-0xBFFF: unlike the Konami-family mappers,
    real ASCII8 cartridges do not mirror their windows into 0x0000-0x3FFF or
    0xC000-0xFFFF (openMSX RomAscii8kB.cc reset() wires those pages
    permanently to unmapped/open bus; openMSX/openMSX issue #1213, opened as
    an ASCII8 mirroring report, was investigated and closed against that
    claim -- real ASCII8 hardware's /SLTSL wiring returns nothing outside
    its own windows, unlike the 32 KB-ROM-specific /CS12 mirror).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0, 0, 0], repr=False)
    # Flat mirror of the four banked windows (0x4000-0xBFFF), rebuilt only on
    # bank switch: reads are hot (millions/frame), switches are rare, so
    # resolving the window on every read is wasted work.
    _flat: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._flat = bytearray(4 * _PAGE_8K)
        for window in range(4):
            self._sync_window(window)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def _sync_window(self, window: int) -> None:
        page = self._banks[window]
        src = self.rom[page * _PAGE_8K : (page + 1) * _PAGE_8K]
        dst = window * _PAGE_8K
        self._flat[dst : dst + len(src)] = src
        if len(src) < _PAGE_8K:
            # Page runs past the end of a short/truncated ROM: open bus.
            self._flat[dst + len(src) : dst + _PAGE_8K] = b"\xff" * (_PAGE_8K - len(src))

    def read(self, addr: int) -> int:
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            return self._flat[idx]
        # Outside the four windows: open bus, not a mirror (see the
        # class docstring's "No mirroring" note).
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            # Bits 12:11 of address select register 0–3
            reg = (addr >> 11) & 0x03
            new = value % self._num_pages()
            old = self._banks[reg]
            self._banks[reg] = new
            if new != old:
                self._sync_window(reg)
            _trace_bank(self, reg, old, new, addr)

    def snapshot(self) -> Ascii8MapperState:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(Ascii8MapperState, state)
        self._banks[:] = typed_state["banks"]
        for window in range(4):
            self._sync_window(window)


class Ascii16MapperState(TypedDict):
    """Save-state schema for Ascii16Mapper.snapshot()/restore()."""

    banks: list[int]


@dataclass
class Ascii16Mapper(_BankTracing):
    """ASCII16 mapper: two 16 KB windows at 0x4000 and 0x8000.

    Control registers at 0x6000–0x6FFF (window 0) and 0x7000–0x7FFF (window 1).

    Power-on state: both windows select bank 0 (matches real ASCII16 hardware
    and openMSX, which reset all segment registers to 0). Some games rely on the
    0x8000 window mirroring bank 0 at startup — e.g. their cartridge INIT does
    `JP 8031h` into bank-0 code visible through the second window.
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0], repr=False)
    # Flat mirror of the two banked windows (0x4000-0xBFFF), rebuilt only on
    # bank switch: reads are hot (millions/frame), switches are rare, so
    # resolving the window on every read is wasted work.
    _flat: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._flat = bytearray(2 * _PAGE_16K)
        for window in range(2):
            self._sync_window(window)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_16K)

    def _sync_window(self, window: int) -> None:
        page = self._banks[window]
        src = self.rom[page * _PAGE_16K : (page + 1) * _PAGE_16K]
        dst = window * _PAGE_16K
        self._flat[dst : dst + len(src)] = src
        if len(src) < _PAGE_16K:
            # Page runs past the end of a short/truncated ROM: open bus.
            self._flat[dst + len(src) : dst + _PAGE_16K] = b"\xff" * (_PAGE_16K - len(src))

    def read(self, addr: int) -> int:
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            return self._flat[idx]
        return self._read_out_of_window(addr)

    def _read_out_of_window(self, addr: int) -> int:
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
            new = value % self._num_pages()
            old = self._banks[window]
            self._banks[window] = new
            if new != old:
                self._sync_window(window)
            _trace_bank(self, window, old, new, addr)

    def snapshot(self) -> Ascii16MapperState:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(Ascii16MapperState, state)
        self._banks[:] = typed_state["banks"]
        for window in range(2):
            self._sync_window(window)


class Ascii8Sram2MapperState(Ascii8MapperState):
    sram: bytes


@dataclass
class Ascii8Sram2Mapper(Ascii8Mapper):
    """ASCII8 mapper + 2 KB battery-backed SRAM (generic ASCII8-SRAM).

    Following openMSX RomAscii8_8: a window maps SRAM when its bank register
    value has the SRAM-enable bit set, where the enable bit equals the ROM's
    8 KB page count (rom_size // 8192). SRAM is only selectable for windows in
    _SRAM_PAGES (default 0x8000 and 0xA000; the region bit for window w is
    1 << (w + 2)). The SRAM-page-select bits are masked with
    round_up(sram_size / 8192) - 1. Writes to 0x6000–0x7FFF always update bank
    registers (raw value, never SRAM).

    KOEI and Wizardry variants (different enable bit / SRAM windows) are out of
    scope here and are not covered by this generic mapper.

    No mirroring outside 0x4000-0xBFFF: same as Ascii8Mapper (see that
    class's own docstring note); ROM or SRAM content outside the four
    windows is open bus, not mirrored.
    """

    _SRAM_SIZE: ClassVar[int] = 2048
    _SRAM_MASK: ClassVar[int] = 0x7FF
    # Region bitmask of windows that may map SRAM: 0x8000 (1<<4) and 0xA000 (1<<5).
    _SRAM_PAGES: ClassVar[int] = 0x30

    # Portability note: `sram` is Optional and every access is guarded / masked
    # with a `# type: ignore`. A Rust/C++ port makes SRAM non-optional (allocate
    # a fixed `[u8; _SRAM_SIZE]` in the constructor/factory) so the read/write
    # paths need no None-check and the type is `&[u8]`, not `Option<&[u8]>`.
    sram: bytearray | None = None
    # Per-window flag: True while that window currently maps SRAM. Read()
    # routes SRAM-mapped windows straight to self.sram and ROM-mapped windows
    # to the inherited flat mirror -- no window is ever both at once, so no
    # write-through between the two is needed.
    # Portability note: length is fixed at 4 for this class and never
    # resized after construction -- a Rust/C++ port should use [bool; 4] /
    # std::array<bool, 4> (or a bitmask) rather than a growable Vec<bool>.
    _window_is_sram: list[bool] = field(
        default_factory=lambda: [False, False, False, False], init=False, repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.sram, bytearray) or len(self.sram) != self._SRAM_SIZE:
            self.sram = bytearray(self._SRAM_SIZE)
        self._flat = bytearray(4 * _PAGE_8K)
        for window in range(4):
            self._sync_window(window)

    def _sram_enable_bit(self) -> int:
        return self._num_pages()

    def _sram_block_mask(self) -> int:
        blocks = max(1, (self._SRAM_SIZE + _PAGE_8K - 1) // _PAGE_8K)
        return blocks - 1

    def _is_sram_bank(self, window: int) -> bool:
        if not (self._SRAM_PAGES & (1 << (window + 2))):
            return False
        return bool(self._banks[window] & self._sram_enable_bit())

    def _sram_offset(self, window: int, addr: int, base: int) -> int:
        block = self._banks[window] & self._sram_block_mask()
        return (block * _PAGE_8K + (addr - base)) & self._SRAM_MASK

    def _sync_window(self, window: int) -> None:
        if self._is_sram_bank(window):
            # SRAM-mapped: read() routes this window to self.sram directly,
            # so _flat is left untouched (stale but unread) for this window.
            self._window_is_sram[window] = True
            return
        self._window_is_sram[window] = False
        super()._sync_window(window)

    def read(self, addr: int) -> int:
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            window = idx >> 13  # idx // _PAGE_8K, as a shift
            if self._window_is_sram[window]:
                base = 0x4000 + window * _PAGE_8K
                return self.sram[self._sram_offset(window, addr, base)]  # type: ignore[index]
            return self._flat[idx]
        # Outside the four windows: open bus, not a mirror (see the
        # class docstring's "No mirroring" note).
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            reg = (addr >> 11) & 0x03
            old = self._banks[reg]
            self._banks[reg] = value
            if value != old:
                self._sync_window(reg)
            _trace_bank(self, reg, old, value, addr)
            return
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        if self._is_sram_bank(window):
            self.sram[self._sram_offset(window, addr, base)] = value & 0xFF  # type: ignore[index]

    def save_sram(self, path: Path) -> None:
        path.write_bytes(self.sram)  # type: ignore[arg-type]

    def snapshot(self) -> Ascii8Sram2MapperState:
        return {**super().snapshot(), "sram": bytes(self.sram)}  # type: ignore[arg-type]

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        typed_state = cast(Ascii8Sram2MapperState, state)
        if self.sram is not None:
            self.sram[:] = typed_state["sram"]


@dataclass
class Ascii8Sram8Mapper(Ascii8Sram2Mapper):
    """ASCII8 mapper + 8 KB battery-backed SRAM."""

    _SRAM_SIZE: ClassVar[int] = 8192
    _SRAM_MASK: ClassVar[int] = 0x1FFF


@dataclass
class KoeiSRAM32Mapper(Ascii8Sram2Mapper):
    """ASCII8 mapper + 32 KB battery-backed SRAM (openMSX RomAscii8_8, KOEI_32).

    Extends the SRAM-selectable window set to include 0x4000 (mask 0x34, vs.
    the standard ASCII8-SRAM 0x30) to match KOEI cartridges.
    """

    _SRAM_SIZE: ClassVar[int] = 32768
    _SRAM_MASK: ClassVar[int] = 0x7FFF
    _SRAM_PAGES: ClassVar[int] = 0x34


class GameMaster2MapperState(TypedDict):
    banks: list[int]
    sram_half: int
    window_sram_half: list[int]
    sram_enabled: bool
    sram: bytes


@dataclass
class GameMaster2Mapper(_BankTracing):
    """Konami Game Master 2 mapper: 128 KB ROM + 8 KB battery-backed SRAM.

    Follows openMSX RomGameMaster2.cc. Four 8 KB windows at 0x4000-0x5FFF
    (window 0, fixed to ROM page 0, not switchable), 0x6000-0x7FFF (window 1),
    0x8000-0x9FFF (window 2), 0xA000-0xBFFF (window 3).

    Windows 1-3 switch only on a write to the *low 4 KB* of their region
    (0x6000-0x6FFF, 0x8000-0x8FFF, 0xA000-0xAFFF); a write to the high 4 KB
    does nothing. Each bank-register value is decoded as:

        bits 0-3           ROM page (0-15) when bit 4 clear
        bit 4 (_SRAM_BIT)  1 = SRAM, 0 = ROM
        bit 5 (_SRAM_HALF_BIT)  which 4 KB half of the 8 KB SRAM when bit 4 set

    When a window maps SRAM, both 4 KB halves of the 8 KB window read the same
    4 KB SRAM block (mirror). Each window remembers the SRAM half captured at
    its switch time (openMSX captures the pointer), so two windows may map
    different halves simultaneously. SRAM is writable only through
    0xB000-0xBFFF, and only while window 3's last write enabled SRAM
    (`_sram_enabled`); the write uses the most recently selected half
    (`_sram_half`).
    """

    # Bank-register bit fields and the 4 KB SRAM-half geometry, named to match
    # the decode table in the class docstring.
    _SRAM_BIT: ClassVar[int] = 0x10        # bit 4: 1 = SRAM, 0 = ROM
    _SRAM_HALF_BIT: ClassVar[int] = 0x20   # bit 5: which 4 KB SRAM half
    _ROM_PAGE_MASK: ClassVar[int] = 0x0F   # bits 0-3: ROM page selector
    _HALF_SIZE: ClassVar[int] = 0x1000     # 4 KB SRAM half
    _HALF_MASK: ClassVar[int] = 0x0FFF     # offset within a 4 KB half

    rom: bytes
    # Portability note: `sram` is Optional only so the loader may pass a
    # preloaded save; __post_init__ guarantees it is a `bytearray` of exactly
    # `_SRAM_SIZE` afterwards, which is why every access carries a
    # `# type: ignore`. A Rust/C++ port makes SRAM non-optional (allocate a
    # fixed `[u8; _SRAM_SIZE]` in the constructor/factory) so the read/write
    # paths need no None-check and the type is `&[u8]`, not `Option<&[u8]>`.
    sram: bytearray | None = None
    _SRAM_SIZE: ClassVar[int] = 8192

    # Portability note: `_banks`, `_window_is_sram` and `_window_sram_half` are
    # all fixed length 4 (indexed by window 0-3) and never resized -- a Rust/C++
    # port should use `[u8; 4]` / `[bool; 4]` / `[u16; 4]` (or a bitmask for the
    # flags) rather than a growable Vec. `_banks` is `init=True`, so a port's
    # constructor must seed it with `[0, 1, 2, 3]`. Its stored values are raw
    # register bytes; a port with `[u8; 4]` must mask `value & 0xFF` at the
    # store (Python's unbounded int keeps the downstream `& _SRAM_BIT` etc.
    # harmless without masking).
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _flat: bytearray = field(init=False, repr=False)
    # Per-window flag: True while that window currently maps SRAM.
    _window_is_sram: list[bool] = field(
        default_factory=lambda: [False, False, False, False], init=False, repr=False,
    )
    # Per-window SRAM half base (0x0000 or 0x1000) captured at the window's
    # switch; used by read() so windows switched to different halves stay
    # independent.
    _window_sram_half: list[int] = field(
        default_factory=lambda: [0, 0, 0, 0], init=False, repr=False,
    )
    # Most recently selected SRAM half base (any window); used by the 0xB000 write.
    _sram_half: int = field(default=0, init=False, repr=False)
    # SRAM writability latch, updated only on window-3 (0xA000) writes.
    _sram_enabled: bool = field(default=False, init=False, repr=False)
    _num_pages: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.sram, bytearray) or len(self.sram) != self._SRAM_SIZE:
            self.sram = bytearray(self._SRAM_SIZE)
        self._num_pages = max(1, len(self.rom) // _PAGE_8K)
        self._flat = bytearray(4 * _PAGE_8K)
        for window in range(4):
            self._sync_window(window)

    def _sync_window(self, window: int) -> None:
        """Refresh derived state for a window from its raw register value."""
        value = self._banks[window]
        # `window != 0` is defensive: window 0 has no switch address and its
        # register stays 0, so its SRAM bit is never set in practice -- the
        # guard just makes window 0 immune to a corrupt restore payload.
        if window != 0 and (value & self._SRAM_BIT):
            # SRAM-mapped: read() routes this window to self.sram directly, so
            # _flat is left untouched (stale but unread) for this window.
            self._window_is_sram[window] = True
            return
        self._window_is_sram[window] = False
        page = (value & self._ROM_PAGE_MASK) % self._num_pages
        src = self.rom[page * _PAGE_8K:(page + 1) * _PAGE_8K]
        dst = window * _PAGE_8K
        self._flat[dst:dst + len(src)] = src
        if len(src) < _PAGE_8K:
            # Page runs past the end of a short/truncated ROM: open bus.
            self._flat[dst + len(src):dst + _PAGE_8K] = b"\xff" * (_PAGE_8K - len(src))

    def read(self, addr: int) -> int:
        # `idx` relies on the signed-subtraction idiom noted at the top of this
        # module (a Rust/C++ port must guard `addr >= 0x4000` before subtracting).
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            window = idx >> 13  # idx // _PAGE_8K
            if self._window_is_sram[window]:
                offset = self._window_sram_half[window] | (addr & self._HALF_MASK)
                return self.sram[offset]  # type: ignore[index]
            return self._flat[idx]
        return 0xFF  # outside 0x4000-0xBFFF: open bus

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr < 0xB000:
            if addr & 0x1000:
                # High 4 KB of a region: not a bank-switch address.
                return
            region = addr >> 12  # 0x6, 0x8 or 0xA
            window = (region >> 1) - 2  # 0x6->1, 0x8->2, 0xA->3
            if window == 3:
                # Only window 3 (0xA000) toggles the SRAM writability latch.
                self._sram_enabled = (value & self._SRAM_BIT) != 0
            old = self._banks[window]
            self._banks[window] = value
            if value & self._SRAM_BIT:
                self._sram_half = self._HALF_SIZE if (value & self._SRAM_HALF_BIT) else 0x0000
                self._window_sram_half[window] = self._sram_half
            # _sync_window derives _window_is_sram / _flat from the raw register
            # value just stored, for both the ROM and SRAM cases.
            self._sync_window(window)
            _trace_bank(self, window, old, value, addr)
        elif 0xB000 <= addr < 0xC000:
            if self._sram_enabled:
                offset = self._sram_half | (addr & self._HALF_MASK)
                self.sram[offset] = value & 0xFF  # type: ignore[index]

    def save_sram(self, path: Path) -> None:
        path.write_bytes(self.sram)  # type: ignore[arg-type]

    # Portability note: snapshot/restore round-trips a heterogeneous
    # `dict[str, object]` (list[int], int, bool, bytes) with runtime casts, like
    # the other mappers in this module. A Rust/C++ port replaces this with a
    # typed state struct plus explicit (de)serialization -- there is no untyped
    # string-keyed map with runtime coercion.
    def snapshot(self) -> GameMaster2MapperState:
        return {
            "banks": list(self._banks),
            "sram_half": self._sram_half,
            "window_sram_half": list(self._window_sram_half),
            "sram_enabled": self._sram_enabled,
            "sram": bytes(self.sram),  # type: ignore[arg-type]
        }

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(GameMaster2MapperState, state)
        self._banks[:] = typed_state["banks"]
        self._sram_half = typed_state["sram_half"]
        self._window_sram_half[:] = typed_state["window_sram_half"]
        self._sram_enabled = typed_state["sram_enabled"]
        if self.sram is not None:
            self.sram[:] = typed_state["sram"]
        for window in range(4):
            self._sync_window(window)


class Ascii16Sram2MapperState(Ascii16MapperState):
    sram: bytes


@dataclass
class Ascii16Sram2Mapper(Ascii16Mapper):
    """ASCII16 mapper + 2 KB battery-backed SRAM (openMSX RomAscii16_2).

    Only window 1 (0x8000–0xBFFF) can be SRAM-mapped. SRAM is selected for
    window 1 when its bank register value equals exactly 0x10 (strict equality;
    any other value selects a ROM page). Writes to 0x6000–0x7FFF always update
    bank registers (raw value).
    """

    _SRAM_SIZE: ClassVar[int] = 2048
    _SRAM_MASK: ClassVar[int] = 0x7FF
    _SRAM_SELECT: ClassVar[int] = 0x10

    sram: bytearray | None = None
    # Per-window flag: True while that window currently maps SRAM (window 1
    # only, ever). Read() routes an SRAM-mapped window straight to self.sram
    # and a ROM-mapped window to the inherited flat mirror -- no window is
    # ever both at once, so no write-through between the two is needed.
    # Portability note: length is fixed at 2 for this class and never
    # resized after construction -- a Rust/C++ port should use [bool; 2] /
    # std::array<bool, 2> (or a bitmask) rather than a growable Vec<bool>.
    _window_is_sram: list[bool] = field(
        default_factory=lambda: [False, False], init=False, repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.sram, bytearray) or len(self.sram) != self._SRAM_SIZE:
            self.sram = bytearray(self._SRAM_SIZE)
        self._flat = bytearray(2 * _PAGE_16K)
        for window in range(2):
            self._sync_window(window)

    def _is_sram_bank(self, window: int) -> bool:
        return window == 1 and self._banks[window] == self._SRAM_SELECT

    def _sync_window(self, window: int) -> None:
        if self._is_sram_bank(window):
            # SRAM-mapped: read() routes this window to self.sram directly,
            # so _flat is left untouched (stale but unread) for this window.
            self._window_is_sram[window] = True
            return
        self._window_is_sram[window] = False
        super()._sync_window(window)

    def read(self, addr: int) -> int:
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            window = idx >> 14  # idx // _PAGE_16K, as a shift
            if self._window_is_sram[window]:
                base = 0x4000 + window * _PAGE_16K
                return self.sram[(addr - base) & self._SRAM_MASK]  # type: ignore[index]
            return self._flat[idx]
        return self._read_out_of_window(addr)

    def _read_out_of_window(self, addr: int) -> int:
        if addr < 0x8000:
            window, base = 0, 0x4000
        else:
            window, base = 1, 0x8000
        if self._is_sram_bank(window):
            return self.sram[(addr - base) & self._SRAM_MASK]  # type: ignore[index]
        page_offset = self._banks[window] * _PAGE_16K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            window = (addr >> 12) & 0x01
            old = self._banks[window]
            self._banks[window] = value
            if value != old:
                self._sync_window(window)
            _trace_bank(self, window, old, value, addr)
        elif addr >= 0x8000:
            if self._is_sram_bank(1):
                self.sram[(addr - 0x8000) & self._SRAM_MASK] = value & 0xFF  # type: ignore[index]

    def save_sram(self, path: Path) -> None:
        path.write_bytes(self.sram)  # type: ignore[arg-type]

    def snapshot(self) -> Ascii16Sram2MapperState:
        return {**super().snapshot(), "sram": bytes(self.sram)}  # type: ignore[arg-type]

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        typed_state = cast(Ascii16Sram2MapperState, state)
        if self.sram is not None:
            self.sram[:] = typed_state["sram"]


@dataclass
class Ascii16Sram8Mapper(Ascii16Sram2Mapper):
    """ASCII16 mapper + 8 KB battery-backed SRAM."""

    _SRAM_SIZE: ClassVar[int] = 8192
    _SRAM_MASK: ClassVar[int] = 0x1FFF


class RTypeMapperState(TypedDict):
    """Save-state schema for RTypeMapper.snapshot()/restore()."""

    bank: int


@dataclass
class RTypeMapper(_BankTracing):
    """R-Type (Irem) mapper: 16 KB fixed at 0x4000, 16 KB switchable at 0x8000.

    The fixed window at 0x4000–0x7FFF always shows ROM block
    _RTYPE_FIXED_BLOCK (0x17) -- a hardcoded block, not the ROM's size-
    derived last page (openMSX RomRType.cc reset(): setRom(1, 0x17); the
    real cartridge wires two physical ROM chips, and 0x17 is one of two
    content-identical "last page" blocks the hardware always shows here,
    per references/docs/"R-Type and Mega Flash ROM _ MSX Resource
    Center.md": "Bank 1: Fixed at 0Fh or 17h").
    The switchable window at 0x8000–0xBFFF starts at page 0.
    Bank register: write anywhere to 0x4000–0x7FFF.
    Bank mask: value & _RTYPE_MASK_HI when bit 4 set, else value & _RTYPE_MASK
    (openMSX RomRType).

    Note on generality: this mirrors openMSX RomBlocks::setRom's own
    two-tier block resolution (direct index if below the ROM's page count,
    else masked with page_count - 1, open bus if still out of range) for
    _RTYPE_FIXED_BLOCK specifically. This has only been verified against
    the one 384 KB (24-page) canonical R-Type dump the primary reference
    and openMSX both describe, where 0x17 is directly selectable (23 < 24
    pages). Behaviour on any other ROM size (untested by this codebase --
    see tests/test_rtype_mapper.py's own 4-page fixture, which only
    exercises the masked-fallback branch) is unconfirmed against real
    hardware.
    """

    rom: bytes
    _bank: int = field(default=0, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_16K)

    def read(self, addr: int) -> int:
        if 0x4000 <= addr < 0x8000:
            pages = self._num_pages()
            if _RTYPE_FIXED_BLOCK < pages:
                fixed_block = _RTYPE_FIXED_BLOCK
            else:
                fixed_block = _RTYPE_FIXED_BLOCK & (pages - 1)
            fixed = fixed_block * _PAGE_16K + (addr - 0x4000)
            if 0 <= fixed < len(self.rom):
                return self.rom[fixed]
            return 0xFF
        if 0x8000 <= addr < 0xC000:
            offset = self._bank * _PAGE_16K + (addr - 0x8000)
            if 0 <= offset < len(self.rom):
                return self.rom[offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x4000 <= addr < 0x8000:
            if value & _RTYPE_HI_BIT:
                value = value & _RTYPE_MASK_HI
            else:
                value = value & _RTYPE_MASK
            old = self._bank
            self._bank = value
            _trace_bank(self, 1, old, self._bank, addr)

    def snapshot(self) -> RTypeMapperState:
        return {"bank": self._bank}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(RTypeMapperState, state)
        self._bank = int(typed_state["bank"])


class KonamiMapperState(TypedDict):
    """Save-state schema for KonamiMapper.snapshot()/restore()."""

    banks: list[int]


@dataclass
class KonamiMapper(_BankTracing):
    """Konami (Konami4) mapper: four 8 KB windows.

    Window 0 (0x4000–0x5FFF) is permanently fixed to page 0.
    Windows 1–3 are switched by writing the page index to an address
    within the window itself (0x6000–0x7FFF, 0x8000–0x9FFF, 0xA000–0xBFFF).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    # Flat mirror of the four banked windows (0x4000-0xBFFF), rebuilt only on
    # bank switch: reads are hot (millions/frame), switches are rare, so
    # resolving the window on every read is wasted work. Window 0 is fixed
    # to page 0 and is populated once in __post_init__ but never re-synced
    # by write() (writes to 0x4000-0x5FFF are ignored).
    _flat: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._flat = bytearray(4 * _PAGE_8K)
        for window in range(4):
            self._sync_window(window)

    def _bank_mask(self) -> int:
        # Konami4 hardware: 5-bit bank register, fixed regardless of the
        # ROM's actual page count (openMSX RomKonami::setBlockMask(31)).
        # Not a cap on the page count itself -- see _select_page.
        return 31

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def _select_page(self, value: int) -> int:
        # openMSX RomBlocks::setRom's two-tier resolution: a raw value
        # already below the ROM's real (uncapped) page count selects that
        # page directly; only a value at or above it is folded through the
        # fixed 5-bit mask, and even then _sync_window's own bounds check
        # resolves it to open bus if it is still out of range. Same shape
        # as KonamiSCCMapper._select_page -- the mask differs (fixed here,
        # derived from the ROM's own page count there), not the two-tier
        # structure.
        pages = self._num_pages()
        if value < pages:
            return value
        return value & self._bank_mask()

    def _sync_window(self, window: int) -> None:
        page = self._banks[window]
        src = self.rom[page * _PAGE_8K : (page + 1) * _PAGE_8K]
        dst = window * _PAGE_8K
        self._flat[dst : dst + len(src)] = src
        if len(src) < _PAGE_8K:
            # Page runs past the end of a short/truncated ROM: open bus.
            self._flat[dst + len(src) : dst + _PAGE_8K] = b"\xff" * (_PAGE_8K - len(src))

    def read(self, addr: int) -> int:
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            return self._flat[idx]
        # Outside the four windows: real hardware mirrors windows 0/1 into
        # 0x0000-0x3FFF and windows 2/3 into 0xC000-0xFFFF (openMSX
        # RomKonami::bankSwitch). Both ranges land inside _flat directly,
        # with no separate bank/bounds arithmetic needed: idx = addr for
        # the low mirror (no shift), idx = addr - 0x8000 for the high one.
        if addr < 0x4000:
            return self._flat[addr]
        return self._flat[addr - 0x8000]

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr < 0x8000:
            window = 1
        elif 0x8000 <= addr < 0xA000:
            window = 2
        elif 0xA000 <= addr < 0xC000:
            window = 3
        else:
            # Writes to 0x4000–0x5FFF are ignored; window 0 is fixed to page 0.
            return
        new = self._select_page(value)
        old = self._banks[window]
        self._banks[window] = new
        if new != old:
            self._sync_window(window)
        _trace_bank(self, window, old, new, addr)

    def snapshot(self) -> KonamiMapperState:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(KonamiMapperState, state)
        banks = typed_state["banks"]
        if len(banks) != 4 or any(b < 0 for b in banks):
            raise ValueError("KonamiMapperState.banks must have 4 non-negative entries")
        self._banks[:] = banks
        for window in range(4):
            self._sync_window(window)


class MajutsushiMapperState(KonamiMapperState):
    last_dac: int


@dataclass
class MajutsushiMapper(KonamiMapper):
    """Konami mapper + DAC for Hai no Majutsushi.

    Writes to 0x5000–0x5FFF are routed to the DAC (8-bit unsigned PCM);
    all other behaviour -- bank switching (write()) and reads, mirror
    included -- is inherited from KonamiMapper unchanged. This matches
    openMSX's RomMajutsushi exactly: it subclasses RomKonami and overrides
    only reset(), writeMem() (the same DAC intercept) and
    getWriteCacheLine() -- no readMem/peekMem override, and RomKonami's
    own bankSwitch() is not virtual, so nothing about bank selection or
    the read-side mirror can differ between the two classes on real
    hardware either.

    DAC writes are timestamped via _get_cycle callback so generate_samples()
    can reproduce sub-frame timing (same role as openMSX's BlipBuffer delta).
    """

    _last_dac: int = field(default=0x80, init=False, repr=False)
    _dac_events: list[tuple[int, int]] = field(default_factory=list, init=False, repr=False)

    def write(self, addr: int, value: int) -> None:
        if 0x5000 <= addr < 0x6000:
            cycle = self._get_cycle() if self._get_cycle else 0
            self._dac_events.append((cycle, value & 0xFF))
        else:
            super().write(addr, value)

    def generate_samples(self, n: int, frame_start: int = 0, frame_end: int = 0) -> bytearray:
        """Return n signed 16-bit LE mono PCM samples from this frame's DAC events.

        frame_start / frame_end: machine.cycle_count at the frame boundaries.
        DAC events recorded during the frame are mapped to sample positions
        proportionally, matching openMSX DACSound8U delta-at-time behaviour.
        Conversion: (uint8 value - 0x80) * 256 → int16.
        """
        events = self._dac_events
        self._dac_events = []

        cycles = frame_end - frame_start if frame_end > frame_start else 1
        out = bytearray(n * 2)
        value = self._last_dac
        ev_idx = 0

        for i in range(n):
            threshold = frame_start + i * cycles // n
            while ev_idx < len(events) and events[ev_idx][0] <= threshold:
                value = events[ev_idx][1]
                ev_idx += 1
            sample = (value - 0x80) * 256
            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        if ev_idx < len(events):
            value = events[-1][1]
        self._last_dac = value
        return out

    def snapshot(self) -> MajutsushiMapperState:
        # _dac_events is transient (consumed each frame), so only last_dac persists.
        return {**super().snapshot(), "last_dac": self._last_dac}

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        typed_state = cast(MajutsushiMapperState, state)
        self._last_dac = typed_state["last_dac"]


class KonamiSCCMapperState(TypedDict):
    banks: list[int]
    scc_mode: bool


@dataclass
class KonamiSCCMapper(_BankTracing):
    """Konami SCC mapper: 8 KB bank switching in the same style as
    KonamiMapper, extended with SCC mode. Same two-tier page-select
    resolution (see _select_page), but a different mask -- derived from
    this ROM's own page count rather than KonamiMapper's fixed 5 bits --
    and windows mirror outside 0x4000-0xBFFF in the opposite direction
    (see read()).

    When the window-2 bank register value has its low 6 bits all set
    ((value & 0x3F) == 0x3F), the address range 0x9800–0x9FFF is redirected to
    SCC registers instead of ROM. This does not suppress the bank update:
    window 2's bank register is selected from the same written value on
    every write to its zone, enable code included (openMSX RomKonamiSCC's
    writeMem tests the two independently, not as an if/else).

    All four windows are switchable. Each bank register occupies only the
    low 2 KB of its window's register zone:
        bank 0 (0x4000): 0x5000–0x57FF
        bank 1 (0x6000): 0x7000–0x77FF
        bank 2 (0x8000): 0x9000–0x97FF  (0x3F also enables SCC)
        bank 3 (0xA000): 0xB000–0xB7FF
    Decoding the whole window would wrongly treat ordinary writes (e.g. a
    BIOS RAM test hitting 0xBF00) as bank switches.
    """

    rom: bytes
    scc: "SCC"
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _scc_mode: bool = field(default=False, init=False, repr=False)
    # Flat mirror of the four banked windows (0x4000-0xBFFF), rebuilt only on
    # bank switch. Reads are hot (millions/frame) and switches are rare, so
    # trading the per-read window-resolution branch + bounds check for a
    # per-switch 8 KB copy is a straight win.
    _flat: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._flat = bytearray(4 * _PAGE_8K)
        for window in range(4):
            self._sync_window(window)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def _select_page(self, value: int) -> int:
        # openMSX RomBlocks::setRom's two-tier resolution (RomKonamiSCC
        # installs no setBlockMask override, so the mask is the default
        # `nrBlocks - 1`, derived from this ROM's own page count -- unlike
        # KonamiMapper's fixed 5-bit mask): a raw value already below the
        # real page count selects that page directly; otherwise it is
        # masked with (pages - 1), which is only a contiguous-low-bits mask
        # when pages is itself a power of two.
        pages = self._num_pages()
        if value < pages:
            return value
        return value & (pages - 1)

    def _sync_window(self, window: int) -> None:
        page = self._banks[window]
        src = self.rom[page * _PAGE_8K : (page + 1) * _PAGE_8K]
        dst = window * _PAGE_8K
        self._flat[dst : dst + len(src)] = src
        if len(src) < _PAGE_8K:
            # Page runs past the end of a short/truncated ROM: open bus.
            self._flat[dst + len(src) : dst + _PAGE_8K] = b"\xff" * (_PAGE_8K - len(src))

    def read(self, addr: int) -> int:
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            return self.scc.read(addr - 0x9800)
        idx = addr - 0x4000
        if 0 <= idx < _WINDOW_BYTES:
            return self._flat[idx]
        # Outside the four windows: real hardware mirrors windows 0/1 into
        # 0xC000-0xFFFF and windows 2/3 into 0x0000-0x3FFF (openMSX
        # RomKonamiSCC::bankSwitch) -- the opposite direction from plain
        # KonamiMapper. Both ranges land inside _flat directly, with no
        # separate bank/bounds arithmetic needed: idx = addr + 0x4000 for
        # the low mirror, idx = addr - 0xC000 for the high one. The
        # mirror is checked here (i.e. only once the SCC
        # zone check above has already missed) because it is pure ROM and
        # never SCC-routed, whatever scc_mode holds -- openMSX's own
        # SCC-visibility check only ever tests the raw address against
        # 0x9800-0x9FFF, never a mirrored one.
        if addr < 0x4000:
            return self._flat[addr + 0x4000]
        return self._flat[addr - 0xC000]

    def _switch_bank(self, window: int, value: int, addr: int) -> None:
        new = self._select_page(value)
        old = self._banks[window]
        self._banks[window] = new
        if new != old:
            self._sync_window(window)
        _trace_bank(self, window, old, new, addr)

    def write(self, addr: int, value: int) -> None:
        # SCC register writes take priority over bank-register writes.
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            self.scc.write(addr - 0x9800, value)
            return
        if 0x5000 <= addr < 0x5800:
            self._switch_bank(0, value, addr)
        elif 0x7000 <= addr < 0x7800:
            self._switch_bank(1, value, addr)
        elif 0x9000 <= addr < 0x9800:
            # Window 2 bank register: low 6 bits all set enables SCC mode
            # (upper 2 bits are don't-care); any other value disables it.
            # openMSX's writeMem tests this and the page-selection zone as
            # two independent conditions, not if/else, so the bank update
            # below runs unconditionally -- including on the enable-code
            # write, which also updates window 2's bank register on real
            # hardware (it does not leave it untouched).
            self._scc_mode = (value & 0x3F) == 0x3F
            self._switch_bank(2, value, addr)
        elif 0xB000 <= addr < 0xB800:
            self._switch_bank(3, value, addr)
        # Writes outside the four register zones are ignored.

    def snapshot(self) -> KonamiSCCMapperState:
        # SCC chip state is snapshotted separately by the state module.
        return {"banks": list(self._banks), "scc_mode": self._scc_mode}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(KonamiSCCMapperState, state)
        banks = typed_state["banks"]
        if len(banks) != 4 or any(b < 0 for b in banks):
            raise ValueError("KonamiSCCMapperState.banks must have 4 non-negative entries")
        self._banks[:] = banks
        self._scc_mode = typed_state["scc_mode"]
        for window in range(4):
            self._sync_window(window)


_SCCI_RAM_BLOCKS = 8
# Real SCC-I cartridges (Konami 052539) have a 128 KB (16 x 8 KB block)
# address space but only 64 KB of physical RAM: bit 3 of a bank register's
# selected block is not connected to a real memory chip on either factory
# cartridge variant -- one variant has real RAM at blocks 0-7 and reads
# 0xFF at 8-15, the other the reverse (see references/docs/Konami's Sound
# Cartridge (SCC+) for the MSX.md, "Hardware Properties"/"Programming"
# sections). Rather than modelling that per-variant asymmetry (which would
# need a way to pick a variant this project has no CLI surface for), this
# mask reproduces the "connect the two 64 KB banks so they mirror" hardware
# modification the same reference documents as making a single physical
# cartridge fully compatible with either variant's software: block N and
# block N+8 always alias the same 8 KB, so no software-visible "which
# variant" asymmetry (including the unpopulated-half open-bus behaviour
# some cartridge-detection code may rely on) can trip either title up.
_SCCI_BANK_MASK = 0x07
_SCCI_RAM_SIZE = _SCCI_RAM_BLOCKS * _PAGE_8K  # 64 KB
_SCCI_SCC_WINDOW_LEN = 0x800  # 0x9800-0x9FFF or 0xB800-0xBFFF


class SCCICartState(TypedDict):
    ram: bytes
    banks: list[int]
    mode_register: int


@dataclass
class SCCICart(_BankTracing):
    """SCC-I cartridge (community/openMSX docs: "SCC+"): 64 KB of physical
    bank-switched RAM, addressed as if it were 128 KB (block N mirrors
    block N+8 -- see `_SCCI_BANK_MASK`) in four 8 KB windows at
    0x4000-0xBFFF -- no ROM is ever loaded, RAM starts blank -- plus an
    `SCC` chip reachable at 0x9800-0x9FFF (classic/Compatible mode) or
    0xB800-0xBFFF (Plus mode), selected by a mode register at 0xBFFE/
    0xBFFF. Ground truth: openMSX `MSXSCCPlusCart`.

    Same four bank-register zones as `KonamiSCCMapper` (0x5000-0x57FF,
    0x7000-0x77FF, 0x9000-0x97FF, 0xB000-0xB7FF), but each window's register
    is masked to 3 bits (8 always-valid, mirrored RAM blocks, no "unmapped"
    case) and a RAM-write-region bit (from the mode register) can turn a
    whole window into a plain RAM window, overriding both the bank-register
    zone and the SCC register window for that window -- see write().
    """

    scc: "SCC"
    ram: bytearray = field(
        default_factory=lambda: bytearray(_SCCI_RAM_SIZE), init=False, repr=False
    )
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _mode_register: int = field(default=0, init=False, repr=False)
    # Portability note: fixed-length (4), one flag per window, allocated once
    # at construction and never resized -- a Rust/C++ port should use
    # [bool; 4]/std::array<bool, 4>, the same convention the other bank-
    # switching mappers' own _window_is_sram fields document (e.g.
    # Ascii8Mapper, GameMaster2Mapper, RTypeMapper in this file).
    _is_ram_segment: list[bool] = field(
        default_factory=lambda: [False, False, False, False], init=False, repr=False
    )
    # Cached `(bank & _SCCI_BANK_MASK) * _PAGE_8K` per window -- recomputed
    # only on bank switch (_sync_window_base), not on every read/write, the
    # same hot-path trade-off KonamiSCCMapper's own _flat mirror documents
    # ("reads are hot (millions/frame) and switches are rare").
    _window_base: list[int] = field(
        default_factory=lambda: [0, 0, 0, 0], init=False, repr=False
    )
    # None when the SCC register window is inactive; otherwise the window's
    # base address (0x9800 classic, 0xB800 Plus).
    _scc_window_base: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        for window in range(4):
            self._sync_window_base(window)
        self._set_mode_register(0)

    def _sync_window_base(self, window: int) -> None:
        self._window_base[window] = (self._banks[window] & _SCCI_BANK_MASK) * _PAGE_8K

    def read(self, addr: int) -> int:
        base = self._scc_window_base
        if base is not None and base <= addr < base + _SCCI_SCC_WINDOW_LEN:
            return self.scc.read(addr - base)
        if 0x4000 <= addr < 0xC000:
            window = (addr >> 13) - 2
            return self.ram[self._window_base[window] + (addr & 0x1FFF)]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if addr < 0x4000 or addr >= 0xC000:
            return
        # Mode register is mapped at both 0xBFFE and 0xBFFF -- OR-ing in bit
        # 0 collapses either address to 0xBFFF, so one comparison covers both.
        if (addr | 0x0001) == 0xBFFF:
            self._set_mode_register(value)
            return
        window = (addr >> 13) - 2
        if self._is_ram_segment[window]:
            # A RAM-write region takes priority over both the bank-register
            # zone and the SCC register window for this window (matches
            # openMSX MSXSCCPlusCart::writeMem's check order).
            self.ram[self._window_base[window] + (addr & 0x1FFF)] = value
            return
        if (addr & 0x1800) == 0x1000:
            old = self._banks[window]
            new = value & 0xFF
            self._banks[window] = new
            self._sync_window_base(window)
            self._sync_scc_window()
            _trace_bank(self, window, old, new, addr)
            return
        base = self._scc_window_base
        if base is not None and base <= addr < base + _SCCI_SCC_WINDOW_LEN:
            self.scc.write(addr - base, value)
        # Writes outside all of the above zones are ignored.

    def _set_mode_register(self, value: int) -> None:
        self._mode_register = value & 0xFF
        self.scc.set_mode(bool(self._mode_register & 0x20))
        if self._mode_register & 0x10:
            self._is_ram_segment[:] = [True, True, True, True]
        else:
            self._is_ram_segment[0] = bool(self._mode_register & 0x01)
            self._is_ram_segment[1] = bool(self._mode_register & 0x02)
            # Window 2's RAM-write bit additionally requires Plus mode.
            self._is_ram_segment[2] = (self._mode_register & 0x24) == 0x24
            self._is_ram_segment[3] = False
        self._sync_scc_window()

    def _sync_scc_window(self) -> None:
        """Recompute which SCC register window, if any, is currently active.

        Plus mode: window 3's bank register bit 7 gates the SCC+ window at
        0xB800. Compatible mode: window 2's low 6 bits all set (same
        enable code as KonamiSCCMapper) gates the classic window at 0x9800.
        """
        if (self._mode_register & 0x20) and (self._banks[3] & 0x80):
            self._scc_window_base = 0xB800
        elif not (self._mode_register & 0x20) and ((self._banks[2] & 0x3F) == 0x3F):
            self._scc_window_base = 0x9800
        else:
            self._scc_window_base = None

    def snapshot(self) -> SCCICartState:
        # SCC chip state is snapshotted separately by the state module.
        return {
            "ram": bytes(self.ram),
            "banks": list(self._banks),
            "mode_register": self._mode_register,
        }

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(SCCICartState, state)
        ram = typed_state["ram"]
        banks = typed_state["banks"]
        if len(ram) != _SCCI_RAM_SIZE or len(banks) != 4:
            raise ValueError(
                f"SCCICartState.ram must be {_SCCI_RAM_SIZE} bytes and "
                f"banks must have 4 entries"
            )
        self.ram[:] = ram
        self._banks[:] = banks
        for window in range(4):
            self._sync_window_base(window)
        self._set_mode_register(typed_state["mode_register"])
