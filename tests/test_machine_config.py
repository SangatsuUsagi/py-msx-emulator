"""machine-loader "Machine construction takes data, not paths"."""
from __future__ import annotations

from pathlib import Path

import pytest

import host.romdb as romdb
from host.machine_config import load_machine_config
from msx.input import InputFrame
from msx.machine_loader import build_machine
from msx.state import _snapshot_from_machine
from tests.factories import _msx2_spec, make_config, make_config_msx2

_ROM = bytes(32 * 1024)
_CART = bytes(range(256)) * 64  # 16 KB


def test_building_twice_from_one_config_gives_equal_machines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config_msx2(_ROM, bytes(16 * 1024), cartridge=_CART, mapper="ASCII8SRAM2")
    first = build_machine(config)
    second = build_machine(config)
    for machine in (first, second):
        for _ in range(3):
            machine.step_frame(InputFrame(), render_video=False)
    assert _snapshot_from_machine(first) == _snapshot_from_machine(second)


def test_auto_mapper_is_resolved_before_building(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(romdb, "lookup", lambda cartridge: "Konami")
    config = make_config(_ROM, cartridge=_CART, mapper="auto")
    assert config.mapper1 == "Konami"

    def no_lookup(cartridge: bytes) -> str | None:
        raise AssertionError("build_machine looked up the ROM database")

    monkeypatch.setattr(romdb, "lookup", no_lookup)
    machine = build_machine(config)
    assert type(machine.memory._mapper).__name__ == "KonamiMapper"


def test_build_reads_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_machine_config(
        _msx2_spec(), bios_override=_ROM, extrom_override=bytes(16 * 1024)
    )

    def no_read(self: Path) -> bytes:
        raise AssertionError(f"build_machine read {self}")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail(f"checked {self}"))
    assert build_machine(config).rtc is not None


def test_sram_size_of_a_non_sram_id_names_it_by_value() -> None:
    from host.machine_config import _sram_size_and_label
    from msx.boundary import MediaId

    config = make_config(_ROM)
    with pytest.raises(ValueError, match="unknown SRAM media id 'drive_a'"):
        _sram_size_and_label(config, MediaId.DRIVE_A)


def test_machine_keyboard_layout_defaults_to_international() -> None:
    # input-keyboard "Default layout is International".
    from msx.machine_spec import KeyboardLayout

    machine = build_machine(make_config(_ROM))
    assert machine.keyboard_layout is KeyboardLayout.INTERNATIONAL
    assert machine.keyboard_layout == "int"


def test_a_jis_machine_definition_gives_the_japanese_layout() -> None:
    from host.machine_config import load_device_registry, load_machine_spec
    from msx.machine_spec import KeyboardLayout

    root = Path(__file__).resolve().parent.parent
    config_dir = root / "config"
    spec = load_machine_spec("hb_f1xd", config_dir, load_device_registry(config_dir), root)
    assert spec.keyboard_type is KeyboardLayout.JAPANESE
