"""WD2793 Type I timing, head load and verify (openspec fdc-wd2793:
"Type I step timing", "Head load", "Type I verify", and the FORCE
INTERRUPT scenarios for a running Type I command).

Expected STATUS bytes come from a BASIC test run on a real HB-F1XD
(2026-11-11, no disk in the drive): C1 right after RESTORE, E4 after RESTORE
with h and after a FORCE INTERRUPT, E5 for a verify with no disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import BUSY, HEAD_LOADED, SECTOR_SIZE, SEEK_ERROR, WD2793
from tests.disk_factories import dsk_image
from tests.wd2793_helpers import finish_type1, ms, wait_ms

_2DD = 737280


def _with_disk(tmp_path: Path) -> tuple[WD2793, DiskDrive]:
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes(_2DD))
    drive = DiskDrive(dsk_image(p))
    wd = WD2793(drive=drive)
    wd.get_status()  # clear the power-on RESTORE's INTRQ
    return wd, drive


def _no_disk() -> tuple[WD2793, DiskDrive]:
    drive = DiskDrive()
    wd = WD2793(drive=drive)
    wd.get_status()
    return wd, drive


def _disk(tmp_path: Path) -> DskDiskImage:
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes(_2DD))
    return dsk_image(p)


def _at(wd: WD2793, start: int, milliseconds: float) -> None:
    """Set the controller's clock to `milliseconds` after cycle `start`."""
    wd._clock.cycle = start + ms(milliseconds)


# --- Type I step timing -----------------------------------------------------


def test_restore_is_busy_right_after_the_write() -> None:
    wd, drive = _no_disk()
    drive.track = 5
    wd.set_command(0x00)  # RESTORE, rate 0
    assert wd.get_irq() is False
    assert wd.get_status() == 0xC1  # NOT_READY | WPRT | BUSY


def test_restore_ends_after_one_step_time_per_track(tmp_path: Path) -> None:
    wd, drive = _with_disk(tmp_path)
    drive.track = 5
    wd.set_track(5)
    start = wd._clock.cycle
    wd.set_command(0x00)  # RESTORE, rate 0: 5 steps of 6 ms
    _at(wd, start, 29)
    assert wd.get_status() & BUSY
    _at(wd, start, 31)
    assert wd.get_irq() is True
    assert not (wd.get_status() & BUSY)
    assert drive.track == 0
    assert wd.get_track() == 0


def test_rate_bits_set_the_step_time(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    start = wd._clock.cycle
    wd.set_command(0x53)  # STEP-IN with T, rate 3: 30 ms
    _at(wd, start, 29)
    assert wd.get_status() & BUSY
    _at(wd, start, 31)
    assert not (wd.get_status() & BUSY)


def test_a_seek_moves_track_one_step_at_a_time(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_data(5)
    start = wd._clock.cycle
    wd.set_command(0x10)  # SEEK, rate 0: step pulses at 0, 6 and 12 ms
    _at(wd, start, 13)
    assert wd.get_track() == 3
    assert wd.get_status() & BUSY


def test_a_seek_to_the_current_track_ends_at_once(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_track(4)
    wd.set_data(4)
    wd.set_command(0x10)  # SEEK
    assert wd.get_irq() is True
    assert not (wd.get_status() & BUSY)


# --- FORCE INTERRUPT during a Type I command --------------------------------


def test_force_interrupt_stops_a_seek_between_steps(tmp_path: Path) -> None:
    wd, drive = _with_disk(tmp_path)
    wd.set_data(5)
    start = wd._clock.cycle
    wd.set_command(0x10)  # SEEK, rate 0
    _at(wd, start, 7)     # step pulses at 0 and 6 ms given
    wd.set_command(0xD0)
    assert not (wd.get_status() & BUSY)
    assert wd.get_track() == 2
    assert drive.track == 2
    _at(wd, start, 100)
    assert wd.get_track() == 2
    assert drive.track == 2


def test_force_interrupt_keeps_the_head_loaded() -> None:
    wd, _ = _no_disk()
    wd.set_command(0x08)  # RESTORE with h
    finish_type1(wd)
    wd.set_command(0xD0)
    assert wd.get_status() & HEAD_LOADED


# --- Head load --------------------------------------------------------------


def test_restore_with_h_loads_the_head() -> None:
    wd, _ = _no_disk()
    wd.set_command(0x08)  # RESTORE with h
    finish_type1(wd)
    assert wd.get_status() == 0xE4  # NOT_READY | WPRT | HEAD_LOADED | TRACK00


def test_a_type1_command_without_h_or_v_unloads_the_head(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_command(0x08)  # RESTORE with h
    finish_type1(wd)
    wd.set_command(0x40)  # STEP-IN, no h, no V
    assert not (wd.get_status() & HEAD_LOADED)


def test_a_sector_read_loads_the_head(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_track(0)
    wd.set_sector(1)
    wd.set_command(0x80)  # READ SECTOR
    for _ in range(SECTOR_SIZE):
        wd.get_data()
    wd.set_command(0xD0)
    assert wd.get_status() & HEAD_LOADED


def test_the_head_unloads_3_s_after_the_controller_is_idle(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_command(0x08)  # RESTORE with h, head at 0: ends at once
    finish_type1(wd)
    end = wd._clock.cycle
    _at(wd, end, 2900)
    assert wd.get_status() & HEAD_LOADED
    _at(wd, end, 3100)
    assert not (wd.get_status() & HEAD_LOADED)


def test_with_no_disk_the_head_stays_loaded() -> None:
    wd, _ = _no_disk()
    wd.set_command(0x08)  # RESTORE with h
    finish_type1(wd)
    wait_ms(wd, 10000)
    assert wd.get_status() & HEAD_LOADED  # no index pulses to count


def test_a_disk_inserted_after_a_long_idle_unloads_the_head_3_s_later(
    tmp_path: Path,
) -> None:
    iface, _, _ = _two_drive_iface()
    wd = iface.controller
    wd.get_status()
    iface.write_mem(0x7FF8, 0x08)  # RESTORE with h on empty drive A
    finish_type1(wd)
    wait_ms(wd, 10000)
    assert wd.get_status() & HEAD_LOADED
    iface.swap(0, _disk(tmp_path))
    start = wd._clock.cycle
    _at(wd, start, 2900)
    assert wd.get_status() & HEAD_LOADED
    _at(wd, start, 3100)
    assert not (wd.get_status() & HEAD_LOADED)


# --- Type I verify ----------------------------------------------------------


def test_verify_with_no_disk_never_ends() -> None:
    wd, _ = _no_disk()
    wd.set_command(0x04)  # RESTORE with V
    wait_ms(wd, 10000)
    assert wd.get_status() == 0xE5  # NOT_READY | WPRT | HEAD_LOADED | TRACK00 | BUSY
    wd.set_command(0xD0)
    assert wd.get_status() == 0xE4


def test_verify_on_a_matching_track_ends_after_the_settle_time(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_data(2)
    start = wd._clock.cycle
    wd.set_command(0x14)  # SEEK with V: 2 steps of 6 ms, then 30 ms settle
    _at(wd, start, 41)
    assert wd.get_status() & BUSY
    _at(wd, start, 43)
    status = wd.get_status()
    assert not (status & BUSY)
    assert not (status & SEEK_ERROR)
    assert status & HEAD_LOADED


def test_verify_on_a_mismatched_track_sets_seek_error(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_track(3)  # head at 0: after one step the head's track is 1
    start = wd._clock.cycle
    wd.set_command(0x44)  # STEP-IN with V, no T
    _at(wd, start, 900)
    assert wd.get_status() & BUSY
    _at(wd, start, 1100)
    assert wd.get_irq() is True
    status = wd.get_status()
    assert not (status & BUSY)
    assert status & SEEK_ERROR


def test_abort_during_a_seek_starts_the_idle_time(tmp_path: Path) -> None:
    # A disk swap (abort) stops a seek; the head-unload time counts from it,
    # not from the end of the command before the seek.
    wd, drive = _with_disk(tmp_path)
    wait_ms(wd, 5000)
    wd.set_data(5)
    start = wd._clock.cycle
    wd.set_command(0x18)  # SEEK with h, rate 0: step pulses at 0, 6, 12 ms
    _at(wd, start, 13)    # no access since the write: two steps not caught up
    wd.abort()
    assert drive.track == 3  # the due steps ran before the stop
    _at(wd, start, 13 + 2900)
    assert wd.get_status() & HEAD_LOADED
    _at(wd, start, 13 + 3100)
    assert not (wd.get_status() & HEAD_LOADED)


def _two_drive_iface() -> tuple[SonyPhilipsInterface, DiskDrive, DiskDrive]:
    drive_a, drive_b = DiskDrive(), DiskDrive()
    iface = SonyPhilipsInterface(WD2793(), [drive_a, drive_b], disk_rom=bytes(16384))
    finish_type1(iface.controller)  # the power-on RESTORE
    return iface, drive_a, drive_b


def test_a_disk_swap_during_a_verify_stops_it_with_no_error(tmp_path: Path) -> None:
    iface, _, _ = _two_drive_iface()
    wd = iface.controller
    wd.get_status()
    iface.write_mem(0x7FF8, 0x04)  # RESTORE with V, drive A empty
    wait_ms(wd, 10000)
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes(_2DD))
    iface.swap(0, dsk_image(p))
    assert wd.get_irq() is False
    status = wd.get_status()
    assert not (status & BUSY)
    assert not (status & SEEK_ERROR)


def test_selecting_another_drive_during_a_restore() -> None:
    iface, drive_a, drive_b = _two_drive_iface()
    wd = iface.controller
    drive_a.track = 5
    drive_b.track = 3
    iface.write_mem(0x7FF9, 5)     # TRACK 5
    iface.write_mem(0x7FF8, 0x00)  # RESTORE on drive A, rate 0
    wait_ms(wd, 100)               # all steps due, none caught up yet
    iface.write_mem(0x7FFD, 0x01)  # select drive B
    assert drive_a.track == 0
    assert drive_b.track == 3


def test_selecting_a_drive_with_a_disk_during_a_verify_starts_the_id_search(
    tmp_path: Path,
) -> None:
    iface, _, drive_b = _two_drive_iface()
    wd = iface.controller
    drive_b.mount(_disk(tmp_path))  # head track 0
    wd.get_status()
    iface.write_mem(0x7FF8, 0x04)  # RESTORE with V on empty drive A
    wait_ms(wd, 10000)
    assert wd.get_status() == 0xE5  # the settle time ended with no disk
    iface.write_mem(0x7FFD, 0x01)  # select drive B
    assert wd.get_irq() is True
    status = wd.get_status()
    assert not (status & BUSY)
    assert not (status & SEEK_ERROR)
    assert wd.get_irq() is False  # the search does not run again


# --- Save state -------------------------------------------------------------


def _reload(wd: WD2793, drive: DiskDrive, cycle: int) -> WD2793:
    """Apply wd's snapshot to a new controller whose clock is at `cycle`."""
    state = wd.snapshot()
    other = WD2793()
    other._clock.cycle = cycle
    other.apply_state(other.parse_state(state, "wd"))
    other.drive = drive
    return other


def test_a_save_during_a_seek_gives_the_same_result_as_no_save(tmp_path: Path) -> None:
    wd, drive = _with_disk(tmp_path)
    wd.set_data(10)
    start = wd._clock.cycle
    wd.set_command(0x10)  # SEEK, rate 0: 10 steps of 6 ms
    _at(wd, start, 50)    # no access since the write
    other = _reload(wd, drive, 123456789)
    other._clock.cycle += ms(15)
    assert other.get_track() == 10
    assert drive.track == 10
    assert not (other.get_status() & BUSY)


def test_a_verify_waiting_for_a_disk_survives_a_save() -> None:
    wd, drive = _no_disk()
    wd.set_command(0x04)  # RESTORE with V
    wait_ms(wd, 10000)
    assert wd.get_status() == 0xE5
    state = wd.snapshot()
    assert state["type1_phase"] == 4  # NO_INDEX
    assert state["type1_cycles_left"] == 0
    other = _reload(wd, drive, 987654321)
    wait_ms(other, 10000)
    assert other.get_status() == 0xE5


def test_no_running_phase_saves_no_cycles_left(tmp_path: Path) -> None:
    wd, _ = _with_disk(tmp_path)
    wd.set_data(5)
    wd.set_command(0x10)  # SEEK
    wd.set_command(0xD0)  # stops it during a step time
    state = wd.snapshot()
    assert state["type1_phase"] == 0
    assert state["type1_cycles_left"] == 0


def test_the_head_unload_time_survives_a_save(tmp_path: Path) -> None:
    wd, drive = _with_disk(tmp_path)
    wd.set_command(0x08)  # RESTORE with h, ends at once
    finish_type1(wd)
    wait_ms(wd, 2000)
    other = _reload(wd, drive, 555555555)
    wait_ms(other, 900)
    assert other.get_status() & HEAD_LOADED
    wait_ms(other, 200)
    assert not (other.get_status() & HEAD_LOADED)


def test_an_out_of_range_type1_phase_is_rejected() -> None:
    wd, _ = _no_disk()
    state = dict(wd.snapshot())
    state["type1_phase"] = 5
    with pytest.raises(ValueError, match="type1_phase"):
        wd.parse_state(state, "wd")
