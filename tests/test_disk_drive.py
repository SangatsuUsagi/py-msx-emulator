"""Tests for DiskDrive's own mount/state/format_track behaviour, in isolation
from any chip core (WD2793/TC8566AF) or connection-style interface.

tests/test_disk_image.py already covers lsn() arithmetic, side-select-changes-
LSN, empty-drive read/write rejection, write-protected write rejection, and
out-of-geometry read rejection directly against DiskDrive -- this file covers
what that one and the chip-core/interface test files leave untested: mount()/
unmount() as DiskDrive's own methods (elsewhere only reached via
SonyPhilipsInterface.mount()/swap() or TC8566AF's own interface), write_sector
out-of-range rejection (only read_sector's out-of-range case was covered
before), and format_track's no-disk/write-protected rejection and full-track
(not just single-sector) blanking scope.
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage

_2DD_BYTES = 737280  # 720 KB: 80 tracks * 2 sides * 9 sectors * 512


def _make_dsk(path: Path, size: int = _2DD_BYTES, fill: int = 0x00) -> Path:
    path.write_bytes(bytes([fill]) * size)
    return path


# --- mount / unmount / has_disk / write_protected, in isolation ----------

def test_new_drive_has_no_disk() -> None:
    drive = DiskDrive()
    assert drive.has_disk is False
    assert drive.write_protected is False
    assert drive.disk_changed is False


def test_mount_sets_has_disk_and_write_protected(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    drive = DiskDrive()
    drive.mount(img)
    assert drive.has_disk is True
    assert drive.write_protected is True


def test_unmount_clears_disk(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk")))
    drive.unmount()
    assert drive.has_disk is False
    assert drive.read_sector(0, 0, 1) is None
    assert drive.write_sector(0, 0, 1, b"\x00" * SECTOR_SIZE) is False


def test_mount_does_not_flush_outgoing_image_or_flag_disk_changed(tmp_path: Path) -> None:
    # DiskMounted/mount() has no side effect beyond the field write itself --
    # no flush of the previous image, no disk_changed -- unlike a connection
    # style's own SwapDisk, which adds that behaviour one layer up.
    p_a = tmp_path / "a.dsk"
    img_a = DskDiskImage(_make_dsk(p_a))
    img_a.write_sector(0, b"\xAB" * SECTOR_SIZE)  # dirty, never flushed

    drive = DiskDrive(img_a)
    drive.mount(DskDiskImage(_make_dsk(tmp_path / "b.dsk")))

    assert DskDiskImage(p_a).read_sector(0) != b"\xAB" * SECTOR_SIZE  # not flushed
    assert drive.disk_changed is False


# --- write_sector out-of-range ---------------------------------------------

def test_write_sector_out_of_range_returns_false(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk")))
    # sector 99 does not exist on a 9-sector track -> LSN beyond the disk end
    assert drive.write_sector(79, 1, 99, b"\x00" * SECTOR_SIZE) is False


# --- format_track -----------------------------------------------------------

def test_format_track_no_disk_returns_false() -> None:
    drive = DiskDrive()
    assert drive.format_track(0, 0) is False


def test_format_track_write_protected_returns_false(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    drive = DiskDrive(img)
    assert drive.format_track(0, 0) is False


def test_format_track_blanks_whole_track_leaves_others_untouched(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk", fill=0x00)))
    assert drive.format_track(2, 1) is True

    # Every sector of track 2 side 1 (9 sectors/track on 2DD) is blanked.
    for sector in range(1, 10):
        assert drive.read_sector(2, 1, sector) == b"\xe5" * SECTOR_SIZE

    # A different side of the same track, and a different track entirely,
    # are left untouched.
    assert drive.read_sector(2, 0, 1) == b"\x00" * SECTOR_SIZE
    assert drive.read_sector(1, 1, 1) == b"\x00" * SECTOR_SIZE


def test_format_track_custom_fill_byte(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk", fill=0x00)))
    assert drive.format_track(0, 0, fill=0x33) is True
    assert drive.read_sector(0, 0, 1) == b"\x33" * SECTOR_SIZE
