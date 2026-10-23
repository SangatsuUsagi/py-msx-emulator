"""Tests for the optional py_emulator.yaml startup configuration loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.app_config import (
    DEFAULT_TURBO_PERIOD,
    AppConfig,
    AppConfigError,
    load_app_config,
)


def _write(root: Path, text: str) -> None:
    (root / "py_emulator.yaml").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Presence / parsing
# ---------------------------------------------------------------------------

def test_absent_file_returns_all_unset(tmp_path: Path) -> None:
    cfg = load_app_config(tmp_path)
    assert cfg == AppConfig()
    assert cfg.machine is None and cfg.speed is None and cfg.extension2 is None


def test_empty_file_returns_all_unset(tmp_path: Path) -> None:
    _write(tmp_path, "")
    assert load_app_config(tmp_path) == AppConfig()


def test_known_scalar_keys_parsed(tmp_path: Path) -> None:
    _write(tmp_path,
           "machine: cbios_msx1_jp\nspeed: 2.0\nscale: 4\nextension: fmpac\n")
    cfg = load_app_config(tmp_path)
    assert cfg.machine == "cbios_msx1_jp"
    assert cfg.speed == 2.0
    assert cfg.scale == 4
    assert cfg.extension2 == "fmpac"


def test_extension_key_parsed_as_slot2_alias(tmp_path: Path) -> None:
    # The pre-existing unqualified `extension` key is a backward-compatible
    # alias for `extension2` (slot 2), used only when `extension2` itself is
    # absent -- see _opt_extension's legacy_alias parameter.
    _write(tmp_path, "extension: scc_plus\n")
    cfg = load_app_config(tmp_path)
    assert cfg.extension2 == "scc_plus"
    assert cfg.extension1 is None


def test_extension2_key_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "extension2: scc_plus\n")
    cfg = load_app_config(tmp_path)
    assert cfg.extension2 == "scc_plus"


def test_extension2_key_takes_precedence_over_legacy_extension_alias(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "extension: fmpac\nextension2: scc_plus\n")
    cfg = load_app_config(tmp_path)
    assert cfg.extension2 == "scc_plus"


def test_extension1_key_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "extension1: fmpac\n")
    cfg = load_app_config(tmp_path)
    assert cfg.extension1 == "fmpac"
    assert cfg.extension2 is None


def test_unknown_extension_id_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "extension: bogus\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_unknown_extension1_id_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "extension1: bogus\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_slot1_key_parsed(tmp_path: Path) -> None:
    rom = tmp_path / "game.rom"
    rom.write_bytes(b"\x00")
    _write(tmp_path, f"slot1: {rom.name}\n")
    cfg = load_app_config(tmp_path)
    assert cfg.slot1 == "game.rom"


def test_slot1_missing_file_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "slot1: missing.rom\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_nested_rpc_group_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "rpc:\n  enabled: true\n  socket: /tmp/alt.sock\n")
    cfg = load_app_config(tmp_path)
    assert cfg.rpc_enabled is True
    assert cfg.rpc_socket == "/tmp/alt.sock"


def test_nested_joystick_group_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "joystick:\n  turbo_hz: 30\n  buttons:\n    trigger_a: leftshoulder\n")
    cfg = load_app_config(tmp_path)
    assert cfg.turbo_hz == 30.0
    assert cfg.gamepad_buttons == {"trigger_a": "leftshoulder"}


def test_slot2_parsed(tmp_path: Path) -> None:
    (tmp_path / "slot2.rom").write_bytes(b"\x00")
    _write(tmp_path, "slot2: slot2.rom\n")
    cfg = load_app_config(tmp_path)
    assert cfg.slot2 == "slot2.rom"


def test_frame_skip_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "frame_skip: false\n")
    cfg = load_app_config(tmp_path)
    assert cfg.frame_skip is False


def test_nested_keyboard_joystick_group_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "keyboard_joystick:\n  buttons:\n    trigger_a: j\n")
    cfg = load_app_config(tmp_path)
    assert cfg.keyboard_joystick_buttons == {"trigger_a": "j"}


def test_unknown_keyboard_joystick_key_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "keyboard_joystick:\n  bogus: 1\n")
    load_app_config(tmp_path)
    assert "keyboard_joystick.bogus" in capsys.readouterr().err


def test_unknown_top_key_warns_but_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "frobnicate: 1\nspeed: 1.5\n")
    cfg = load_app_config(tmp_path)
    assert cfg.speed == 1.5
    assert "frobnicate" in capsys.readouterr().err


def test_unknown_nested_key_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "rpc:\n  enabled: true\n  bogus: 1\n")
    load_app_config(tmp_path)
    assert "rpc.bogus" in capsys.readouterr().err


def test_mapper_key_not_recognized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`mapper` is CLI-only (--mapper); the config key is unrecognized. See
    app-config-file spec, "Cartridge mapper selection is CLI-only"."""
    _write(tmp_path, "mapper: Konami\n")
    cfg = load_app_config(tmp_path)
    assert not hasattr(cfg, "mapper")
    assert "mapper" in capsys.readouterr().err


def test_mapper2_key_not_recognized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`mapper2` is CLI-only (--mapper2); the config key is unrecognized."""
    _write(tmp_path, "mapper2: Konami\n")
    cfg = load_app_config(tmp_path)
    assert not hasattr(cfg, "mapper2")
    assert "mapper2" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_non_positive_speed_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "speed: 0\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_non_positive_scale_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "scale: 0\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_non_integer_scale_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "scale: 2.5\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_non_positive_turbo_hz_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "joystick:\n  turbo_hz: -5\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_unknown_button_label_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "joystick:\n  buttons:\n    trigger_a: notabutton\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_unknown_button_function_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "joystick:\n  buttons:\n    fire3: a\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_nonexistent_slot2_path_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "slot2: does/not/exist.rom\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_non_bool_frame_skip_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "frame_skip: auto\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_unknown_keyboard_key_name_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "keyboard_joystick:\n  buttons:\n    up: numpad8\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_unknown_keyboard_joystick_function_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "keyboard_joystick:\n  buttons:\n    jump: space\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_duplicate_keyboard_key_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "keyboard_joystick:\n  buttons:\n    up: j\n    down: j\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_non_mapping_top_level_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_wrong_type_speed_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "speed: fast\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


# ---------------------------------------------------------------------------
# Gamepad map resolution
# ---------------------------------------------------------------------------

def test_default_gamepad_buttons_match_builtin() -> None:
    assert AppConfig().gamepad_buttons_resolved() == {
        "up": "dpup", "down": "dpdown", "left": "dpleft", "right": "dpright",
        "trigger_a": "a", "trigger_b": "b", "turbo_a": "y", "turbo_b": "x",
    }


def test_trigger_swap_rebinds_buttons() -> None:
    # app-config-file "Custom trigger assignment resolved" (config side).
    cfg = AppConfig(gamepad_buttons={"trigger_a": "b", "trigger_b": "a"})
    resolved = cfg.gamepad_buttons_resolved()
    assert (resolved["trigger_a"], resolved["trigger_b"]) == ("b", "a")


def test_duplicate_button_assignment_rejected(tmp_path: Path) -> None:
    # trigger_a moved onto 'b' while trigger_b still defaults to 'b' → conflict.
    _write(tmp_path, "joystick:\n  buttons:\n    trigger_a: b\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_turbo_override_rebinds_button() -> None:
    cfg = AppConfig(gamepad_buttons={"turbo_a": "leftshoulder"})
    resolved = cfg.gamepad_buttons_resolved()
    assert resolved["turbo_a"] == "leftshoulder"
    assert resolved["up"] == "dpup"  # omitted function keeps its default


def test_loader_output_contains_no_sdl_numbers(tmp_path: Path) -> None:
    # app-config-file "Loader output contains no SDL numbers".
    _write(tmp_path, "joystick:\n  buttons:\n    trigger_a: x\n    turbo_b: a\n"
                     "keyboard_joystick:\n  buttons:\n    up: j\n"
                     "hotkeys:\n  mute: shift+f8\n")
    cfg = load_app_config(tmp_path)
    resolved = [cfg.gamepad_buttons_resolved(), cfg.keyboard_joystick_buttons]
    for table in resolved:
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in table.items())
    for (key_name, shift), action in cfg.hotkey_bindings().items():
        assert isinstance(key_name, str) and isinstance(shift, bool) and isinstance(action, str)


# ---------------------------------------------------------------------------
# Keyboard joystick (Joy1) map resolution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Turbo period
# ---------------------------------------------------------------------------

def test_turbo_period_default_when_unset() -> None:
    assert AppConfig().turbo_period() == DEFAULT_TURBO_PERIOD == 3


def test_turbo_period_20hz() -> None:
    assert AppConfig(turbo_hz=20.0).turbo_period() == 3


def test_turbo_period_30hz() -> None:
    assert AppConfig(turbo_hz=30.0).turbo_period() == 2


def test_turbo_period_min_one() -> None:
    assert AppConfig(turbo_hz=1000.0).turbo_period() == 1


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------

def test_nested_mouse_group_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "mouse:\n  enabled: true\n  port: 1\n")
    cfg = load_app_config(tmp_path)
    assert cfg.mouse_enabled is True
    assert cfg.mouse_port == 1


def test_unknown_mouse_key_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "mouse:\n  enabled: true\n  bogus: 1\n")
    load_app_config(tmp_path)
    assert "mouse.bogus" in capsys.readouterr().err


def test_invalid_mouse_port_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "mouse:\n  port: 3\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_mouse_port_index_disabled_by_default() -> None:
    assert AppConfig().mouse_port_index() is None


def test_mouse_port_index_disabled_explicitly() -> None:
    assert AppConfig(mouse_enabled=False, mouse_port=1).mouse_port_index() is None


def test_mouse_port_index_defaults_to_joy2_when_enabled() -> None:
    assert AppConfig(mouse_enabled=True).mouse_port_index() == 1


def test_mouse_port_index_honors_configured_port() -> None:
    assert AppConfig(mouse_enabled=True, mouse_port=1).mouse_port_index() == 0
    assert AppConfig(mouse_enabled=True, mouse_port=2).mouse_port_index() == 1


# ---------------------------------------------------------------------------
# VALID_MAPPERS <-> machine_loader._SUPPORTED_MAPPERS consistency
# ---------------------------------------------------------------------------

def test_valid_mappers_match_supported_mappers() -> None:
    # app_config.VALID_MAPPERS duplicates machine_loader._SUPPORTED_MAPPERS
    # (plus "auto") to avoid an import cycle; guard against the two drifting.
    from msx.app_config import VALID_MAPPERS
    from msx.machine_loader import _MAPPER_BUILDERS, _SUPPORTED_MAPPERS

    assert set(VALID_MAPPERS) == _SUPPORTED_MAPPERS | {"auto"}
    # Every non-"auto" accepted name must actually have a builder.
    assert set(VALID_MAPPERS) - {"auto"} <= set(_MAPPER_BUILDERS)


# ---------------------------------------------------------------------------
# fdd1 (drive A disk list)
# ---------------------------------------------------------------------------

def test_fdd1_single_path_parsed(tmp_path: Path) -> None:
    (tmp_path / "disks").mkdir()
    (tmp_path / "disks" / "a.dsk").write_bytes(b"\x00")
    _write(tmp_path, "fdd1: disks/a.dsk\n")
    assert load_app_config(tmp_path).fdd1 == ["disks/a.dsk"]


def test_fdd1_list_parsed(tmp_path: Path) -> None:
    (tmp_path / "disks").mkdir()
    (tmp_path / "disks" / "a.dsk").write_bytes(b"\x00")
    (tmp_path / "disks" / "b.dsk").write_bytes(b"\x00")
    _write(tmp_path, "fdd1: [disks/a.dsk, disks/b.dsk]\n")
    assert load_app_config(tmp_path).fdd1 == ["disks/a.dsk", "disks/b.dsk"]


def test_nonexistent_fdd1_path_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.dsk").write_bytes(b"\x00")
    _write(tmp_path, "fdd1: [a.dsk, does/not/exist.dsk]\n")
    with pytest.raises(AppConfigError, match="does/not/exist.dsk"):
        load_app_config(tmp_path)


@pytest.mark.parametrize("value", ["3", "[]", "[a.dsk, 3]", "{a: b}"])
def test_fdd1_of_wrong_type_rejected(tmp_path: Path, value: str) -> None:
    (tmp_path / "a.dsk").write_bytes(b"\x00")
    _write(tmp_path, f"fdd1: {value}\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


# ---------------------------------------------------------------------------
# hotkeys (SDL2 frontend hotkey assignment)
# ---------------------------------------------------------------------------

_F6, _F8, _F12 = "f6", "f8", "f12"


def test_hotkeys_group_parsed(tmp_path: Path) -> None:
    _write(tmp_path, "hotkeys:\n  mute: shift+f8\n")
    bindings = load_app_config(tmp_path).hotkey_bindings()
    assert bindings[(_F8, True)] == "mute"
    assert (_F12, False) not in bindings


def test_unset_hotkeys_gives_the_built_in_table(tmp_path: Path) -> None:
    from msx.app_config import DEFAULT_HOTKEYS

    _write(tmp_path, "speed: 1.0\n")
    bindings = load_app_config(tmp_path).hotkey_bindings()
    assert sorted(bindings.values()) == sorted(DEFAULT_HOTKEYS)
    assert bindings[(_F6, False)] == "disk_next"
    assert bindings[(_F12, True)] == "reset"


@pytest.mark.parametrize("text", [
    "hotkeys: [f6]\n",                    # not a mapping
    "hotkeys:\n  rewind: f6\n",           # unknown action
    "hotkeys:\n  mute: ctrl+f6\n",        # unknown key
    "hotkeys:\n  mute: f13\n",
    "hotkeys:\n  mute: 6\n",
    "hotkeys:\n  mute: shift+f3\n",       # MSX key (MSX F8)
    "hotkeys:\n  mute: f1\n",
    "hotkeys:\n  mute: f9\n",             # fixed hotkey
])
def test_invalid_hotkeys_rejected(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_hotkey_conflict_with_another_action_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "hotkeys:\n  mute: f6\n")
    with pytest.raises(AppConfigError) as exc:
        load_app_config(tmp_path)
    message = str(exc.value)
    assert "'f6'" in message and "disk_next" in message and "mute" in message


def test_swapping_two_hotkey_actions_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path, "hotkeys:\n  mute: f6\n  disk_next: f12\n")
    bindings = load_app_config(tmp_path).hotkey_bindings()
    assert bindings[(_F6, False)] == "mute"
    assert bindings[(_F12, False)] == "disk_next"
