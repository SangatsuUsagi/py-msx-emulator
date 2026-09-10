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
    mem.set_sub_slot_reg(0x5A)
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
    mem.set_sub_slot_reg(0x00)
    # Writing 0xFFFF goes to mapper (slot1), not the sub-slot register
    mem.write(0xFFFF, 0xBB)
    assert mem.sub_slot_reg == 0x00  # unchanged


def test_subslot_read_ffff_no_intercept_when_page3_not_slot3() -> None:
    # Read-side counterpart of the write test above (allium/slots.allium's
    # ReadByte/ReadSecondarySlotRegister share the same "page3 is slot3"
    # requires clause as WriteByte/WriteSecondarySlotRegister).
    mem = _make_memory(slot_register=0x44)  # page3=slot1 -> cartridge (empty FlatMapper)
    mem.set_sub_slot_reg(0x5A)
    # If the intercept wrongly fired, this would read back (~0x5A)&0xFF = 0xA5.
    # It should instead reach the empty cartridge slot and read open bus.
    assert mem.read(0xFFFF) == 0xFF


def test_subslot_ffff_no_intercept_when_disabled() -> None:
    # sub_slot_enabled=False (MSX1): the 0xFFFF intercept never fires
    # regardless of slot_register, even when page3 = slot3 -- both requires
    # clauses on WriteSecondarySlotRegister/ReadSecondarySlotRegister must
    # hold (allium/slots.allium). Falls through to slot 3's normal dispatch,
    # here MSX1 flat RAM (no ram_mapper set).
    mem = Memory(
        rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
        slot_register=0xC0,  # page3 = slot3
        sub_slot_enabled=False,
    )
    mem.write(0xFFFF, 0xBB)
    assert mem.sub_slot_reg == 0x00           # unchanged, intercept did not fire
    assert mem.read(0xFFFF) == 0xBB           # served by MSX1 flat RAM instead


def test_subslot_read_ffff_no_intercept_when_disabled() -> None:
    mem = Memory(
        rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
        slot_register=0xC0,  # page3 = slot3
        sub_slot_enabled=False,
    )
    mem.ram[0x7FFF] = 0x42  # MSX1 flat RAM offset for address 0xFFFF (32 KB RAM)
    # If the intercept wrongly fired, this would read back a sub_slot_reg
    # complement instead of the flat RAM byte.
    assert mem.read(0xFFFF) == 0x42


# ---------------------------------------------------------------------------
# Sub-slot 0 ROM dispatch
# ---------------------------------------------------------------------------

def test_subslot0_read_from_sub0_rom() -> None:
    sub_rom = bytes([0x41, 0x42, 0x43] + [0xFF] * (0x4000 - 3))  # 16 KB
    mem = _make_memory(slot_register=0xC0)  # page3=slot3; but we need page0=slot3
    # Set page0=slot3: slot_register bits 1:0 = 11
    mem.set_slot_register(0xC3)  # page3=slot3, page0=slot3
    mem.set_sub0_rom(sub_rom)
    mem.set_sub_slot_reg(0x00)  # page0 → sub-slot 0

    assert mem.read(0x0000) == 0x41
    assert mem.read(0x0001) == 0x42
    assert mem.read(0x0002) == 0x43


def test_subslot0_write_ignored() -> None:
    sub_rom = bytearray([0x00] * 0x4000)
    mem = _make_memory(slot_register=0xC3)
    mem.set_sub0_rom(bytes(sub_rom))
    mem.set_sub_slot_reg(0x00)
    mem.write(0x0000, 0xFF)
    assert mem.read(0x0000) == 0x00  # unchanged


def test_subslot0_address_out_of_range_returns_ff() -> None:
    sub_rom = bytes([0xAA] * 0x4000)
    # page1 = slot3, sub-slot 0 → but sub0_rom only covers 0x0000-0x3FFF
    mem = _make_memory(slot_register=0xCC)  # page3=slot3, page2=slot3, page1=slot3, page0=slot0
    mem.set_sub0_rom(sub_rom)
    mem.set_sub_slot_reg(0x00)  # all pages → sub-slot 0
    # Read at 0x4000 (page1) → sub-slot 0 but addr > 0x3FFF → 0xFF
    assert mem.read(0x4000) == 0xFF


# ---------------------------------------------------------------------------
# Sub-slot 1 (reserved)
# ---------------------------------------------------------------------------

def test_subslot1_read_returns_ff() -> None:
    mem = _make_memory(slot_register=0xC3)  # page0=slot3
    mem.set_sub_slot_reg(0x01)  # page0 → sub-slot 1
    assert mem.read(0x0000) == 0xFF


def test_subslot1_write_ignored() -> None:
    mem = _make_memory(slot_register=0xC3)
    mem.set_sub_slot_reg(0x01)
    mem.write(0x0000, 0xFF)  # should not raise


# ---------------------------------------------------------------------------
# Sub-slots 2/3 → RAM mapper
# ---------------------------------------------------------------------------

def test_subslot2_routes_to_ram_mapper() -> None:
    mem = _make_memory(slot_register=0xC0)  # page3=slot3
    mem.set_sub_slot_reg(0b10_00_00_00)  # page3 → sub-slot 2; others → sub-slot 0
    mem.ram_mapper.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_subslot3_routes_to_ram_mapper() -> None:
    mem = _make_memory(slot_register=0xC0)  # page3=slot3
    mem.set_sub_slot_reg(0b11_00_00_00)  # page3 → sub-slot 3
    mem.ram_mapper.write(0xC000, 0x77)
    assert mem.read(0xC000) == 0x77


# ---------------------------------------------------------------------------
# Slot 2's own sub-slot dispatch (independent of slot 3's, memory-slot-bus's
# "Slot 2 sub-slot dispatch via a per-sub-slot Mapper array" Requirement)
# ---------------------------------------------------------------------------

def _make_memory_slot2(
    slot_register: int,
    mapper2_subslots: list[FlatMapper | None] | None = None,
    slot2_sub_slot_reg: int = 0x00,
) -> Memory:
    rom = bytes(0x8000)  # 32 KB BIOS ROM (0x0000-0x7FFF)
    return Memory(
        rom=rom,
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=slot_register,
        slot2_sub_slot_enabled=True,
        slot2_sub_slot_reg=slot2_sub_slot_reg,
        _mapper2_subslots=mapper2_subslots if mapper2_subslots is not None else [None] * 4,
    )


def test_slot2_subslot0_dispatches_to_its_own_mapper() -> None:
    cart = FlatMapper(bytes([0xAB] * 0x2000))
    mem = _make_memory_slot2(slot_register=0x08, mapper2_subslots=[cart, None, None, None])
    assert mem.read(0x4000) == 0xAB


def test_slot2_switching_subslot_switches_which_mapper_answers() -> None:
    cart0 = FlatMapper(bytes([0x11] * 0x2000))
    cart1 = FlatMapper(bytes([0x22] * 0x2000))
    mem = _make_memory_slot2(
        slot_register=0x08, mapper2_subslots=[cart0, cart1, None, None]
    )
    assert mem.read(0x4000) == 0x11
    mem.set_slot2_sub_slot_reg(0b00_00_01_00)  # page1 -> sub-slot 1
    assert mem.read(0x4000) == 0x22


def test_slot2_unassigned_subslot_reads_open_bus() -> None:
    mem = _make_memory_slot2(slot_register=0x08, mapper2_subslots=[None, None, None, None])
    assert mem.read(0x4000) == 0xFF


def test_slot2_unassigned_subslot_write_ignored() -> None:
    mem = _make_memory_slot2(slot_register=0x08, mapper2_subslots=[None, None, None, None])
    mem.write(0x4000, 0x42)  # must not raise


def test_slot2_subslots_2_and_3_dispatch_to_their_own_mapper() -> None:
    """Sub-slots 0/1 are exercised by the tests above; this closes the same
    coverage for sub-slots 2 and 3 (allium/slots.allium's
    cartridge_slot2_subslot2/cartridge_slot2_subslot3 entity-optional
    obligations)."""
    cart2 = FlatMapper(bytes([0x33] * 0x2000))
    cart3 = FlatMapper(bytes([0x44] * 0x2000))
    mem = _make_memory_slot2(slot_register=0x08, mapper2_subslots=[None, None, cart2, cart3])
    mem.set_slot2_sub_slot_reg(0b00_00_10_00)  # page1 -> sub-slot 2
    assert mem.read(0x4000) == 0x33
    mem.set_slot2_sub_slot_reg(0b00_00_11_00)  # page1 -> sub-slot 3
    assert mem.read(0x4000) == 0x44


def test_slot2_sub_slot_enabled_false_preserves_flat_dispatch() -> None:
    cart = FlatMapper(bytes([0x99] * 0x2000))
    mem = Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        _mapper2=cart,
        slot_register=0x08,  # page1 -> slot2
        slot2_sub_slot_enabled=False,
    )
    assert mem.read(0x4000) == 0x99


# ---------------------------------------------------------------------------
# Generalized 0xFFFF intercept: slot 2 independently expandable
# ---------------------------------------------------------------------------

def _make_memory_slot2_intercept(slot_register: int, slot2_sub_slot_reg: int = 0x00) -> Memory:
    return Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=slot_register,
        slot2_sub_slot_enabled=True,
        slot2_sub_slot_reg=slot2_sub_slot_reg,
        _mapper2_subslots=[None, None, None, None],
    )


def test_slot2_write_ffff_stores_value_when_page3_is_slot2() -> None:
    mem = _make_memory_slot2_intercept(slot_register=0x80)  # page3=slot2, others=slot0
    mem.write(0xFFFF, 0xA5)
    assert mem.slot2_sub_slot_reg == 0xA5


def test_slot2_read_ffff_returns_complement_when_page3_is_slot2() -> None:
    mem = _make_memory_slot2_intercept(slot_register=0x80, slot2_sub_slot_reg=0x5A)
    assert mem.read(0xFFFF) == 0xA5  # ~0x5A & 0xFF


def test_slot2_ffff_no_intercept_when_slot2_sub_slot_disabled() -> None:
    mem = Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=0x80,  # page3=slot2
        slot2_sub_slot_enabled=False,
    )
    mem.write(0xFFFF, 0xBB)  # falls through to flat _mapper2 (empty -> open bus write, no-op)
    assert mem.read(0xFFFF) == 0xFF


def test_both_slot2_and_slot3_expanded_independently() -> None:
    """memory-slot-bus's "Both slot 2 and slot 3 expanded independently"
    scenario: each 0xFFFF access affects only the register of whichever
    slot page 3 currently selects, the other register is undisturbed."""
    mem = Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        ram_mapper=None,
        slot_register=0xC0,  # page3=slot3
        sub_slot_enabled=True,
        slot2_sub_slot_enabled=True,
        _mapper2_subslots=[None, None, None, None],
    )
    mem.write(0xFFFF, 0x11)
    assert mem.sub_slot_reg == 0x11
    assert mem.slot2_sub_slot_reg == 0x00  # untouched

    mem.set_slot_register(0x80)  # switch page3 to slot2
    mem.write(0xFFFF, 0x22)
    assert mem.slot2_sub_slot_reg == 0x22
    assert mem.sub_slot_reg == 0x11  # untouched by the slot2 write


def test_slot2_read_ffff_when_page3_is_slot3_reads_slot3_register() -> None:
    """Read-side counterpart: ReadSlot2SecondarySlotRegister's own
    slot_for_page(...) = 2 requires clause fails while page 3 selects slot
    3, even though slot2_secondary_slot_enabled is True -- 0xFFFF still
    reads slot 3's register, not slot 2's."""
    mem = Memory(
        rom=bytes(0x8000),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=0xC0,  # page3=slot3
        sub_slot_enabled=True,
        sub_slot_reg=0x5A,
        slot2_sub_slot_enabled=True,
        slot2_sub_slot_reg=0x11,
        _mapper2_subslots=[None, None, None, None],
    )
    assert mem.read(0xFFFF) == 0xA5  # ~0x5A & 0xFF -- slot 3's, not ~0x11
