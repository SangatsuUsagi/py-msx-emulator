"""Tests for FDC save/load state (WD2793/Sony and TC8566AF connection styles)."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import REG_DRIVE, FloppyDiskState
from msx.fdc.tc8566af import TC8566AF
from msx.fdc.wd2793 import WD2793
from msx.machine_loader import MachineSpec, _FdcDef, _RomEntry, build_machine
from msx.state import load_state, save_state

_2DD = 737280
_RGB_MSX2 = bytearray(256 * 192 * 3)


@pytest.fixture()
def saves_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


def _make_roms(tmp_path: Path) -> None:
    (tmp_path / "main.rom").write_bytes(bytes([0xC9]) + bytes(32768 - 1))
    (tmp_path / "sub.rom").write_bytes(bytes([0xAB]) + bytes(16384 - 1))
    (tmp_path / "disk.rom").write_bytes(bytes([0xC3]) + bytes(16384 - 1))


def _fdc_spec(tmp_path: Path, *, controller: str, connection_style: str) -> MachineSpec:
    return MachineSpec(
        name="test_fdc_state",
        generation="msx2",
        rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=False,
        flat_ram_subslot=3,
        flat_ram_size_kb=64,
        fdc=_FdcDef(
            disk_rom_entry=_RomEntry(file="disk.rom", size_kb=16, pages=[1]),
            controller=controller,
            connection_style=connection_style,
            drives=1,
        ),
    )


def _wd2793_spec(tmp_path: Path) -> MachineSpec:
    return _fdc_spec(tmp_path, controller="wd2793", connection_style="sony")


def _tc8566af_spec(tmp_path: Path) -> MachineSpec:
    return _fdc_spec(tmp_path, controller="tc8566af", connection_style="tc8566af")


def _make_disk(path: Path, fill: int = 0x00) -> None:
    path.write_bytes(bytes([fill]) * _2DD)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_wd2793_roundtrip(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    drive = machine.fdc.drives[0]
    drive.track = 5
    drive.side = 1
    drive.disk_changed = True
    ctrl = cast(WD2793, machine.fdc.controller)
    ctrl.track_reg = 5
    ctrl.sector_reg = 3
    ctrl.status_reg = 0x03

    save_state(machine, _RGB_MSX2, "test")

    drive.track = 0
    drive.side = 0
    drive.disk_changed = False
    ctrl.track_reg = 0
    ctrl.sector_reg = 1
    ctrl.status_reg = 0

    load_state(machine)

    assert drive.track == 5
    assert drive.side == 1
    assert drive.disk_changed is True
    assert ctrl.track_reg == 5
    assert ctrl.sector_reg == 3
    assert ctrl.status_reg == 0x03


def test_wd2793_restore_reselects_no_drive(tmp_path: Path, saves_dir: Path) -> None:
    """drive_reg bits 1:0 = 0b11 ("no drive selected") must survive a
    save/load round-trip -- restore must not leave controller.drive
    pointing at drives[0] (FloppyDiskState.__init__'s construction-time
    default) just because that's what a fresh machine starts with."""
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    machine.fdc.write_mem(REG_DRIVE, 0x03)
    ctrl = cast(WD2793, machine.fdc.controller)
    assert ctrl.drive is None

    save_state(machine, _RGB_MSX2, "test")

    ctrl.drive = machine.fdc.drives[0]

    load_state(machine)

    assert ctrl.drive is None


def test_tc8566af_roundtrip(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine(_tc8566af_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    drive = machine.fdc.drives[0]
    drive.track = 7
    drive.side = 1
    ctrl = cast(TC8566AF, machine.fdc.controller)
    ctrl.control_reg0 = 0x11

    save_state(machine, _RGB_MSX2, "test")

    drive.track = 0
    drive.side = 0
    ctrl.control_reg0 = 0

    load_state(machine)

    assert drive.track == 7
    assert drive.side == 1
    assert ctrl.control_reg0 == 0x11


# ---------------------------------------------------------------------------
# Mismatch rejection
# ---------------------------------------------------------------------------

def test_different_mounted_disk_is_rejected(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk_a = tmp_path / "a.dsk"
    _make_disk(dsk_a, fill=0xAA)
    machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk_a)
    save_state(machine, _RGB_MSX2, "test")

    dsk_b = tmp_path / "b.dsk"
    _make_disk(dsk_b, fill=0xBB)
    assert machine.fdc is not None
    machine.fdc.mount(DskDiskImage(dsk_b), drive=0)

    with pytest.raises(ValueError, match="(?i)disk"):
        load_state(machine)


def test_unmounted_vs_mounted_is_rejected(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(machine, _RGB_MSX2, "test")

    assert machine.fdc is not None
    machine.fdc.mount(None, drive=0)

    with pytest.raises(ValueError, match="(?i)disk"):
        load_state(machine)


def test_fdc_kind_mismatch_is_rejected(tmp_path: Path, saves_dir: Path) -> None:
    """A state saved on a WD2793/Sony (HB-F1XD-style) machine must be
    rejected when loaded into a TC8566AF (FS-A1F-style) machine, even if
    both happen to have a disk with the same content mounted."""
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    wd_machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(wd_machine, _RGB_MSX2, "test")

    tc_machine = build_machine(_tc8566af_spec(tmp_path), fdd1=dsk)
    with pytest.raises(ValueError, match="(?i)kind"):
        load_state(tc_machine)


def test_fdc_unwired_machine_rejects_fdc_state(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    fdc_machine = build_machine(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(fdc_machine, _RGB_MSX2, "test")

    no_fdc_spec = _wd2793_spec(tmp_path)
    no_fdc_spec.fdc = None
    no_fdc_machine = build_machine(no_fdc_spec)
    assert no_fdc_machine.fdc is None

    with pytest.raises(ValueError, match="(?i)fdc"):
        load_state(no_fdc_machine)


# ---------------------------------------------------------------------------
# Restore atomicity (openspec/changes/2026-09-01-restore-atomicity)
# ---------------------------------------------------------------------------

def test_floppy_disk_state_drive_count_mismatch_rejected_before_restore() -> None:
    """FloppyDiskState.restore() must reject a drive-count mismatch before
    restoring the controller or any drive, not partway through the
    drive-restore loop."""
    drive = DiskDrive()
    controller = WD2793(drive=drive)
    state = FloppyDiskState(controller, drives=[drive])
    drive.track = 5
    controller.track_reg = 9

    snap = dict(state.snapshot())
    snap["drives"] = list(snap["drives"]) + [dict(snap["drives"][0])]  # type: ignore[list-item]

    with pytest.raises(ValueError, match="(?i)drive count"):
        state.restore(snap)

    assert drive.track == 5
    assert controller.track_reg == 9
