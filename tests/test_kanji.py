"""Tests for the JIS Kanji font ROM device (Sony HBI-J1's I/O-port protocol,
ports 0xD8-0xDB). Ground truth: openMSX MSXKanji.
"""
from __future__ import annotations

import pytest

from msx.kanji import KanjiRom
from tests.factories import make_machine

_ROM_128K = bytes(range(256)) * (131072 // 256)
_ROM_256K = bytes(range(256)) * (262144 // 256)


def _make_glyph_rom(size: int, address: int, glyph: bytes) -> bytes:
    """A ROM of `size` bytes, all zero except 32 bytes of `glyph` at `address`."""
    buf = bytearray(size)
    buf[address:address + len(glyph)] = glyph
    return bytes(buf)


def _select(rom: KanjiRom, *, level: int, row: int, column: int) -> None:
    """Select an address via the level's column/row write ports."""
    col_port = 0xD8 if level == 0 else 0xDA
    row_port = 0xD9 if level == 0 else 0xDB
    rom.write_port(col_port, column)
    rom.write_port(row_port, row)


# ---------------------------------------------------------------------------
# ROM size validation
# ---------------------------------------------------------------------------

def test_128kb_rom_accepted() -> None:
    KanjiRom(rom=_ROM_128K)


def test_256kb_rom_accepted() -> None:
    KanjiRom(rom=_ROM_256K)


def test_wrong_size_rom_rejected() -> None:
    with pytest.raises(ValueError):
        KanjiRom(rom=bytes(1024))


# ---------------------------------------------------------------------------
# Address register writes
# ---------------------------------------------------------------------------

def test_column_write_sets_address_bits_5_10() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD8, 0x2A)
    # Read at row=0, level=0: resolved address == column bits only (bits 0-4
    # are 0 immediately after a write, per the reset-counter requirement).
    address = 0x2A << 5
    assert rom.read_port(0xD9) == _ROM_256K[address]


def test_row_write_sets_address_bits_11_16() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD9, 0x15)
    address = 0x15 << 11
    assert rom.read_port(0xD9) == _ROM_256K[address]


def test_column_write_preserves_row_field() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD9, 0x15)  # select row
    rom.write_port(0xD8, 0x2A)  # column write must not disturb the row
    address = (0x15 << 11) | (0x2A << 5)
    assert rom.read_port(0xD9) == _ROM_256K[address]


def test_row_write_preserves_column_field() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD8, 0x2A)  # select column
    rom.write_port(0xD9, 0x15)  # row write must not disturb the column
    address = (0x15 << 11) | (0x2A << 5)
    assert rom.read_port(0xD9) == _ROM_256K[address]


def test_register_write_resets_read_counter() -> None:
    glyph = bytes(range(32))
    rom = KanjiRom(rom=_make_glyph_rom(262144, 0, glyph))
    _select(rom, level=0, row=0, column=0)
    for _ in range(5):
        rom.read_port(0xD9)  # advance the counter partway through the glyph
    rom.write_port(0xD8, 0)  # any register write resets the counter
    assert rom.read_port(0xD9) == glyph[0]


# ---------------------------------------------------------------------------
# Level selects address bit 17 on read
# ---------------------------------------------------------------------------

def test_reading_level2_port_selects_upper_rom_half() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    assert rom.read_port(0xDB) == _ROM_256K[0x20000]


def test_reading_level1_port_selects_lower_rom_half() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    assert rom.read_port(0xD9) == _ROM_256K[0x00000]


def test_read_level_is_independent_of_write_level() -> None:
    """The level used on read comes from the port being read, not from
    whichever port was last written -- writing via the level-2 ports (0xDA/
    0xDB) does not make a level-1 read (0xD9) return level-2 data."""
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xDA, 0x2A)  # write column via level-2 port
    rom.write_port(0xDB, 0x15)  # write row via level-2 port
    address = (0x15 << 11) | (0x2A << 5)
    assert rom.read_port(0xD9) == _ROM_256K[address]  # level-1 read: bit 17 clear
    rom.write_port(0xDA, 0x2A)
    rom.write_port(0xDB, 0x15)
    assert rom.read_port(0xDB) == _ROM_256K[0x20000 | address]  # level-2 read: bit 17 set


# ---------------------------------------------------------------------------
# Auto-increment and 32-byte wrap
# ---------------------------------------------------------------------------

def test_32_consecutive_reads_return_one_glyph_in_order() -> None:
    glyph = bytes(range(1, 33))
    rom = KanjiRom(rom=_make_glyph_rom(262144, 0, glyph))
    _select(rom, level=0, row=0, column=0)
    read_bytes = [rom.read_port(0xD9) for _ in range(32)]
    assert bytes(read_bytes) == glyph


def test_33rd_read_repeats_byte_0_of_same_glyph() -> None:
    glyph = bytes(range(1, 33))
    rom = KanjiRom(rom=_make_glyph_rom(262144, 0, glyph))
    _select(rom, level=0, row=0, column=0)
    first = rom.read_port(0xD9)
    for _ in range(31):
        rom.read_port(0xD9)
    thirty_third = rom.read_port(0xD9)
    assert thirty_third == first


def test_out_of_range_address_reads_0xff() -> None:
    rom = KanjiRom(rom=_ROM_128K)  # level 1 only; level 2 is out of range
    assert rom.read_port(0xDB) == 0xFF


# ---------------------------------------------------------------------------
# Field ranges (allium/kanji-rom.allium's FieldsStayInRange invariant)
# ---------------------------------------------------------------------------

def test_column_write_masks_to_6_bits() -> None:
    """A byte with high bits set (e.g. 0xFF) still only ever selects one of
    the 64 columns -- the column field stays in 0-63 regardless of what a
    misbehaving (or simply careless) caller writes."""
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD8, 0xFF)
    address = 0x3F << 5  # 0xFF & 0x3F == 0x3F
    assert rom.read_port(0xD9) == _ROM_256K[address]


def test_row_write_masks_to_6_bits() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD9, 0xFF)
    address = 0x3F << 11  # 0xFF & 0x3F == 0x3F
    assert rom.read_port(0xD9) == _ROM_256K[address]


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

def test_reset_zeroes_the_address() -> None:
    rom = KanjiRom(rom=_ROM_256K)
    rom.write_port(0xD8, 0x2A)
    rom.write_port(0xD9, 0x15)
    rom.reset()
    assert rom.read_port(0xD9) == _ROM_256K[0]


def test_reset_zeroes_the_counter_independent_of_column_row() -> None:
    """WriteColumn/WriteRow already zero the counter as a side effect, so a
    reset() call immediately after a register write can't distinguish
    "reset() zeroed the counter" from "it was already 0". Advance the
    counter via reads only (column/row stay at their already-zero default),
    then reset() with no intervening write, and confirm the next read
    returns byte 0 again -- not byte 5, which is what an un-reset counter
    would return."""
    rom = KanjiRom(rom=_ROM_256K)
    for _ in range(5):
        rom.read_port(0xD9)  # advance the counter to 5, no register write
    rom.reset()
    assert rom.read_port(0xD9) == _ROM_256K[0]


# ---------------------------------------------------------------------------
# Machine.reset() wiring (openspec/changes/wire-kanji-and-halnote-reset)
# ---------------------------------------------------------------------------

def test_machine_reset_zeroes_kanji_address() -> None:
    machine = make_machine(rom=b"\x00" * 0x8000)
    machine.kanji = KanjiRom(rom=_ROM_256K)
    machine.kanji.write_port(0xD8, 0x2A)
    machine.kanji.write_port(0xD9, 0x15)
    machine.reset()
    assert machine.kanji.read_port(0xD9) == _ROM_256K[0]
