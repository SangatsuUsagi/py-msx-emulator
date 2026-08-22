"""Characterization tests locking in msx/memory.py's current slot-3 sub-slot
dispatch behaviour ahead of openspec/changes/parameterize-subslot-index's
refactor (generalising the `sub0_rom` / `if sub == 0` hard-coding to
configurable per-role sub-slot indices).

These are deliberately not a rewrite of tests/test_memory_flat_ram.py or
tests/test_memory_subslot.py (both already cover the bulk of the dispatch
logic) -- they fill three specific gaps a refactor of this exact code is
most likely to break silently:

1. Page 2 of the data-driven (flat_ram_subslot) sub-slot 0 branch is never
   exercised by any existing test (only page 0 [SUB ROM] and page 1 [FDC]
   are).
2. Page 1's own sub-slot value is only ever exercised at sub == 0 (FDC) in
   existing tests; sub == 1/2/3 at page 1's own address are inferred from
   *other pages'* tests, never checked directly at 0x4000.
3. No existing test writes a single sub_slot_reg value that selects a
   *different* sub-slot for each of the 4 pages simultaneously and asserts
   all 4 independently -- exactly the scenario a refactor that accidentally
   shares state across pages (instead of resolving each page's own bit pair
   independently) would break without any current test catching it.

Every assertion below is against the current (pre-refactor) code and must
stay true, unchanged, after the refactor -- these are not new requirements,
they are the existing behaviour's floor.
"""
from __future__ import annotations

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper

_ALL_SLOT3 = 0xFF  # every page -> slot 3


class _StubFdc:
    def __init__(self) -> None:
        self.reads: list[int] = []
        self.writes: list[tuple[int, int]] = []

    def read_mem(self, addr: int) -> int:
        self.reads.append(addr)
        return 0x99

    def write_mem(self, addr: int, value: int) -> None:
        self.writes.append((addr, value))


def _make_flat(sub0_rom: bytes | None = None, fdc: object | None = None) -> Memory:
    """Same shape as test_memory_flat_ram.py's own helper (HB-F1XD-style:
    flat_ram_subslot=3, sub-slot 0 hosts SUB ROM page 0 / FDC page 1)."""
    return Memory(
        rom=bytes(32768),
        ram=bytearray(65536),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=sub0_rom,
        flat_ram_subslot=3,
        fdc=fdc,  # type: ignore[arg-type]
    )


# -- Gap 1: page 2 of the flat_ram_subslot sub-slot-0 branch -----------------

def test_flat_subslot0_page2_is_open_bus_even_with_subrom_and_fdc_present() -> None:
    """0x8000-0xBFFF (page 2) is untouched by _resolve_slot3_read_leaf's
    sub == 0 branch (which only special-cases page 0 and page 1) -- it must
    fall through to open bus regardless of whether SUB ROM/FDC are wired."""
    sub_rom = bytes([0xAA] * 0x4000)
    fdc = _StubFdc()
    mem = _make_flat(sub0_rom=sub_rom, fdc=fdc)
    mem.set_sub_slot_reg(0x00)  # every page -> sub-slot 0
    assert mem.read(0x8000) == 0xFF
    assert fdc.reads == []  # page 2 must not reach the FDC either


def test_flat_subslot0_page2_write_is_ignored() -> None:
    fdc = _StubFdc()
    mem = _make_flat(fdc=fdc)
    mem.set_sub_slot_reg(0x00)
    mem.write(0x8000, 0x42)
    assert mem.read(0x8000) == 0xFF
    assert fdc.writes == []


# -- Gap 2: page 1's own 4-value sub-slot sweep -------------------------------

def test_flat_subslot_page1_sweeps_all_four_subslot_values() -> None:
    """0x4000 (page 1) resolved independently for each of the 4 possible
    sub-slot values page 1's own bit pair (sub_slot_reg bits 3:2) can select."""
    fdc = _StubFdc()
    mem = _make_flat(fdc=fdc)

    mem.set_sub_slot_reg(0b00_00_00_00)  # page1 -> sub-slot 0: FDC
    assert mem.read(0x4000) == 0x99

    mem.set_sub_slot_reg(0b00_00_01_00)  # page1 -> sub-slot 1: reserved/empty
    assert mem.read(0x4000) == 0xFF

    mem.set_sub_slot_reg(0b00_00_10_00)  # page1 -> sub-slot 2: empty (not flat_ram_subslot)
    assert mem.read(0x4000) == 0xFF

    mem.set_sub_slot_reg(0b00_00_11_00)  # page1 -> sub-slot 3: flat RAM
    mem.write(0x4000, 0x5A)
    assert mem.read(0x4000) == 0x5A

    # The FDC was only ever reached during the sub-slot-0 case above.
    assert fdc.reads == [0x4000]


# -- Gap 3: independent per-page resolution from one register write ----------

def test_flat_subslot_all_four_pages_resolve_independently_at_once() -> None:
    """A single sub_slot_reg write selecting a *different* sub-slot for each
    of the 4 pages must resolve every page independently -- the scenario
    most exposed to a refactor that accidentally shares dispatch state
    across pages instead of keying strictly off each page's own bit pair."""
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    fdc = _StubFdc()
    mem = _make_flat(sub0_rom=sub_rom, fdc=fdc)

    # page0 -> sub-slot 0 (SUB ROM), page1 -> sub-slot 1 (empty),
    # page2 -> sub-slot 2 (empty), page3 -> sub-slot 3 (flat RAM).
    # bits: page3=11 page2=10 page1=01 page0=00
    mem.set_sub_slot_reg(0b11_10_01_00)
    mem.write(0xC000, 0x77)  # page3: flat RAM

    assert mem.read(0x0000) == 0x41   # page0: SUB ROM
    assert mem.read(0x4000) == 0xFF   # page1: reserved/empty
    assert mem.read(0x8000) == 0xFF   # page2: empty (not flat_ram_subslot)
    assert mem.read(0xC000) == 0x77   # page3: flat RAM
    assert fdc.reads == []  # FDC only ever answers sub-slot 0 page 1, unselected here


def test_legacy_subslot_all_four_pages_resolve_independently_at_once() -> None:
    """Same independence check for the legacy (ram_mapper, flat_ram_subslot
    is None) dispatch branch used by cbios_msx2* -- sub-slot 0 (SUB ROM),
    sub-slot 1 (reserved), and sub-slot 2/3 (RAM mapper) all selected by
    different pages from one register write."""
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    mem = Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        ram_mapper=RamMapper(),
        sub_slot_enabled=True,
    )
    mem.set_sub0_rom(sub_rom)
    mem.ram_mapper.write(0xC000, 0x77)  # pre-seed sub-slot 3 -> RAM mapper's own store
    # page0 -> sub-slot 0 (SUB ROM), page1 -> sub-slot 1 (reserved),
    # page2 -> sub-slot 2 (RAM mapper), page3 -> sub-slot 3 (RAM mapper).
    mem.set_sub_slot_reg(0b11_10_01_00)

    assert mem.read(0x0000) == 0x41   # page0: SUB ROM
    assert mem.read(0x4000) == 0xFF   # page1: reserved
    assert mem.read(0xC000) == 0x77   # page3: RAM mapper (pre-seeded)


# -- Gap 4: the exact SUB-ROM/FDC page boundary byte pair ---------------------

def test_flat_subslot0_subrom_fdc_boundary_bytes_do_not_bleed() -> None:
    """0x3FFF (last byte of sub-slot 0 page 0, SUB ROM) and 0x4000 (first
    byte of sub-slot 0 page 1, the FDC) are on opposite sides of a page
    boundary within the *same* sub-slot -- confirm neither device sees the
    other's address."""
    sub_rom = bytes([0x00] * 0x3FFF + [0xEE])  # last SUB ROM byte is distinctive
    fdc = _StubFdc()
    mem = _make_flat(sub0_rom=sub_rom, fdc=fdc)
    mem.set_sub_slot_reg(0x00)  # every page -> sub-slot 0

    assert mem.read(0x3FFF) == 0xEE   # last SUB ROM byte
    assert fdc.reads == []            # FDC not consulted for a page-0 address

    assert mem.read(0x4000) == 0x99   # first FDC byte
    assert fdc.reads == [0x4000]      # exactly one FDC read, for the page-1 address only
