"""Tests for FDC save/load state (WD2793/Sony and TC8566AF connection styles)."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from host.machine_config import build_machine_from_spec
from host.state_files import save_state
from msx.boundary import RgbFrame
from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import REG_DRIVE, FloppyDiskState
from msx.fdc.tc8566af import TC8566AF
from msx.fdc.wd2793 import WD2793
from msx.machine_spec import FdcDef, MachineSpec, RomEntry
from tests.disk_factories import dsk_image
from tests.factories import load_state_file, restore_state

_2DD = 737280
_RGB_MSX2 = RgbFrame(256, 192, bytes(256 * 192 * 3))


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
        main_rom_entry=RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=False,
        flat_ram_subslot=3,
        flat_ram_size_kb=64,
        fdc=FdcDef(
            disk_rom_entry=RomEntry(file="disk.rom", size_kb=16, pages=[1]),
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
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
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

    load_state_file(machine)

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
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    machine.fdc.write_mem(REG_DRIVE, 0x03)
    ctrl = cast(WD2793, machine.fdc.controller)
    assert ctrl.drive is None

    save_state(machine, _RGB_MSX2, "test")

    ctrl.drive = machine.fdc.drives[0]

    load_state_file(machine)

    assert ctrl.drive is None


def test_tc8566af_roundtrip(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine_from_spec(_tc8566af_spec(tmp_path), fdd1=dsk)
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

    load_state_file(machine)

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
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk_a)
    save_state(machine, _RGB_MSX2, "test")

    dsk_b = tmp_path / "b.dsk"
    _make_disk(dsk_b, fill=0xBB)
    assert machine.fdc is not None
    machine.fdc.mount(dsk_image(dsk_b), drive=0)

    with pytest.raises(ValueError, match="(?i)disk"):
        load_state_file(machine)


def test_the_snapshot_holds_no_disk_file_name(tmp_path: Path, saves_dir: Path) -> None:
    # machine-state "The snapshot holds no disk file name".
    import hashlib
    import json

    _make_roms(tmp_path)
    dsk = tmp_path / "named_game.dsk"
    _make_disk(dsk, 0x3C)
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    data = machine.save_state()
    drive = json.loads(data)["fdc"]["drives"][0]
    assert drive["disk_size"] == _2DD
    assert drive["disk_sha1"] == hashlib.sha1(dsk.read_bytes()).hexdigest()
    assert "disk_path" not in drive
    assert b"named_game" not in data


def test_the_same_content_from_another_file_is_accepted(
    tmp_path: Path, saves_dir: Path
) -> None:
    # machine-state "The same content from another file is accepted".
    _make_roms(tmp_path)
    original, copy = tmp_path / "a.dsk", tmp_path / "copy_of_a.dsk"
    _make_disk(original, 0x11)
    _make_disk(copy, 0x11)
    saved = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=original)
    assert saved.fdc is not None
    saved.fdc.drives[0].track = 7
    state_path = save_state(saved, _RGB_MSX2, "test")

    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=copy)
    load_state_file(machine, state_path)
    assert machine.fdc is not None and machine.fdc.drives[0].track == 7


def test_unmounted_vs_mounted_is_rejected(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(machine, _RGB_MSX2, "test")

    assert machine.fdc is not None
    machine.fdc.mount(None, drive=0)

    with pytest.raises(ValueError, match="(?i)disk"):
        load_state_file(machine)


def test_fdc_kind_mismatch_is_rejected(tmp_path: Path, saves_dir: Path) -> None:
    """A state saved on a WD2793/Sony (HB-F1XD-style) machine must be
    rejected when loaded into a TC8566AF (FS-A1F-style) machine, even if
    both happen to have a disk with the same content mounted."""
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    wd_machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(wd_machine, _RGB_MSX2, "test")

    tc_machine = build_machine_from_spec(_tc8566af_spec(tmp_path), fdd1=dsk)
    with pytest.raises(ValueError, match="(?i)kind"):
        load_state_file(tc_machine)


def test_fdc_unwired_machine_rejects_fdc_state(tmp_path: Path, saves_dir: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    fdc_machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(fdc_machine, _RGB_MSX2, "test")

    no_fdc_spec = _wd2793_spec(tmp_path)
    no_fdc_spec.fdc = None
    no_fdc_machine = build_machine_from_spec(no_fdc_spec)
    assert no_fdc_machine.fdc is None

    with pytest.raises(ValueError, match="(?i)fdc"):
        load_state_file(no_fdc_machine)


# ---------------------------------------------------------------------------
# Restore atomicity (openspec/changes/2026-09-01-restore-atomicity)
# ---------------------------------------------------------------------------

def test_floppy_disk_state_drive_count_mismatch_rejected_before_restore() -> None:
    """restore_state(FloppyDiskState, ) must reject a drive-count mismatch before
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
        restore_state(state, snap)

    assert drive.track == 5
    assert controller.track_reg == 9


def test_load_parses_each_producer_once_and_hashes_each_disk_once(
    tmp_path: Path, saves_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # machine-state "A load parses each producer object once".
    from msx.psg import PSG

    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk)
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    save_state(machine, _RGB_MSX2, "test")

    counts = {"sha1": 0, "fdc": 0, "psg": 0, "mapper": 0}
    real_sha1 = DskDiskImage.content_sha1
    real_fdc_parse = type(machine.fdc).parse_state
    real_psg_parse = PSG.parse_synth_state
    mapper_cls = type(machine.memory.mapper1)
    real_mapper_parse = mapper_cls.parse_state

    def sha1(self):
        counts["sha1"] += 1
        return real_sha1(self)

    def fdc_parse(self, obj, label):
        counts["fdc"] += 1
        return real_fdc_parse(self, obj, label)

    def psg_parse(self, obj, label):
        counts["psg"] += 1
        return real_psg_parse(self, obj, label)

    def mapper_parse(self, obj, label):
        counts["mapper"] += 1
        return real_mapper_parse(self, obj, label)

    monkeypatch.setattr(DskDiskImage, "content_sha1", sha1)
    monkeypatch.setattr(type(machine.fdc), "parse_state", fdc_parse)
    monkeypatch.setattr(PSG, "parse_synth_state", psg_parse)
    monkeypatch.setattr(mapper_cls, "parse_state", mapper_parse)

    load_state_file(machine)

    assert counts["fdc"] == 1
    assert counts["psg"] == 1
    assert counts["sha1"] == 1  # one mounted disk
    # slot 1 and slot 2 each hold a mapper; count parses of this class's instances
    same_class = int(type(machine.memory.mapper2) is mapper_cls) + 1
    assert counts["mapper"] == same_class


@pytest.mark.parametrize("case", ["other_disk", "other_kind"])
def test_fdc_identity_mismatch_is_reported_as_an_fdc_state_load_error(
    tmp_path: Path, saves_dir: Path, case: str
) -> None:
    """The FDC's own SnapshotMismatchError reaches the caller wrapped as
    StateLoadError("fdc"), with the mismatch chained as its cause."""
    from msx.snapshot_schema import SnapshotMismatchError
    from msx.state import StateLoadError

    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    _make_disk(dsk, fill=0xAA)
    machine = build_machine_from_spec(_wd2793_spec(tmp_path), fdd1=dsk)
    save_state(machine, _RGB_MSX2, "test")

    if case == "other_disk":
        other = tmp_path / "other.dsk"
        _make_disk(other, fill=0xBB)
        assert machine.fdc is not None
        machine.fdc.mount(dsk_image(other), drive=0)
        target = machine
    else:
        target = build_machine_from_spec(_tc8566af_spec(tmp_path), fdd1=dsk)

    with pytest.raises(StateLoadError) as exc_info:
        load_state_file(target)
    assert exc_info.value.producer == "fdc"
    assert isinstance(exc_info.value.__cause__, SnapshotMismatchError)
