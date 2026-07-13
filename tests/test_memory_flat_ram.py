"""Tests for the data-driven MSX2 slot-3 layout (flat 64 KB RAM, e.g. HB-F1XD).

slot_register 0xFF selects slot 3 for every page, so sub_slot_reg alone drives
the sub-slot dispatch. sub_slot_reg bit pairs: page0=1:0, page1=3:2, page2=5:4,
page3=7:6.
"""
from msx.mapper import FlatMapper
from msx.memory import Memory

_ALL_SLOT3 = 0xFF  # every page -> slot 3


def _make_flat(sub0_rom: bytes | None = None) -> Memory:
    return Memory(
        rom=bytes(32768),
        ram=bytearray(65536),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=sub0_rom,
        flat_ram_subslot=3,
    )


def test_flat_ram_responds_only_in_its_subslot() -> None:
    """Flat RAM answers in sub-slot 3; the same address in empty sub-slot 2 is 0xFF."""
    mem = _make_flat()
    mem.sub_slot_reg = 0xC0  # page3 -> sub-slot 3
    mem.write(0xC000, 0x5A)
    assert mem.read(0xC000) == 0x5A
    mem.sub_slot_reg = 0x80  # page3 -> sub-slot 2 (empty)
    assert mem.read(0xC000) == 0xFF


def test_flat_ram_addressable_across_full_space() -> None:
    """Offset == address: byte written via page 0 is read back at the same RAM cell."""
    mem = _make_flat()
    mem.sub_slot_reg = 0x03  # page0 -> sub-slot 3
    mem.write(0x0000, 0x11)
    mem.sub_slot_reg = 0xC0  # page3 -> sub-slot 3
    mem.write(0xC000, 0x22)
    mem.sub_slot_reg = 0x03
    assert mem.read(0x0000) == 0x11
    mem.sub_slot_reg = 0xC0
    assert mem.read(0xC000) == 0x22


def test_empty_subslot_1_reads_open_bus() -> None:
    mem = _make_flat()
    mem.sub_slot_reg = 0x40  # page3 -> sub-slot 1
    assert mem.read(0xC000) == 0xFF


def test_sub0_page0_serves_sub_rom() -> None:
    sub_rom = bytes([0xAA] + [0x00] * 0x3FFF)
    mem = _make_flat(sub0_rom=sub_rom)
    mem.sub_slot_reg = 0x00  # page0 -> sub-slot 0
    assert mem.read(0x0000) == 0xAA


def test_sub0_page1_open_bus_without_fdc() -> None:
    """Sub-slot 0 page 1 (0x4000) is open bus until the FDC is wired (phase 5)."""
    sub_rom = bytes([0xAA] + [0x00] * 0x3FFF)
    mem = _make_flat(sub0_rom=sub_rom)
    mem.sub_slot_reg = 0x00  # page1 -> sub-slot 0
    assert mem.read(0x4000) == 0xFF


def test_write_to_empty_subslot_is_ignored() -> None:
    """A write while an empty sub-slot is selected must not reach the flat RAM."""
    mem = _make_flat()
    mem.sub_slot_reg = 0x80  # page3 -> sub-slot 2 (empty)
    mem.write(0xC000, 0x77)
    mem.sub_slot_reg = 0xC0  # page3 -> sub-slot 3 (RAM)
    assert mem.read(0xC000) == 0x00


def test_sub_rom_write_is_ignored() -> None:
    sub_rom = bytes([0xAA] + [0x00] * 0x3FFF)
    mem = _make_flat(sub0_rom=sub_rom)
    mem.sub_slot_reg = 0x00
    mem.write(0x0000, 0xFF)
    assert mem.read(0x0000) == 0xAA
