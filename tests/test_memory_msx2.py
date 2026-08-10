"""Tests for Memory MSX2 extensions: ext ROM and RamMapper integration."""
import pytest

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper

# slot_register layouts used in tests
# 0b11_00_01_00 = 0xC4: page0=slot0, page1=slot1, page2=slot0, page3=slot3
_SLOTS_PAGE2_IN_SLOT0 = 0xC4
# 0b11_01_01_00 = 0xD4: MSX1 default
_MSX1_SLOTS = 0xD4


def _make_mem(**kwargs) -> Memory:
    defaults = dict(
        rom=bytes(32768),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
        slot_register=_MSX1_SLOTS,
    )
    defaults.update(kwargs)
    return Memory(**defaults)


# ---------------------------------------------------------------------------
# Ext ROM: slot 0 pages 2 (0x8000–0xBFFF)
# ---------------------------------------------------------------------------

def test_extrom_byte_accessible_at_0x8000() -> None:
    """ext ROM byte at index 0 is readable at address 0x8000 when slot 0 selected."""
    extrom = bytes([0xC9] + [0x00] * 0x3FFF)
    mem = _make_mem(extrom=extrom, slot_register=_SLOTS_PAGE2_IN_SLOT0)
    assert mem.read(0x8000) == 0xC9


def test_extrom_byte_at_end_of_range() -> None:
    """ext ROM byte at index 0x3FFF is readable at address 0xBFFF."""
    extrom = bytes([0x00] * 0x3FFF + [0xAB])
    mem = _make_mem(extrom=extrom, slot_register=_SLOTS_PAGE2_IN_SLOT0)
    assert mem.read(0xBFFF) == 0xAB


def test_extrom_beyond_length_returns_0xff() -> None:
    """Reads past the end of extrom return 0xFF."""
    extrom = bytes([0x42])  # only 1 byte
    mem = _make_mem(extrom=extrom, slot_register=_SLOTS_PAGE2_IN_SLOT0)
    assert mem.read(0x8001) == 0xFF


def test_extrom_write_is_noop() -> None:
    """Writing to ext ROM region is silently ignored."""
    extrom = bytes([0xC9] + [0x00] * 0x3FFF)
    mem = _make_mem(extrom=extrom, slot_register=_SLOTS_PAGE2_IN_SLOT0)
    mem.write(0x8000, 0xFF)
    assert mem.read(0x8000) == 0xC9


def test_extrom_absent_reads_rom_normally() -> None:
    """Without extrom, slot 0 page 2 reads from the BIOS ROM as before."""
    rom = bytes([0x00] * 0x8000 + [0x55] * 0x4000 + [0x00] * 0x4000)
    mem = _make_mem(rom=rom, slot_register=_SLOTS_PAGE2_IN_SLOT0)
    assert mem.read(0x8000) == 0x55


# ---------------------------------------------------------------------------
# RamMapper: slot-3 delegation
# ---------------------------------------------------------------------------

def test_slot3_read_dispatches_to_ram_mapper() -> None:
    """slot-3 read goes to RamMapper when one is provided."""
    rm = RamMapper()
    rm.write(0xC000, 0x55)
    mem = _make_mem(ram_mapper=rm)
    assert mem.read(0xC000) == 0x55


def test_slot3_write_dispatches_to_ram_mapper() -> None:
    """slot-3 write goes to RamMapper when one is provided."""
    rm = RamMapper()
    mem = _make_mem(ram_mapper=rm)
    mem.write(0xC000, 0x42)
    assert rm.read(0xC000) == 0x42


def test_slot3_ram_mapper_bank_translation() -> None:
    """RamMapper bank switching is visible through Memory slot-3 reads."""
    rm = RamMapper()
    rm.banks[3] = 5                   # page 3 → bank 5
    rm.ram[5 * 0x4000] = 0xBB
    mem = _make_mem(ram_mapper=rm)
    assert mem.read(0xC000) == 0xBB


# ---------------------------------------------------------------------------
# MSX1 compatibility: flat RAM unchanged when no RamMapper
# ---------------------------------------------------------------------------

def test_msx1_flat_ram_round_trip() -> None:
    """Without RamMapper, slot-3 uses the flat bytearray (MSX1 behaviour)."""
    mem = _make_mem()
    mem.write(0xC000, 0x77)
    assert mem.read(0xC000) == 0x77


def test_msx1_flat_ram_independent_of_ram_mapper() -> None:
    """MSX1 Memory with no RamMapper — ram_mapper field is None."""
    mem = _make_mem()
    assert mem.ram_mapper is None


# ---------------------------------------------------------------------------
# Slot-3 RAM-strategy exclusivity (allium/slots.allium's
# SlotThreeStrategyIsExclusive invariant on Memory), enforced by
# Memory.__post_init__
# ---------------------------------------------------------------------------

def test_ram_mapper_and_flat_subslot_together_rejected() -> None:
    """ram_mapper and flat_ram_subslot are mutually exclusive slot-3 RAM
    strategies (allium/slots.allium SlotThreeStrategyIsExclusive). Before
    this was enforced, constructing both together silently made
    main_ram_range() report the wrong window (see
    logs/review-python-20260809-195530.md Major finding 3) -- now it is
    rejected at construction instead, closing the gap that
    tests/test_machine_msx2.py's `_make_mem(**kwargs)` helper could
    previously exploit to bypass machine_loader.py:1011's own check."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        _make_mem(ram_mapper=RamMapper(), flat_ram_subslot=3, sub_slot_enabled=True)


def test_fdc_without_flat_subslot_rejected() -> None:
    """fdc is only meaningful under the data-driven slot-3 layout
    (flat_ram_subslot set) -- allium/slots.allium's Memory entity docs."""
    class _StubFdc:
        def read_mem(self, addr: int) -> int:
            return 0xFF

        def write_mem(self, addr: int, value: int) -> None:
            pass

    with pytest.raises(ValueError, match="flat_ram_subslot"):
        _make_mem(fdc=_StubFdc())


def test_ram_mapper_and_flat_subslot_together_rejected_post_construction() -> None:
    """SlotThreeStrategyIsExclusive must hold not just at construction but
    after any later reassignment of ram_mapper/flat_ram_subslot too --
    otherwise the page-routing cache (which only invalidates on
    reassignment, it doesn't re-derive from scratch) could silently keep
    routing through one strategy while the other field disagrees."""
    mem = _make_mem(flat_ram_subslot=3, sub_slot_enabled=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        mem.ram_mapper = RamMapper()


def test_fdc_without_flat_subslot_rejected_post_construction() -> None:
    class _StubFdc:
        def read_mem(self, addr: int) -> int:
            return 0xFF

        def write_mem(self, addr: int, value: int) -> None:
            pass

    mem = _make_mem()
    with pytest.raises(ValueError, match="flat_ram_subslot"):
        mem.fdc = _StubFdc()
