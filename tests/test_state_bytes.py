"""machine-state "Snapshot encoding is separate from storage" and the format-17
byte encoding (canonical JSON, strict decoding, base64 blobs)."""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from host import state_files
from host.machine_config import build_machine_from_spec
from host.state_files import save_state as save_state_file
from msx import state
from msx.boundary import RgbFrame
from msx.input import InputFrame
from msx.machine import Machine
from msx.machine_spec import MachineSpec, _FdcDef, _RomEntry
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes(32 * 1024)


def test_core_save_returns_bytes_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    machine.step_frame(InputFrame(), render_video=False)
    data = state.save_state(machine)
    assert isinstance(data, bytes)
    assert json.loads(data)["format_version"] == state.CURRENT_FORMAT_VERSION
    assert list(tmp_path.iterdir()) == []


def test_core_load_restores_from_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    saved = make_machine(rom=_ROM)
    for _ in range(2):
        saved.step_frame(InputFrame(), render_video=False)
    data = state.save_state(saved)
    restored = make_machine(rom=_ROM)
    state.load_state(restored, data)
    assert state._snapshot_from_machine(restored) == state._snapshot_from_machine(saved)


def test_host_save_writes_the_same_files_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    path = save_state_file(machine, RgbFrame(256, 192, bytes(256 * 192 * 3)), "My Title")
    states = tmp_path / "saves" / "states"
    assert path.parent == Path("saves") / "states"
    assert path.name.startswith("My_Title_") and path.suffix == ".state"
    assert (states / path.name).with_suffix(".png").exists()
    assert (states / "latest.state").resolve() == (states / path.name).resolve()
    assert (states / "latest.png").resolve() == (states / path.name).with_suffix(".png").resolve()


def test_host_save_scales_the_thumbnail_to_256x192(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PIL import Image

    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    path = save_state_file(machine, RgbFrame(512, 212, bytes(512 * 212 * 3)), "wide")
    with Image.open(path.with_suffix(".png")) as img:
        assert img.size == (256, 192)


@pytest.mark.parametrize(("resume", "expected"), [("", None), ("a/b.state", Path("a/b.state"))])
def test_resume_state_maps_empty_to_the_latest_state(
    monkeypatch: pytest.MonkeyPatch, resume: str, expected: Path | None
) -> None:
    calls: list[Path | None] = []
    monkeypatch.setattr(state_files, "load_state", lambda machine, path=None: calls.append(path))
    state_files.resume_state(make_machine(rom=_ROM), resume)
    assert calls == [expected]


# ---------------------------------------------------------------------------
# machine-state "Snapshot bytes are canonical JSON", "Snapshot decoding is
# strict" and "Byte blobs are base64 strings"
# ---------------------------------------------------------------------------

def _canonical(data: bytes) -> bytes:
    text = json.dumps(
        json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return text.encode("utf-8")


def _ran(frames: int) -> Machine:
    machine = make_machine(rom=_ROM)
    for _ in range(frames):
        machine.step_frame(InputFrame(), render_video=False)
    return machine


def test_saved_bytes_are_already_canonical() -> None:
    data = state.save_state(_ran(3))
    assert _canonical(data) == data
    assert b" " not in data and b"\n" not in data


def test_a_non_ascii_disk_path_is_written_as_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for name, size in (("main.rom", 32768), ("sub.rom", 16384), ("disk.rom", 16384)):
        (tmp_path / name).write_bytes(bytes(size))
    disk = tmp_path / "ゲーム.dsk"
    disk.write_bytes(bytes(737280))
    spec = MachineSpec(
        name="non_ascii_disk", generation="msx2", rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=False, ram_size_kb=32, has_v9938=True, has_rtc=False,
        flat_ram_subslot=3, flat_ram_size_kb=64,
        fdc=_FdcDef(
            disk_rom_entry=_RomEntry(file="disk.rom", size_kb=16, pages=[1]),
            controller="wd2793", connection_style="sony", drives=1,
        ),
    )
    data = state.save_state(build_machine_from_spec(spec, fdd1=disk))
    assert "ゲーム.dsk".encode("utf-8") in data
    assert b"\\u" not in data


def test_the_same_state_gives_the_same_bytes() -> None:
    assert state.save_state(_ran(5)) == state.save_state(_ran(5))


def _load_edited(edit: Callable[[str], str]) -> None:
    """Load an edited save into another machine: it must be refused as
    corrupt, and the machine left as it was."""
    text = state.save_state(_ran(3)).decode("utf-8")
    edited = edit(text)
    assert edited != text
    target = _ran(1)
    before = state.save_state(target)
    with pytest.raises(ValueError, match="corrupt or unsupported save state"):
        state.load_state(target, edited.encode("utf-8"))
    assert state.save_state(target) == before


def test_a_float_value_is_rejected() -> None:
    _load_edited(lambda text: re.sub(r'"cpu_im":\d+', '"cpu_im":0.0', text, count=1))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_are_rejected(constant: str) -> None:
    _load_edited(lambda text: re.sub(r'"cpu_im":\d+', f'"cpu_im":{constant}', text, count=1))


def test_a_duplicate_key_is_rejected() -> None:
    _load_edited(lambda text: text.replace('"cpu_im":', '"cpu_im":0,"cpu_im":', 1))


def test_a_pretty_printed_file_still_loads() -> None:
    saved = _ran(3)
    data = state.save_state(saved)
    decoded = json.loads(data)
    reordered = dict(reversed(list(decoded.items())))
    pretty = json.dumps(reordered, indent=2).encode("utf-8")
    target = _ran(1)
    state.load_state(target, pretty)
    assert state.save_state(target) == data


def test_an_integer_beyond_2_to_the_53_is_not_written() -> None:
    machine = _ran(1)
    machine.vdp.frame_count = 2**53
    with pytest.raises(ValueError, match="outside"):
        state.save_state(machine)


def test_vram_is_a_plain_base64_string() -> None:
    machine = make_machine_msx2(_ROM, bytes(0x4000))
    machine.vdp.vram[0x1FFFF] = 0x5A
    decoded = json.loads(state.save_state(machine))
    vram = decoded["vdp_vram"]
    assert isinstance(vram, str)
    raw = base64.b64decode(vram, validate=True)
    assert len(raw) == 131072 and raw[-1] == 0x5A


def test_invalid_base64_is_rejected_naming_the_field() -> None:
    machine = _ran(1)
    decoded = json.loads(state.save_state(machine))
    decoded["ram"] = "*" + decoded["ram"][1:]
    with pytest.raises(state.StateLoadError, match="ram"):
        state.load_state(_ran(1), json.dumps(decoded).encode("utf-8"))
