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


# --- BPB geometry detection ----------------------------------------------

def _bpb_image(path: Path, *, total: int, spt: int, heads: int,
               bytes_per_sector: int = 512) -> Path:
    """Create a *.dsk whose LSN 0 holds a FAT12 BPB with the given geometry."""
    data = bytearray(total * 512)
    data[0x00:0x03] = b"\xeb\xfe\x90"          # jump + nop
    data[0x0B] = bytes_per_sector & 0xFF
    data[0x0C] = (bytes_per_sector >> 8) & 0xFF
    data[0x13] = total & 0xFF
    data[0x14] = (total >> 8) & 0xFF
    data[0x18] = spt & 0xFF
    data[0x19] = (spt >> 8) & 0xFF
    data[0x1A] = heads & 0xFF
    data[0x1B] = (heads >> 8) & 0xFF
    path.write_bytes(data)
    return path


def test_bpb_720k_double_sided(tmp_path: Path) -> None:
    img = DskDiskImage(_bpb_image(tmp_path / "2dd.dsk", total=1440, spt=9, heads=2))
    assert img.sectors_per_track == 9
    assert img.sides == 2


def test_bpb_360k_single_sided(tmp_path: Path) -> None:
    img = DskDiskImage(_bpb_image(tmp_path / "1dd.dsk", total=720, spt=9, heads=1))
    assert img.sectors_per_track == 9
    assert img.sides == 1


def test_single_sided_geometry_changes_lsn(tmp_path: Path) -> None:
    """With sides=1 the second track begins after one side, not two."""
    drive = DiskDrive(DskDiskImage(_bpb_image(tmp_path / "1dd.dsk", total=720,
                                              spt=9, heads=1)))
    assert drive.lsn(1, 0, 1) == 9   # single-sided: track 1 after 1 side x 9
    assert drive.lsn(0, 0, 1) == 0


def test_blank_image_falls_back_to_2dd(tmp_path: Path) -> None:
    img = DskDiskImage(_make_dsk(tmp_path / "blank.dsk", fill=0xE5))
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_invalid_bpb_falls_back_to_2dd(tmp_path: Path) -> None:
    # Wrong bytes-per-sector (not 512) -> not a recognised BPB.
    img = DskDiskImage(_bpb_image(tmp_path / "bad.dsk", total=1440, spt=8,
                                  heads=1, bytes_per_sector=256))
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_bpb_total_mismatch_falls_back(tmp_path: Path) -> None:
    # BPB total (700) disagrees with the actual file size (1440 sectors).
    p = tmp_path / "mismatch.dsk"
    _make_dsk(p)  # 1440-sector file
    data = bytearray(p.read_bytes())
    data[0x0B:0x0D] = b"\x00\x02"  # 512
    data[0x13:0x15] = (700).to_bytes(2, "little")
    data[0x18:0x1A] = (9).to_bytes(2, "little")
    data[0x1A:0x1C] = (1).to_bytes(2, "little")
    p.write_bytes(data)
    img = DskDiskImage(p)
    assert (img.sectors_per_track, img.sides) == (9, 2)
