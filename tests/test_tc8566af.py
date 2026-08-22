"""Tests for the TC8566AF controller (functional model).

One test per Scenario in openspec/changes/archive/2026-08-21-add-tc8566af-fdc/specs/fdc-tc8566af/
spec.md (written before msx/fdc/tc8566af.py exists -- TDD red phase).
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage
from msx.fdc.tc8566af import (
    CMD_FORMAT,
    CMD_READ_DATA,
    CMD_RECALIBRATE,
    CMD_SEEK,
    CMD_SENSE_DEVICE_STATUS,
    CMD_SENSE_INTERRUPT_STATUS,
    CMD_WRITE_DATA,
    MSR_CB,
    MSR_DIO,
    MSR_RQM,
    TC8566AF,
)

_2DD = 737280

# HD_DS,C,H,R,N,EOT,GPL,DTL -- a valid READ/WRITE DATA parameter block.
_READ_WRITE_PARAMS = (0, 0, 0, 1, 2, 1, 0x1B, 0xFF)


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
    for b in _READ_WRITE_PARAMS:
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
    for b in _READ_WRITE_PARAMS:
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
    for b in _READ_WRITE_PARAMS:
        tc.write_data(b)
    for b in payload:
        tc.write_data(b)
    assert drive.image.read_sector(0) == payload
    for _ in range(7):  # drain WRITE DATA's Result Phase before the next command
        tc.read_data()

    tc.write_data(CMD_READ_DATA)
    for b in _READ_WRITE_PARAMS:
        tc.write_data(b)
    out = bytes(tc.read_data() for _ in range(SECTOR_SIZE))
    assert out == payload


def test_format_blanks_track_then_write_read_round_trips(tmp_path: Path) -> None:
    """CALL FORMAT's underlying protocol: 5 command params (HD_DS, N, SC, GPL,
    D) then SC*4 descriptor bytes (C,H,R,N per sector, discarded -- see
    _cmd_format's docstring), blanking every sector of the drive's current
    (track, side) to the D fill byte. Regression for FS-A1F's CALL FORMAT
    never completing (FORMAT, opcode 0x0D, was entirely unimplemented)."""
    tc, drive = _ctrl(tmp_path)
    sectors_per_track = 9

    tc.write_data(CMD_FORMAT)
    for b in (0x00, 0x02, sectors_per_track, 0x50, 0xF6):  # HD_DS,N,SC,GPL,D
        tc.write_data(b)
    for sector in range(1, sectors_per_track + 1):
        for b in (0, 0, sector, 2):  # C,H,R,N per sector (content ignored)
            tc.write_data(b)

    msr = tc.read_main_status()
    assert msr & MSR_DIO                    # Result Phase
    st0 = tc.read_data()
    assert not (st0 & 0x40)                 # IC0 (abnormal termination) clear
    for _ in range(6):                      # drain the rest of the Result Phase
        tc.read_data()

    assert drive.image.read_sector(0) == bytes([0xF6]) * SECTOR_SIZE

    payload = bytes(i & 0xFF for i in range(SECTOR_SIZE))
    tc.write_data(CMD_WRITE_DATA)
    for b in _READ_WRITE_PARAMS:
        tc.write_data(b)
    for b in payload:
        tc.write_data(b)
    for _ in range(7):
        tc.read_data()

    tc.write_data(CMD_READ_DATA)
    for b in _READ_WRITE_PARAMS:
        tc.write_data(b)
    out = bytes(tc.read_data() for _ in range(SECTOR_SIZE))
    assert out == payload


def test_format_no_disk_reports_not_ready(tmp_path: Path) -> None:
    tc = TC8566AF(drives=[DiskDrive()])     # empty drive
    tc.write_data(CMD_FORMAT)
    for b in (0x00, 0x02, 9, 0x50, 0xF6):
        tc.write_data(b)
    st0 = tc.read_data()
    assert st0 & 0x08                       # NR (not ready)
    assert st0 & 0x40                       # IC0 (abnormal termination)


def test_format_write_protected_disk_rejected(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, write_protected=True)
    tc.write_data(CMD_FORMAT)
    for b in (0x00, 0x02, 9, 0x50, 0xF6):
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40                       # IC0 (abnormal termination)
    assert st1 & 0x02                       # NW (not writable)


def test_recalibrate_seeks_selected_drive_to_track0(tmp_path: Path) -> None:
    tc, drive = _ctrl(tmp_path)
    drive.track = 30
    tc.write_data(CMD_RECALIBRATE)
    tc.write_data(0x00)                     # HD_DS -> drive 0
    assert drive.track == 0


def test_no_disk_reports_not_ready_on_sense_device_status(tmp_path: Path) -> None:
    tc = TC8566AF(drives=[DiskDrive()])     # empty drive
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


def test_recalibrate_against_nonexistent_drive_still_reports_seek_end() -> None:
    """A RECALIBRATE targeting a drive index beyond the configured drive
    count (e.g. a DISK ROM probing for a second drive the machine doesn't
    have) reports Seek End + Not Ready, not Abnormal Termination -- matching
    openMSX's DummyDrive.isTrack00() always returning false
    ("National_FS-5500F1 2nd drive detection depends on this"), which makes
    TC8566AF::doSeek's RECALIBRATE step the full 255-pulse limit before
    giving up (Seek End + Equipment Check, not Abnormal Termination) rather
    than aborting immediately. Reporting Abnormal Termination here (as this
    model used to) hung FS-A1F's boot: the DISK ROM's second-drive probe
    polls SENSE INTERRUPT STATUS in a tight loop waiting specifically for
    Seek End, which this model's own SENSE INTERRUPT STATUS retry-then-
    invalid fix (see test_no_disk_reports_not_ready_on_sense_device_status's
    neighbour) does not by itself provide -- Seek End never came, so it
    never stopped."""
    tc = TC8566AF(drives=[DiskDrive()])     # only drive 0 configured

    tc.write_data(CMD_RECALIBRATE)
    tc.write_data(0x01)                     # HD_DS -> drive 1 (nonexistent)
    tc.write_data(CMD_SENSE_INTERRUPT_STATUS)
    st0 = tc.read_data()
    assert st0 & 0x20                       # SE (seek end) set
    assert st0 & 0x08                       # NR (not ready) set
    assert not (st0 & 0x40)                 # IC0 (abnormal termination) clear


def test_write_protect_rejects_write_data(tmp_path: Path) -> None:
    tc, _drive = _ctrl(tmp_path, write_protected=True)
    tc.write_data(CMD_WRITE_DATA)
    for b in _READ_WRITE_PARAMS:
        tc.write_data(b)
    st0 = tc.read_data()
    st1 = tc.read_data()
    assert st0 & 0x40                       # abnormal termination
    assert st1 & 0x02                       # NW (not writable)
