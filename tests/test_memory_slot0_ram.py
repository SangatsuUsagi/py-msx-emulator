"""Tests for the optional flat RAM region in primary slot 0 (e.g. Sony HB-10:
32 KB main ROM in pages 0-1, 16 KB RAM in page 3), coexisting with the main
ROM at the slot's other pages. See openspec/specs/memory-slot-bus/spec.md's
"Slot 0 MAY host flat RAM at a configured page" Requirement.
"""
import pytest

from msx.mapper import FlatMapper
from msx.memory import Memory

_ALL_SLOT0 = 0x00  # every page -> slot 0


def _make_mem(rom: bytes | None = None, slot0_ram: bytearray | None = None,
              slot0_ram_page: int = 3, extrom: bytes | None = None) -> Memory:
    if rom is None:
        rom = bytes(32768)
    return Memory(
        rom=rom,
        ram=bytearray(65536),
        _mapper=FlatMapper(None),
        slot_register=_ALL_SLOT0,
        slot0_ram=slot0_ram,
        slot0_ram_page=slot0_ram_page,
        extrom=extrom,
    )


def test_slot0_ram_round_trips_a_write() -> None:
    mem = _make_mem(slot0_ram=bytearray(0x4000))
    mem.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_other_slot0_pages_still_route_to_rom() -> None:
    rom = bytes([0xAB] + [0] * 32767)
    mem = _make_mem(rom=rom, slot0_ram=bytearray(0x4000))
    assert mem.read(0x0000) == 0xAB


def test_no_slot0_ram_configured_preserves_default_behavior() -> None:
    mem = _make_mem()
    mem.write(0xC000, 0x99)
    assert mem.read(0xC000) == 0xFF  # falls through to ROM bounds-check, open bus


def test_slot0_ram_out_of_range_access_is_safe() -> None:
    short_ram = bytearray(0x2000)  # shorter than the full 16 KB page
    mem = _make_mem(slot0_ram=short_ram, slot0_ram_page=3)
    assert mem.read(0xE000) == 0xFF  # 0xE000 is past the 0x2000-byte buffer
    mem.write(0xE000, 0x11)
    assert mem.read(0xE000) == 0xFF  # write silently ignored, no wraparound


def test_slot0_ram_page2_conflicts_with_extrom() -> None:
    with pytest.raises(ValueError):
        _make_mem(slot0_ram=bytearray(0x4000), slot0_ram_page=2, extrom=bytes(0x4000))


def test_zero_length_slot3_ram_is_pure_open_bus() -> None:
    """Sony HB-10 shape: slot 3 is genuinely unpopulated on real hardware
    (only slot 0's page-3 RAM exists). A zero-length `ram` buffer must make
    every slot-3 access open bus -- otherwise this codebase's MSX1 flat-RAM
    fallback would supply phantom RAM there, which the BIOS's own memory-
    size probe would find (and could prefer over slot 0's real RAM)."""
    mem = Memory(
        rom=bytes(32768),
        ram=bytearray(0),
        _mapper=FlatMapper(None),
        slot_register=0xFF,  # every page -> slot 3
        slot0_ram=bytearray(0x4000),
        slot0_ram_page=3,
    )
    for addr in (0x0000, 0x4000, 0x8000, 0xC000, 0xFFFF):
        assert mem.read(addr) == 0xFF
    mem.write(0xC000, 0x77)
    assert mem.read(0xC000) == 0xFF  # write silently ignored
