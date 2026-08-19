"""Tests for msx/state.py save/load state functionality."""
from __future__ import annotations

import pytest
from PIL import Image

from msx.state import CURRENT_FORMAT_VERSION, StateLoadError, load_state, save_state
from tests.factories import make_machine

_CART_64K = b"\x00" * 0x10000

_ROM = b"\x00" * 0x8000


@pytest.fixture()
def machine():
    return make_machine(rom=_ROM)


@pytest.fixture()
def saves_dir(tmp_path, monkeypatch):
    """Redirect saves/ to a temporary directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


# --- save_state ---------------------------------------------------------------

def test_save_state_creates_state_file(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    path = save_state(machine, rgb, "test")
    assert path.exists()
    assert path.suffix == ".state"
    assert "test_" in path.name


def test_save_state_creates_png(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")
    png_path = state_path.with_suffix(".png")
    assert png_path.exists()
    img = Image.open(png_path)
    assert img.size == (256, 192)
    assert img.mode == "RGB"


def test_save_state_creates_latest_symlinks(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")
    png_path = state_path.with_suffix(".png")

    latest_state = saves_dir / "states" / "latest.state"
    latest_png = saves_dir / "states" / "latest.png"
    assert latest_state.is_symlink()
    assert latest_png.is_symlink()
    assert latest_state.resolve() == state_path.resolve()
    assert latest_png.resolve() == png_path.resolve()


def test_save_state_creates_saves_dir(machine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saves = tmp_path / "saves"
    assert not saves.exists()
    rgb = bytearray(256 * 192 * 3)
    save_state(machine, rgb, "test")
    assert saves.is_dir()


def test_save_state_title_sanitised(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    # Spaces become underscores; non-ASCII (e.g. Japanese) is now preserved;
    # filesystem-unsafe characters are stripped.
    path = save_state(machine, rgb, "テスト タイトル")
    assert " " not in path.name
    assert "テスト_タイトル" in path.name

def test_save_state_title_strips_unsafe_chars(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    path = save_state(machine, rgb, 'bad/name:file*?"')
    assert "/" not in path.name
    assert ":" not in path.name
    assert "*" not in path.name


# --- load_state ---------------------------------------------------------------

def test_load_state_restores_cpu_registers(machine, saves_dir):
    machine.cpu.registers.A = 0x42
    machine.cpu.registers.PC = 0x1234
    machine.memory.ram[0] = 0xAB
    rgb = bytearray(256 * 192 * 3)
    save_state(machine, rgb, "test")

    machine.cpu.registers.A = 0x00
    machine.cpu.registers.PC = 0x0000
    machine.memory.ram[0] = 0x00

    load_state(machine)

    assert machine.cpu.registers.A == 0x42
    assert machine.cpu.registers.PC == 0x1234
    assert machine.memory.ram[0] == 0xAB


def test_load_state_missing_file_raises(machine, saves_dir):
    with pytest.raises(FileNotFoundError):
        load_state(machine)


def test_load_state_wrong_version_raises(machine, saves_dir):
    import json
    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")

    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["format_version"] = CURRENT_FORMAT_VERSION + 99
    bad_path = saves_dir / "states" / "bad.state"
    with open(bad_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    latest = saves_dir / "states" / "latest.state"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    import os
    os.symlink(bad_path.name, latest)

    with pytest.raises(ValueError, match="version"):
        load_state(machine)


def test_legacy_pickle_state_rejected(machine, saves_dir):
    import pickle as _pickle
    saves_dir.mkdir(parents=True, exist_ok=True)
    bad = saves_dir / "legacy.state"
    with open(bad, "wb") as f:
        _pickle.dump({"format_version": 4}, f)
    with pytest.raises(ValueError, match="legacy pickle"):
        load_state(machine, bad)


# --- StateLoadError -------------------------------------------------------------

def test_malformed_mapper_state_raises_state_load_error(saves_dir):
    import json

    # ASCII8's restore() indexes "banks" directly (KeyError if absent), unlike
    # the flat-cartridge default mapper whose restore() is a no-op.
    machine = make_machine(rom=_ROM, cartridge=b"\x00" * 0x10000, mapper="ASCII8")
    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")

    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    del data["mapper_state"]["banks"]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)
    assert "mapper" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, KeyError)


def test_malformed_psg_synth_raises_state_load_error(machine, saves_dir):
    import json

    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")

    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    del data["psg_synth"]["_tone_cnt"]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)
    assert "psg" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, KeyError)


def test_well_formed_state_does_not_raise_state_load_error(machine, saves_dir):
    rgb = bytearray(256 * 192 * 3)
    save_state(machine, rgb, "test")
    load_state(machine)  # must not raise StateLoadError (or anything else)


# --- mapper_kind ------------------------------------------------------------

def test_snapshot_persists_mapper_kind_value_not_class_name(saves_dir):
    import json

    machine = make_machine(rom=_ROM, cartridge=_CART_64K, mapper="KonamiSCC")
    rgb = bytearray(256 * 192 * 3)
    state_path = save_state(machine, rgb, "test")

    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["mapper_kind"] == "konami_scc"
    assert data["mapper_kind"] != "KonamiSCCMapper"


def test_mapper_kind_mismatch_raises_value_error(saves_dir):
    saved_machine = make_machine(rom=_ROM, cartridge=_CART_64K, mapper="Konami")
    rgb = bytearray(256 * 192 * 3)
    save_state(saved_machine, rgb, "test")

    running_machine = make_machine(rom=_ROM, cartridge=_CART_64K, mapper="ASCII8")
    with pytest.raises(ValueError, match="mapper mismatch") as exc_info:
        load_state(running_machine)
    assert "ascii8" in str(exc_info.value)
    assert "konami" in str(exc_info.value)
