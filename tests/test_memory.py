import pytest
from msx.memory import Memory

# Standard MSX1 boot slot layout:
#   page 0 (0x0000-0x3FFF): slot 0 = BIOS ROM
#   page 1 (0x4000-0x7FFF): slot 1 = cartridge
#   page 2 (0x8000-0xBFFF): slot 1 = cartridge
#   page 3 (0xC000-0xFFFF): slot 3 = RAM
# slot_register = 0b11_01_01_00 = 0xD4
_MSX1_SLOTS = 0xD4


def make_mem(rom: bytes | None = None, cartridge: bytes | None = None,
             slot_register: int = _MSX1_SLOTS) -> Memory:
    if rom is None:
        rom = bytes(32768)
    return Memory(rom=rom, ram=bytearray(16384), cartridge=cartridge,
                  slot_register=slot_register)


# ---------------------------------------------------------------------------
# ROM region (slot 0)
# ---------------------------------------------------------------------------

def test_rom_read() -> None:
    rom = bytes([0xAB] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x0000) == 0xAB


def test_rom_read_only() -> None:
    rom = bytes([0] * 0x0100 + [0x12] + [0] * (32768 - 0x0101))
    mem = make_mem(rom=rom)
    mem.write(0x0100, 0xFF)
    assert mem.read(0x0100) == 0x12


# ---------------------------------------------------------------------------
# RAM region (slot 3, page 3)
# ---------------------------------------------------------------------------

def test_ram_round_trip() -> None:
    mem = make_mem()
    mem.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_ram_mirror() -> None:
    mem = make_mem()
    mem.write(0xE000, 0x55)
    assert mem.read(0xE000) == 0x55


# ---------------------------------------------------------------------------
# Cartridge region (slot 1, pages 1+2)
# ---------------------------------------------------------------------------

def test_cartridge_read() -> None:
    cart = bytes([0xCC] + [0] * 32767)
    mem = make_mem(cartridge=cart)
    assert mem.read(0x4000) == 0xCC


def test_empty_cartridge_returns_ff() -> None:
    mem = make_mem()
    assert mem.read(0x6000) == 0xFF


# ---------------------------------------------------------------------------
# Address wraparound
# ---------------------------------------------------------------------------

def test_address_wraps_at_16_bits() -> None:
    rom = bytes([0x77] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x10000) == mem.read(0x0000)


# ---------------------------------------------------------------------------
# Slot-register port helpers
# ---------------------------------------------------------------------------

def test_slot_register_read_back() -> None:
    mem = make_mem()
    mem.write_port_a8(0x09)
    assert mem.read_port_a8() == 0x09


def test_slot_register_dataclass_default_msx1_layout() -> None:
    # Default is standard MSX1 layout: page3=slot3(RAM), page1+2=slot1(cart)
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None)
    assert mem.read_port_a8() == 0xD4


# ---------------------------------------------------------------------------
# Slot dispatch — new tests
# ---------------------------------------------------------------------------

def test_page1_slot0_reads_bios_rom() -> None:
    rom = bytes(0x4001)
    rom = bytes([0] * 0x4000 + [0xBB])
    # slot_register: page 1 (bits 3:2) = slot 0 → reads BIOS ROM
    mem = Memory(rom=rom, ram=bytearray(16384), cartridge=None, slot_register=0x00)
    assert mem.read(0x4000) == 0xBB


def test_page1_slot1_reads_cartridge() -> None:
    cart = bytes([0xCC] + [0] * 32767)
    # bits 3:2 = 0b01 → page 1 = slot 1 = cartridge
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=cart,
                 slot_register=0x04)
    assert mem.read(0x4000) == 0xCC


def test_slot2_open_bus_returns_ff() -> None:
    # bits 1:0 = 0b10 → page 0 = slot 2 = open bus
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None,
                 slot_register=0x02)
    assert mem.read(0x0000) == 0xFF


def test_slot2_open_bus_write_is_noop() -> None:
    # page 0 = slot 2
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None,
                 slot_register=0x02)
    mem.write(0x0000, 0xAB)  # should not raise


def test_initial_slot_register_all_pages_slot0() -> None:
    # Default slot_register=0: all pages → slot 0 (BIOS ROM)
    rom = bytes([0xAA] + [0] * 32767)
    mem = Memory(rom=rom, ram=bytearray(16384), cartridge=None, slot_register=0x00)
    assert mem.read(0x0000) == 0xAA
    assert mem.read(0x4000) == 0x00  # ROM byte at 0x4000
