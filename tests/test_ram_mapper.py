"""Tests for msx.ram_mapper.RamMapper."""
from msx.ram_mapper import RamMapper

# ---------------------------------------------------------------------------
# Buffer size and initial state
# ---------------------------------------------------------------------------

def test_ram_size_is_128kb() -> None:
    rm = RamMapper()
    assert len(rm.ram) == 131072


def test_initial_bank_registers() -> None:
    rm = RamMapper()
    assert rm.banks == [3, 2, 1, 0]


def test_initial_ram_is_zero() -> None:
    rm = RamMapper()
    assert all(b == 0 for b in rm.ram)


# ---------------------------------------------------------------------------
# Read/write round-trip and bank translation
# ---------------------------------------------------------------------------

def test_write_read_roundtrip_page3() -> None:
    rm = RamMapper()
    # Default: page 3 → bank 0, physical offset = 0*0x4000 + (0xC000 & 0x3FFF) = 0
    rm.write(0xC000, 0x42)
    assert rm.read(0xC000) == 0x42
    assert rm.ram[0x0000] == 0x42


def test_bank_translation_two_pages() -> None:
    rm = RamMapper()
    rm.banks[2] = 5  # page 2 → bank 5
    rm.banks[3] = 7  # page 3 → bank 7
    rm.write(0x8000, 0xAA)
    rm.write(0xC000, 0xBB)
    assert rm.ram[5 * 0x4000 + 0] == 0xAA
    assert rm.ram[7 * 0x4000 + 0] == 0xBB


def test_same_bank_aliasing() -> None:
    rm = RamMapper()
    rm.banks[2] = 0
    rm.banks[3] = 0
    rm.write(0x8000, 0x11)
    assert rm.read(0xC000) == 0x11


def test_bank_number_masked_to_3_bits() -> None:
    rm = RamMapper()
    rm.banks[3] = 0x08 & 0x07  # write_port would mask; set directly for unit test
    assert rm.banks[3] == 0
    rm.write(0xC000, 0x55)
    assert rm.ram[0x0000] == 0x55


def test_offset_within_bank() -> None:
    rm = RamMapper()
    rm.banks[3] = 0  # page 3 → bank 0
    rm.write(0xC001, 0x77)
    assert rm.ram[0x0001] == 0x77


def test_page2_bank_offset() -> None:
    rm = RamMapper()
    rm.banks[2] = 1  # page 2 → bank 1, physical base = 0x4000
    rm.write(0x8000, 0x33)
    assert rm.ram[0x4000] == 0x33


# ---------------------------------------------------------------------------
# I/O port read/write
# ---------------------------------------------------------------------------

def test_write_port_ff_sets_page3_bank() -> None:
    rm = RamMapper()
    rm.write_port(0xFF, 0x05)
    assert rm.banks[3] == 5
    rm.write(0xC000, 0xAB)
    assert rm.ram[5 * 0x4000 + 0] == 0xAB


def test_write_port_fc_sets_page0_bank() -> None:
    rm = RamMapper()
    rm.write_port(0xFC, 0x07)
    assert rm.banks[0] == 7


def test_read_port_fe_returns_page2_bank() -> None:
    rm = RamMapper()
    rm.banks[2] = 3
    assert rm.read_port(0xFE) == 3


def test_read_port_all_pages() -> None:
    rm = RamMapper()
    rm.banks = [1, 2, 3, 4]
    assert rm.read_port(0xFC) == 1
    assert rm.read_port(0xFD) == 2
    assert rm.read_port(0xFE) == 3
    assert rm.read_port(0xFF) == 4


def test_write_port_masks_to_3_bits() -> None:
    rm = RamMapper()
    rm.write_port(0xFF, 0x0F)  # 0x0F & 0x07 = 7
    assert rm.banks[3] == 7


def test_write_port_value_zero() -> None:
    rm = RamMapper()
    rm.write_port(0xFD, 0x00)
    assert rm.banks[1] == 0
