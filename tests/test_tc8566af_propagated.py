"""TC8566AF controller: obligations from `allium plan allium/fdc-tc8566af-core.allium`
not already covered by tests/test_tc8566af.py.

Generated via /propagate against the distilled spec (allium/fdc-tc8566af-core.allium),
which was itself distilled FROM this already-working code -- so, unlike the
spec-first TDD pass that produced test_tc8566af.py, these are expected to be
green from the start (the rule already existed in the code; the spec just
named it). A red result here would mean the code does not actually behave the
way the distillation described -- a real bug, not an expected TDD step.
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from msx.fdc.tc8566af import (
    CMD_READ_DATA,
    CMD_RECALIBRATE,
    CMD_SEEK,
    CMD_SENSE_INTERRUPT_STATUS,
    CMD_SPECIFY,
    CMD_WRITE_DATA,
    TC8566AF,
    Phase,
)

_2DD = 737280


def _ctrl(tmp_path: Path, *, with_disk: bool = True) -> tuple[TC8566AF, DiskDrive]:
    image = None
    if with_disk:
        p = tmp_path / "d.dsk"
        p.write_bytes(bytes(_2DD))
        image = DskDiskImage(p)
    drive = DiskDrive(image)
    return TC8566AF(drives=[drive]), drive


# -- rule-success.WriteControlReg1 / rule-failure.WriteControlReg1.1 --------
# (WriteControlReg1 has no `requires` clause, so there is no failure case to
# exercise -- only the plain store.)

def test_write_control_reg1_stores_value(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_control_reg1(0x81)
    assert tc.control_reg1 == 0x81


# -- rule-success.AbortTransfer (distinct from Reset) ------------------------

def test_abort_transfer_clears_phase_but_not_control_registers(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    drive.image.write_sector(0, b"\x11" * SECTOR_SIZE)
    tc.write_control_reg0(0x14)  # MEN0 on, -FRST=1 (bit2 set): normal operation
    tc.write_data(CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    assert tc._phase == Phase.EXECUTION  # mid-transfer

    tc.abort()

    assert tc._phase == Phase.IDLE
    assert tc.control_reg0 == 0x14  # untouched by abort, unlike reset()


def test_control_reg0_frst_bit_low_triggers_abort(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    drive.image.write_sector(0, b"\x22" * SECTOR_SIZE)
    tc.write_data(CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    assert tc._phase == Phase.EXECUTION

    tc.write_control_reg0(0x00)  # bit2 (-FRST) low -> hold FDC in reset

    assert tc._phase == Phase.IDLE
    assert tc.control_reg0 == 0x00  # the write itself still lands


# -- rule-success/failure.ReadDataRegisterOtherwise --------------------------

def test_read_data_register_idle_returns_open_bus(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    assert tc.read_data() == 0xFF


def test_read_data_register_mid_command_phase_returns_open_bus(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_SEEK)  # Command Phase, awaiting 2 parameter bytes
    assert tc.read_data() == 0xFF


def test_read_data_register_during_write_execution_returns_open_bus(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_WRITE_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    assert tc._phase == Phase.EXECUTION
    assert tc.read_data() == 0xFF  # WRITE DATA's own Execution Phase is host->FDC


# -- rule-success.SeekCommand / RecalibrateCommand: not-ready abnormal path --

def test_seek_no_disk_reports_not_ready_via_sense_interrupt_status(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, with_disk=False)
    tc.write_data(CMD_SEEK)
    tc.write_data(0x00)
    tc.write_data(42)
    tc.write_data(CMD_SENSE_INTERRUPT_STATUS)
    st0 = tc.read_data()
    assert st0 & 0x40  # abnormal termination
    assert st0 & 0x08  # not ready


def test_recalibrate_no_disk_reports_not_ready(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, with_disk=False)
    tc.write_data(CMD_RECALIBRATE)
    tc.write_data(0x00)
    tc.write_data(CMD_SENSE_INTERRUPT_STATUS)
    st0 = tc.read_data()
    assert st0 & 0x40
    assert st0 & 0x08


# -- rule-success.ReadDataCommand / WriteDataCommand: not-ready / no-data ----

def test_read_data_no_disk_reports_not_ready(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, with_disk=False)
    tc.write_data(CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40  # abnormal
    assert st0 & 0x08  # not ready
    assert st1 == 0


def test_read_data_missing_sector_reports_no_data(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_READ_DATA)
    for b in (0, 79, 0, 99, 2, 1, 0x1B, 0xFF):  # sector 99 doesn't exist
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40
    assert st1 & 0x04  # ST1 no-data bit


def test_write_data_no_disk_reports_not_ready(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, with_disk=False)
    tc.write_data(CMD_WRITE_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40
    assert st0 & 0x08
    assert st1 == 0


def test_write_data_missing_sector_reports_no_data(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_WRITE_DATA)
    for b in (0, 79, 0, 99, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40
    assert st1 & 0x04


# -- MotorOn (derived): every drive index, not just 0 ------------------------

def test_motor_on_derivation_covers_all_four_drive_bits(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_control_reg0(0xF4)  # MEN0-3 all on, -FRST=1
    for i in range(4):
        assert tc.motor_on(i) is True
    tc.write_control_reg0(0x24)  # only MEN1
    assert tc.motor_on(0) is False
    assert tc.motor_on(1) is True
    assert tc.motor_on(2) is False
    assert tc.motor_on(3) is False


# -- SpecifyCommand (untested in test_tc8566af.py) ---------------------------

def test_specify_command_accepts_two_params_and_returns_to_idle(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_SPECIFY)
    assert tc._phase == Phase.COMMAND
    tc.write_data(0xDF)  # SRT/HUT
    tc.write_data(0x02)  # HLT/ND
    assert tc._phase == Phase.IDLE
    assert tc.read_main_status() & 0x80  # RQM: ready for the next command


# -- StartCommand: unrecognised opcode -> ST0=0x80 in Result Phase ----------

def test_unrecognised_command_reports_invalid_command_status(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(0x1F)  # low 5 bits = 0x1F, not a recognised opcode
    assert tc.read_data() == 0x80
    assert tc.read_main_status() & 0x80  # back to ready for a new command


# -- invariant.RegistersAreBytes / SelectedDriveIsInRange --------------------

def test_registers_and_selected_drive_stay_in_range_after_writes(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_control_reg0(0x1FF & 0xFF)  # write_control_reg0 also masks internally
    tc.write_control_reg1(0x2FF & 0xFF)
    tc.write_data(CMD_SEEK)
    tc.write_data(0x03)  # drive index 3 (out of range for a 1-drive machine, still 0-3)
    tc.write_data(10)
    assert 0 <= tc.control_reg0 <= 255
    assert 0 <= tc.control_reg1 <= 255
    assert 0 <= tc._selected_drive_index <= 3
