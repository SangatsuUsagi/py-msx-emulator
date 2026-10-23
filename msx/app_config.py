"""Optional root ``py_emulator.yaml`` startup configuration.

Loads user defaults for startup options (machine, speed, extension, RPC),
gamepad settings (button assignment, turbo rate), mouse settings (enabled,
port), and the SDL2 frontend hotkey assignment. Values are merged with the
precedence built-in defaults < config file < CLI arguments; see
``__main__``. Cartridge mapper selection (``--mapper`` / ``--mapper2``) is
CLI-only and has no config-file equivalent.

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
DEFAULT_SCALE = 3
DEFAULT_TURBO_PERIOD = 3
DEFAULT_MOUSE_PORT = 2

# Accepted cartridge mapper names for --mapper (CLI-only; no config-file
# equivalent, see app-config-file spec). Must stay in sync with
# machine_loader._SUPPORTED_MAPPERS (plus "auto"); a test asserts the two agree
# so the lists cannot drift. app_config does not import machine_loader to avoid
# an import cycle, hence the duplicated literal guarded by that test.
VALID_MAPPERS: tuple[str, ...] = (
    "auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
    "Konami", "KonamiSCC", "Majutsushi",
    "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
    "R-Type", "Page2", "0x4000", "0x8000", "KoeiSRAM32", "GameMaster2",
)

# --mapper1/--mapper2 both accept the identical VALID_MAPPERS list (slot 2
# gained the same SRAM load-on-start/save-on-exit persistence slot 1 already
# had -- see cart-sram-mapper's "machine_loader SRAM load/save integration"
# Requirement, generalized to both slots by openspec/changes/
# slot1-slot2-symmetrization). KonamiSCC in either slot shares Machine's
# single scc field with the other slot -- rejected at build_machine time if
# both slots' mappers resolve to KonamiSCC, or one slot's mapper resolves to
# KonamiSCC while the other slot's --extensionN is scc_plus (see
# machine-loader's "Either slot's KonamiSCC mapper shares the machine's
# single SCC chip" spec Requirement).

# Accepted --extension ids (see the cart-extension-overlay spec). Each maps to
# a config/extensions/<id>.yaml overlay fragment placed in primary slot 2.
VALID_EXTENSIONS: tuple[str, ...] = (
    "fmpac", "scc_plus", "hbi_j1", "memory_512k", "msxdos2_512k", "msxdos2_512k_kanjirom",
    "msxdos2_512k_viewfont",
)

# GameController button labels accepted by joystick.buttons. The SDL2
# frontend translates each label to an SDL button index
# (frontend/sdl2_joystick.py's GC_BUTTON_NAME_TO_INDEX).
GC_BUTTON_NAMES: tuple[str, ...] = (
    "a", "b", "x", "y",
    "back", "guide", "start",
    "leftstick", "rightstick",
    "leftshoulder", "rightshoulder",
    "dpup", "dpdown", "dpleft", "dpright",
)

# MSX joystick function → default button label. Only the label is
# user-configurable; the frontend maps each function to its port bit and
# turbo flag (frontend/sdl2_joystick.py's GAMEPAD_FUNCTION_BITS).
_GAMEPAD_FUNCTIONS: dict[str, str] = {
    "up":        "dpup",
    "down":      "dpdown",
    "left":      "dpleft",
    "right":     "dpright",
    "trigger_a": "a",
    "trigger_b": "b",
    "turbo_a":   "y",
    "turbo_b":   "x",
}

# Public view of the joystick.buttons key set, so tools that emit or check that
# block (scripts/list_joystick_buttons.py) cannot drift from the validator.
GAMEPAD_FUNCTION_KEYS: tuple[str, ...] = tuple(_GAMEPAD_FUNCTIONS)

# MSX Joy1 functions configurable under keyboard_joystick.buttons. The
# frontend keyboard maps each to its port bit and default key(s).
_KEYBOARD_JOY_FUNCTIONS: tuple[str, ...] = (
    "up", "down", "left", "right", "trigger_a", "trigger_b",
)

# Keyboard key names accepted by keyboard_joystick.buttons: letters, digits,
# arrows, comma and period. The SDL2 frontend translates each name to a key
# code (frontend/sdl2_keyboard.py's KEY_NAME_TO_SDLKEY).
KEYBOARD_KEY_NAMES: tuple[str, ...] = (
    *"abcdefghijklmnopqrstuvwxyz", *"0123456789",
    "up", "down", "left", "right", "comma", "period",
)

# SDL2 frontend hotkey action → built-in key name ("f1".."f12", optionally
# "shift+"-prefixed). `hotkeys:` in the config replaces single entries.
DEFAULT_HOTKEYS: dict[str, str] = {
    "disk_next": "f6",
    "disk_eject": "shift+f6",
    "fast_forward_hold": "f7",
    "fast_forward_lock": "shift+f7",
    "pause": "f11",
    "fullscreen": "shift+f11",
    "mute": "f12",
    "reset": "shift+f12",
}

# Function-key names a hotkey may use (before the optional "shift+"). The
# SDL2 frontend translates each to a key code
# (frontend/sdl2_keyboard.py's FKEY_NAME_TO_SDLKEY).
FKEY_NAMES: tuple[str, ...] = (
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
)

_SHIFT_PREFIX = "shift+"
# Keys a hotkey may not take: F1-F5 (plain and Shift) reach the MSX matrix
# (Shift+F1..F5 are MSX F6..F10); F8-F10 are fixed save/load/screenshot keys.
_RESERVED_HOTKEY_NAMES = frozenset({
    "f1", "f2", "f3", "f4", "f5",
    "shift+f1", "shift+f2", "shift+f3", "shift+f4", "shift+f5",
    "f8", "f9", "f10",
})

_KNOWN_TOP_KEYS = frozenset({
    "machine", "speed", "scale", "extension", "extension1", "extension2",
    "slot1", "slot2", "fdd1",
    "frame_skip", "rpc", "joystick", "keyboard_joystick", "mouse", "hotkeys",
})
_KNOWN_RPC_KEYS = frozenset({"enabled", "socket"})
_KNOWN_JOYSTICK_KEYS = frozenset({"turbo_hz", "buttons"})
_KNOWN_KEYBOARD_JOYSTICK_KEYS = frozenset({"buttons"})
_KNOWN_MOUSE_KEYS = frozenset({"enabled", "port"})


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
    scale: int | None = None
    # `extension2` is slot 2's extension id; the pre-existing `extension` key
    # (unqualified, historically meant slot 2 -- see app-cli's "CLI startup
    # option config-file fallback" Requirement) keeps working as an alias for
    # it, resolved in `_opt_extension2` below. `extension1` has no legacy
    # alias -- it did not exist before slot 1 gained its own extension flag.
    extension1: str | None = None
    extension2: str | None = None
    slot1: str | None = None
    slot2: str | None = None
    # Drive A disk list; a single path in the file becomes a one-element list.
    fdd1: list[str] | None = None
    frame_skip: bool | None = None
    rpc_enabled: bool | None = None
    rpc_socket: str | None = None
    turbo_hz: float | None = None
    gamepad_buttons: dict[str, str] = field(default_factory=dict)
    keyboard_joystick_buttons: dict[str, str] = field(default_factory=dict)
    mouse_enabled: bool | None = None
    mouse_port: int | None = None
    # Overridden hotkey actions only: action name -> key name.
    hotkeys: dict[str, str] = field(default_factory=dict)

    def mouse_port_index(self) -> int | None:
        """Resolve the config-file mouse setting to a 0-based joystick port.

        Returns None when ``mouse.enabled`` is not true; defaults to Joy2
        (``DEFAULT_MOUSE_PORT``) when enabled but ``mouse.port`` is unset.
        """
        if not self.mouse_enabled:
            return None
        port = self.mouse_port if self.mouse_port is not None else DEFAULT_MOUSE_PORT
        return port - 1

    def turbo_period(self) -> int:
        """Return the turbo per-frame period from ``turbo_hz`` (min 1).

        Uses ``round(60 / turbo_hz)`` so the ON phase recurs at roughly the
        requested rate; exact only when ``turbo_hz`` divides 60.
        """
        if self.turbo_hz is None:
            return DEFAULT_TURBO_PERIOD
        return max(1, round(60.0 / self.turbo_hz))

    def gamepad_buttons_resolved(self) -> dict[str, str]:
        """Resolve joystick.buttons over the built-in defaults.

        Returns:
            ``function -> button label`` for every gamepad function.

        Raises:
            AppConfigError: If two functions resolve to the same button.
        """
        result: dict[str, str] = {}
        seen: dict[str, str] = {}
        for func, default_label in _GAMEPAD_FUNCTIONS.items():
            label = self.gamepad_buttons.get(func, default_label)
            if label in seen:
                raise AppConfigError(
                    f"{CONFIG_FILENAME}: joystick.buttons: {label!r} assigned to both "
                    f"{seen[label]!r} and {func!r}"
                )
            seen[label] = func
            result[func] = label
        return result

    def hotkey_bindings(self) -> dict[tuple[str, bool], str]:
        """Resolve the SDL2 frontend hotkey table, by key name.

        Returns:
            ``(function-key name, Shift held) -> action`` for every action,
            with ``hotkeys`` overrides applied over ``DEFAULT_HOTKEYS``. The
            frontend translates the key names to key codes.

        Raises:
            AppConfigError: If two actions end up on the same key.
        """
        result: dict[tuple[str, bool], str] = {}
        for action, default_name in DEFAULT_HOTKEYS.items():
            key_name = self.hotkeys.get(action, default_name)
            key = _hotkey_key(key_name)
            if key in result:
                raise AppConfigError(
                    f"{CONFIG_FILENAME}: hotkeys: {key_name!r} assigned to both "
                    f"{result[key]!r} and {action!r}"
                )
            result[key] = action
        return result


def _hotkey_key(key_name: str) -> tuple[str, bool]:
    """Split a validated hotkey name into (function-key name, Shift held)."""
    shift = key_name.startswith(_SHIFT_PREFIX)
    fkey = key_name[len(_SHIFT_PREFIX):] if shift else key_name
    return fkey, shift


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
    cfg.scale = _opt_positive_int(raw, "scale")
    cfg.extension1 = _opt_extension(raw, "extension1")
    cfg.extension2 = _opt_extension(raw, "extension2", legacy_alias="extension")
    cfg.slot1 = _opt_rom_path(raw, "slot1", root)
    cfg.slot2 = _opt_rom_path(raw, "slot2", root)
    cfg.fdd1 = _opt_path_list(raw, "fdd1", root)
    cfg.frame_skip = _opt_bool(raw, "frame_skip")
    _parse_rpc(raw.get("rpc"), cfg)
    _parse_joystick(raw.get("joystick"), cfg)
    _parse_keyboard_joystick(raw.get("keyboard_joystick"), cfg)
    _parse_mouse(raw.get("mouse"), cfg)
    _parse_hotkeys(raw.get("hotkeys"), cfg)
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


def _opt_positive_int(raw: dict[str, Any], key: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be an integer")
    if value < 1:
        raise AppConfigError(f"{CONFIG_FILENAME}: {key} must be a positive integer")
    return value


def _opt_extension(
    raw: dict[str, Any], key: str, legacy_alias: str | None = None
) -> str | None:
    """Resolve an extensionN key, falling back to `legacy_alias` (the
    pre-existing unqualified `extension` key, kept as slot 2's alias for
    backward compatibility) when `key` itself is absent."""
    value = _opt_str(raw, key)
    if value is None and legacy_alias is not None:
        value = _opt_str(raw, legacy_alias)
    if value is not None and value not in VALID_EXTENSIONS:
        raise AppConfigError(
            f"{CONFIG_FILENAME}: {key}: {value!r} is not one of {VALID_EXTENSIONS}"
        )
    return value


def _opt_rom_path(raw: dict[str, Any], key: str, root: Path) -> str | None:
    value = _opt_str(raw, key)
    if value is not None and not (root / value).exists():
        raise AppConfigError(f"{CONFIG_FILENAME}: {key}: file not found: {value}")
    return value


def _opt_path_list(raw: dict[str, Any], key: str, root: Path) -> list[str] | None:
    value = raw.get(key)
    if value is None:
        return None
    paths = [value] if isinstance(value, str) else value
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise AppConfigError(
            f"{CONFIG_FILENAME}: {key} must be a path string or a non-empty list of them"
        )
    for path in paths:
        if not (root / path).exists():
            raise AppConfigError(f"{CONFIG_FILENAME}: {key}: file not found: {path}")
    return list(paths)


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
        if not isinstance(label, str) or label not in GC_BUTTON_NAMES:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: joystick.buttons.{func}: unknown button {label!r} "
                f"(expected one of {', '.join(GC_BUTTON_NAMES)})"
            )
        cfg.gamepad_buttons[func] = label
    _validate_no_duplicate_buttons(cfg)


def _validate_no_duplicate_buttons(cfg: AppConfig) -> None:
    """Surface duplicate-button conflicts at load time (raises AppConfigError)."""
    cfg.gamepad_buttons_resolved()


def _parse_keyboard_joystick(keyboard_joystick: Any, cfg: AppConfig) -> None:
    if keyboard_joystick is None:
        return
    if not isinstance(keyboard_joystick, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: keyboard_joystick must be a mapping")
    for key in keyboard_joystick:
        if key not in _KNOWN_KEYBOARD_JOYSTICK_KEYS:
            print(f"warning: {CONFIG_FILENAME}: unknown key 'keyboard_joystick.{key}' (ignored)",
                  file=sys.stderr)
    _parse_keyboard_joystick_buttons(keyboard_joystick.get("buttons"), cfg)


def _parse_keyboard_joystick_buttons(buttons: Any, cfg: AppConfig) -> None:
    if buttons is None:
        return
    if not isinstance(buttons, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: keyboard_joystick.buttons must be a mapping")
    for func, key_name in buttons.items():
        if func not in _KEYBOARD_JOY_FUNCTIONS:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: keyboard_joystick.buttons: unknown function {func!r} "
                f"(expected one of {', '.join(_KEYBOARD_JOY_FUNCTIONS)})"
            )
        if not isinstance(key_name, str) or key_name not in KEYBOARD_KEY_NAMES:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: keyboard_joystick.buttons.{func}: unknown key {key_name!r} "
                f"(expected one of {', '.join(KEYBOARD_KEY_NAMES)})"
            )
        cfg.keyboard_joystick_buttons[func] = key_name
    _validate_no_duplicate_keyboard_keys(cfg)


def _validate_no_duplicate_keyboard_keys(cfg: AppConfig) -> None:
    """Reject two keyboard_joystick functions on one key (raises AppConfigError).

    Each key name maps to one key code, so comparing names is enough.
    """
    seen: dict[str, str] = {}
    for func, key_name in cfg.keyboard_joystick_buttons.items():
        if key_name in seen:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: keyboard_joystick.buttons: {key_name!r} assigned to "
                f"both {seen[key_name]!r} and {func!r}"
            )
        seen[key_name] = func


def _parse_mouse(mouse: Any, cfg: AppConfig) -> None:
    if mouse is None:
        return
    if not isinstance(mouse, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: mouse must be a mapping")
    for key in mouse:
        if key not in _KNOWN_MOUSE_KEYS:
            print(f"warning: {CONFIG_FILENAME}: unknown key 'mouse.{key}' (ignored)",
                  file=sys.stderr)
    cfg.mouse_enabled = _opt_bool(mouse, "enabled")
    cfg.mouse_port = _opt_mouse_port(mouse)


def _opt_mouse_port(raw: dict[str, Any]) -> int | None:
    if "port" not in raw or raw["port"] is None:
        return None
    value = raw["port"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2):
        raise AppConfigError(f"{CONFIG_FILENAME}: mouse.port must be 1 or 2")
    return value



def _parse_hotkeys(hotkeys: Any, cfg: AppConfig) -> None:
    if hotkeys is None:
        return
    if not isinstance(hotkeys, dict):
        raise AppConfigError(f"{CONFIG_FILENAME}: hotkeys must be a mapping")
    for action, key_name in hotkeys.items():
        if action not in DEFAULT_HOTKEYS:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: hotkeys: unknown action {action!r} "
                f"(expected one of {', '.join(DEFAULT_HOTKEYS)})"
            )
        fkey = key_name
        if isinstance(key_name, str) and key_name.startswith(_SHIFT_PREFIX):
            fkey = key_name[len(_SHIFT_PREFIX):]
        if not isinstance(key_name, str) or fkey not in FKEY_NAMES:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: hotkeys.{action}: unknown key {key_name!r} "
                f"(expected f1..f12, optionally prefixed with 'shift+')"
            )
        if key_name in _RESERVED_HOTKEY_NAMES:
            raise AppConfigError(
                f"{CONFIG_FILENAME}: hotkeys.{action}: {key_name!r} is reserved "
                f"(F1-F5 and Shift+F1-F5 are MSX keys; F8-F10 are fixed hotkeys)"
            )
        cfg.hotkeys[action] = key_name
    cfg.hotkey_bindings()  # surface conflicts at load time
