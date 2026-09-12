"""Tests for msx.ram_mapper.RamMapper."""
import pytest

from msx.ram_mapper import RamMapper

# ---------------------------------------------------------------------------
# Buffer size and initial state
# ---------------------------------------------------------------------------

def test_ram_size_is_128kb() -> None:
    rm = RamMapper()
    assert len(rm.ram) == 131072


def test_configured_size_256kb() -> None:
    rm = RamMapper(size_kb=256)
    assert len(rm.ram) == 262144
    assert rm.bank_mask == 0x0F


def test_configured_size_512kb() -> None:
    rm = RamMapper(size_kb=512)
    assert len(rm.ram) == 524288
    assert rm.bank_mask == 0x1F


@pytest.mark.parametrize("bad_size", [0, -16, 100, 15])
def test_invalid_size_rejected(bad_size: int) -> None:
    with pytest.raises(ValueError):
        RamMapper(size_kb=bad_size)


def test_initial_bank_registers() -> None:
    # Power-on/reset state is all banks at segment 0 (openMSX
    # MSXMemoryMapperBase::reset()); [3, 2, 1, 0] is what the MSX2 BIOS boot
    # routine subsequently writes, not the hardware reset value.
    rm = RamMapper()
    assert rm.banks == [0, 0, 0, 0]


def test_initial_ram_is_zero() -> None:
    rm = RamMapper()
    assert all(b == 0 for b in rm.ram)


def test_reset_restores_power_on_banks_keeps_ram() -> None:
    rm = RamMapper()
    rm.banks = [3, 2, 1, 0]  # e.g. after BIOS boot
    rm.write(0xC000, 0x42)
    rm.reset()
    assert rm.banks == [0, 0, 0, 0]
    assert rm.ram[0] == 0x42  # RAM contents retained across reset


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
    # Unused high bits (3-7) read back as 1, not 0 (openMSX
    # MSXMemoryMapperBase::peekIO; see allium/slots.allium
    # ReadRamMapperBankRegister): bank 3 = 0b011 -> 0xFB, not 0x03.
    rm = RamMapper()
    rm.banks[2] = 3
    assert rm.read_port(0xFE) == 0xFB


def test_read_port_all_pages() -> None:
    rm = RamMapper()
    rm.banks = [1, 2, 3, 4]
    assert rm.read_port(0xFC) == 0xF9  # 1 | 0xF8
    assert rm.read_port(0xFD) == 0xFA  # 2 | 0xF8
    assert rm.read_port(0xFE) == 0xFB  # 3 | 0xF8
    assert rm.read_port(0xFF) == 0xFC  # 4 | 0xF8


def test_write_port_masks_to_3_bits() -> None:
    rm = RamMapper()
    rm.write_port(0xFF, 0x0F)  # 0x0F & 0x07 = 7
    assert rm.banks[3] == 7


def test_write_port_value_zero() -> None:
    rm = RamMapper()
    rm.write_port(0xFD, 0x00)
    assert rm.banks[1] == 0


# ---------------------------------------------------------------------------
# Configurable size: bank masking and port read-back for larger mappers
# ---------------------------------------------------------------------------

def test_bank_number_masked_to_4_bits_on_256kb() -> None:
    rm = RamMapper(size_kb=256)
    rm.write_port(0xFF, 0x10)  # 0x10 & 0x0F = 0
    assert rm.banks[3] == 0
    rm.write_port(0xFF, 0x1F)  # 0x1F & 0x0F = 0x0F (bank 15)
    assert rm.banks[3] == 0x0F


def test_read_port_returns_page2_bank_on_256kb() -> None:
    rm = RamMapper(size_kb=256)
    rm.banks[2] = 3
    assert rm.read_port(0xFE) == 0xF3  # 0x03 | 0xF0


def test_read_port_returns_page2_bank_on_512kb() -> None:
    rm = RamMapper(size_kb=512)
    rm.banks[2] = 3
    assert rm.read_port(0xFE) == 0xE3  # 0x03 | 0xE0


def test_bank_number_masked_to_5_bits_on_512kb() -> None:
    rm = RamMapper(size_kb=512)
    rm.write_port(0xFF, 0x20)  # 0x20 & 0x1F = 0
    assert rm.banks[3] == 0
    rm.write_port(0xFF, 0x3F)  # 0x3F & 0x1F = 0x1F (bank 31)
    assert rm.banks[3] == 0x1F


def test_minimum_size_16kb_single_bank() -> None:
    # num_banks = 1 -> bit_ceil(1) - 1 = 0: the smallest valid configuration,
    # where every bank register is forced to bank 0 regardless of what is
    # written to it.
    rm = RamMapper(size_kb=16)
    assert len(rm.ram) == 16384
    assert rm.bank_mask == 0
    rm.write_port(0xFF, 0x07)
    assert rm.banks[3] == 0
    assert rm.read_port(0xFF) == 0xFF  # 0 | ~0 & 0xFF


# ---------------------------------------------------------------------------
# Mapper protocol: kind, snapshot/restore, debug_bank_info
# ---------------------------------------------------------------------------

def test_kind_is_ram_mapper() -> None:
    from msx.mapper import MapperKind
    assert RamMapper.kind == MapperKind.RAM_MAPPER


def test_snapshot_restore_round_trip() -> None:
    rm = RamMapper(size_kb=256)
    rm.banks = [1, 2, 3, 4]
    rm.write(0xC000, 0x99)
    state = rm.snapshot()

    fresh = RamMapper(size_kb=256)
    fresh.restore(state)
    assert fresh.banks == [1, 2, 3, 4]
    assert fresh.ram == rm.ram


def test_restore_rejects_wrong_bank_count() -> None:
    rm = RamMapper()
    with pytest.raises(ValueError):
        rm.restore({"banks": [0, 0, 0], "ram": bytes(len(rm.ram))})


def test_restore_rejects_wrong_ram_length() -> None:
    rm = RamMapper(size_kb=256)
    with pytest.raises(ValueError):
        rm.restore({"banks": [0, 0, 0, 0], "ram": bytes(16384)})


def test_debug_bank_info_describes_page_bank() -> None:
    rm = RamMapper()
    rm.banks[2] = 5
    info = rm.debug_bank_info(2)
    assert info is not None
    assert "5" in info
    assert f"{5 * 0x4000:05X}" in info


def test_debug_bank_info_out_of_range_page_is_none() -> None:
    rm = RamMapper()
    assert rm.debug_bank_info(4) is None
    assert rm.debug_bank_info(-1) is None
