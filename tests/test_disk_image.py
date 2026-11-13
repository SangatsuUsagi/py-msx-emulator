"""Tests for DskDiskImage and DiskDrive."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from tests.disk_factories import _2DD_BYTES, dsk_image
from tests.disk_factories import make_dsk as _make_dsk

# --- DskDiskImage --------------------------------------------------------

def test_num_sectors_from_size(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk"))
    assert img.num_sectors == 1440


def test_read_sector_returns_file_slice(tmp_path: Path) -> None:
    p = tmp_path / "d.dsk"
    data = bytearray(_2DD_BYTES)
    data[5 * SECTOR_SIZE:5 * SECTOR_SIZE + 3] = b"\xde\xad\xbe"
    p.write_bytes(data)
    img = dsk_image(p)
    assert img.read_sector(5)[:3] == b"\xde\xad\xbe"


def test_write_then_read_back(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk"))
    payload = bytes(range(256)) * 2
    img.write_sector(10, payload)
    assert img.read_sector(10) == payload


def test_write_marks_dirty_and_data_holds_it(tmp_path: Path) -> None:
    p = _make_dsk(tmp_path / "d.dsk")
    img = dsk_image(p)
    payload = b"\x55" * SECTOR_SIZE
    img.write_sector(0, payload)
    assert img.dirty is True
    assert img.data()[:SECTOR_SIZE] == payload
    assert p.read_bytes()[:SECTOR_SIZE] != payload  # the image opens no file
    img.mark_persisted()
    assert img.dirty is False


def test_read_out_of_range_raises(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk"))
    with pytest.raises(IndexError):
        img.read_sector(img.num_sectors)


def test_bad_size_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.dsk"
    p.write_bytes(b"\x00" * 100)  # not a multiple of 512
    with pytest.raises(ValueError):
        DskDiskImage(p.read_bytes())


def test_write_protected_rejects_write(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    with pytest.raises(PermissionError):
        img.write_sector(0, b"\x00" * SECTOR_SIZE)


# --- DiskDrive geometry --------------------------------------------------

def test_lsn_track_side_sector(tmp_path: Path) -> None:
    drive = DiskDrive(dsk_image(_make_dsk(tmp_path / "d.dsk")))
    assert drive.lsn(1, 0, 1) == 18   # track 1 begins after 2 sides x 9 of track 0
    assert drive.lsn(0, 1, 1) == 9    # side 1 of track 0
    assert drive.lsn(0, 0, 1) == 0


def test_drive_read_sector_maps_through_geometry(tmp_path: Path) -> None:
    p = tmp_path / "d.dsk"
    data = bytearray(_2DD_BYTES)
    data[18 * SECTOR_SIZE] = 0x7E  # LSN 18 == track 1, side 0, sector 1
    p.write_bytes(data)
    drive = DiskDrive(dsk_image(p))
    sector = drive.read_sector(1, 0, 1)
    assert sector is not None and sector[0] == 0x7E


def test_empty_drive_reports_no_disk() -> None:
    drive = DiskDrive()
    assert drive.has_disk is False
    assert drive.read_sector(0, 0, 1) is None
    assert drive.write_sector(0, 0, 1, b"\x00" * SECTOR_SIZE) is False


def test_write_protected_drive_rejects_write(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk"), write_protected=True)
    drive = DiskDrive(img)
    assert drive.write_protected is True
    assert drive.write_sector(0, 0, 1, b"\x00" * SECTOR_SIZE) is False


def test_out_of_geometry_sector_returns_none(tmp_path: Path) -> None:
    drive = DiskDrive(dsk_image(_make_dsk(tmp_path / "d.dsk")))
    # sector 99 does not exist on a 9-sector track -> LSN beyond the disk end
    assert drive.read_sector(79, 1, 99) is None


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
    img = dsk_image(_bpb_image(tmp_path / "2dd.dsk", total=1440, spt=9, heads=2))
    assert img.sectors_per_track == 9
    assert img.sides == 2


def test_bpb_360k_single_sided(tmp_path: Path) -> None:
    img = dsk_image(_bpb_image(tmp_path / "1dd.dsk", total=720, spt=9, heads=1))
    assert img.sectors_per_track == 9
    assert img.sides == 1


def test_single_sided_geometry_changes_lsn(tmp_path: Path) -> None:
    """With sides=1 the second track begins after one side, not two."""
    drive = DiskDrive(dsk_image(_bpb_image(tmp_path / "1dd.dsk", total=720,
                                              spt=9, heads=1)))
    assert drive.lsn(1, 0, 1) == 9   # single-sided: track 1 after 1 side x 9
    assert drive.lsn(0, 0, 1) == 0


def test_blank_image_falls_back_to_2dd(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "blank.dsk", fill=0xE5))
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_720k_image_is_2dd_whatever_its_bpb_says(tmp_path: Path) -> None:
    # openMSX SectorBasedDisk::detectGeometry: a 1440-sector image is 9/2.
    img = dsk_image(_bpb_image(tmp_path / "ss.dsk", total=1440, spt=8, heads=1))
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_bpb_needs_a_boot_jump(tmp_path: Path) -> None:
    # Without 0xE9/0xEB the BPB is ignored; an all-zero FAT gives no media
    # byte, so a 640-sector image falls back to 9 sectors, 2 sides.
    p = _bpb_image(tmp_path / "nojmp.dsk", total=640, spt=8, heads=2)
    data = bytearray(p.read_bytes())
    data[0] = 0x00
    p.write_bytes(data)
    img = dsk_image(p)
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_bpb_values_are_taken_without_a_size_check(tmp_path: Path) -> None:
    # openMSX checks only 1..255; the BPB total and bytes/sector are unused.
    img = dsk_image(_bpb_image(tmp_path / "odd.dsk", total=640, spt=8,
                               heads=2, bytes_per_sector=256))
    assert (img.sectors_per_track, img.sides) == (8, 2)
    img_big = dsk_image(_bpb_image(tmp_path / "big.dsk", total=80, spt=255, heads=3))
    assert (img_big.sectors_per_track, img_big.sides) == (255, 3)


def test_bogus_bpb_values_fall_back(tmp_path: Path) -> None:
    for name, spt, heads in (("zero", 0, 2), ("over", 256, 2), ("noheads", 9, 0)):
        img = dsk_image(_bpb_image(tmp_path / f"{name}.dsk", total=640,
                                   spt=spt, heads=heads))
        assert (img.sectors_per_track, img.sides) == (9, 2)


def test_bogus_bpb_on_360k_image_falls_back_to_one_side(tmp_path: Path) -> None:
    img = dsk_image(_bpb_image(tmp_path / "bogus.dsk", total=720, spt=0, heads=2))
    assert (img.sectors_per_track, img.sides) == (9, 1)


@pytest.mark.parametrize(
    ("media", "geometry"),
    [(0xF8, (9, 1)), (0xF9, (9, 2)), (0xFA, (8, 1)), (0xFB, (8, 2))],
)
def test_fat_media_byte_gives_geometry_without_boot_jump(
    tmp_path: Path, media: int, geometry: tuple[int, int],
) -> None:
    p = tmp_path / "media.dsk"
    data = bytearray(640 * SECTOR_SIZE)
    data[SECTOR_SIZE] = media
    p.write_bytes(data)
    img = dsk_image(p)
    assert (img.sectors_per_track, img.sides) == geometry


def test_media_byte_below_f8_falls_back(tmp_path: Path) -> None:
    p = tmp_path / "media.dsk"
    data = bytearray(640 * SECTOR_SIZE)
    data[SECTOR_SIZE] = 0xF7
    p.write_bytes(data)
    img = dsk_image(p)
    assert (img.sectors_per_track, img.sides) == (9, 2)


def test_blank_360k_image_is_single_sided(tmp_path: Path) -> None:
    p = tmp_path / "blank1dd.dsk"
    p.write_bytes(bytes(720 * SECTOR_SIZE))
    img = dsk_image(p)
    assert (img.sectors_per_track, img.sides) == (9, 1)


def test_sector_number_outside_track_is_not_found(tmp_path: Path) -> None:
    # Sector 10 on a 9-sector track must not read LSN 9 (track 0 side 1),
    # and sector 0 must not read LSN -1.
    drive = DiskDrive(dsk_image(_make_dsk(tmp_path / "d.dsk")))
    assert drive.read_sector(0, 0, 10) is None
    assert drive.read_sector(0, 0, 0) is None
    assert drive.write_sector(0, 0, 10, b"\x00" * SECTOR_SIZE) is False


def test_sector_9_on_8_sector_image_reads_next_side(tmp_path: Path) -> None:
    # openMSX's raw track always carries sector IDs 1-9, so on an 8-sector
    # image R = 9 maps through the geometry to side 1 sector 1 (LSN 8).
    drive = DiskDrive(dsk_image(_bpb_image(tmp_path / "8s.dsk", total=640,
                                           spt=8, heads=2)))
    assert drive.lsn(0, 0, 9) == 8
    assert drive.read_sector(0, 0, 9) == drive.read_sector(0, 1, 1)


def test_clean_image_is_not_dirty(tmp_path: Path) -> None:
    img = dsk_image(_make_dsk(tmp_path / "d.dsk", fill=0x11))
    img.read_sector(0)
    assert img.dirty is False
