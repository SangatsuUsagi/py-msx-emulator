"""Optional root ``py_emulator.yaml`` startup configuration.

Loads user defaults for startup options (machine, speed, mapper, FM-PAC, RPC)
and gamepad settings (button assignment, turbo rate). Values are merged with the
precedence built-in defaults < config file < CLI arguments; see ``__main__``.

When the file is absent (or unreadable), an all-unset :class:`AppConfig` is
returned and behaviour is identical to running with no config.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "py_emulator.yaml"

# Built-in defaults — the single source of truth for values that are not set by
# either the config file or the CLI.
DEFAULT_SPEED = 1.0
DEFAULT_MAPPER = "auto"
DEFAULT_TURBO_PERIOD = 3

# Accepted cartridge mapper names (mirrors the --mapper CLI choices).
VALID_MAPPERS: tuple[str, ...] = (
    "auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
    "Konami", "KonamiSCC", "Majutsushi",
    "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
    "R-Type",
)

# SDL GameController button label → SDL_CONTROLLER_BUTTON_* index.
_GC_BUTTON_NAME_TO_INDEX: dict[str, int] = {
    "a": 0, "b": 1, "x": 2, "y": 3,
    "back": 4, "guide": 5, "start": 6,
    "leftstick": 7, "rightstick": 8,
    "leftshoulder": 9, "rightshoulder": 10,
    "dpup": 11, "dpdown": 12, "dpleft": 13, "dpright": 14,
}

# MSX joystick function → (default button label, port bit, is_turbo).
# The port bit is fixed by MSX semantics (up=0..right=3, trigA=4, trigB=5); only
# the button label is user-configurable. Turbo functions drive the same trigger
# bits but through the turbo state machine.
_GAMEPAD_FUNCTIONS: dict[str, tuple[str, int, bool]] = {
    "up":        ("dpup", 0, False),
    "down":      ("dpdown", 1, False),
    "left":      ("dpleft", 2, False),
    "right":     ("dpright", 3, False),
    "trigger_a": ("a", 4, False),
    "trigger_b": ("b", 5, False),
    "turbo_a":   ("y", 4, True),
    "turbo_b":   ("x", 5, True),
}

_KNOWN_TOP_KEYS = frozenset({"machine", "speed", "mapper", "fmpac", "rpc", "joystick"})
_KNOWN_RPC_KEYS = frozenset({"enabled", "socket"})
_KNOWN_JOYSTICK_KEYS = frozenset({"turbo_hz", "buttons"})


class AppConfigError(Exception):
    """Raised when ``py_emulator.yaml`` contains an invalid value."""


@dataclass
class AppConfig:
    """Parsed ``py_emulator.yaml`` values. ``None`` means "not set" (fall back).

    ``gamepad_buttons`` holds only the functions the user overrode, mapping an
    MSX function name (``trigger_a`` …) to an SDL GameController button label.
    """

    machine: str | None = None
    speed: float | None = None
    mapper: str | None = None
    fmpac: bool | None = None
    rpc_enabled: bool | None = None
    rpc_socket: str | None = None
    turbo_hz: float | None = None
    gamepad_buttons: dict[str, str] = field(default_factory=dict)

    def turbo_period(self) -> int:
        """Return the turbo per-frame period from ``turbo_hz`` (min 1).

        Uses ``round(60 / turbo_hz)`` so the ON phase recurs at roughly the
        requested rate; exact only when ``turbo_hz`` divides 60.
        """
        if self.turbo_hz is None:
            return DEFAULT_TURBO_PERIOD
        return max(1, round(60.0 / self.turbo_hz))

    def gamepad_maps(self) -> tuple[dict[int, int], dict[int, int]]:
        """Resolve the gamepad button map to ``JoystickManager`` dicts.

        Returns:
            A ``(direct, turbo)`` pair, each mapping an SDL GameController button
            index to the MSX port bit it drives. ``direct`` bits are written
            immediately; ``turbo`` bits are driven by the turbo state machine.

        Raises:
            AppConfigError: If two functions resolve to the same button.
        """
        direct: dict[int, int] = {}
        turbo: dict[int, int] = {}
        seen: dict[int, str] = {}
        for func, (default_label, bit, is_turbo) in _GAMEPAD_FUNCTIONS.items():
            label = self.gamepad_buttons.get(func, default_label)
            index = _GC_BUTTON_NAME_TO_INDEX[label]
            if index in seen:
                raise AppConfigError(
                    f"{CONFIG_FILENAME}: joystick.buttons: {label!r} assigned to both "
                    f"{seen[index]!r} and {func!r}"
                )
            seen[index] = func
            (turbo if is_turbo else direct)[index] = bit
        return direct, turbo


def load_app_config(root: Path) -> AppConfig:
    """Load ``py_emulator.yaml`` from ``root``; return an all-unset config if absent.

    Args:
        root: Directory to look for ``py_emulator.yaml`` in (the repo root).

    Returns:
        The parsed :class:`AppConfig`, or an all-unset one when the file does not
        exist or cannot be read.

    Raises:
        AppConfigError: If the file exists but contains malformed or invalid
            content.
    """
    path = root / CONFIG_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Absent or unreadable → behave as if no config was provided.
        return AppConfig()

    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AppConfigError(f"{CONFIG_FILENAME}: invalid YAML: {exc}") from exc

    if raw is None:
        return AppConfig()
    if not isinstance(raw, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: top level must be a mapping")

    for key in raw:
        if key not in _KNOWN_TOP_KEYS:
            print(f"warning: {CONFIG_FILENAME}: unknown key {key!r} (ignored)",
                  file=sys.stderr)

    cfg = AppConfig()
    cfg.machine = _opt_str(raw, "machine")
    cfg.speed = _opt_positive_float(raw, "speed")
    cfg.mapper = _opt_mapper(raw)
    cfg.fmpac = _opt_bool(raw, "fmpac")
    _parse_rpc(raw.get("rpc"), cfg)
    _parse_joystick(raw.get("joystick"), cfg)
    return cfg


def _opt_str(raw: dict[str, Any], key: str) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be a string")
    return value


def _opt_bool(raw: dict[str, Any], key: str) -> bool | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, bool):
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be a boolean")
    return value


def _opt_positive_float(raw: dict[str, Any], key: str) -> float | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be a number")
    if value <= 0:
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be positive")
    return float(value)


def _opt_mapper(raw: dict[str, Any]) -> str | None:
    value = _opt_str(raw, "mapper")
    if value is not None and value not in VALID_MAPPERS:
        raise AppConfigError(
            f"{CONFIG_FILENAME}: mapper {value!r} is not one of {', '.join(VALID_MAPPERS)}"
        )
    return value


def _parse_rpc(rpc: Any, cfg: AppConfig) -> None:
    if rpc is None:
        return
    if not isinstance(rpc, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: rpc must be a mapping")
    for key in rpc:
        if key not in _KNOWN_RPC_KEYS:
            print(f"warning: {CONFIG_FILENAME}: unknown key 'rpc.{key}' (ignored)",
                  file=sys.stderr)
    cfg.rpc_enabled = _opt_bool(rpc, "enabled")
    cfg.rpc_socket = _opt_str(rpc, "socket")


def _parse_joystick(joystick: Any, cfg: AppConfig) -> None:
    if joystick is None:
        return
    if not isinstance(joystick, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: joystick must be a mapping")
    for key in joystick:
        if key not in _KNOWN_JOYSTICK_KEYS:
            print(f"warning: {CONFIG_FILENAME}: unknown key 'joystick.{key}' (ignored)",
                  file=sys.stderr)
    cfg.turbo_hz = _opt_positive_float(joystick, "turbo_hz")
    _parse_joystick_buttons(joystick.get("buttons"), cfg)


def _parse_joystick_buttons(buttons: Any, cfg: AppConfig) -> None:
    if buttons is None:
        return
    if not isinstance(buttons, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: joystick.buttons must be a mapping")
    for func, label in buttons.items():
        if func not in _GAMEPAD_FUNCTIONS:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: joystick.buttons: unknown function {func!r} "
                f"(expected one of {', '.join(_GAMEPAD_FUNCTIONS)})"
            )
        if not isinstance(label, str) or label not in _GC_BUTTON_NAME_TO_INDEX:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: joystick.buttons.{func}: unknown button {label!r} "
                f"(expected one of {', '.join(_GC_BUTTON_NAME_TO_INDEX)})"
            )
        cfg.gamepad_buttons[func] = label
    _validate_no_duplicate_buttons(cfg)


def _validate_no_duplicate_buttons(cfg: AppConfig) -> None:
    """Surface duplicate-button conflicts at load time (raises AppConfigError)."""
    cfg.gamepad_maps()

