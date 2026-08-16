"""HB-F1XD connection-style obligations from allium/fdc-hbf1xd.allium.

Rule-by-rule coverage map (built by reading every test file under tests/
that touches msx/fdc/interface.py, directly or through the debugger/RPC
layers, before writing anything here):

Already covered, no new test added for these:
  - MountDisk (success) -- tests/test_fdc_disk_swap.py's initial
    iface.mount() calls; tests/test_debugger_fdd.py::test_fdd1_mounts_file,
    test_fdd1_status_shows_path, test_fdd1_eject, test_fdd1_missing_file_
    keeps_disk
  - SwapDisk (success, including the flush/no-flush and eject branches)
    -- tests/test_fdc_disk_swap.py::test_swap_mounts_new_image_for_
    controller, test_swap_flushes_outgoing_image, test_swap_aborts_in_
    progress_transfer, test_swap_sets_disk_change, test_eject_empties_
    drive; tests/test_rpc_fdd.py::test_fdd_mount, test_fdd_eject
  - FlushAllDrives -- tests/test_fdc_acceptance.py::test_format_write_
    read_roundtrip_and_persist (iface.flush() then remount-and-verify)
  - ForwardStatusRead, ForwardCommandWrite, ForwardTrackWrite,
    ForwardSectorWrite, ForwardDataRead, ForwardDataWrite -- exercised
    throughout tests/test_fdc_interface.py, test_fdc_acceptance.py,
    test_fdc_disk_swap.py (every one of those files drives the FDC via
    0x7FF8-0x7FFB)
  - ReadDriveRegister, both the "changed" and "not changed" branches --
    tests/test_fdc_interface.py::test_disk_change_bit_reports_not_changed,
    test_disk_change_reported_then_consumed, test_disk_change_not_set_
    reports_not_changed; tests/test_fdc_disk_swap.py::test_swap_sets_
    disk_change
  - ReadControlStatusRegister's INTRQ (bit 6) branch --
    tests/test_fdc_interface.py::test_irq_status_byte_active_low
  - ReadDiskRom (success, ROM present and offset in range) --
    tests/test_fdc_interface.py::test_disk_rom_read_at_0x4000
  - WriteDriveRegister, the "select drive A" branch (sel=00) --
    tests/test_fdc_interface.py::test_drive_and_motor_select
  - WriteSideRegister, the single-drive case --
    tests/test_fdc_interface.py::test_side_select
  - Mutual exclusion among the ten ReadMemory-triggered and eight
    WriteMemory-triggered rules (each rule's own rule-failure obligation)
    -- every offset in the register window (0x7FF8-0x7FFF) plus the ROM
    window is already exercised by the tests above, so each is
    simultaneously a negative test for the others; same reasoning as
    tests/test_wd2793_spec.py's docstring for the sibling spec.

Gaps this file fills:
  - InitializeFloppyDisk's success case -- no existing test asserts that
    drive 0 is connected to the controller *before* any register write;
    test_drive_and_motor_select only checks the post-write state, which a
    0x7FFD write with sel=00 would produce anyway even if the constructor
    connected nothing
  - InitializeFloppyDisk's failure case (empty drives list -> ValueError)
    -- not constructed anywhere in tests/
  - MountDisk's and SwapDisk's failure case (out-of-range drive index) --
    both the debugger (_cmd_fdd) and the RPC handler (_h_fdd_swap) guard
    the index themselves before ever calling FloppyDisk.mount()/swap(), so
    no existing test reaches msx/fdc/interface.py's own (guardless) list
    indexing; confirmed by reading msx/debugger/prompt.py:724 and
    msx/rpc_server.py's _h_fdd_swap, neither of which delegates an
    out-of-range index down to the interface layer
  - ReadTrackFromController, ReadSectorFromController -- no test anywhere
    reads 0x7FF9/0x7FFA back through the memory window (existing tests
    only *write* those offsets to set up a command)
  - ReadSideRegister -- 0x7FFC is written and its effect checked
    (drive.side), but never read back through the memory window
  - ReadUnconnectedRegister -- 0x7FFE is never read anywhere
  - ReadControlStatusRegister's DRQ (bit 7) branch -- only the INTRQ bit
    is exercised via a memory read; DRQ is checked elsewhere only via
    controller.get_drq() directly, never via iface.read_mem(0x7FFF)
  - ReadDiskRom's failure branches / ReadOpenBus's success -- every
    existing SonyPhilipsInterface in tests/ is built with a full 16 KB
    disk_rom; no test constructs one with disk_rom=None or a short ROM
  - WriteDriveRegister's drive-B and no-drive branches, and the
    out-of-range-B-on-a-single-drive-machine case -- every existing
    drive/motor-select test uses a single-drive machine and sel=00; no
    test in this codebase constructs a *two*-drive SonyPhilipsInterface at
    all (test_debugger_fdd.py's `_dbg(drives=...)` helper defaults to 1
    and is only ever called with 1)
  - WriteSideRegister's "every drive gets the side, not just the selected
    one" as-built behaviour -- the single-drive tests above cannot
    distinguish this from "only the selected drive gets it"
  - WriteSideRegister vs. openMSX's DriveMultiplexer under a drive switch
    with no further side write -- select a side, switch drives, check the
    newly-selected drive's side without rewriting it; this is the exact
    scenario the spec's WriteSideRegister open question flagged as
    inferred-but-untested. It holds: closes that open question.
  - invariant.FloppyDiskHasAtLeastOneDrive, invariant.RegisterBytesAreBytes
    -- no property-based tests exist for either
  - config-default.* -- msx/fdc/interface.py has no exported named
    constants for its register offsets or bit positions (unlike
    msx/fdc/wd2793.py's BUSY/S_DRQ/etc.), so there is nothing to assert
    against as a standalone value-equality test. Every config default is
    instead exercised behaviourally, by address, in the register tests
    above and below (e.g. reading exactly 0x7FFC exercises
    config.side_offset = 16380; the drive-B test below exercises
    config.drive_b_select_code = 1).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793

_2DD = 737280

# Register addresses in the DISK-ROM page (0x7FF8-0x7FFF), same convention
# as tests/test_fdc_acceptance.py.
_STATUS = 0x7FF8
_CMD = 0x7FF8
_TRACK = 0x7FF9
_SECTOR = 0x7FFA
_DATA = 0x7FFB
_SIDE = 0x7FFC
_DRIVE = 0x7FFD
_UNCONNECTED = 0x7FFE
_CONTROL_STATUS = 0x7FFF


def _blank(path: Path) -> Path:
    path.write_bytes(bytes(_2DD))
    return path


def _iface(drives: int = 1, *, disk_rom: bytes | None = bytes(16384)) -> SonyPhilipsInterface:
    return SonyPhilipsInterface(
        WD2793(), [DiskDrive() for _ in range(drives)], disk_rom=disk_rom
    )


# ---------------------------------------------------------------------------
# InitializeFloppyDisk
# ---------------------------------------------------------------------------

def test_construction_connects_drive_zero_before_any_register_write() -> None:
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    assert controller.drive is drive_a  # power-on default, no 0x7FFD write yet


def test_construction_with_no_drives_raises() -> None:
    with pytest.raises(ValueError):
        SonyPhilipsInterface(WD2793(), [], disk_rom=bytes(16384))


# ---------------------------------------------------------------------------
# MountDisk / SwapDisk -- out-of-range drive index
# ---------------------------------------------------------------------------

def test_mount_out_of_range_drive_raises(tmp_path: Path) -> None:
    """Neither the debugger (_cmd_fdd) nor the RPC handler (_h_fdd_swap)
    ever forwards an out-of-range index this far -- both check the drive
    count themselves first. This documents what actually happens if
    something does reach FloppyDisk.mount() directly: an unguarded list
    index, not a graceful rejection."""
    iface = _iface(drives=1)
    with pytest.raises(IndexError):
        iface.mount(DskDiskImage(_blank(tmp_path / "d.dsk")), drive=1)


def test_swap_out_of_range_drive_raises(tmp_path: Path) -> None:
    iface = _iface(drives=1)
    with pytest.raises(IndexError):
        iface.swap(1, DskDiskImage(_blank(tmp_path / "d.dsk")))


# ---------------------------------------------------------------------------
# ReadTrackFromController / ReadSectorFromController
# ---------------------------------------------------------------------------

def test_track_and_sector_registers_read_back_through_memory_window() -> None:
    iface = _iface()
    iface.write_mem(_TRACK, 0x2A)
    iface.write_mem(_SECTOR, 0x07)
    assert iface.read_mem(_TRACK) == 0x2A
    assert iface.read_mem(_SECTOR) == 0x07


# ---------------------------------------------------------------------------
# ReadSideRegister
# ---------------------------------------------------------------------------

def test_side_register_reads_back_whole_written_byte() -> None:
    iface = _iface()
    iface.write_mem(_SIDE, 0x01)
    assert iface.read_mem(_SIDE) == 0x01  # whole byte echoed, not masked to bit 0


# ---------------------------------------------------------------------------
# ReadUnconnectedRegister
# ---------------------------------------------------------------------------

def test_unconnected_register_reads_open_bus() -> None:
    iface = _iface()
    assert iface.read_mem(_UNCONNECTED) == 0xFF


# ---------------------------------------------------------------------------
# ReadControlStatusRegister -- DRQ bit (INTRQ already covered by
# tests/test_fdc_interface.py::test_irq_status_byte_active_low)
# ---------------------------------------------------------------------------

def test_drq_status_byte_active_low(tmp_path: Path) -> None:
    iface = _iface()
    iface.drives[0].image = DskDiskImage(_blank(tmp_path / "d.dsk"))
    iface.write_mem(_TRACK, 0)
    iface.write_mem(_SECTOR, 1)
    iface.write_mem(_CMD, 0x80)  # READ SECTOR -> DRQ asserted
    value = iface.read_mem(_CONTROL_STATUS)
    assert value & 0x80 == 0        # bit 7 low == DRQ asserted
    assert value & 0x40 != 0        # INTRQ not asserted mid-transfer


# ---------------------------------------------------------------------------
# ReadDiskRom failure branches / ReadOpenBus success
# ---------------------------------------------------------------------------

def test_no_disk_rom_reads_open_bus() -> None:
    iface = _iface(disk_rom=None)
    assert iface.read_mem(0x4000) == 0xFF


def test_past_end_of_short_disk_rom_reads_open_bus() -> None:
    iface = _iface(disk_rom=bytes([0x11, 0x22, 0x33]))  # 3-byte ROM
    assert iface.read_mem(0x4000) == 0x11
    assert iface.read_mem(0x4002) == 0x33
    assert iface.read_mem(0x4003) == 0xFF  # past the end: open bus, not IndexError


# ---------------------------------------------------------------------------
# WriteDriveRegister -- drive-B and no-drive branches (a two-drive machine)
# ---------------------------------------------------------------------------

def test_drive_register_selects_drive_b() -> None:
    controller = WD2793()
    iface = SonyPhilipsInterface(
        controller, [DiskDrive(), DiskDrive()], disk_rom=bytes(16384)
    )
    iface.write_mem(_DRIVE, 0x01)  # bits1:0 = 01 -> drive B
    assert controller.drive is iface.drives[1]


def test_drive_register_no_drive_selected_disconnects_controller() -> None:
    controller = WD2793()
    iface = SonyPhilipsInterface(
        controller, [DiskDrive(), DiskDrive()], disk_rom=bytes(16384)
    )
    iface.write_mem(_DRIVE, 0x03)  # bits1:0 = 11 -> no drive
    assert controller.drive is None


def test_drive_register_drive_b_on_single_drive_machine_disconnects() -> None:
    """A single-drive machine given sel=01 (drive B) takes the same
    controller.drive = None path as sel=11 (no drive) -- the source does
    not distinguish "no such drive" from "no drive requested"."""
    controller = WD2793()
    iface = SonyPhilipsInterface(controller, [DiskDrive()], disk_rom=bytes(16384))
    iface.write_mem(_DRIVE, 0x01)
    assert controller.drive is None


# ---------------------------------------------------------------------------
# WriteSideRegister -- "every drive in the list", not just the selected one
# ---------------------------------------------------------------------------

def test_side_register_write_updates_every_drive_not_just_selected() -> None:
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    iface = SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    iface.write_mem(_DRIVE, 0x01)  # select drive B
    iface.write_mem(_SIDE, 0x01)   # side select while B is connected
    assert drive_b.side == 1
    assert drive_a.side == 1       # as-built: A got it too, though it isn't selected


def test_side_selected_before_switching_drives_still_applies_to_new_drive() -> None:
    """Closes allium/fdc-hbf1xd.allium's WriteSideRegister open question
    (see module docstring): side selected while drive A is connected must
    still land on drive B after switching, with no further side write."""
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    iface = SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    iface.write_mem(_SIDE, 0x01)    # side select while A (default) is connected
    iface.write_mem(_DRIVE, 0x01)   # switch to drive B, no further side write
    assert controller.drive is drive_b
    assert drive_b.side == 1


# ---------------------------------------------------------------------------
# invariant.FloppyDiskHasAtLeastOneDrive / invariant.RegisterBytesAreBytes
# ---------------------------------------------------------------------------

@given(drive_count=st.integers(min_value=1, max_value=4))
@settings(max_examples=25)
def test_floppy_disk_always_has_at_least_one_drive(drive_count: int) -> None:
    iface = _iface(drives=drive_count)
    assert len(iface.drives) >= 1


@given(
    side_writes=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=20),
    drive_writes=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=20),
)
@settings(max_examples=100)
def test_side_and_drive_registers_stay_within_byte_range(
    side_writes: list[int], drive_writes: list[int]
) -> None:
    iface = _iface(drives=2)
    for i in range(max(len(side_writes), len(drive_writes))):
        if i < len(side_writes):
            iface.write_mem(_SIDE, side_writes[i])
        if i < len(drive_writes):
            iface.write_mem(_DRIVE, drive_writes[i])
        assert 0 <= iface.side_reg <= 255
        assert 0 <= iface.drive_reg <= 255
