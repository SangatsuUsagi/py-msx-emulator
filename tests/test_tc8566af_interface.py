"""Tests for the TC8566AF FDC connection-style interface (Panasonic FS-A1F).

One test per Scenario in openspec/changes/archive/2026-08-21-add-tc8566af-fdc/specs/fdc-interface/
spec.md's "TC8566AF connection style register decode" Requirement (written
before TC8566AFInterface exists -- TDD red phase).
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import TC8566AFInterface
from msx.fdc.tc8566af import CMD_READ_DATA, TC8566AF

_2DD = 737280


def _iface(tmp_path: Path, *, with_disk: bool = True) -> TC8566AFInterface:
    disk_rom = bytes([0xC3, 0x00, 0x40] + [0x00] * (0x4000 - 3))  # 16 KB, byte0=0xC3
    image = None
    if with_disk:
        p = tmp_path / "d.dsk"
        p.write_bytes(bytes(_2DD))
        image = DskDiskImage(p)
    drive = DiskDrive(image)
    return TC8566AFInterface(TC8566AF(drives=[drive]), [drive], disk_rom=disk_rom)


def test_data_register_access_routed_to_controller(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.drives[0].image.write_sector(0, b"\x99" + b"\x00" * 511)
    iface.write_mem(0x7FFB, CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):  # HD_DS,C,H,R,N,EOT,GPL,DTL
        iface.write_mem(0x7FFB, b)
    assert iface.read_mem(0x7FFB) == 0x99


def test_main_status_read_does_not_consume_data_register(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FFB, CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        iface.write_mem(0x7FFB, b)
    before = iface.read_mem(0x7FFA)  # Main Status Register
    after = iface.read_mem(0x7FFA)
    assert before == after           # reading MSR twice doesn't advance phase


def test_control_reg0_selects_drive_and_enables_motor(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FF8, 0x10)  # Control Register 0: MEN0 set
    assert iface.controller.motor_on(0) is True


def test_control_registers_read_as_open_bus_not_disk_rom(tmp_path: Path) -> None:
    """0x7FF8/0x7FF9 are write-only; reading them must not fall through to
    the DISK ROM byte at that offset (regression: allium:weed found this
    falling through prior to the fix)."""
    iface = _iface(tmp_path)
    assert iface.read_mem(0x7FF8) == 0xFF
    assert iface.read_mem(0x7FF9) == 0xFF


def test_reserved_offsets_read_fixed_diagnostic_values(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert iface.read_mem(0x7FFC) == 0xFC
    assert iface.read_mem(0x7FFD) == 0xFC
    assert iface.read_mem(0x7FFE) == 0xFF
    assert iface.read_mem(0x7FFF) == 0x3F


def test_disk_rom_outside_register_window_unaffected(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert iface.read_mem(0x4000) == 0xC3  # first DISK ROM byte
    iface.write_mem(0x4000, 0x55)          # ROM is read-only
    assert iface.read_mem(0x4000) == 0xC3
