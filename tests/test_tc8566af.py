"""Tests for the TC8566AF controller (functional model).

One test per Scenario in openspec/changes/add-tc8566af-fdc/specs/fdc-tc8566af/
spec.md (written before msx/fdc/tc8566af.py exists -- TDD red phase).
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
    CMD_WRITE_DATA,
    MSR_CB,
    MSR_DIO,
    MSR_RQM,
    TC8566AF,
)

_2DD = 737280


def _ctrl(tmp_path: Path, *, write_protected: bool = False) -> tuple[TC8566AF, DiskDrive]:
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes(_2DD))
    image = DskDiskImage(p, write_protected=write_protected)
    drive = DiskDrive(image)
    return TC8566AF(drives=[drive]), drive


# -- Requirement: TC8566AF register file ------------------------------------

def test_main_status_register_reflects_readiness_and_direction(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_READ_DATA)          # command byte
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):  # HD_DS,C,H,R,N,EOT,GPL,DTL
        tc.write_data(b)
    msr = tc.read_main_status()
    assert msr & MSR_RQM                  # ready
    assert msr & MSR_DIO                  # FDC -> processor direction


def test_control_reg0_motor_enable_turns_on_drive(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_control_reg0(0x10)           # MEN0 set, no other drive touched
    assert tc.motor_on(0) is True
    assert tc.motor_on(1) is False


# -- Requirement: Non-DMA mode register access -------------------------------

def test_command_byte_accepted_only_when_ready(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_SEEK)               # command byte, needs 2 params
    msr = tc.read_main_status()
    assert msr & MSR_RQM and not (msr & MSR_DIO)  # ready for next param, host->FDC
    tc.write_data(0x00)                   # HD_DS
    msr = tc.read_main_status()
    assert msr & MSR_RQM and not (msr & MSR_DIO)  # still ready for last param


def test_no_dma_acknowledge_or_terminal_count_signal(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    assert not hasattr(tc, "get_drq")
    assert not hasattr(tc, "dma_request")
    assert not hasattr(tc, "dack")
    assert not hasattr(tc, "terminal_count")


# -- Requirement: RESET-driven register initialization -----------------------

def test_reset_clears_motor_and_drive_select(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_control_reg0(0xF3)           # all motors on + drive select bits
    tc.reset()
    assert tc.control_reg0 == 0
    for i in range(4):
        assert tc.motor_on(i) is False


def test_reset_returns_msr_to_idle_ready(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path)
    tc.write_data(CMD_SEEK)
    tc.write_data(0x00)                   # mid Command Phase
    tc.reset()
    msr = tc.read_main_status()
    assert msr & MSR_RQM
    assert not (msr & MSR_DIO)
    assert not (msr & MSR_CB)


# -- Requirement: Command/Execution/Result phase protocol --------------------

def test_multi_byte_command_spans_multiple_gated_writes(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    tc.write_data(CMD_SEEK)
    assert tc.read_main_status() & MSR_CB   # Command Phase busy
    tc.write_data(0x00)                     # HD_DS = drive 0
    tc.write_data(42)                       # NCN
    assert drive.track == 42                # only completes after both params

    # SENSE INTERRUPT STATUS surfaces the seek result (ST0 seek-end, PCN)
    tc.write_data(CMD_SENSE_INTERRUPT_STATUS)
    st0 = tc.read_data()
    pcn = tc.read_data()
    assert st0 & 0x20                       # SE (seek end)
    assert pcn == 42


def test_result_phase_must_be_fully_drained(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    payload = bytes((i * 3) & 0xFF for i in range(SECTOR_SIZE))
    drive.image.write_sector(0, payload)  # track0 side0 sector1 -> LSN 0

    tc.write_data(CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    out = bytes(tc.read_data() for _ in range(SECTOR_SIZE))
    assert out == payload

    # Result Phase: 7 bytes (ST0,ST1,ST2,C,H,R,N). Reading only some of them
    # must not let a new command start.
    tc.read_data()  # ST0
    tc.read_data()  # ST1
    msr = tc.read_main_status()
    assert msr & MSR_DIO                   # still Result Phase, not idle
    tc.write_data(CMD_RECALIBRATE)          # attempted new command: ignored
    assert msr == tc.read_main_status()     # unchanged -- still draining Result Phase


# -- Supporting scenarios (READ/WRITE DATA execution, drive-select-in-command) -

def test_write_data_persists_and_read_data_round_trips(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    payload = bytes((255 - (i & 0xFF)) for i in range(SECTOR_SIZE))

    tc.write_data(CMD_WRITE_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    for b in payload:
        tc.write_data(b)
    assert drive.image.read_sector(0) == payload
    for _ in range(7):  # drain WRITE DATA's Result Phase before the next command
        tc.read_data()

    tc.write_data(CMD_READ_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    out = bytes(tc.read_data() for _ in range(SECTOR_SIZE))
    assert out == payload


def test_recalibrate_seeks_selected_drive_to_track0(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    drive.track = 30
    tc.write_data(CMD_RECALIBRATE)
    tc.write_data(0x00)                     # HD_DS -> drive 0
    assert drive.track == 0


def test_no_disk_reports_not_ready_on_sense_device_status(tmp_path: Path) -> None:
    tc = TC8566AF(drives=[DiskDrive()])     # empty drive
    from msx.fdc.tc8566af import CMD_SENSE_DEVICE_STATUS

    tc.write_data(CMD_SENSE_DEVICE_STATUS)
    tc.write_data(0x00)
    st3 = tc.read_data()
    assert not (st3 & 0x20)                 # RDY not set


def test_recalibrate_succeeds_with_no_disk_inserted() -> None:
    """RECALIBRATE/SEEK readiness depends only on the drive existing, not on
    disk presence -- the head/track-0 sensor is mechanical (openMSX's
    TC8566AF::doSeek: Not Ready only for a non-existent drive). A real MSX2
    DISK ROM issues RECALIBRATE during its init before it can know whether a
    disk is present; reporting abnormal termination here hung FS-A1F's boot
    (SENSE INTERRUPT STATUS retried forever waiting for a completion that
    never came)."""
    drive = DiskDrive()                     # empty drive, no disk mounted
    tc = TC8566AF(drives=[drive])
    drive.track = 30

    tc.write_data(CMD_RECALIBRATE)
    tc.write_data(0x00)                     # HD_DS -> drive 0
    assert drive.track == 0                 # seek to track 0 still runs

    tc.write_data(CMD_SENSE_INTERRUPT_STATUS)
    st0 = tc.read_data()
    assert st0 & 0x20                       # SE (seek end), not abnormal
    assert not (st0 & 0x40)                 # IC0 (abnormal termination) clear
    assert not (st0 & 0x08)                 # NR (not ready) clear


def test_write_protect_rejects_write_data(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, write_protected=True)
    tc.write_data(CMD_WRITE_DATA)
    for b in (0, 0, 0, 1, 2, 1, 0x1B, 0xFF):
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40                       # abnormal termination
    assert st1 & 0x02                       # NW (not writable)
