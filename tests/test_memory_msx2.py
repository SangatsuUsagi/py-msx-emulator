"""Tests for Memory MSX2 extensions: ext ROM and RamMapper integration."""
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
