"""machine-core "Media are exchanged as bytes with dirty flags"."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.boundary import DiskMedium
from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793
from msx.machine import Machine
from msx.mapper import Ascii8Sram2Mapper
from tests.factories import make_machine

_ROM = bytes(32 * 1024)
_CART = bytes(range(256)) * 32  # 8 KB
_2DD = bytes(737280)


def _sram_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Machine:
    monkeypatch.chdir(tmp_path)
    return make_machine(rom=_ROM, cartridge=_CART, mapper="ASCII8SRAM2")


def _fdc_machine(drives: int = 2) -> Machine:
    m = make_machine(rom=_ROM)
    m.fdc = SonyPhilipsInterface(
        WD2793(), [DiskDrive() for _ in range(drives)], disk_rom=bytes(16384)
    )
    return m


def test_guest_write_marks_the_medium_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    m = _sram_machine(monkeypatch, tmp_path)
    mapper = m.memory._mapper
    assert isinstance(mapper, Ascii8Sram2Mapper)
    assert m.dirty_media() == []
    mapper.write(0x7000, mapper._sram_enable_bit())  # window 2 = SRAM
    mapper.write(0x8000, 0x42)
    assert m.dirty_media() == ["slot1.sram"]
    assert m.read_media("slot1.sram")[0] == 0x42
    assert m.dirty_media() == ["slot1.sram"]  # reading does not clear it
    m.mark_persisted("slot1.sram")
    assert m.dirty_media() == []


def test_write_protected_disk_is_never_dirty() -> None:
    m = _fdc_machine()
    m.mount_disk(0, DiskMedium(_2DD, write_protected=True))
    image = m.fdc.drives[0].image
    assert image is not None
    with pytest.raises(PermissionError):
        image.write_sector(0, b"\x55" * SECTOR_SIZE)
    assert m.dirty_media() == []


def test_disk_write_is_listed_by_drive_id() -> None:
    m = _fdc_machine()
    m.mount_disk(1, DiskMedium(_2DD))
    m.fdc.drives[1].image.write_sector(3, b"\x77" * SECTOR_SIZE)
    assert m.dirty_media() == ["drive_b"]
    assert m.read_media("drive_b")[3 * SECTOR_SIZE] == 0x77


def test_mount_disk_returns_the_dirty_outgoing_image() -> None:
    m = _fdc_machine()
    m.mount_disk(0, DiskMedium(_2DD))
    m.fdc.drives[0].image.write_sector(0, b"\xAB" * SECTOR_SIZE)
    outgoing = m.mount_disk(0, DiskMedium(_2DD))
    assert outgoing is not None and outgoing[:SECTOR_SIZE] == b"\xAB" * SECTOR_SIZE
    assert m.mount_disk(0, None) is None  # the new image is clean
    assert m.fdc.drives[0].image is None


def test_mount_disk_rejects_a_bad_size_or_drive_without_change() -> None:
    m = _fdc_machine(drives=1)
    m.mount_disk(0, DiskMedium(_2DD))
    m.fdc.drives[0].disk_changed = False  # as if the guest saw the change
    before = m.fdc.drives[0].image
    with pytest.raises(ValueError):
        m.mount_disk(0, DiskMedium(bytes(100)))
    with pytest.raises(ValueError):
        m.mount_disk(1, DiskMedium(_2DD))
    assert m.fdc.drives[0].image is before
    assert m.fdc.drives[0].disk_changed is False


def test_disk_queries_report_the_mounted_image() -> None:
    m = _fdc_machine(drives=1)
    assert (m.disk_mounted(0), m.disk_path(0)) == (False, None)
    m.mount_disk(0, DiskMedium(_2DD, path=Path("game.dsk")))
    assert (m.disk_mounted(0), m.disk_path(0)) == (True, Path("game.dsk"))
    m.mount_disk(0, DiskMedium(_2DD))
    assert (m.disk_mounted(0), m.disk_path(0)) == (True, None)
    assert (m.disk_mounted(1), m.disk_path(1)) == (False, None)
