"""Tests for the Halnote ROM mapper (Sony HBI-J1's MSX-JE ROM,
msx/mapper.py:HalnoteMapper). Ground truth: openMSX RomHalnote.
"""
from __future__ import annotations

import pytest

from msx.mapper import HalnoteMapper


def _make_rom(page_marker: bool = True) -> bytearray:
    """A 1 MB ROM where each 8 KB page's first byte is its page index (mod
    256), so tests can identify which page is visible without depending on
    specific ROM content elsewhere."""
    rom = bytearray(1048576)
    if page_marker:
        for page in range(128):
            rom[page * 8192] = page & 0xFF
    return rom


def _cart(rom: bytearray | None = None) -> HalnoteMapper:
    return HalnoteMapper(rom=bytes(rom if rom is not None else _make_rom()))


# ---------------------------------------------------------------------------
# ROM size validation
# ---------------------------------------------------------------------------

def test_correct_size_rom_accepted() -> None:
    HalnoteMapper(rom=bytes(1048576))


def test_wrong_size_rom_rejected() -> None:
    with pytest.raises(ValueError):
        HalnoteMapper(rom=bytes(1024))


# ---------------------------------------------------------------------------
# Four main 8 KB bank-switched windows
# ---------------------------------------------------------------------------

def test_initial_bank_state() -> None:
    cart = _cart()
    assert cart.read(0x4000) == 0
    assert cart.read(0x6000) == 1
    assert cart.read(0x8000) == 2
    assert cart.read(0xA000) == 3


def test_bank_switch_selects_new_page() -> None:
    cart = _cart()
    cart.write(0x8FFF, 0x05)
    assert cart.read(0x8000) == 5


def test_bank_register_top_bit_does_not_affect_page_selection() -> None:
    cart = _cart()
    cart.write(0x8FFF, 0x85)
    assert cart.read(0x8000) == 5  # 0x85 & 0x7F == 0x05


@pytest.mark.parametrize(
    ("register", "window_base"),
    [(0x4FFF, 0x4000), (0x6FFF, 0x6000), (0x8FFF, 0x8000), (0xAFFF, 0xA000)],
)
def test_each_window_has_its_own_bank_register(register: int, window_base: int) -> None:
    cart = _cart()
    cart.write(register, 0x0A)
    assert cart.read(window_base) == 0x0A


def test_page_stays_within_7_bits_at_max_register_value() -> None:
    """allium/halnote-mapper.allium's PagesAreSevenBits invariant: a bank
    register byte of 0xFF must still select one of the 128 pages (0-127),
    never overflowing into an out-of-range page index."""
    cart = _cart()
    cart.write(0x8FFF, 0xFF)
    assert cart.read(0x8000) == 0x7F  # page 127, the highest valid page


# ---------------------------------------------------------------------------
# SRAM enable via bank 0's top bit
# ---------------------------------------------------------------------------

def test_sram_disabled_by_default() -> None:
    cart = _cart()
    assert cart.read(0x0000) == 0xFF


def test_enabling_sram_makes_it_read_write_at_low_16k() -> None:
    cart = _cart()
    cart.write(0x4FFF, 0x85)  # bank 0 stays page 5, SRAM enabled
    cart.write(0x0000, 0xAB)
    assert cart.read(0x0000) == 0xAB


def test_sram_spans_the_full_16kb_region() -> None:
    cart = _cart()
    cart.write(0x4FFF, 0x80)
    cart.write(0x3FFF, 0xCD)
    assert cart.read(0x3FFF) == 0xCD


def test_disabling_sram_unmaps_it_again() -> None:
    cart = _cart()
    cart.write(0x4FFF, 0x85)
    cart.write(0x0000, 0xAB)
    cart.write(0x4FFF, 0x05)  # bit 0x80 cleared, bank stays page 5
    assert cart.read(0x0000) == 0xFF
    cart.write(0x4FFF, 0x85)  # re-enable
    assert cart.read(0x0000) == 0xAB  # underlying SRAM byte survived


def test_sram_write_ignored_while_disabled() -> None:
    cart = _cart()
    cart.write(0x0000, 0xAB)  # SRAM disabled -- write must be a no-op
    cart.write(0x4FFF, 0x80)  # now enable SRAM
    assert cart.read(0x0000) == 0x00


# ---------------------------------------------------------------------------
# JIS2 dictionary sub-mapper via bank 1's top bit
# ---------------------------------------------------------------------------

def test_submapper_disabled_leaves_bank1_content_visible() -> None:
    rom = _make_rom()
    rom[1 * 8192 + 0x1000] = 0xEE  # bank 1 (page 1)'s content at offset 0x7000-0x6000
    cart = _cart(rom)
    cart.write(0x77FF, 0x30)
    assert cart.read(0x7000) == 0xEE  # bank 1's ordinary ROM content, not sub-bank 0x30's


def test_enabling_submapper_shadows_0x7000() -> None:
    rom = _make_rom()
    rom[0x80000 + 3 * 0x800] = 0x99
    cart = _cart(rom)
    cart.write(0x6FFF, 0x85)  # sub-mapper enabled, bank 1 stays page 5
    cart.write(0x77FF, 0x03)
    assert cart.read(0x7000) == 0x99


def test_sub_windows_independently_selected() -> None:
    rom = _make_rom()
    rom[0x80000 + 3 * 0x800] = 0x11
    rom[0x80000 + 9 * 0x800] = 0x22
    cart = _cart(rom)
    cart.write(0x6FFF, 0x80)
    cart.write(0x77FF, 0x03)
    cart.write(0x7FFF, 0x09)
    assert cart.read(0x7000) == 0x11
    assert cart.read(0x7800) == 0x22


def test_submapper_does_not_affect_lower_half_of_window1() -> None:
    """0x6000-0x6FFF is the lower half of bank 1's window -- the sub-mapper
    only ever shadows the upper half (0x7000-0x7FFF), regardless of which
    page bank 1's own register (also written by this same byte) selects."""
    rom = _make_rom()
    cart = _cart(rom)
    cart.write(0x6FFF, 0x85)  # sub-mapper enabled, bank 1 -> page 5 (0x85 & 0x7F)
    cart.write(0x77FF, 0x03)
    assert cart.read(0x6000) == 5  # bank 1's own page marker, unaffected by the sub-mapper


# ---------------------------------------------------------------------------
# Unmapped regions outside the four main windows
# ---------------------------------------------------------------------------

def test_top_16k_always_reads_0xff() -> None:
    cart = _cart()
    assert cart.read(0xC000) == 0xFF
    cart.write(0x4FFF, 0x85)  # even with SRAM/banks configured
    assert cart.read(0xFFFF) == 0xFF


def test_top_16k_write_ignored() -> None:
    cart = _cart()
    cart.write(0xC000, 0x42)  # must not raise or affect anything readable
    assert cart.read(0xC000) == 0xFF


# ---------------------------------------------------------------------------
# Mapper protocol: snapshot/restore
# ---------------------------------------------------------------------------

def test_state_round_trips_through_snapshot_restore() -> None:
    rom = _make_rom()
    rom[0x80000 + 7 * 0x800] = 0x77
    cart = _cart(rom)
    cart.write(0x4FFF, 0x85)  # SRAM enabled, bank 0 -> page 5
    cart.write(0x6FFF, 0x8A)  # sub-mapper enabled, bank 1 -> page 10
    cart.write(0x8FFF, 0x02)
    cart.write(0xAFFF, 0x03)
    cart.write(0x77FF, 0x07)
    cart.write(0x7FFF, 0x01)
    cart.write(0x0000, 0xAB)
    state = cart.snapshot()

    fresh = _cart(rom)
    fresh.restore(dict(state))

    assert fresh.read(0x4000) == 5
    assert fresh.read(0x6000) == 10
    assert fresh.read(0x8000) == 2
    assert fresh.read(0xA000) == 3
    assert fresh.read(0x0000) == 0xAB
    assert fresh.read(0x7000) == 0x77  # sub-bank 7 restored
