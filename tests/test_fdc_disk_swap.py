"""Tests for FloppyDisk.swap (runtime disk swap)."""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import BUSY, WD2793, Mode
from tests.disk_factories import dsk_image

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
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk", first_byte=0x11)), 0)
    assert _read_lsn0_first_byte(iface) == 0x11
    iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk", first_byte=0x77)))
    assert _read_lsn0_first_byte(iface) == 0x77


def test_swap_returns_the_dirty_outgoing_image(tmp_path: Path) -> None:
    iface = _iface()
    img_a = dsk_image(_blank(tmp_path / "a.dsk"))
    iface.mount(img_a, 0)
    img_a.write_sector(5, b"\xAB" * SECTOR_SIZE)  # dirty, not persisted
    # The core hands the outgoing image back for the host to write.
    assert iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk"))) is img_a


def test_swap_returns_none_for_a_clean_outgoing_image(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk")), 0)
    assert iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk"))) is None


def test_swap_aborts_in_progress_transfer(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk", first_byte=0x22)), 0)
    iface.write_mem(0x7FF8, 0x80)         # start READ SECTOR -> BUSY
    assert iface.controller.get_status() & BUSY
    iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk")))
    assert not (iface.controller.get_status() & BUSY)
    assert iface.controller._mode is Mode.IDLE


def test_swap_on_non_selected_drive_does_not_abort_selected_drives_transfer(
    tmp_path: Path,
) -> None:
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    iface = SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk", first_byte=0x11)), 0)
    iface.mount(dsk_image(_blank(tmp_path / "b.dsk")), 1)
    assert controller.drive is drive_a  # power-on default

    iface.write_mem(0x7FF8, 0x80)  # start READ SECTOR on drive A -> BUSY
    assert iface.controller.get_status() & BUSY

    iface.swap(1, dsk_image(_blank(tmp_path / "c.dsk")))  # swap drive B, not A

    assert iface.controller.get_status() & BUSY  # drive A's transfer untouched
    assert iface.controller._mode is not Mode.IDLE


def test_swap_on_selected_drive_still_aborts(tmp_path: Path) -> None:
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    iface = SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk", first_byte=0x11)), 0)
    assert controller.drive is drive_a

    iface.write_mem(0x7FF8, 0x80)  # start READ SECTOR on the selected drive
    assert iface.controller.get_status() & BUSY

    iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk")))  # swap the selected drive

    assert not (iface.controller.get_status() & BUSY)
    assert iface.controller._mode is Mode.IDLE


def test_swap_sets_disk_change(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk")), 0)
    iface.read_mem(0x7FFD)  # clear any initial state
    iface.swap(0, dsk_image(_blank(tmp_path / "b.dsk")))
    assert iface.read_mem(0x7FFD) & 0x04 == 0  # reports changed after swap


def test_eject_empties_drive(tmp_path: Path) -> None:
    iface = _iface()
    iface.mount(dsk_image(_blank(tmp_path / "a.dsk")), 0)
    iface.swap(0, None)
    assert iface.drives[0].image is None
    assert iface.drives[0].has_disk is False
