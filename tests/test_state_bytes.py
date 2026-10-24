"""machine-state "Snapshot encoding is separate from storage"."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.state_files import save_state as save_state_file
from msx import state
from msx.boundary import RgbFrame
from msx.input import InputState
from tests.factories import make_machine

_ROM = bytes(32 * 1024)


def test_core_save_returns_bytes_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    machine.step_frame(InputState(), render_video=False)
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
        saved.step_frame(InputState(), render_video=False)
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
