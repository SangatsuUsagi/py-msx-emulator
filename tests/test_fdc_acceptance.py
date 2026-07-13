"""Device-level acceptance: format -> write -> read -> persist, no BASIC needed.

Drives the FloppyDisk through its memory-mapped registers exactly as the DISK ROM
would (WRITE TRACK to format, WRITE SECTOR, READ SECTOR), then flushes and
remounts to prove persistence. This exercises the full image/drive/controller/
interface stack without needing the real ROMs.
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import BUSY, TRACK_BYTES, WD2793

_2DD = 737280

# Register addresses in the DISK-ROM page (0x7FF8-0x7FFF).
_STATUS = 0x7FF8
_CMD = 0x7FF8
_TRACK = 0x7FF9
_SECTOR = 0x7FFA
_DATA = 0x7FFB
_SIDE = 0x7FFC
_DRIVE = 0x7FFD


def _iface_with_disk(dsk: Path) -> SonyPhilipsInterface:
    iface = SonyPhilipsInterface(WD2793(), [DiskDrive()], disk_rom=bytes(16384))
    iface.mount(DskDiskImage(dsk), drive=0)
    iface.write_mem(_DRIVE, 0x80)  # drive A + motor on
    iface.write_mem(_SIDE, 0x00)   # side 0
    return iface


def _format_track(iface: SonyPhilipsInterface, track: int) -> None:
    iface.write_mem(_TRACK, track)
    iface.write_mem(_CMD, 0xF0)  # WRITE TRACK
    for _ in range(TRACK_BYTES + 16):  # small margin; loop stops when BUSY clears
        if not (iface.read_mem(_STATUS) & BUSY):
            break
        iface.write_mem(_DATA, 0x4E)


def _write_sector(iface: SonyPhilipsInterface, track: int, sector: int, data: bytes) -> None:
    iface.write_mem(_TRACK, track)
    iface.write_mem(_SECTOR, sector)
    iface.write_mem(_CMD, 0xA0)  # WRITE SECTOR
    for b in data:
        iface.write_mem(_DATA, b)


def _read_sector(iface: SonyPhilipsInterface, track: int, sector: int) -> bytes:
    iface.write_mem(_TRACK, track)
    iface.write_mem(_SECTOR, sector)
    iface.write_mem(_CMD, 0x80)  # READ SECTOR
    return bytes(iface.read_mem(_DATA) for _ in range(SECTOR_SIZE))


def test_format_write_read_roundtrip_and_persist(tmp_path: Path) -> None:
    dsk = tmp_path / "blank.dsk"
    dsk.write_bytes(bytes(_2DD))  # all zeros: clearly not yet formatted
    iface = _iface_with_disk(dsk)

    # 1. FORMAT track 0: WRITE TRACK blanks every sector to 0xE5.
    _format_track(iface, 0)
    assert iface.drives[0].image.read_sector(0) == b"\xe5" * SECTOR_SIZE

    # 2. WRITE a data sector (stand-in for a boot/FAT/dir/data sector).
    payload = bytes((i * 3 + 1) & 0xFF for i in range(SECTOR_SIZE))
    _write_sector(iface, 0, 1, payload)  # track 0 side 0 sector 1 -> LSN 0

    # 3. READ it back through the controller: identical.
    assert _read_sector(iface, 0, 1) == payload

    # 4. Persist: flush, then remount the file and confirm the sector survived.
    iface.flush()
    remounted = DskDiskImage(dsk)
    assert remounted.read_sector(0) == payload


def test_write_track_then_read_is_e5_before_data_write(tmp_path: Path) -> None:
    dsk = tmp_path / "blank.dsk"
    dsk.write_bytes(bytes(_2DD))
    iface = _iface_with_disk(dsk)
    _format_track(iface, 3)
    # Every sector of track 3 side 0 reads back as the format fill byte.
    assert _read_sector(iface, 3, 1) == b"\xe5" * SECTOR_SIZE
    assert _read_sector(iface, 3, 9) == b"\xe5" * SECTOR_SIZE
