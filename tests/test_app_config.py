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
    assert cfg.machine is None and cfg.speed is None and cfg.fmpac is None


def test_empty_file_returns_all_unset(tmp_path: Path) -> None:
    _write(tmp_path, "")
    assert load_app_config(tmp_path) == AppConfig()


def test_known_scalar_keys_parsed(tmp_path: Path) -> None:
    _write(tmp_path,
           "machine: cbios_msx1_jp\nspeed: 2.0\nscale: 4\nmapper: KonamiSCC\nfmpac: true\n")
    cfg = load_app_config(tmp_path)
    assert cfg.machine == "cbios_msx1_jp"
    assert cfg.speed == 2.0
    assert cfg.scale == 4
    assert cfg.mapper == "KonamiSCC"
    assert cfg.fmpac is True


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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_invalid_mapper_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "mapper: NotAMapper\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


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

def test_default_gamepad_maps_match_builtin() -> None:
    direct, turbo = AppConfig().gamepad_maps()
    # dpad up/down/left/right → bits 0-3, A→4, B→5 (direct)
    assert direct == {11: 0, 12: 1, 13: 2, 14: 3, 0: 4, 1: 5}
    # Y (index 3) → trigger A turbo (bit 4); X (index 2) → trigger B turbo (bit 5)
    assert turbo == {3: 4, 2: 5}


def test_trigger_swap_rebinds_buttons() -> None:
    cfg = AppConfig(gamepad_buttons={"trigger_a": "b", "trigger_b": "a"})
    direct, _turbo = cfg.gamepad_maps()
    assert direct[1] == 4  # SDL button b (index 1) → Trigger A (bit 4)
    assert direct[0] == 5  # SDL button a (index 0) → Trigger B (bit 5)


def test_duplicate_button_assignment_rejected(tmp_path: Path) -> None:
    # trigger_a moved onto 'b' while trigger_b still defaults to 'b' → conflict.
    _write(tmp_path, "joystick:\n  buttons:\n    trigger_a: b\n")
    with pytest.raises(AppConfigError):
        load_app_config(tmp_path)


def test_turbo_override_rebinds_button() -> None:
    cfg = AppConfig(gamepad_buttons={"turbo_a": "leftshoulder"})
    _direct, turbo = cfg.gamepad_maps()
    assert turbo[9] == 4  # leftshoulder index 9 → trigger A turbo bit 4
    assert 3 not in turbo  # default Y no longer turbo-a


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
