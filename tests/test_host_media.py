"""host/media.py: files <-> the machine's media bytes."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from host.machine_config import sram_paths
from host.media import persist_dirty_media, read_disk_medium, swap_disk
from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.mapper import Ascii8Sram2Mapper
from tests.factories import make_config, make_config_msx2, make_machine

_ROM = bytes(32 * 1024)
_CART = bytes(range(256)) * 32
_2DD = bytes(737280)


def _fdc_machine() -> Machine:
    m = make_machine(rom=_ROM)
    m.fdc = SonyPhilipsInterface(WD2793(), [DiskDrive()], disk_rom=bytes(16384))
    return m


def _dsk(path: Path, fill: int = 0) -> Path:
    path.write_bytes(bytes([fill]) * len(_2DD))
    return path


# --- cart-sram-mapper "machine_loader SRAM load/save integration" ------------

def test_dirty_cartridge_sram_is_written_to_its_sha1_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config(rom=_ROM, cartridge=_CART, mapper="ASCII8SRAM2")
    m = build_machine(config)
    mapper = m.memory._mapper
    assert isinstance(mapper, Ascii8Sram2Mapper)
    mapper.write(0x7000, mapper._sram_enable_bit())
    mapper.write(0x8000, 0x5A)
    persist_dirty_media(m, sram_paths(config))
    assert sram_paths(config)["slot1.sram"].read_bytes()[0] == 0x5A
    assert m.dirty_media() == []


def test_unchanged_sram_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config(rom=_ROM, cartridge=_CART, mapper="ASCII8SRAM2")
    persist_dirty_media(build_machine(config), sram_paths(config))
    assert not (tmp_path / "saves").exists()


def test_non_sram_mapper_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config(rom=_ROM, cartridge=_CART, mapper="ASCII8")
    m = build_machine(config)
    m.memory._mapper.write(0x6000, 1)
    persist_dirty_media(m, sram_paths(config))
    assert not (tmp_path / "saves").exists()


# --- io-rtc "RTC sram file created on first shutdown" ------------------------

def test_rtc_file_is_created_on_first_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config_msx2(bytes(32 * 1024), bytes(16 * 1024))
    m = build_machine(config)
    assert m.rtc is not None
    m.rtc.write_port(0xB4, 0x0D)  # mode register
    m.rtc.write_port(0xB5, 0x02)  # select block 2
    m.rtc.write_port(0xB4, 0x00)
    m.rtc.write_port(0xB5, 0x0A)  # CMOS write
    persist_dirty_media(m, sram_paths(config))
    data = (tmp_path / sram_paths(config)["rtc.cmos"]).read_bytes()
    assert len(data) == 26 and data[0] == 0x0A


# --- fdc-interface swap through the host --------------------------------------

def test_outgoing_image_is_flushed_before_unmount(tmp_path: Path) -> None:
    m = _fdc_machine()
    a = _dsk(tmp_path / "a.dsk")
    swap_disk(m, 0, a)
    m.fdc.drives[0].image.write_sector(5, b"\xAB" * SECTOR_SIZE)
    swap_disk(m, 0, _dsk(tmp_path / "b.dsk"))
    assert a.read_bytes()[5 * SECTOR_SIZE:6 * SECTOR_SIZE] == b"\xAB" * SECTOR_SIZE


def test_eject_flushes_the_outgoing_image(tmp_path: Path) -> None:
    m = _fdc_machine()
    a = _dsk(tmp_path / "a.dsk")
    swap_disk(m, 0, a)
    m.fdc.drives[0].image.write_sector(0, b"\x11" * SECTOR_SIZE)
    swap_disk(m, 0, None)
    assert m.fdc.drives[0].image is None
    assert a.read_bytes()[:SECTOR_SIZE] == b"\x11" * SECTOR_SIZE


def test_a_failed_write_back_keeps_the_current_disk(tmp_path: Path) -> None:
    # fdc-interface "A failed write-back keeps the current disk".
    m = _fdc_machine()
    a = _dsk(tmp_path / "a.dsk")
    swap_disk(m, 0, a)
    before = m.fdc.drives[0].image
    before.write_sector(5, b"\xAB" * SECTOR_SIZE)
    a.unlink()
    a.mkdir()                                   # the file can no longer be written
    with pytest.raises(OSError):
        swap_disk(m, 0, _dsk(tmp_path / "b.dsk"))
    assert m.fdc.drives[0].image is before
    assert m.disk_path(0) == a
    assert "drive_a" in m.dirty_media()


def test_nonexistent_file_keeps_the_current_disk(tmp_path: Path) -> None:
    m = _fdc_machine()
    swap_disk(m, 0, _dsk(tmp_path / "a.dsk"))
    before = m.fdc.drives[0].image
    with pytest.raises(OSError):
        swap_disk(m, 0, tmp_path / "missing.dsk")
    assert m.fdc.drives[0].image is before


def test_invalid_image_size_keeps_the_current_disk(tmp_path: Path) -> None:
    m = _fdc_machine()
    swap_disk(m, 0, _dsk(tmp_path / "a.dsk"))
    before = m.fdc.drives[0].image
    bad = tmp_path / "bad.dsk"
    bad.write_bytes(bytes(100))
    with pytest.raises(ValueError):
        swap_disk(m, 0, bad)
    assert m.fdc.drives[0].image is before


def test_swap_of_a_missing_drive_keeps_everything(tmp_path: Path) -> None:
    m = _fdc_machine()
    with pytest.raises(ValueError):
        swap_disk(m, 1, _dsk(tmp_path / "a.dsk"))
    assert m.fdc.drives[0].image is None


def test_readonly_file_is_read_write_protected(tmp_path: Path) -> None:
    p = _dsk(tmp_path / "ro.dsk")
    os.chmod(p, 0o444)
    try:
        assert read_disk_medium(p).write_protected is True
    finally:
        os.chmod(p, 0o644)


def test_writable_file_is_read_writable_and_named(tmp_path: Path) -> None:
    p = _dsk(tmp_path / "rw.dsk")
    medium = read_disk_medium(p)
    assert (medium.write_protected, medium.path, medium.data) == (False, p, p.read_bytes())
