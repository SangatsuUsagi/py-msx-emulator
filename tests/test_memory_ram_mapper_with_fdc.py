"""Tests for FDC + RAM mapper coexistence in the legacy slot-3 strategy.

Before openspec/changes/slot3-fdc-ram-mapper-coexistence, an FDC required
flat_ram_subslot (the data-driven strategy); this file covers the newly
allowed combination of an FDC with a RAM mapper instead.

slot_register 0xFF selects slot 3 for every page, so sub_slot_reg alone
drives the sub-slot dispatch. sub_slot_reg bit pairs: page0=1:0, page1=3:2,
page2=5:4, page3=7:6.
"""
import pytest

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper

_ALL_SLOT3 = 0xFF  # every page -> slot 3


class _StubFdc:
    """Minimal FloppyDisk stand-in: records calls, does not model the wd2793
    controller/register protocol (out of scope for Memory delegation tests --
    see allium/slots.allium's external entity FloppyDisk)."""

    def __init__(self) -> None:
        self.reads: list[int] = []
        self.writes: list[tuple[int, int]] = []

    def read_mem(self, addr: int) -> int:
        self.reads.append(addr)
        return 0x99

    def write_mem(self, addr: int, value: int) -> None:
        self.writes.append((addr, value))


def _make_mapper(sub0_rom: bytes | None = None, fdc: object | None = None) -> Memory:
    """HB-F1XD's real sub-slot-0-sharing layout (SUB ROM page 0, FDC page 1,
    both sub-slot 0), but with a RAM mapper instead of flat RAM."""
    return Memory(
        rom=bytes(32768),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=sub0_rom,
        ram_mapper=RamMapper(),
        fdc=fdc,  # type: ignore[arg-type]
    )


def test_sub0_page0_serves_sub_rom_with_mapper_present() -> None:
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    mem = _make_mapper(sub0_rom=sub_rom)
    mem.set_sub_slot_reg(0x00)  # every page -> sub-slot 0
    assert mem.read(0x0000) == 0x41


def test_sub0_page1_read_delegates_to_fdc() -> None:
    fdc = _StubFdc()
    mem = _make_mapper(fdc=fdc)
    mem.set_sub_slot_reg(0x00)  # every page -> sub-slot 0
    assert mem.read(0x4000) == 0x99
    assert fdc.reads == [0x4000]


def test_sub0_page0_not_affected_by_fdc_presence() -> None:
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    fdc = _StubFdc()
    mem = _make_mapper(sub0_rom=sub_rom, fdc=fdc)
    mem.set_sub_slot_reg(0x00)
    assert mem.read(0x0000) == 0x41
    assert fdc.reads == []


def test_other_subslot_routes_to_ram_mapper() -> None:
    fdc = _StubFdc()
    mem = _make_mapper(fdc=fdc)
    mem.set_sub_slot_reg(0b11_11_11_11)  # every page -> sub-slot 3: RAM mapper
    mem.write(0xC000, 0x55)
    assert mem.read(0xC000) == 0x55
    assert fdc.reads == []


def test_sub0_page1_write_delegates_to_fdc() -> None:
    fdc = _StubFdc()
    mem = _make_mapper(fdc=fdc)
    mem.set_sub_slot_reg(0x00)
    mem.write(0x4000, 0x77)
    assert fdc.writes == [(0x4000, 0x77)]


def test_sub0_page0_write_ignored_when_sub_rom_present() -> None:
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    mem = _make_mapper(sub0_rom=sub_rom)
    mem.set_sub_slot_reg(0x00)
    mem.write(0x0000, 0xFF)
    assert mem.read(0x0000) == 0x41  # unchanged: SUB ROM is read-only


def test_write_to_other_subslot_reaches_ram_mapper() -> None:
    fdc = _StubFdc()
    mem = _make_mapper(fdc=fdc)
    mem.set_sub_slot_reg(0b10_10_10_10)  # every page -> sub-slot 2: RAM mapper
    mem.write(0x8000, 0x33)
    assert mem.read(0x8000) == 0x33
    assert fdc.writes == []


def test_sub_rom_and_fdc_in_independent_subslots_with_mapper() -> None:
    """FS-A1F-style independent sub-slots, but with a RAM mapper instead of
    flat RAM: SUB ROM in sub-slot 1, FDC in sub-slot 2, RAM mapper elsewhere."""
    sub_rom = bytes([0x41] + [0x00] * 0x3FFF)
    fdc = _StubFdc()
    mem = Memory(
        rom=bytes(32768),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=sub_rom,
        sub_rom_subslot=1,
        fdc=fdc,  # type: ignore[arg-type]
        fdc_subslot=2,
        ram_mapper=RamMapper(),
    )

    mem.set_sub_slot_reg(0b00_00_00_00)  # every page -> sub-slot 0: RAM mapper
    mem.write(0x0000, 0x77)
    assert mem.read(0x0000) == 0x77

    mem.set_sub_slot_reg(0b01_01_01_01)  # every page -> sub-slot 1: SUB ROM
    assert mem.read(0x0000) == 0x41

    mem.set_sub_slot_reg(0b10_10_10_10)  # every page -> sub-slot 2: FDC
    assert mem.read(0x4000) == 0x99
    assert fdc.reads == [0x4000]


def test_fdc_requires_a_ram_strategy() -> None:
    with pytest.raises(ValueError, match="fdc requires a slot-3 RAM strategy"):
        Memory(
            rom=bytes(32768),
            ram=bytearray(32768),
            _mapper=FlatMapper(None),
            slot_register=_ALL_SLOT3,
            sub_slot_enabled=True,
            fdc=_StubFdc(),  # type: ignore[arg-type]
        )


def test_sub_rom_subslot_1_without_image_still_falls_through_to_ram_on_read() -> None:
    """Pre-existing quirk (allium/slots.allium ReadByte guidance), preserved
    by the msx/memory.py refactor for FDC+RAM-mapper coexistence: a machine
    with sub_rom_subslot == 1 and no sub0_rom loaded reads sub-slot 1 from
    the RAM mapper rather than open bus -- because the original code reached
    the "reserved sub-slot 1" case only via an implicit fallthrough that
    sub_rom_subslot's own (unmatched) branch never took. No machine does
    this; it is reproduced here, not newly introduced."""
    mem = Memory(
        rom=bytes(32768),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=None,
        sub_rom_subslot=1,
        ram_mapper=RamMapper(),
    )
    mem.ram_mapper.ram[0] = 0x42  # poke directly: write to sub==1 is a documented no-op
    mem.set_sub_slot_reg(0b01_01_01_01)  # every page -> sub-slot 1
    assert mem.read(0x0000) == 0x42
    assert mem._resolve_slot3_write_leaf(0) == mem._write_noop


def test_fdc_with_ram_mapper_does_not_raise() -> None:
    Memory(
        rom=bytes(32768),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        fdc=_StubFdc(),  # type: ignore[arg-type]
        ram_mapper=RamMapper(),
    )
