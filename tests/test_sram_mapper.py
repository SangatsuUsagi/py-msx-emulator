"""Tests for SRAM-backed cartridge mappers: Ascii8Sram2/8, Ascii16Sram2/8."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.mapper import (
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
)

# 16 KB = 2 pages of 8 KB. ASCII8 SRAM enable bit = num_pages = 2.
_ROM_16K = bytes(range(256)) * 64
# 32 KB = 2 pages of 16 KB. ASCII16 SRAM selected for window 1 when bank == 0x10.
_ROM_32K = bytes(range(256)) * 128

# ASCII8: a bank value with the enable bit (= num_pages = 2) set selects SRAM.
_SRAM_BANK = 2


# ---------------------------------------------------------------------------
# Ascii8Sram2Mapper
# ---------------------------------------------------------------------------

class TestAscii8Sram2:
    def test_rom_read_page0(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        assert m.read(0x4000) == _ROM_16K[0]

    def test_rom_read_page1(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x01)  # window 0 = ROM page 1
        assert m.read(0x4000) == _ROM_16K[8192]

    def test_sram_read_when_enable_bit_set(self):
        # Window 2 (0x8000) is in _SRAM_PAGES; enable bit (= num_pages = 2) set.
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.sram[0x100] = 0xAB
        m.write(0x7000, _SRAM_BANK)  # window 2 bank reg, enable bit set → SRAM
        assert m.read(0x8000 + 0x100) == 0xAB

    def test_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        m.write(0x8000 + 0x200, 0x55)
        assert m.sram[0x200] == 0x55

    def test_sram_2kb_wrap(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        m.write(0x8800, 0x77)  # offset 0x800 & 0x7FF = 0x000
        assert m.sram[0x000] == 0x77

    def test_windows_0_and_1_never_map_sram(self):
        # _SRAM_PAGES (0x30) only allows windows 2 (0x8000) and 3 (0xA000).
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.sram[0x00] = 0xCC
        m.write(0x6000, _SRAM_BANK)  # window 0 bank reg, enable bit set
        m.write(0x6800, _SRAM_BANK)  # window 1 bank reg, enable bit set
        # Window 0 body reads ROM (out-of-range page → 0xFF), not SRAM.
        assert m.read(0x4000) != 0xCC
        # Window 0 body write does not reach SRAM.
        m.write(0x4100, 0xEE)
        assert m.sram[0x100] == 0x00

    def test_bank_reg_write_never_writes_sram(self):
        # Writes to 0x6000-0x7FFF always update bank registers, never SRAM.
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        original_sram_0 = m.sram[0]
        m.write(0x6000, 0x05)  # bank reg write for window 0 — not a SRAM write
        assert m.sram[0] == original_sram_0
        assert m._banks[0] == 0x05

    def test_window2_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        m.write(0x8000 + 0x50, 0x42)
        assert m.sram[0x50] == 0x42

    def test_window3_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7800, _SRAM_BANK)  # window 3 = SRAM
        m.write(0xA000 + 0x10, 0x99)
        assert m.sram[0x10] == 0x99

    def test_large_rom_sram_selected_by_enable_bit(self):
        # 256 KB = 32 pages of 8 KB; enable bit = 32 (0x20). Window 2 (0x8000).
        rom_256k = bytes(32 * 8192)
        m = Ascii8Sram2Mapper(rom=rom_256k)
        m.sram[0] = 0xBB
        m.write(0x7000, 31)   # 31 & 0x20 == 0 → ROM page 31
        assert m.read(0x8000) == 0x00   # ROM byte (zeroed ROM)
        m.write(0x7000, 32)   # 32 & 0x20 == 0x20 → SRAM
        assert m.read(0x8000) == 0xBB  # SRAM

    def test_mapper_trace_called_on_bank_write(self):
        # Verify _trace_bank path: after write, _banks is updated (no crash)
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x01)
        assert m._banks[0] == 0x01


# ---------------------------------------------------------------------------
# Ascii8Sram8Mapper
# ---------------------------------------------------------------------------

class TestAscii8Sram8:
    def test_sram_size(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        assert len(m.sram) == 8192

    def test_sram_write_high_offset(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM (0x8000-0x9FFF)
        m.write(0x9FFF, 0xCC)        # offset 0x1FFF
        assert m.sram[0x1FFF] == 0xCC

    def test_sram_write_low_offset(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        m.write(0x8000, 0xDD)
        assert m.sram[0x0000] == 0xDD

    def test_sram_shared_across_windows(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        m.sram[0] = 0xAB
        m.write(0x7800, _SRAM_BANK)  # window 3 = SRAM
        assert m.read(0xA000) == 0xAB  # reads same physical SRAM

    def test_rom_page_below_enable_bit_is_not_sram(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x6000, 1)   # 1 & 2 == 0 → ROM page 1
        assert m.read(0x4000) == _ROM_16K[8192]


# ---------------------------------------------------------------------------
# Ascii16Sram2Mapper
# ---------------------------------------------------------------------------

class TestAscii16Sram2:
    def test_rom_read_window0(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        assert m.read(0x4000) == _ROM_32K[0]

    def test_window1_sram_read(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.sram[0x100] = 0x42
        m.write(0x7000, 0x10)  # bank == 0x10 → SRAM
        assert m.read(0x8100) == 0x42

    def test_window1_sram_write(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x10)
        m.write(0x8200, 0x99)
        assert m.sram[0x200] == 0x99

    def test_sram_2kb_wrap(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x10)
        m.write(0x8800, 0x11)  # offset 0x800 & 0x7FF = 0x000
        assert m.sram[0x000] == 0x11

    def test_window1_non_0x10_value_reads_rom(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x01)  # not 0x10 → ROM page 1
        assert m.read(0x8000) == _ROM_32K[16384]

    def test_window0_never_sram(self):
        # Window 0 cannot be SRAM regardless of bank value
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x6000, _SRAM_BANK)  # high bank for window 0 — still ROM
        m.write(0x4100, 0xBB)        # write to window 0 body — not SRAM
        assert m.sram[0x100] == 0x00

    def test_window1_rom_read_when_below_num_pages(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x01)  # page 1 < 2 → ROM
        assert m.read(0x8000) == _ROM_32K[16384]

    def test_sram_selected_only_on_exact_0x10(self):
        rom_256k = bytes(16 * 16384)  # 256KB = 16 pages of 16KB
        m = Ascii16Sram2Mapper(rom=rom_256k)
        m.sram[0] = 0xAB
        m.write(0x7000, 0x0F)   # not 0x10 → ROM page 15 (zeroed)
        assert m.read(0x8000) == 0x00
        m.write(0x7000, 0x10)   # exactly 0x10 → SRAM
        assert m.read(0x8000) == 0xAB


# ---------------------------------------------------------------------------
# Ascii16Sram8Mapper
# ---------------------------------------------------------------------------

class TestAscii16Sram8:
    def test_sram_size(self):
        m = Ascii16Sram8Mapper(rom=_ROM_32K)
        assert len(m.sram) == 8192

    def test_sram_write_high_offset(self):
        m = Ascii16Sram8Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x10)
        m.write(0x8000 + 0x1FFF, 0xBB)
        assert m.sram[0x1FFF] == 0xBB


# ---------------------------------------------------------------------------
# Constructor parameter: sram argument
# ---------------------------------------------------------------------------

class TestSramConstructorParam:
    def test_none_gives_zeroed_sram_ascii8sram2(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        assert m.sram == bytearray(2048)

    def test_none_gives_zeroed_sram_ascii8sram8(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        assert m.sram == bytearray(8192)

    def test_none_gives_zeroed_sram_ascii16sram2(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        assert m.sram == bytearray(2048)

    def test_none_gives_zeroed_sram_ascii16sram8(self):
        m = Ascii16Sram8Mapper(rom=_ROM_32K)
        assert m.sram == bytearray(8192)

    def test_preloaded_bytes_used(self):
        sram_data = bytearray(b'\xAB' * 2048)
        m = Ascii8Sram2Mapper(rom=_ROM_16K, sram=sram_data)
        m.write(0x7000, _SRAM_BANK)  # window 2 = SRAM
        assert m.read(0x8000) == 0xAB

    def test_wrong_size_gives_zeroed_sram(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K, sram=bytearray(100))
        assert len(m.sram) == 8192
        assert m.sram == bytearray(8192)

    def test_wrong_size_ascii16(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K, sram=bytearray(5))
        assert len(m.sram) == 2048


# ---------------------------------------------------------------------------
# save_sram
# ---------------------------------------------------------------------------

class TestSaveSram:
    def test_save_writes_bytes(self, tmp_path: Path):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.sram[0] = 0x42
        m.sram[10] = 0xFF
        p = tmp_path / "test.sram"
        m.save_sram(p)
        data = p.read_bytes()
        assert len(data) == 2048
        assert data[0] == 0x42
        assert data[10] == 0xFF

    def test_save_sram8(self, tmp_path: Path):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.sram[0x1FFF] = 0x99
        p = tmp_path / "test.sram"
        m.save_sram(p)
        data = p.read_bytes()
        assert len(data) == 8192
        assert data[0x1FFF] == 0x99

    def test_save_ascii16sram2(self, tmp_path: Path):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.sram[0x7FF] = 0x55
        p = tmp_path / "test.sram"
        m.save_sram(p)
        data = p.read_bytes()
        assert len(data) == 2048
        assert data[0x7FF] == 0x55


# ---------------------------------------------------------------------------
# machine_loader SRAM integration
# ---------------------------------------------------------------------------

class TestMachineLoaderSramIntegration:
    def test_sram_loaded_from_saves_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import hashlib

        from msx.machine_loader import _SRAM_SIZES
        from tests.factories import make_machine

        rom = bytes(64 * 1024)
        cartridge = bytes(range(256)) * 32  # 8KB cart
        sha1 = hashlib.sha1(cartridge).hexdigest()
        expected_size = _SRAM_SIZES["ASCII8SRAM2"]

        sram_dir = tmp_path / "saves" / "sram"
        sram_dir.mkdir(parents=True)
        sram_file = sram_dir / f"{sha1}.sram"
        sram_file.write_bytes(bytes([0xAB] * expected_size))

        monkeypatch.chdir(tmp_path)
        machine = make_machine(rom=rom, cartridge=cartridge, mapper="ASCII8SRAM2")
        assert machine.sram_save_path is not None
        assert machine.sram_save_path.name == f"{sha1}.sram"
        assert machine.memory._mapper.sram[0] == 0xAB

    def test_no_sram_file_gives_zeroed_sram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from tests.factories import make_machine

        rom = bytes(64 * 1024)
        cartridge = bytes(range(256)) * 32
        monkeypatch.chdir(tmp_path)
        machine = make_machine(rom=rom, cartridge=cartridge, mapper="ASCII8SRAM2")
        assert machine.memory._mapper.sram == bytearray(2048)


# ---------------------------------------------------------------------------
# snapshot() / restore() round-trip
# ---------------------------------------------------------------------------

class TestSnapshotRestore:
    def test_ascii8sram_roundtrips_banks_and_sram(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)   # window 2 = SRAM
        m.write(0x8100, 0x5A)         # SRAM byte
        m.write(0x6000, 0x01)         # window 0 bank
        snap = m.snapshot()

        m2 = Ascii8Sram2Mapper(rom=_ROM_16K)
        m2.restore(snap)
        assert m2._banks == m._banks
        assert m2.read(0x8100) == 0x5A

    def test_ascii16sram_roundtrips(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x10)         # window 1 = SRAM
        m.write(0x8200, 0x3C)
        snap = m.snapshot()

        m2 = Ascii16Sram2Mapper(rom=_ROM_32K)
        m2.restore(snap)
        assert m2._banks == m._banks
        assert m2.read(0x8200) == 0x3C

    def test_snapshot_survives_mapper_mutation(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x7000, _SRAM_BANK)
        m.write(0x8000, 0x11)
        snap = m.snapshot()
        m.write(0x8000, 0x22)         # mutate after snapshot
        m.restore(snap)
        assert m.read(0x8000) == 0x11
