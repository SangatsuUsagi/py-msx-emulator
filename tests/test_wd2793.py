"""Tests for the WD2793 controller (functional model)."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import SECTOR_SIZE
from msx.fdc.wd2793 import (
    BUSY,
    NOT_READY,
    RECORD_NOT_FOUND,
    S_DRQ,
    TRACK00,
    TRACK_BYTES,
    WD2793,
    WRITE_PROTECTED,
    Mode,
)
from msx.snapshot_schema import SchemaError
from tests.disk_factories import dsk_image
from tests.factories import restore_state
from tests.wd2793_helpers import finish_type1

_2DD = 737280


def _ctrl(tmp_path: Path, *, fill: int = 0x00, write_protected: bool = False):
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes([fill]) * _2DD)
    image = dsk_image(p, write_protected=write_protected)
    drive = DiskDrive(image)
    return WD2793(drive=drive), drive, image


def test_track_sector_register_round_trip(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(0x2A)
    wd.set_sector(0x07)
    assert wd.get_track() == 0x2A
    assert wd.get_sector() == 0x07


def test_restore_seeks_to_track0(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(20)
    drive.track = 20
    wd.set_command(0x00)  # RESTORE
    finish_type1(wd)
    assert wd.get_track() == 0
    assert drive.track == 0
    assert wd.get_status() & TRACK00
    assert wd.get_irq() is False  # get_status above cleared INTRQ


def test_seek_moves_to_data_register_track(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_data(15)
    wd.set_command(0x10)  # SEEK
    finish_type1(wd)
    assert wd.get_track() == 15
    assert drive.track == 15


def test_read_sector_streams_bytes_and_raises_intrq(tmp_path: Path) -> None:
    wd, _, image = _ctrl(tmp_path)
    payload = bytes((i * 7) & 0xFF for i in range(SECTOR_SIZE))
    image.write_sector(0, payload)  # track 0 side 0 sector 1 -> LSN 0
    wd.set_track(0)
    wd.set_sector(1)
    wd.set_command(0x80)  # READ SECTOR
    assert wd.get_status() & (BUSY | S_DRQ) == (BUSY | S_DRQ)
    out = bytes(wd.get_data() for _ in range(SECTOR_SIZE))
    assert out == payload
    assert wd.get_irq() is True             # INTRQ after last byte
    assert not (wd.get_status() & BUSY)     # transfer finished


def test_write_sector_persists_and_round_trips(tmp_path: Path) -> None:
    wd, _, image = _ctrl(tmp_path)
    payload = bytes((255 - (i & 0xFF)) for i in range(SECTOR_SIZE))
    wd.set_track(0)
    wd.set_sector(1)
    wd.set_command(0xA0)  # WRITE SECTOR
    for b in payload:
        wd.set_data(b)
    assert image.read_sector(0) == payload
    # read it back through the controller
    wd.set_command(0x80)
    out = bytes(wd.get_data() for _ in range(SECTOR_SIZE))
    assert out == payload


def test_write_track_blanks_track_to_e5(tmp_path: Path) -> None:
    wd, drive, image = _ctrl(tmp_path, fill=0x00)
    wd.set_data(1)
    wd.set_command(0x10)  # SEEK: WRITE TRACK formats the head's track
    finish_type1(wd)
    wd.set_command(0xF0)  # WRITE TRACK
    for _ in range(TRACK_BYTES):
        wd.set_data(0x4E)  # gap filler; content is ignored by the model
    assert not (wd.get_status() & BUSY)
    # track 1, side 0, sector 1 -> LSN 18
    assert image.read_sector(drive.lsn(1, 0, 1)) == b"\xe5" * SECTOR_SIZE


def test_record_not_found_for_missing_sector(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(79)
    wd.set_sector(99)  # no such sector on a 9-sector track
    wd.set_command(0x80)
    assert wd.get_status() & RECORD_NOT_FOUND
    assert wd.get_drq() is False


def test_record_not_found_for_missing_sector_on_write(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(79)
    wd.set_sector(99)  # no such sector on a 9-sector track
    wd.set_command(0xA0)  # WRITE SECTOR
    assert wd.get_status() & RECORD_NOT_FOUND
    assert wd.get_drq() is False


def test_write_protect_rejects_write_sector(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path, write_protected=True)
    wd.set_command(0xA0)
    assert wd.get_status() & WRITE_PROTECTED


def test_write_protect_rejects_write_track(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path, write_protected=True)
    wd.set_command(0xF0)
    assert wd.get_status() & WRITE_PROTECTED


def test_no_disk_reports_not_ready(tmp_path: Path) -> None:
    wd = WD2793(drive=DiskDrive())  # empty drive
    wd.set_command(0x80)
    assert wd.get_status() & NOT_READY


def test_status_read_clears_intrq(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_command(0x00)  # RESTORE -> raises INTRQ
    finish_type1(wd)
    assert wd.get_irq() is True
    wd.get_status()
    assert wd.get_irq() is False


def test_force_interrupt_aborts_busy_command(tmp_path: Path) -> None:
    wd, _, image = _ctrl(tmp_path)
    image.write_sector(0, b"\x11" * SECTOR_SIZE)
    wd.set_command(0x80)          # start a read (BUSY)
    assert wd.get_status() & BUSY
    wd.set_command(0xD0)          # FORCE INTERRUPT
    assert not (wd.get_status() & BUSY)
    assert wd._mode is Mode.IDLE


def test_multi_sector_flag_decoded_as_single(tmp_path: Path) -> None:
    """0x90 (m=1) currently behaves like a single-sector read (stubbed multi)."""
    wd, _, image = _ctrl(tmp_path)
    image.write_sector(0, b"\x5a" * SECTOR_SIZE)
    wd.set_track(0)
    wd.set_sector(1)
    wd.set_command(0x90)
    out = bytes(wd.get_data() for _ in range(SECTOR_SIZE))
    assert out == b"\x5a" * SECTOR_SIZE


def test_restore_with_missing_key_does_not_partially_mutate(tmp_path: Path) -> None:
    """restore() (save-state) reads every field before assigning any of
    them, so a missing key fails before earlier fields are touched."""
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(0x2A)
    wd.set_sector(0x07)
    pre = dict(wd.snapshot())

    snap = dict(pre)
    del snap["step_dir"]
    with pytest.raises(SchemaError, match="missing keys"):
        restore_state(wd, snap)

    assert dict(wd.snapshot()) == pre


# --- Type I head stepping (openMSX WD2793 / RealDrive) ---------------------


def test_seek_moves_the_head_by_the_register_difference(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(2)  # TRACK 2, head still at 0
    wd.set_data(5)
    wd.set_command(0x10)  # SEEK
    finish_type1(wd)
    assert wd.get_track() == 5
    assert drive.track == 3


def test_step_without_t_flag_moves_only_the_head(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(4)
    wd.set_command(0x40)  # STEP-IN, T clear
    finish_type1(wd)
    assert drive.track == 1
    assert wd.get_track() == 4


def test_step_repeats_the_direction_of_the_last_seek(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    drive.track = 5
    wd.set_track(5)
    wd.set_data(2)
    wd.set_command(0x10)  # SEEK out to 2
    finish_type1(wd)
    wd.set_command(0x30)  # STEP, T set
    finish_type1(wd)
    assert wd.get_track() == 1
    assert drive.track == 1


def test_step_out_at_head_track0_sets_track_to_0(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(3)
    wd.set_command(0x60)  # STEP-OUT, T clear
    finish_type1(wd)
    assert drive.track == 0
    assert wd.get_track() == 0


def test_step_in_past_track_255_wraps(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(255)
    wd.set_command(0x50)  # STEP-IN, T set
    finish_type1(wd)
    assert wd.get_track() == 0
    assert drive.track == 1


def test_step_in_stops_the_head_at_track_85(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_data(90)
    wd.set_command(0x10)  # SEEK past the last head position
    finish_type1(wd)
    assert wd.get_track() == 90
    assert drive.track == 85


# --- STATUS composition ------------------------------------------------------


def test_seek_with_no_disk_clears_track00_and_keeps_not_ready() -> None:
    wd = WD2793(drive=DiskDrive())  # empty drive
    wd.set_data(3)
    wd.set_command(0x10)  # SEEK
    finish_type1(wd)
    status = wd.get_status()
    assert status & NOT_READY
    assert not (status & TRACK00)


def test_force_interrupt_after_a_sector_read_shows_type1_status(
    tmp_path: Path,
) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_command(0x80)  # READ SECTOR, track 0 sector 1
    for _ in range(SECTOR_SIZE):
        wd.get_data()
    wd.set_command(0xD0)  # FORCE INTERRUPT
    assert wd.get_status() & TRACK00


# --- Type II/III use the head's track -----------------------------------------


def test_record_not_found_when_track_differs_from_the_head(
    tmp_path: Path,
) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(1)  # head still at 0
    wd.set_command(0x80)  # READ SECTOR
    assert wd.get_status() & RECORD_NOT_FOUND
    assert wd.get_drq() is False


def test_read_address_on_a_track_with_no_sectors_is_record_not_found(
    tmp_path: Path,
) -> None:
    wd, _, _ = _ctrl(tmp_path)  # 2DD: tracks 0-79
    wd.set_data(82)
    wd.set_command(0x10)  # SEEK past the last track of the image
    finish_type1(wd)
    wd.set_command(0xC0)  # READ ADDRESS
    assert wd.get_status() & RECORD_NOT_FOUND
    assert wd.get_drq() is False


# --- FORCE INTERRUPT immediate latch ------------------------------------------


def test_immediate_interrupt_holds_intrq_until_d0(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_command(0xD8)
    assert wd.get_irq() is True
    wd.get_status()
    assert wd.get_irq() is True  # a STATUS read does not clear the latch
    wd.set_command(0x00)  # RESTORE
    finish_type1(wd)
    wd.get_status()
    assert wd.get_irq() is True
    wd.set_command(0xD0)
    assert wd.get_irq() is False


def test_type1_status_reports_a_write_protected_disk(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path, write_protected=True)
    wd.set_command(0x00)  # RESTORE
    finish_type1(wd)
    assert wd.get_status() == (TRACK00 | WRITE_PROTECTED)
