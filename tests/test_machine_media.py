"""machine-core "Media are exchanged as bytes with dirty flags"."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.boundary import (
    STATUS_BAD_IMAGE,
    STATUS_NO_DRIVE,
    STATUS_NO_MEDIUM,
    STATUS_OK,
    MediaId,
    drive_media_id,
    slot_sram_id,
    subslot_sram_id,
)
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
    assert m.dirty_media() == [MediaId.SLOT1_SRAM]
    assert m.read_media(MediaId.SLOT1_SRAM)[0] == 0x42
    assert m.dirty_media() == [MediaId.SLOT1_SRAM]  # reading does not clear it
    m.mark_persisted(MediaId.SLOT1_SRAM)
    assert m.dirty_media() == []


def test_write_protected_disk_is_never_dirty() -> None:
    m = _fdc_machine()
    m.mount_disk(0, _2DD, True)
    image = m.fdc.drives[0].image
    assert image is not None
    with pytest.raises(PermissionError):
        image.write_sector(0, b"\x55" * SECTOR_SIZE)
    assert m.dirty_media() == []


def test_disk_write_is_listed_by_drive_id() -> None:
    m = _fdc_machine()
    m.mount_disk(1, _2DD, False)
    m.fdc.drives[1].image.write_sector(3, b"\x77" * SECTOR_SIZE)
    assert m.dirty_media() == [MediaId.DRIVE_B]
    assert m.read_media(MediaId.DRIVE_B)[3 * SECTOR_SIZE] == 0x77


def test_a_missing_drive_returns_a_status() -> None:
    # machine-core "A missing drive returns a status".
    m = _fdc_machine(drives=1)
    m.mount_disk(0, _2DD, False)
    m.fdc.drives[0].disk_changed = False  # as if the guest saw the change
    before = m.fdc.drives[0].image
    assert m.mount_disk(1, _2DD, False) == STATUS_NO_DRIVE
    assert m.last_error_text() == b"no floppy drive 1 on this machine"
    assert m.fdc.drives[0].image is before
    assert m.fdc.drives[0].disk_changed is False


def test_a_bad_image_size_returns_a_status() -> None:
    # machine-core "A bad image size returns a status".
    m = _fdc_machine(drives=1)
    m.mount_disk(0, _2DD, False)
    m.fdc.drives[0].disk_changed = False
    before = m.fdc.drives[0].image
    for size in (1000, 0):
        assert m.mount_disk(0, bytes(size), False) == STATUS_BAD_IMAGE
        assert m.last_error_text() == (
            f"size {size} is not a positive multiple of 512".encode()
        )
    assert m.fdc.drives[0].image is before
    assert m.fdc.drives[0].disk_changed is False


def test_a_success_clears_the_message() -> None:
    # machine-core "A success clears the message".
    m = _fdc_machine(drives=1)
    assert m.mount_disk(0, bytes(1000), False) == STATUS_BAD_IMAGE
    assert m.last_error_text() != b""
    assert m.mount_disk(0, _2DD, False) == STATUS_OK
    assert m.last_error_text() == b""


def test_eject_disk_empties_the_drive() -> None:
    # machine-core "eject_disk empties the drive".
    m = _fdc_machine()
    m.mount_disk(0, _2DD, False)
    m.fdc.drives[0].image.write_sector(0, b"\xAB" * SECTOR_SIZE)
    assert m.eject_disk(0) == STATUS_OK
    assert m.fdc.drives[0].image is None
    assert not m.disk_mounted(0)


def test_eject_disk_on_a_missing_drive_returns_a_status() -> None:
    # machine-core "eject_disk on a missing drive returns a status".
    m = _fdc_machine(drives=1)
    m.mount_disk(0, _2DD, False)
    assert m.eject_disk(1) == STATUS_NO_DRIVE
    assert b"drive 1" in m.last_error_text()
    assert m.disk_mounted(0)


def test_disk_queries_report_the_mounted_image() -> None:
    m = _fdc_machine(drives=1)
    assert m.disk_mounted(0) is False
    m.mount_disk(0, _2DD, False)
    assert m.disk_mounted(0) is True
    assert m.disk_mounted(1) is False


def test_media_ids_are_integers() -> None:
    # machine-core "Media ids are integers".
    m = make_machine(rom=_ROM, cartridge=bytes(65536), mapper="ASCII8SRAM2")
    m.memory._mapper.sram_image.write(0, 0x42)
    ids = m.dirty_media()
    assert ids == [MediaId.SLOT1_SRAM]
    for media_id in ids:
        assert type(int(media_id)) is int and isinstance(media_id, int)
        assert MediaId(int(media_id)) is media_id
    assert drive_media_id(0) is MediaId.DRIVE_A
    assert drive_media_id(3) is MediaId.DRIVE_D
    assert slot_sram_id(1) is MediaId.SLOT1_SRAM
    assert slot_sram_id(2) is MediaId.SLOT2_SRAM
    assert subslot_sram_id(1, 0) is MediaId.SLOT1_SUB0_SRAM
    assert subslot_sram_id(2, 3) is MediaId.SLOT2_SUB3_SRAM
    # The values are fixed, in declaration order.
    assert [int(member) for member in MediaId] == list(range(16))


@pytest.mark.parametrize("make_id", [
    lambda: slot_sram_id(0), lambda: slot_sram_id(3),
    lambda: subslot_sram_id(0, 0), lambda: subslot_sram_id(3, 0),
    lambda: subslot_sram_id(1, -1), lambda: subslot_sram_id(1, 4),
    lambda: drive_media_id(-1), lambda: drive_media_id(4),
])
def test_an_id_outside_the_closed_set_raises(make_id) -> None:
    with pytest.raises(ValueError):
        make_id()


def test_an_id_outside_the_machines_media_is_rejected() -> None:
    # machine-core "An id outside the machine's media is rejected".
    m = make_machine(rom=_ROM, cartridge=bytes(65536), mapper="ASCII8SRAM2")
    before = m.dirty_media()
    assert m.read_media(MediaId.FMPAC_SRAM) == b""
    assert m.mark_persisted(MediaId.FMPAC_SRAM) == STATUS_NO_MEDIUM
    assert m.dirty_media() == before


def test_a_missing_medium_sets_the_message() -> None:
    # machine-core "A missing medium sets the message".
    m = make_machine(rom=_ROM)
    assert m.read_media(MediaId.FMPAC_SRAM) == b""
    assert m.last_error_text() == b"no medium FMPAC_SRAM on this machine"


def test_the_sram_table_is_built_once() -> None:
    m = make_machine(rom=_ROM, cartridge=bytes(65536), mapper="ASCII8SRAM2")
    assert m.sram_media() is m.sram_media()
    assert m.sram_media()[MediaId.SLOT1_SRAM] is m.memory._mapper.sram_medium()
