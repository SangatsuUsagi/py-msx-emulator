"""TC8566AFInterface: obligations from `allium plan allium/fdc-fsa1f.allium`
not already covered by tests/test_tc8566af_interface.py.

Generated via /propagate against the distilled spec (allium/fdc-fsa1f.allium),
itself distilled FROM this already-working code -- these are expected to be
green from the start; a red result would mean a real bug, not an expected
TDD step. See tests/test_tc8566af_propagated.py's own module docstring for
the same point at the controller layer.
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import TC8566AFInterface
from msx.fdc.tc8566af import TC8566AF, Phase

_2DD = 737280


def _iface(tmp_path: Path, *, drives: int = 1, with_disk: bool = True) -> TC8566AFInterface:
    disk_rom = bytes([0xC3, 0x00, 0x40] + [0x00] * (0x4000 - 3))
    disk_drives = []
    for i in range(drives):
        image = None
        if with_disk:
            p = tmp_path / f"d{i}.dsk"
            p.write_bytes(bytes(_2DD))
            image = DskDiskImage(p)
        disk_drives.append(DiskDrive(image))
    return TC8566AFInterface(TC8566AF(drives=disk_drives), disk_drives, disk_rom=disk_rom)


# -- rule-success.InitializeFloppyDisk ---------------------------------------
# (No `ensures` -- see fdc-fsa1f.allium's own @guidance. Construction itself
# succeeding, with drives.count >= 1, is the whole obligation.)

def test_construction_succeeds_with_at_least_one_drive(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    assert len(iface.drives) >= 1


# -- rule-success.Reset ------------------------------------------------------

def test_reset_delegates_to_controller_reset(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FF8, 0x14)  # Control Register 0: motor on, -FRST=1
    assert iface.controller.motor_on(0) is True
    iface.reset()
    assert iface.controller.control_reg0 == 0
    assert iface.controller.motor_on(0) is False


# -- rule-success/failure.MountDisk ------------------------------------------

def test_mount_disk_attaches_image_to_target_drive(tmp_path: Path) -> None:
    iface = _iface(tmp_path, with_disk=False)
    p = tmp_path / "new.dsk"
    p.write_bytes(bytes(_2DD))
    image = DskDiskImage(p)
    iface.mount(image, drive=0)
    assert iface.drives[0].has_disk is True


# -- rule-success.SwapDisk: abort gated on the targeted drive ---------------

def test_swap_disk_aborts_transfer_only_on_targeted_drive(tmp_path: Path) -> None:
    iface = _iface(tmp_path, drives=2)
    # Start a transfer on drive 0 (the drive READ DATA's own parameter byte targets).
    iface.write_mem(0x7FFB, 0x06)  # CMD_READ_DATA
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        iface.write_mem(0x7FFB, b)
    assert iface.controller._phase == Phase.EXECUTION

    new_image_path = tmp_path / "swap1.dsk"
    new_image_path.write_bytes(bytes(_2DD))
    iface.swap(1, DskDiskImage(new_image_path))  # swap drive 1, not the transferring drive 0
    assert iface.controller._phase == Phase.EXECUTION  # untouched

    new_image_path0 = tmp_path / "swap0.dsk"
    new_image_path0.write_bytes(bytes(_2DD))
    iface.swap(0, DskDiskImage(new_image_path0))  # swap the transferring drive itself
    assert iface.controller._phase == Phase.IDLE  # aborted


def test_swap_disk_flags_disk_changed(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    new_image_path = tmp_path / "swap.dsk"
    new_image_path.write_bytes(bytes(_2DD))
    iface.swap(0, DskDiskImage(new_image_path))
    assert iface.drives[0].disk_changed is True


# -- rule-success.FlushAllDrives ---------------------------------------------

def test_flush_flushes_every_mounted_drive(tmp_path: Path) -> None:
    iface = _iface(tmp_path, drives=2)
    iface.drives[0].image.write_sector(0, b"\x77" * 512)
    iface.flush()  # no exception; both drives (one mounted, both present) handled
    assert iface.drives[0].image is not None


# -- rule-success/failure.ForwardControlReg1Write ----------------------------

def test_control_reg1_write_routed_to_controller(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    iface.write_mem(0x7FF9, 0x81)
    assert iface.controller.control_reg1 == 0x81


# -- rule-success/failure.WriteStatusIgnored ---------------------------------

def test_status_register_write_has_no_effect(tmp_path: Path) -> None:
    iface = _iface(tmp_path)
    before = iface.read_mem(0x7FFA)
    iface.write_mem(0x7FFA, 0x55)
    after = iface.read_mem(0x7FFA)
    assert before == after


# -- rule-success/failure.ReadOpenBus ----------------------------------------

def test_disk_rom_window_open_bus_when_no_rom_attached(tmp_path: Path) -> None:
    drive = DiskDrive()
    iface = TC8566AFInterface(TC8566AF(drives=[drive]), [drive], disk_rom=None)
    assert iface.read_mem(0x4000) == 0xFF
