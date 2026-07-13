"""Tests for DskDiskImage and DiskDrive (fixed 2DD geometry)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage

_2DD_BYTES = 737280  # 720 KB: 80 tracks * 2 sides * 9 sectors * 512


def _make_dsk(path: Path, size: int = _2DD_BYTES, fill: int = 0x00) -> Path:
    path.write_bytes(bytes([fill]) * size)
    return path


# --- DskDiskImage --------------------------------------------------------

def test_num_sectors_from_size(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"))
    assert img.num_sectors == 1440


def test_read_sector_returns_file_slice(tmp_path: Path) -> None:
    p = tmp_path / "d.dsk"
    data = bytearray(_2DD_BYTES)
    data[5 * SECTOR_SIZE:5 * SECTOR_SIZE + 3] = b"\xde\xad\xbe"
    p.write_bytes(data)
    img = DskDiskImage(p)
    assert img.read_sector(5)[:3] == b"\xde\xad\xbe"


def test_write_then_read_back(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"))
    payload = bytes(range(256)) * 2
    img.write_sector(10, payload)
    assert img.read_sector(10) == payload


def test_flush_persists_to_file(tmp_path: Path) -> None:
    p = _make_dsk(tmp_path / "d.dsk")
    img = DskDiskImage(p)
    payload = b"\x55" * SECTOR_SIZE
    img.write_sector(0, payload)
    img.flush()
    assert p.read_bytes()[:SECTOR_SIZE] == payload


def test_read_out_of_range_raises(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"))
    with pytest.raises(IndexError):
        img.read_sector(img.num_sectors)


def test_bad_size_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.dsk"
    p.write_bytes(b"\x00" * 100)  # not a multiple of 512
    with pytest.raises(ValueError):
        DskDiskImage(p)


def test_write_protected_rejects_write(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    with pytest.raises(PermissionError):
        img.write_sector(0, b"\x00" * SECTOR_SIZE)


def test_readonly_file_is_write_protected(tmp_path: Path) -> None:
    p = _make_dsk(tmp_path / "ro.dsk")
    os.chmod(p, 0o444)
    try:
        img = DskDiskImage(p)
        assert img.write_protected is True
    finally:
        os.chmod(p, 0o644)


# --- DiskDrive geometry --------------------------------------------------

def test_lsn_track_side_sector(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk")))
    assert drive.lsn(1, 0, 1) == 18   # track 1 begins after 2 sides x 9 of track 0
    assert drive.lsn(0, 1, 1) == 9    # side 1 of track 0
    assert drive.lsn(0, 0, 1) == 0


def test_drive_read_sector_maps_through_geometry(tmp_path: Path) -> None:
    p = tmp_path / "d.dsk"
    data = bytearray(_2DD_BYTES)
    data[18 * SECTOR_SIZE] = 0x7E  # LSN 18 == track 1, side 0, sector 1
    p.write_bytes(data)
    drive = DiskDrive(DskDiskImage(p))
    sector = drive.read_sector(1, 0, 1)
    assert sector is not None and sector[0] == 0x7E


def test_empty_drive_reports_no_disk() -> None:
    drive = DiskDrive()
    assert drive.has_disk is False
    assert drive.read_sector(0, 0, 1) is None
    assert drive.write_sector(0, 0, 1, b"\x00" * SECTOR_SIZE) is False


def test_write_protected_drive_rejects_write(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    drive = DiskDrive(img)
    assert drive.write_protected is True
    assert drive.write_sector(0, 0, 1, b"\x00" * SECTOR_SIZE) is False


def test_out_of_geometry_sector_returns_none(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk")))
    # sector 99 does not exist on a 9-sector track -> LSN beyond the disk end
    assert drive.read_sector(79, 1, 99) is None


def test_format_track_blanks_sectors_to_e5(tmp_path: Path) -> None:
    drive = DiskDrive(DskDiskImage(_make_dsk(tmp_path / "d.dsk", fill=0x00)))
    assert drive.format_track(2, 1) is True
    sector = drive.read_sector(2, 1, 1)
    assert sector == b"\xe5" * SECTOR_SIZE
