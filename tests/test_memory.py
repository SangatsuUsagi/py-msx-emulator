import pytest
from msx.memory import Memory


def make_mem(rom: bytes | None = None, cartridge: bytes | None = None) -> Memory:
    if rom is None:
        rom = bytes(32768)
    return Memory(rom=rom, ram=bytearray(16384), cartridge=cartridge)


def test_rom_read() -> None:
    rom = bytes([0xAB] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x0000) == 0xAB


def test_rom_read_only() -> None:
    rom = bytes([0] * 0x0100 + [0x12] + [0] * (32768 - 0x0101))
    mem = make_mem(rom=rom)
    mem.write(0x0100, 0xFF)
    assert mem.read(0x0100) == 0x12


def test_ram_round_trip() -> None:
    mem = make_mem()
    mem.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_ram_mirror() -> None:
    mem = make_mem()
    mem.write(0xE000, 0x55)
    assert mem.read(0xE000) == 0x55


def test_cartridge_read() -> None:
    cart = bytes([0xCC] + [0] * 32767)
    mem = make_mem(cartridge=cart)
    assert mem.read(0x4000) == 0xCC


def test_empty_cartridge_returns_ff() -> None:
    mem = make_mem()
    assert mem.read(0x6000) == 0xFF


def test_address_wraps_at_16_bits() -> None:
    rom = bytes([0x77] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x10000) == mem.read(0x0000)


def test_slot_register_read_back() -> None:
    mem = make_mem()
    mem.write_port_a8(0x09)
    assert mem.read_port_a8() == 0x09


def test_slot_register_default_zero() -> None:
    mem = make_mem()
    assert mem.read_port_a8() == 0x00
