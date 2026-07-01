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

_ROM_16K = bytes(range(256)) * 64   # 16 KB = 2 pages of 8 KB
_ROM_32K = bytes(range(256)) * 128  # 32 KB = 2 pages of 16 KB


# ---------------------------------------------------------------------------
# Ascii8Sram2Mapper
# ---------------------------------------------------------------------------

class TestAscii8Sram2:
    def test_rom_read_when_bank_not_sram(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x01)  # window 0 = ROM page 1
        assert m.read(0x4000) == _ROM_16K[8192]

    def test_sram_read_when_bank_is_sram(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.sram[0x100] = 0xAB
        m.write(0x6000, 0x08)  # window 0 = SRAM (bit 3 set)
        assert m.read(0x4000 + 0x100) == 0xAB

    def test_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x08)  # window 0 = SRAM
        m.write(0x4000 + 0x200, 0x55)
        assert m.sram[0x200] == 0x55

    def test_sram_2kb_wrap(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x08)
        m.write(0x4800, 0x77)  # offset 0x800 & 0x7FF = 0x000
        assert m.sram[0x000] == 0x77

    def test_window1_read_sram(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.sram[0x00] = 0xCC
        m.write(0x6800, 0x08)  # window 1 = SRAM
        assert m.read(0x6000) == 0xCC

    def test_window1_write_goes_to_bank_reg_not_sram(self):
        # Writes to 0x6000-0x7FFF always update bank registers
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6800, 0x08)  # window 1 = SRAM
        original_sram_0 = m.sram[0]
        m.write(0x6000, 0x05)  # this is a bank reg write for window 0, not SRAM write
        assert m.sram[0] == original_sram_0
        assert m._banks[0] == 0x05  # bank reg 0 updated

    def test_window2_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7000, 0x08)  # window 2 = SRAM
        m.write(0x8000 + 0x50, 0x42)
        assert m.sram[0x50] == 0x42

    def test_window3_sram_write(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x7800, 0x08)  # window 3 = SRAM
        m.write(0xA000 + 0x10, 0x99)
        assert m.sram[0x10] == 0x99

    def test_returns_0xff_for_out_of_range_rom(self):
        m = Ascii8Sram2Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x05)  # page 5 doesn't exist (only 2 pages)
        assert m.read(0x4000) == 0xFF


# ---------------------------------------------------------------------------
# Ascii8Sram8Mapper
# ---------------------------------------------------------------------------

class TestAscii8Sram8:
    def test_sram_size(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        assert len(m.sram) == 8192

    def test_sram_write_high_offset(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x08)  # window 0 = SRAM (0x4000-0x5FFF)
        m.write(0x5FFF, 0xCC)  # offset 0x1FFF
        assert m.sram[0x1FFF] == 0xCC

    def test_sram_write_low_offset(self):
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x08)
        m.write(0x4000, 0xDD)
        assert m.sram[0x0000] == 0xDD

    def test_sram_shared_across_windows(self):
        # Same physical SRAM accessible through multiple windows if all set to SRAM
        m = Ascii8Sram8Mapper(rom=_ROM_16K)
        m.write(0x6000, 0x08)  # window 0 = SRAM
        m.sram[0] = 0xAB
        m.write(0x6800, 0x08)  # window 1 = SRAM (reads return SRAM[0])
        assert m.read(0x6000) == 0xAB


# ---------------------------------------------------------------------------
# Ascii16Sram2Mapper
# ---------------------------------------------------------------------------

class TestAscii16Sram2:
    def test_rom_read_window0_unaffected_by_sram_bit(self):
        # Window 0 is always ROM; bit 4 in window 0 register is treated as page bit
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x6000, 0x00)
        assert m.read(0x4000) == _ROM_32K[0]

    def test_window1_sram_read(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.sram[0x100] = 0x42
        m.write(0x7000, 0x10)  # window 1 = SRAM (bit 4 set)
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

    def test_window0_never_sram_even_with_bit4(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x6000, 0x10)  # bit 4 set for window 0, but window 0 can't be SRAM
        # read should return ROM (page index = 0x10 & 0x0F = 0, ROM page 0)
        assert m.read(0x4000) == _ROM_32K[0]
        # write to window 0 body should NOT go to SRAM
        m.write(0x4100, 0xBB)
        assert m.sram[0x100] == 0x00

    def test_window1_rom_read_when_not_sram(self):
        m = Ascii16Sram2Mapper(rom=_ROM_32K)
        m.write(0x7000, 0x01)  # window 1 = ROM page 1
        assert m.read(0x8000) == _ROM_32K[16384]


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
        m.write(0x6000, 0x08)
        assert m.read(0x4000) == 0xAB

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
        from tests.factories import make_machine
        from msx.machine_loader import _SRAM_SIZES

        rom = bytes(64 * 1024)  # 64KB all-zeros BIOS
        cartridge = bytes(range(256)) * 32  # 8KB cart (deterministic content)
        sha1 = hashlib.sha1(cartridge).hexdigest()
        expected_size = _SRAM_SIZES["ASCII8SRAM2"]

        saves = tmp_path / "saves"
        saves.mkdir()
        sram_file = saves / f"{sha1}.sram"
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
