"""Snapshot format 16: RTC and mouse state (machine-state "Snapshot includes RTC
state" / "Snapshot includes mouse state")."""
from __future__ import annotations

import pytest

from msx.boundary import RtcTime
from msx.machine import Machine
from msx.mouse import MouseDevice
from msx.psg import JoystickPort
from msx.state import SnapshotMismatchError, _restore_snapshot, _snapshot_from_machine
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes([0xF3, 0x18, 0xFE]) + bytes(0x8000 - 3)   # DI; JR $
_EXTROM = bytes(0x4000)
_SECOND = 3_579_545


def _rtc_read(machine: Machine, block: int, reg: int) -> int:
    machine.io.write_port(0xB4, 13)
    machine.io.write_port(0xB5, 0x08 | block)
    machine.io.write_port(0xB4, reg)
    return machine.io.read_port(0xB5) & 0x0F


def _rtc_write(machine: Machine, block: int, reg: int, value: int) -> None:
    machine.io.write_port(0xB4, 13)
    machine.io.write_port(0xB5, 0x08 | block)
    machine.io.write_port(0xB4, reg)
    machine.io.write_port(0xB5, value)


def _msx2(start: RtcTime) -> Machine:
    machine = make_machine_msx2(_ROM, _EXTROM)
    assert machine.rtc is not None
    machine.rtc.start = start
    return machine


def test_rtc_time_and_cmos_ram_round_trip() -> None:
    saved = _msx2(RtcTime(2026, 10, 23, 12, 0, 0))
    saved.cycle_count += 10 * _SECOND                 # 10 emulated seconds
    _rtc_write(saved, 2, 3, 0x9)                      # a CMOS RAM nibble
    snap = _snapshot_from_machine(saved)

    loaded = _msx2(RtcTime(1990, 1, 1, 0, 0, 0))      # different start time
    loaded.cycle_count = 123_456                      # and a different clock
    _restore_snapshot(loaded, snap)

    for block in (0, 2):
        for reg in range(13):
            assert _rtc_read(loaded, block, reg) == _rtc_read(saved, block, reg)
    assert (_rtc_read(loaded, 0, 0), _rtc_read(loaded, 0, 1)) == (0, 1)   # 12:00:10

    loaded.cycle_count += _SECOND                     # and keeps advancing
    assert _rtc_read(loaded, 0, 0) == 1


def _with_mouse(machine: Machine) -> MouseDevice:
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    return mouse


def test_mouse_mid_scan_round_trips() -> None:
    saved = make_machine(_ROM)
    saved_mouse = _with_mouse(saved)
    saved_mouse.add_motion(40, -7)
    saved.cycle_count = 1_000
    for level in (1, 0, 1):                           # advance part-way through a scan
        saved_mouse.write_pin8(level, saved.cycle_count)
        saved.cycle_count += 100
    snap = _snapshot_from_machine(saved)

    loaded = make_machine(_ROM)
    loaded_mouse = _with_mouse(loaded)
    loaded.cycle_count = saved.cycle_count + 50_000_000   # unrelated clock value
    _restore_snapshot(loaded, snap)

    # The same pin-8 toggles, at the same distances, read the same nibbles.
    nibbles_saved, nibbles_loaded = [], []
    for step in range(8):
        level = step & 1
        saved_mouse.write_pin8(level, saved.cycle_count + 100 * (step + 1))
        loaded_mouse.write_pin8(level, loaded.cycle_count + 100 * (step + 1))
        nibbles_saved.append(saved_mouse.read())
        nibbles_loaded.append(loaded_mouse.read())
    assert nibbles_loaded == nibbles_saved
    assert any(n & 0x0F for n in nibbles_saved)       # motion was reported


def test_mouse_wiring_mismatch_is_rejected() -> None:
    saved = make_machine(_ROM)
    _with_mouse(saved)
    snap = _snapshot_from_machine(saved)
    with pytest.raises(SnapshotMismatchError, match="mouse"):
        _restore_snapshot(make_machine(_ROM), snap)


def test_rtc_state_with_an_invalid_date_is_rejected() -> None:
    saved = _msx2(RtcTime(2026, 1, 1, 0, 0, 0))
    snap = _snapshot_from_machine(saved)
    assert snap.rtc is not None
    snap.rtc["start"] = [2026, 2, 30, 0, 0, 0]
    with pytest.raises(ValueError, match="rtc"):
        _restore_snapshot(_msx2(RtcTime(2026, 1, 1, 0, 0, 0)), snap)


def _with_mouse_machine() -> Machine:
    machine = make_machine(_ROM)
    _with_mouse(machine)
    return machine


@pytest.mark.parametrize(
    ("edit", "field"),
    [
        ({"status": 0x31}, "mouse.status"),          # a data-nibble bit set
        ({"cur_x_rel": 32768}, "mouse.cur_x_rel"),   # beyond the pending-motion bound
        ({"cur_y_rel": -32768}, "mouse.cur_y_rel"),
        ({"phase": 5, "x_rel": 3}, "phase 5"),       # alternate cycle with a delta
    ],
)
def test_a_mouse_state_the_device_cannot_hold_is_rejected(
    edit: dict[str, int], field: str
) -> None:
    saved = make_machine(_ROM)
    _with_mouse(saved)
    snap = _snapshot_from_machine(saved)
    assert snap.mouse is not None
    snap.mouse.update(edit)
    with pytest.raises(ValueError, match=field):
        _restore_snapshot(_with_mouse_machine(), snap)


def test_a_resynced_scan_may_hold_a_delta_in_its_last_phase() -> None:
    # PHASE_YLOW2 after a timeout keeps the main cycle's delta; it must load.
    saved = make_machine(_ROM)
    _with_mouse(saved)
    snap = _snapshot_from_machine(saved)
    assert snap.mouse is not None
    snap.mouse.update({"phase": 7, "x_rel": 3})
    _restore_snapshot(_with_mouse_machine(), snap)
