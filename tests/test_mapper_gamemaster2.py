"""Tests for the Game Master 2 cartridge mapper (128 KB ROM + 8 KB SRAM).

Behaviour follows openMSX RomGameMaster2.cc.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.mapper import GameMaster2Mapper

_PAGE = 0x2000  # 8 KB

# 128 KB ROM (16 pages). Each byte encodes its own offset low byte so a page's
# first byte is (page * 0x2000) & 0xFF == 0; use direct indexing for asserts.
_ROM_128K = bytes(i & 0xFF for i in range(16 * _PAGE))


def _rom_byte(page: int, local: int = 0) -> int:
    return _ROM_128K[page * _PAGE + local]


class TestBankSwitching:
    def test_initial_bank_state(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        # Windows 0..3 map ROM pages 0..3 at construction.
        assert m.read(0x4000) == _rom_byte(0, 0)
        assert m.read(0x6010) == _rom_byte(1, 0x10)
        assert m.read(0x8010) == _rom_byte(2, 0x10)
        assert m.read(0xA010) == _rom_byte(3, 0x10)

    def test_window0_is_fixed(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        # There is no switch address for window 0; a 0x6000 write moves window 1.
        m.write(0x6000, 0x05)
        assert m.read(0x4010) == _rom_byte(0, 0x10)  # window 0 still page 0

    def test_rom_switch_low_4k(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x6000, 0x07)  # window 1 -> ROM page 7 (bit 4 clear)
        assert m.read(0x6010) == _rom_byte(7, 0x10)
        m.write(0x8000, 0x0B)  # window 2 -> ROM page 11
        assert m.read(0x8010) == _rom_byte(11, 0x10)
        m.write(0xA000, 0x0F)  # window 3 -> ROM page 15
        assert m.read(0xA010) == _rom_byte(15, 0x10)

    def test_write_high_4k_does_not_switch(self):
        m = GameMaster2Mapper(rom=_ROM_128K)  # window 2 = page 2
        m.write(0x9000, 0x05)  # high 4 KB of the 0x8000 region: ignored
        assert m.read(0x8010) == _rom_byte(2, 0x10)
        m.write(0x7000, 0x05)  # high 4 KB of the 0x6000 region: ignored
        assert m.read(0x6010) == _rom_byte(1, 0x10)

    def test_rom_page_wraps_modulo_page_count(self):
        # 32 KB ROM = 4 pages; page selector 0x05 wraps to page 1.
        rom = bytes(i & 0xFF for i in range(4 * _PAGE))
        m = GameMaster2Mapper(rom=rom)
        m.write(0x6000, 0x05)  # 5 % 4 == 1
        assert m.read(0x6010) == rom[1 * _PAGE + 0x10]

    def test_write_entirely_outside_register_zones_has_no_effect(self):
        # allium/konami-mapper.allium GameMaster2BankSelect: its requires
        # clause accepts only the low 4 KB of windows 1-3's own regions.
        # An address in neither that range nor the 0xB000-0xBFFF SRAM-write
        # zone (e.g. window 0's own body, or above 0xC000) falls through
        # msx/mapper.py's write() entirely -- no bank register is touched.
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x4500, 0x05)  # window 0's own body -- no register there at all
        m.write(0xC000, 0x05)  # above the cartridge's own address range
        assert m.read(0x4010) == _rom_byte(0, 0x10)  # window 0 still page 0
        assert m.read(0x6010) == _rom_byte(1, 0x10)  # window 1 still page 1 (untouched)


class TestSramRouting:
    def test_sram_4k_mirror_across_window(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x8000, 0x10)  # window 2 -> SRAM, first 4 KB half
        m.sram[0x100] = 0xAB
        m.sram[0x1100] = 0xCD  # upper half, must not be seen
        assert m.read(0x8100) == 0xAB  # low half of window
        assert m.read(0x9100) == 0xAB  # high half mirrors the same 4 KB

    def test_bit5_selects_second_sram_half(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x8000, 0x30)  # SRAM enabled, bit 5 set -> second 4 KB half
        m.sram[0x1100] = 0x42
        assert m.read(0x8100) == 0x42

    def test_two_windows_hold_independent_sram_halves(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x8000, 0x10)  # window 2 -> SRAM half 0
        m.write(0xA000, 0x30)  # window 3 -> SRAM half 1
        m.sram[0x0100] = 0x11
        m.sram[0x1100] = 0x22
        assert m.read(0x8100) == 0x11  # window 2 keeps its captured half 0
        assert m.read(0xA100) == 0x22  # window 3 keeps its captured half 1

    def test_sram_write_only_via_0xb000_when_bank4_enabled(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x10)  # window 3 SRAM enabled -> sram_enabled True
        m.write(0xB100, 0x55)
        assert m.sram[0x100] == 0x55

    def test_sram_write_ignored_when_bank4_not_sram(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x02)  # window 3 selects ROM -> sram_enabled False
        m.write(0xB100, 0x77)
        assert m.sram[0x100] == 0x00  # write ignored

    def test_sram_write_outside_0xb000_zone_has_no_effect_even_when_enabled(self):
        # allium/konami-mapper.allium GameMaster2WriteSram: its own requires
        # clause bounds address to 0xB000-0xBFFF specifically (the *high*
        # 4 KB of window 3's own region, the opposite half from
        # GameMaster2BankSelect's own trigger zone for that same window).
        # A write elsewhere must not reach SRAM even while sram_enabled is
        # true.
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x10)  # window 3 SRAM enabled -> sram_enabled True
        m.write(0x5000, 0x11)  # nowhere near any register or SRAM zone
        m.write(0xA500, 0x22)  # window 3's own low-4KB (BankSelect's zone, not WriteSram's)
        assert m.sram == bytearray(8192)

    def test_bank4_enable_uses_selected_half_for_write(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x30)  # SRAM enabled, second half selected
        m.write(0xB100, 0x99)
        assert m.sram[0x1100] == 0x99


class TestSnapshotRestore:
    def test_sram_write_persists_via_snapshot(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x10)
        m.write(0xB100, 0x99)
        snap = m.snapshot()

        m2 = GameMaster2Mapper(rom=_ROM_128K)
        m2.restore(snap)
        assert m2.sram[0x100] == 0x99

    def test_restore_with_missing_key_does_not_partially_mutate(self):
        """A malformed state dict must not leave the mapper half-restored:
        restore() reads every field from `state` before assigning any of
        them, so a missing key fails before `_banks`/`_window_sram_half`
        (assigned earlier in the method than the missing key used to be
        read) are touched."""
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x10)
        m.write(0xB100, 0x99)
        snap = dict(m.snapshot())
        pre_banks = list(m._banks)
        pre_sram_half = m._sram_half
        pre_window_sram_half = list(m._window_sram_half)
        pre_sram_enabled = m._sram_enabled
        pre_sram = bytes(m.sram)

        del snap["sram_enabled"]
        with pytest.raises(KeyError):
            m.restore(snap)

        assert list(m._banks) == pre_banks
        assert m._sram_half == pre_sram_half
        assert list(m._window_sram_half) == pre_window_sram_half
        assert m._sram_enabled == pre_sram_enabled
        assert bytes(m.sram) == pre_sram

    def test_restore_rebuilds_rom_and_sram_windows(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x6000, 0x07)  # window 1 -> ROM page 7
        m.write(0x8000, 0x10)  # window 2 -> SRAM
        m.sram[0x100] = 0x5A
        snap = m.snapshot()

        m2 = GameMaster2Mapper(rom=_ROM_128K)
        m2.restore(snap)
        assert m2.read(0x6010) == _rom_byte(7, 0x10)
        assert m2.read(0x8100) == 0x5A

    def test_snapshot_survives_mutation(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0x8000, 0x10)
        m.sram[0x100] = 0x3C
        snap = m.snapshot()
        m.write(0x8000, 0x04)  # mutate: window 2 back to ROM
        m.sram[0x100] = 0xFF
        m.restore(snap)
        assert m.read(0x8100) == 0x3C


class TestInvariants:
    def test_banks_list_has_four_entries(self):
        # allium/konami-mapper.allium invariant GameMaster2BanksAreOnePerWindow.
        m = GameMaster2Mapper(rom=_ROM_128K)
        assert len(m._banks) == 4
        m.write(0x6000, 0x07)
        m.write(0x8000, 0x10)
        m.write(0xA000, 0x0F)
        assert len(m._banks) == 4


class TestSramConstruction:
    def test_none_gives_zeroed_8k_sram(self):
        m = GameMaster2Mapper(rom=_ROM_128K)
        assert m.sram is not None
        assert len(m.sram) == 8192
        assert all(b == 0 for b in m.sram)

    def test_preloaded_sram_used(self):
        pre = bytearray(8192)
        pre[0x100] = 0x7E
        m = GameMaster2Mapper(rom=_ROM_128K, sram=pre)
        assert m.sram is pre

    def test_wrong_size_sram_replaced(self):
        m = GameMaster2Mapper(rom=_ROM_128K, sram=bytearray(16))
        assert m.sram is not None
        assert len(m.sram) == 8192

    def test_save_sram_writes_bytes(self, tmp_path: Path):
        m = GameMaster2Mapper(rom=_ROM_128K)
        m.write(0xA000, 0x10)
        m.write(0xB100, 0x42)
        p = tmp_path / "gm2.sram"
        m.save_sram(p)
        assert p.read_bytes()[0x100] == 0x42
        assert len(p.read_bytes()) == 8192
