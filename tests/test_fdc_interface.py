"""Tests for the Sony/Philips FDC connection-style interface."""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793

_2DD = 737280


def _iface(tmp_path: Path | None = None, *, with_disk: bool = True) -> SonyPhilipsInterface:
    disk_rom = bytes([0xC3, 0x00, 0x40] + [0x00] * (0x4000 - 3))  # 16 KB, byte0=0xC3
    image = None
    if with_disk and tmp_path is not None:
        p = tmp_path / "d.dsk"
        p.write_bytes(bytes(_2DD))
        image = DskDiskImage(p)
    drive = DiskDrive(image)
    return SonyPhilipsInterface(WD2793(), [drive], disk_rom=disk_rom)


def test_disk_rom_read_at_0x4000(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert iface.read_mem(0x4000) == 0xC3  # first DISK ROM byte


def test_data_register_routed_to_controller(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    # Put a known sector, RESTORE + READ SECTOR via the register window, then
    # read the DATA register at 0x7FFB.
    iface.drives[0].image.write_sector(0, b"\x99" + b"\x00" * (SECTOR_SIZE - 1))
    iface.write_mem(0x7FF9, 0)     # TRACK = 0
    iface.write_mem(0x7FFA, 1)     # SECTOR = 1
    iface.write_mem(0x7FF8, 0x80)  # COMMAND = READ SECTOR
    assert iface.read_mem(0x7FFB) == 0x99


def test_drive_and_motor_select(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FFD, 0x80)  # bit7 motor on, bits1:0=00 -> drive A
    assert iface.controller.drive is iface.drives[0]


def test_side_select(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FFC, 0x01)  # side 1
    assert iface.drives[0].side == 1


def test_irq_status_byte_active_low(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FF8, 0x00)  # RESTORE -> raises INTRQ
    value = iface.read_mem(0x7FFF)
    assert value & 0x40 == 0        # bit 6 low == INTRQ asserted


def test_disk_change_bit_reports_not_changed(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert iface.read_mem(0x7FFD) & 0x04  # bit 2 = 1 (disk not changed)


def test_unmounted_register_read_is_open_bus_safe() -> None:
    iface = _iface(with_disk=False)
    iface.write_mem(0x7FF8, 0x80)  # READ SECTOR with no disk
    status = iface.read_mem(0x7FF8)
    assert status & 0x80  # NOT_READY, no crash


def test_rom_write_is_ignored(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x4000, 0x55)
    assert iface.read_mem(0x4000) == 0xC3


# --- disk-change signal (for runtime swap) --------------------------------

def test_disk_change_reported_then_consumed(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.controller.drive.disk_changed = True
    first = iface.read_mem(0x7FFD)
    assert first & 0x04 == 0        # bit 2 = 0 -> disk changed
    second = iface.read_mem(0x7FFD)
    assert second & 0x04            # consumed: reverts to not-changed


def test_disk_change_not_set_reports_not_changed(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert iface.read_mem(0x7FFD) & 0x04  # no swap -> not changed
