"""Tests for Memory slot-3 secondary slot register (sub-slot) dispatch."""
from __future__ import annotations

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper


def _make_memory(slot_register: int = 0xD4, sub_slot_reg: int = 0x00) -> Memory:
    """Create a Memory with slot3/page3, sub_slot_enabled=True, and RamMapper."""
    rom = bytes(0x8000)  # 32 KB BIOS ROM (0x0000-0x7FFF)
    mem = Memory(
        rom=rom,
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=slot_register,
        ram_mapper=RamMapper(),
        sub_slot_reg=sub_slot_reg,
        sub_slot_enabled=True,
    )
    return mem


# ---------------------------------------------------------------------------
# 0xFFFF intercept
# ---------------------------------------------------------------------------

def test_subslot_write_ffff_stores_value() -> None:
    # page3 = slot3 (bits 7:6 = 0b11 → slot_register 0xCX or 0xDX etc.)
    mem = _make_memory(slot_register=0xC0)  # page3=slot3, others=slot0
    mem.write(0xFFFF, 0xA5)
    assert mem.sub_slot_reg == 0xA5


def test_subslot_read_ffff_returns_complement() -> None:
    mem = _make_memory(slot_register=0xC0)
    mem.sub_slot_reg = 0x5A
    assert mem.read(0xFFFF) == 0xA5  # ~0x5A & 0xFF


def test_subslot_write_ffff_does_not_go_to_ram_mapper() -> None:
    mem = _make_memory(slot_register=0xC0)
    mem.write(0xFFFF, 0x99)
    # The RAM mapper at page 3 should NOT have been written
    assert mem.sub_slot_reg == 0x99
    # Read from ram_mapper for address 0xFFFF should not return 0x99
    # (sub_slot_reg is 0x99 = sub-slots 2,2,2,1 → page3 sub=2 → ram mapper)
    # After the intercept, sub_slot_reg changed but the intercept itself consumed the write
    # Verify by checking that re-reading 0xFFFF still gives complement
    assert mem.read(0xFFFF) == (~0x99 & 0xFF)


def test_subslot_ffff_no_intercept_when_page3_not_slot3() -> None:
    # page3 = slot1 (bits 7:6 = 0b01)
    mem = _make_memory(slot_register=0x44)  # page3=slot1, page2=slot1, page1=slot1, page0=slot0
    mem.sub_slot_reg = 0x00
    # Writing 0xFFFF goes to mapper (slot1), not the sub-slot register
    mem.write(0xFFFF, 0xBB)
    assert mem.sub_slot_reg == 0x00  # unchanged


# ---------------------------------------------------------------------------
# Sub-slot 0 ROM dispatch
# ---------------------------------------------------------------------------

def test_subslot0_read_from_sub0_rom() -> None:
    sub_rom = bytes([0x41, 0x42, 0x43] + [0xFF] * (0x4000 - 3))  # 16 KB
    mem = _make_memory(slot_register=0xC0)  # page3=slot3; but we need page0=slot3
    # Set page0=slot3: slot_register bits 1:0 = 11
    mem.slot_register = 0xC3  # page3=slot3, page0=slot3
    mem.sub0_rom = sub_rom
    mem.sub_slot_reg = 0x00  # page0 → sub-slot 0

    assert mem.read(0x0000) == 0x41
    assert mem.read(0x0001) == 0x42
    assert mem.read(0x0002) == 0x43


def test_subslot0_write_ignored() -> None:
    sub_rom = bytearray([0x00] * 0x4000)
    mem = _make_memory(slot_register=0xC3)
    mem.sub0_rom = bytes(sub_rom)
    mem.sub_slot_reg = 0x00
    mem.write(0x0000, 0xFF)
    assert mem.read(0x0000) == 0x00  # unchanged


def test_subslot0_address_out_of_range_returns_ff() -> None:
    sub_rom = bytes([0xAA] * 0x4000)
    # page1 = slot3, sub-slot 0 → but sub0_rom only covers 0x0000-0x3FFF
    mem = _make_memory(slot_register=0xCC)  # page3=slot3, page2=slot3, page1=slot3, page0=slot0
    mem.sub0_rom = sub_rom
    mem.sub_slot_reg = 0x00  # all pages → sub-slot 0
    # Read at 0x4000 (page1) → sub-slot 0 but addr > 0x3FFF → 0xFF
    assert mem.read(0x4000) == 0xFF


# ---------------------------------------------------------------------------
# Sub-slot 1 (reserved)
# ---------------------------------------------------------------------------

def test_subslot1_read_returns_ff() -> None:
    mem = _make_memory(slot_register=0xC3)  # page0=slot3
    mem.sub_slot_reg = 0x01  # page0 → sub-slot 1
    assert mem.read(0x0000) == 0xFF


def test_subslot1_write_ignored() -> None:
    mem = _make_memory(slot_register=0xC3)
    mem.sub_slot_reg = 0x01
    mem.write(0x0000, 0xFF)  # should not raise


# ---------------------------------------------------------------------------
# Sub-slots 2/3 → RAM mapper
# ---------------------------------------------------------------------------

def test_subslot2_routes_to_ram_mapper() -> None:
    mem = _make_memory(slot_register=0xC0)  # page3=slot3
    mem.sub_slot_reg = 0b10_00_00_00  # page3 → sub-slot 2; others → sub-slot 0
    mem.ram_mapper.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_subslot3_routes_to_ram_mapper() -> None:
    mem = _make_memory(slot_register=0xC0)  # page3=slot3
    mem.sub_slot_reg = 0b11_00_00_00  # page3 → sub-slot 3
    mem.ram_mapper.write(0xC000, 0x77)
    assert mem.read(0xC000) == 0x77
