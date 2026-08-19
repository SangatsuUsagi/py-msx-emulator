"""Tests for RTypeMapper (Irem's ASCII16 MegaROM variant mapper).

Layout (per openMSX RomRType.cc):
  0x4000-0x7FFF: Fixed to ROM block 0x17 (hardcoded, RomRType.cc reset():
    setRom(1, 0x17)) resolved through the same direct-or-masked selection
    as RomBlocks::setRom -- block 0x17 directly if the ROM has more than
    0x17 (23) pages, else masked with (page_count - 1)
  0x8000-0xBFFF: Switchable 16 KB, initial page 0
  Bank register: write anywhere to 0x4000-0x7FFF
  Bank mask: value & 0x17 if bit 4 set, else value & 0x1F
"""
from __future__ import annotations

from msx.mapper import RTypeMapper

# 4 pages of 16 KB. Pages have distinct fill bytes.
_PAGE0 = bytes([0xAA] * 16384)
_PAGE1 = bytes([0xBB] * 16384)
_PAGE2 = bytes([0xCC] * 16384)
_PAGE3 = bytes([0xDD] * 16384)
_ROM_4P = _PAGE0 + _PAGE1 + _PAGE2 + _PAGE3  # 64 KB, 4 pages


class TestRTypeFixed:
    def test_fixed_window_reads_last_page(self):
        # 4-page ROM: 0x17 (23) >= 4 pages, so masked with (4-1)=3:
        # 23 & 3 = 3 -> page 3 (0xDD). Coincides with the old "last page"
        # formula for this particular page count, but via the masked
        # fallback branch, not direct selection.
        m = RTypeMapper(rom=_ROM_4P)
        assert m.read(0x4000) == 0xDD

    def test_fixed_window_end_reads_last_page(self):
        m = RTypeMapper(rom=_ROM_4P)
        assert m.read(0x7FFF) == 0xDD

    def test_fixed_window_unaffected_by_bank_write(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0)  # switch 0x8000 to page 0
        assert m.read(0x4000) == 0xDD  # still last page

    def test_below_0x4000_returns_0xff(self):
        m = RTypeMapper(rom=_ROM_4P)
        assert m.read(0x3FFF) == 0xFF

    def test_above_0xbfff_returns_0xff(self):
        m = RTypeMapper(rom=_ROM_4P)
        assert m.read(0xC000) == 0xFF

    def test_fixed_window_direct_selects_block_0x17_for_large_rom(self):
        # 25-page (400 KB) ROM: 0x17 (23) < 25 pages, so block 23 is
        # selected directly (the un-tested-until-now branch -- pages > 23).
        pages = [bytes([i]) * 16384 for i in range(25)]
        rom = b"".join(pages)
        m = RTypeMapper(rom=rom)
        assert m.read(0x4000) == 23

    def test_fixed_window_masked_result_differs_from_old_last_page_formula(self):
        # 16-page (256 KB) ROM: 0x17 (23) >= 16 pages, masked with
        # (16-1)=15: 23 & 15 = 7 -> page 7. The old `pages - 1` formula
        # would have given page 15 instead -- confirms this is a real
        # behaviour change, not just a rename, for ROM sizes where the two
        # formulas disagree.
        pages = [bytes([i]) * 16384 for i in range(16)]
        rom = b"".join(pages)
        m = RTypeMapper(rom=rom)
        assert m.read(0x4000) == 7


class TestRTypeSwitchable:
    def test_initial_bank_is_0(self):
        m = RTypeMapper(rom=_ROM_4P)
        assert m.read(0x8000) == 0xAA  # page 0

    def test_bank_switch_via_write_to_0x4000(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 1)
        assert m.read(0x8000) == 0xBB  # page 1

    def test_bank_switch_via_write_to_0x7fff(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x7FFF, 2)
        assert m.read(0x8000) == 0xCC  # page 2

    def test_bank_switch_via_write_anywhere_in_range(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x6000, 1)
        assert m.read(0x8000) == 0xBB

    def test_write_below_0x4000_ignored(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x3FFF, 2)
        assert m.read(0x8000) == 0xAA  # initial page 0 unchanged

    def test_write_0x8000_ignored(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x8000, 2)
        assert m.read(0x8000) == 0xAA  # still page 0

    def test_switchable_window_end(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 1)
        assert m.read(0xBFFF) == 0xBB


class TestRTypeBankMask:
    def test_mask_bit4_clear_uses_0x1f(self):
        # value = 0x01 (bit 4 clear) → 0x01 & 0x1F = 0x01
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0x01)
        assert m._bank == 0x01

    def test_mask_bit4_set_uses_0x17(self):
        # value = 0x11 (bit 4 set) → 0x11 & 0x17 = 0x11
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0x11)
        assert m._bank == 0x11 & 0x17  # = 0x11 = 17

    def test_mask_bit4_set_removes_bit3(self):
        # value = 0x18 (bits 4 and 3 set) → bit4 set → 0x18 & 0x17 = 0x10
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0x18)
        assert m._bank == 0x10  # bit 3 removed by mask

    def test_mask_bit4_clear_keeps_high_bits(self):
        # value = 0x03 → 0x03 & 0x1F = 0x03
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0x03)
        assert m._bank == 0x03


class TestRTypeOOB:
    def test_oob_bank_read_returns_0xff(self):
        # 4-page ROM; write bank 10 (OOB for this ROM)
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 0x0A)  # bit 4 clear → 0x0A & 0x1F = 10
        assert m._bank == 10
        assert m.read(0x8000) == 0xFF  # OOB → 0xFF

    def test_single_page_rom_fixed_and_switchable_same(self):
        rom = bytes([0x42] * 16384)
        m = RTypeMapper(rom=rom)
        assert m.read(0x4000) == 0x42   # fixed = last (only) page
        assert m.read(0x8000) == 0x42   # switchable = page 0


class TestRTypeTrace:
    def test_bank_stored_after_write(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 2)
        assert m._bank == 2

    def test_initial_bank_is_0(self):
        m = RTypeMapper(rom=_ROM_4P)
        assert m._bank == 0


class TestRTypeSnapshotRestore:
    def test_snapshot_restore_roundtrips_bank(self):
        m = RTypeMapper(rom=_ROM_4P)
        m.write(0x4000, 2)
        snap = m.snapshot()

        m2 = RTypeMapper(rom=_ROM_4P)
        m2.restore(snap)
        assert m2._bank == 2
        assert m2.read(0x8000) == 0xCC
