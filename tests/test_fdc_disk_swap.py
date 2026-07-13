"""Tests for FloppyDisk.swap (runtime disk swap)."""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import BUSY, WD2793

_2DD = 737280


def _iface() -> SonyPhilipsInterface:
    return SonyPhilipsInterface(WD2793(), [DiskDrive()], disk_rom=bytes(16384))


def _blank(path: Path, first_byte: int = 0x00) -> Path:
    data = bytearray(_2DD)
    data[0] = first_byte  # marker at LSN 0, byte 0
    path.write_bytes(data)
    return path


def _read_lsn0_first_byte(iface: SonyPhilipsInterface) -> int:
    iface.write_mem(0x7FF9, 0)     # TRACK 0
    iface.write_mem(0x7FFA, 1)     # SECTOR 1 -> LSN 0
    iface.write_mem(0x7FF8, 0x80)  # READ SECTOR
    return iface.read_mem(0x7FFB)   # first DATA byte


def test_swap_mounts_new_image_for_controller(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(DskDiskImage(_blank(tmp_path / "a.dsk", first_byte=0x11)), 0)
    assert _read_lsn0_first_byte(iface) == 0x11
    iface.swap(0, DskDiskImage(_blank(tmp_path / "b.dsk", first_byte=0x77)))
    assert _read_lsn0_first_byte(iface) == 0x77


def test_swap_flushes_outgoing_image(tmp_path: Path) -> None:
    a = _blank(tmp_path / "a.dsk")
    iface = _iface()
    img_a = DskDiskImage(a)
    iface.mount(img_a, 0)
    img_a.write_sector(5, b"\xAB" * SECTOR_SIZE)  # dirty, not flushed
    iface.swap(0, DskDiskImage(_blank(tmp_path / "b.dsk")))
    # Reopening the outgoing file shows the write was flushed on swap.
    assert DskDiskImage(a).read_sector(5) == b"\xAB" * SECTOR_SIZE


def test_swap_aborts_in_progress_transfer(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(DskDiskImage(_blank(tmp_path / "a.dsk", first_byte=0x22)), 0)
    iface.write_mem(0x7FF8, 0x80)         # start READ SECTOR -> BUSY
    assert iface.controller.get_status() & BUSY
    iface.swap(0, DskDiskImage(_blank(tmp_path / "b.dsk")))
    assert not (iface.controller.get_status() & BUSY)
    assert iface.controller._mode == "idle"


def test_swap_sets_disk_change(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(DskDiskImage(_blank(tmp_path / "a.dsk")), 0)
    iface.read_mem(0x7FFD)  # clear any initial state
    iface.swap(0, DskDiskImage(_blank(tmp_path / "b.dsk")))
    assert iface.read_mem(0x7FFD) & 0x04 == 0  # reports changed after swap


def test_eject_empties_drive(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(DskDiskImage(_blank(tmp_path / "a.dsk")), 0)
    iface.swap(0, None)
    assert iface.drives[0].image is None
    assert iface.drives[0].has_disk is False
