"""Tests for the FDC's parse_state() / apply_state(): the whole fdc_state
subtree (connection style, controller, every drive) is validated before
anything is assigned (openspec/changes/producer-state-validation,
openspec/changes/producer-state-single-parse)."""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import (
    REG_DATA,
    REG_DRIVE,
    REG_SIDE,
    REG_STATUS_CMD,
    REG_TRACK,
    TC_REG_CONTROL0,
    TC_REG_DATA,
    FloppyDisk,
    FloppyDiskParsed,
)
from msx.fdc.tc8566af import CMD_READ_DATA, CMD_SEEK
from msx.machine_loader import MachineSpec, _FdcDef, _RomEntry, build_machine
from msx.snapshot_schema import SchemaError
from msx.state import _from_jsonable, _to_jsonable

_2DD = 737280
_KINDS = ["wd2793", "tc8566af"]


def _make_roms(tmp_path: Path) -> None:
    (tmp_path / "main.rom").write_bytes(bytes([0xC9]) + bytes(32768 - 1))
    (tmp_path / "sub.rom").write_bytes(bytes([0xAB]) + bytes(16384 - 1))
    (tmp_path / "disk.rom").write_bytes(bytes([0xC3]) + bytes(16384 - 1))


def _spec(tmp_path: Path, kind: str) -> MachineSpec:
    style = "sony" if kind == "wd2793" else "tc8566af"
    return MachineSpec(
        name="test_fdc_parse_state",
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
            controller=kind,
            connection_style=style,
            drives=2,
        ),
    )


def _fdc(tmp_path: Path, kind: str) -> FloppyDisk:
    _make_roms(tmp_path)
    dsk_a = tmp_path / "a.dsk"
    dsk_b = tmp_path / "b.dsk"
    dsk_a.write_bytes(bytes([0x11]) * _2DD)
    dsk_b.write_bytes(bytes([0x22]) * _2DD)
    machine = build_machine(_spec(tmp_path, kind), fdd1=dsk_a, fdd2=dsk_b)
    assert machine.fdc is not None
    return machine.fdc


def _activity(fdc: FloppyDisk, track: int) -> None:
    """Seek drive A to `track`, start a sector read and consume part of it,
    leaving the controller mid-transfer."""
    if fdc.kind.value == "wd2793_sony":
        fdc.write_mem(REG_SIDE, 0x01)
        fdc.write_mem(REG_DRIVE, 0x80)
        fdc.write_mem(REG_DATA, track)
        fdc.write_mem(REG_STATUS_CMD, 0x10)  # SEEK
        fdc.read_mem(REG_STATUS_CMD)
        fdc.write_mem(REG_STATUS_CMD, 0x80)  # READ SECTOR
        for _ in range(track + 3):
            fdc.read_mem(REG_DATA)
    else:
        fdc.write_mem(TC_REG_CONTROL0, 0x1C)
        for value in (CMD_SEEK, 0x00, track):
            fdc.write_mem(TC_REG_DATA, value)
        for value in (CMD_READ_DATA, 0x00, track, 0, 1, 2, 9, 0x2A, 0xFF):
            fdc.write_mem(TC_REG_DATA, value)
        for _ in range(track + 3):
            fdc.read_mem(TC_REG_DATA)
    fdc.drives[1].track = track + 1
    fdc.drives[1].side = 1


def _roundtrip(snap: dict[str, object]) -> Any:
    """The load path's own view of a snapshot: JSON-encoded and decoded."""
    return _from_jsonable(json.loads(json.dumps(_to_jsonable(snap))))


def _as_snapshot(fdc: FloppyDisk, parsed: FloppyDiskParsed) -> dict[str, object]:
    """A parse_state() result in snapshot() form, for comparison."""
    return {
        "kind": fdc.kind.value,
        "connection_style": parsed.connection_style,
        "controller": parsed.controller,
        "drives": parsed.drives,
    }


# ---------------------------------------------------------------------------
# A valid snapshot parses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", _KINDS)
def test_own_snapshot_parses_and_equals_itself(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    _activity(fdc, 4)
    snap = fdc.snapshot()
    assert _as_snapshot(fdc, fdc.parse_state(snap, "fdc_state")) == snap
    decoded = _roundtrip(snap)
    assert _as_snapshot(fdc, fdc.parse_state(decoded, "fdc_state")) == decoded
    assert decoded == snap


@pytest.mark.parametrize("kind", _KINDS)
def test_parse_does_not_mutate(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    _activity(fdc, 4)
    before = fdc.snapshot()
    fdc.parse_state(_roundtrip(before), "fdc_state")
    assert fdc.snapshot() == before


@pytest.mark.parametrize("kind", _KINDS)
def test_restore_of_decoded_snapshot_restores_it(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    _activity(fdc, 4)
    saved = _roundtrip(fdc.snapshot())
    _activity(fdc, 9)
    fdc.restore(saved)
    assert fdc.snapshot() == saved


@pytest.mark.parametrize("kind", _KINDS)
def test_apply_state_of_parsed_snapshot_restores_it(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    _activity(fdc, 4)
    saved = _roundtrip(fdc.snapshot())
    selected = fdc.controller.drive
    _activity(fdc, 9)
    if kind == "wd2793":
        fdc.write_mem(REG_DRIVE, 0x01)  # select drive B; the restore reselects A
    fdc.apply_state(fdc.parse_state(saved, "fdc_state"))
    assert fdc.snapshot() == saved
    assert fdc.controller.drive is selected


@pytest.mark.parametrize("kind", _KINDS)
def test_parse_then_apply_hashes_each_mounted_disk_once(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load path's parse_state + apply_state reads each mounted disk's
    identity once: apply_state re-checks nothing."""
    fdc = _fdc(tmp_path, kind)
    saved = _roundtrip(fdc.snapshot())
    hashed: list[Path] = []
    original = DskDiskImage.content_sha1

    def spy(image: DskDiskImage) -> str:
        hashed.append(image.path)
        return original(image)

    monkeypatch.setattr(DskDiskImage, "content_sha1", spy)
    fdc.apply_state(fdc.parse_state(saved, "fdc_state"))
    assert sorted(hashed) == sorted(drive.image.path for drive in fdc.drives if drive.image)
    assert len(hashed) == 2


# ---------------------------------------------------------------------------
# Malformed snapshots are rejected with the FDC unchanged
# ---------------------------------------------------------------------------

Mutation = Callable[[dict[str, Any]], None]


def _set_controller(key: str, value: object) -> Mutation:
    def mutate(snap: dict[str, Any]) -> None:
        snap["controller"][key] = value
    return mutate


def _set_drive(index: int, key: str, value: object) -> Mutation:
    def mutate(snap: dict[str, Any]) -> None:
        snap["drives"][index][key] = value
    return mutate


def _drop_drive(snap: dict[str, Any]) -> None:
    snap["drives"].pop()


def _extra_drive(snap: dict[str, Any]) -> None:
    snap["drives"].append(copy.deepcopy(snap["drives"][1]))


def _unknown_connection_key(snap: dict[str, Any]) -> None:
    snap["connection_style"]["bogus"] = 0


def _other_disk_sha1(snap: dict[str, Any]) -> None:
    snap["drives"][1]["disk_sha1"] = "0" * 40


def _other_disk_path(snap: dict[str, Any]) -> None:
    snap["drives"][0]["disk_path"] = "/nonexistent/other.dsk"


def _no_disk(snap: dict[str, Any]) -> None:
    snap["drives"][1].update(disk_path=None, disk_size=None, disk_sha1=None)


def _partial_identity(snap: dict[str, Any]) -> None:
    snap["drives"][1]["disk_sha1"] = None


def _assert_rejected_unchanged(
    tmp_path: Path, kind: str, mutate: Mutation, match: str
) -> None:
    fdc = _fdc(tmp_path, kind)
    _activity(fdc, 4)
    bad = _roundtrip(fdc.snapshot())
    mutate(bad)
    # Move every part of the FDC away from the saved state, so a partial
    # restore would show up in the comparison below.
    _activity(fdc, 9)
    if kind == "wd2793":
        fdc.write_mem(REG_SIDE, 0x00)
        fdc.write_mem(REG_TRACK, 0x33)
    before = fdc.snapshot()
    selected = fdc.controller.drive

    with pytest.raises(ValueError, match=match):
        fdc.restore(bad)
    with pytest.raises(ValueError, match=match):
        fdc.parse_state(bad, "fdc_state")

    assert fdc.snapshot() == before
    assert fdc.controller.drive is selected


_COMMON_CASES: list[tuple[str, Mutation, str]] = [
    ("drive_track", _set_drive(1, "track", 256), r"fdc_state\.drives\[1\]\.track"),
    ("drive_side", _set_drive(1, "side", 2), r"fdc_state\.drives\[1\]\.side"),
    ("drive_changed", _set_drive(1, "disk_changed", 1), r"drives\[1\]\.disk_changed"),
    ("drive_size_str", _set_drive(1, "disk_size", "737280"), r"drives\[1\]\.disk_size"),
    ("drive_unknown_key", _set_drive(1, "extra", 0), r"drives\[1\] has unknown keys"),
    ("fewer_drives", _drop_drive, "drive count"),
    ("more_drives", _extra_drive, "drive count"),
    ("connection_key", _unknown_connection_key, r"connection_style has unknown keys"),
    ("sha1_mismatch", _other_disk_sha1, "(?i)disk mismatch"),
    ("path_mismatch", _other_disk_path, "(?i)disk mismatch"),
    ("mount_mismatch", _no_disk, "(?i)disk mount mismatch"),
    ("partial_identity", _partial_identity, r"drives\[1\]"),
]


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize(
    ("mutate", "match"),
    [(case[1], case[2]) for case in _COMMON_CASES],
    ids=[case[0] for case in _COMMON_CASES],
)
def test_malformed_drive_or_interface_rejected(
    tmp_path: Path, kind: str, mutate: Mutation, match: str
) -> None:
    _assert_rejected_unchanged(tmp_path, kind, mutate, match)


_WD2793_CASES: list[tuple[str, Mutation, str]] = [
    ("mode", _set_controller("mode", 4), r"fdc_state\.controller\.mode"),
    ("step_dir", _set_controller("step_dir", 0), r"fdc_state\.controller\.step_dir"),
    ("buffer", _set_controller("buffer", bytearray(513)), r"controller\.buffer"),
    ("index", _set_controller("index", 6251), r"controller\.index"),
    ("track_reg", _set_controller("track_reg", -1), r"controller\.track_reg"),
    ("intrq", _set_controller("intrq", 0), r"controller\.intrq"),
    ("side_reg", lambda s: s["connection_style"].update(side_reg=256), "side_reg"),
    ("drive_reg", lambda s: s["connection_style"].update(drive_reg="0"), "drive_reg"),
]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [(case[1], case[2]) for case in _WD2793_CASES],
    ids=[case[0] for case in _WD2793_CASES],
)
def test_malformed_wd2793_rejected(tmp_path: Path, mutate: Mutation, match: str) -> None:
    _assert_rejected_unchanged(tmp_path, "wd2793", mutate, match)


def _result_index_past_buffer(snap: dict[str, Any]) -> None:
    snap["controller"]["result_buffer"] = bytearray(3)
    snap["controller"]["result_index"] = 4


def _exec_index_past_buffer(snap: dict[str, Any]) -> None:
    snap["controller"]["exec_index"] = len(snap["controller"]["exec_buffer"]) + 1


_TC8566AF_CASES: list[tuple[str, Mutation, str]] = [
    ("cmd_needed", _set_controller("cmd_needed", 3), r"fdc_state\.controller\.cmd_needed"),
    ("result_index", _result_index_past_buffer, r"controller\.result_index"),
    ("exec_index", _exec_index_past_buffer, r"controller\.exec_index"),
    ("cmd_buffer", _set_controller("cmd_buffer", bytearray(9)), r"controller\.cmd_buffer"),
    ("exec_buffer", _set_controller("exec_buffer", [0] * 4), r"controller\.exec_buffer"),
    ("phase", _set_controller("phase", 4), r"controller\.phase"),
    ("drive_index", _set_controller("selected_drive_index", 4), "selected_drive_index"),
    ("exec_ctr", _set_controller("exec_ctr", [0, 0, 0]), r"controller\.exec_ctr"),
    ("exec_needed", _set_controller("exec_needed", 1025), r"controller\.exec_needed"),
    ("connection_key", lambda s: s["connection_style"].update(side_reg=0), "connection_style"),
]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [(case[1], case[2]) for case in _TC8566AF_CASES],
    ids=[case[0] for case in _TC8566AF_CASES],
)
def test_malformed_tc8566af_rejected(tmp_path: Path, mutate: Mutation, match: str) -> None:
    _assert_rejected_unchanged(tmp_path, "tc8566af", mutate, match)


# ---------------------------------------------------------------------------
# Error types and top-level shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", _KINDS)
def test_schema_failures_are_schema_errors(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    snap = _roundtrip(fdc.snapshot())
    del snap["controller"]
    with pytest.raises(SchemaError, match="fdc_state is missing keys"):
        fdc.parse_state(snap, "fdc_state")
    with pytest.raises(SchemaError, match="fdc_state must be an object"):
        fdc.parse_state([], "fdc_state")


@pytest.mark.parametrize("kind", _KINDS)
def test_kind_mismatch_and_unknown_kind_rejected(tmp_path: Path, kind: str) -> None:
    fdc = _fdc(tmp_path, kind)
    snap = _roundtrip(fdc.snapshot())
    snap["kind"] = "tc8566af" if kind == "wd2793" else "wd2793_sony"
    with pytest.raises(ValueError, match="FDC kind mismatch"):
        fdc.parse_state(snap, "fdc_state")
    snap["kind"] = "nope"
    with pytest.raises(SchemaError, match=r"fdc_state\.kind"):
        fdc.parse_state(snap, "fdc_state")


def test_drive_identity_reads_the_mounted_image(tmp_path: Path) -> None:
    """A drive whose image was swapped since the snapshot is rejected."""
    fdc = _fdc(tmp_path, "wd2793")
    snap = _roundtrip(fdc.snapshot())
    other = tmp_path / "c.dsk"
    other.write_bytes(bytes([0x33]) * _2DD)
    fdc.mount(DskDiskImage(other), drive=1)
    with pytest.raises(ValueError, match=r"fdc_state\.drives\[1\].*disk mismatch"):
        fdc.parse_state(snap, "fdc_state")
