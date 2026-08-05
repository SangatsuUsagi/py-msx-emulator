"""CLI resolves startup options with precedence builtin < py_emulator.yaml < CLI."""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import msx.machine_loader as machine_loader
from msx.app_config import AppConfig

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str], app_cfg: AppConfig | None = None):
    """Run main() with heavy build/frontend patched. Returns a result bundle.

    Returns:
        (exit_code, stdout, stderr, run_mock, build_mock, fmpac_mock, spec_spy).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    run_mock = MagicMock(name="run")
    build_mock = MagicMock(name="build_machine")
    fmpac_mock = MagicMock(name="load_fmpac_overlay", return_value=None)
    spec_spy = MagicMock(name="load_machine_spec",
                         side_effect=machine_loader.load_machine_spec)

    patches = [
        patch.object(sys, "argv", [".", *argv]),
        patch("builtins.print", side_effect=lambda *a, **kw: (
            stdout_buf.write(" ".join(str(x) for x in a) + "\n")
            if kw.get("file") is None else
            stderr_buf.write(" ".join(str(x) for x in a) + "\n")
        )),
        patch.object(Path, "exists", lambda self: True),
        patch.object(Path, "read_bytes", lambda self: b"\x00" * 32768),
        patch("frontend.sdl2_frontend.run", run_mock),
        patch("msx.debugger.prompt.Debugger"),
        patch("msx.machine_loader.build_machine", build_mock),
        patch("msx.machine_loader.load_fmpac_overlay", fmpac_mock),
        patch("msx.machine_loader.load_machine_spec", spec_spy),
    ]
    if app_cfg is not None:
        patches.append(patch("msx.app_config.load_app_config", lambda root: app_cfg))

    for p in patches:
        p.start()
    code = 0
    try:
        spec = importlib.util.spec_from_file_location("_emulator_main_config", _MAIN_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        try:
            mod.main()
        except SystemExit as exc:
            code = int(exc.code or 0)
    finally:
        for p in reversed(patches):
            p.stop()
    return (code, stdout_buf.getvalue(), stderr_buf.getvalue(),
            run_mock, build_mock, fmpac_mock, spec_spy)


# ---------------------------------------------------------------------------
# speed
# ---------------------------------------------------------------------------

def test_config_speed_used_when_cli_omitted() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(speed=2.0))
    assert run_mock.call_args.kwargs["speed"] == 2.0


def test_cli_speed_overrides_config() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--speed", "1.0"], app_cfg=AppConfig(speed=2.0))
    assert run_mock.call_args.kwargs["speed"] == 1.0


def test_builtin_speed_when_neither_set() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert run_mock.call_args.kwargs["speed"] == 1.0


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------

def test_config_scale_used_when_cli_omitted() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(scale=2))
    assert run_mock.call_args.kwargs["scale"] == 2


def test_cli_scale_overrides_config() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--scale", "4"], app_cfg=AppConfig(scale=2))
    assert run_mock.call_args.kwargs["scale"] == 4


def test_builtin_scale_when_neither_set() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert run_mock.call_args.kwargs["scale"] == 3


# ---------------------------------------------------------------------------
# mapper
# ---------------------------------------------------------------------------

def test_config_mapper_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(mapper="KonamiSCC"))
    assert build_mock.call_args.kwargs["mapper"] == "KonamiSCC"


def test_cli_mapper_overrides_config() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper", "Konami"],
        app_cfg=AppConfig(mapper="KonamiSCC"))
    assert build_mock.call_args.kwargs["mapper"] == "Konami"


def test_builtin_mapper_auto_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper"] == "auto"


# ---------------------------------------------------------------------------
# machine resolution
# ---------------------------------------------------------------------------

def test_config_machine_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, _build, _fmpac, spec_spy = _run_main(
        [], app_cfg=AppConfig(machine="cbios_msx1"))
    assert spec_spy.call_args.args[0] == "cbios_msx1"


def test_cli_machine_overrides_config() -> None:
    _c, _o, _e, _run, _build, _fmpac, spec_spy = _run_main(
        ["--machine", "cbios_msx2_jp"], app_cfg=AppConfig(machine="cbios_msx1"))
    assert spec_spy.call_args.args[0] == "cbios_msx2_jp"


def test_default_machine_when_neither_set() -> None:
    _c, _o, _e, _run, _build, _fmpac, spec_spy = _run_main([], app_cfg=AppConfig())
    assert spec_spy.call_args.args[0] == "cbios_msx2_jp"


# ---------------------------------------------------------------------------
# fmpac
# ---------------------------------------------------------------------------

def test_config_fmpac_enables_overlay() -> None:
    _c, _o, _e, _run, _build, fmpac_mock, _spy = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(fmpac=True))
    fmpac_mock.assert_called_once()


def test_no_fmpac_overlay_by_default() -> None:
    _c, _o, _e, _run, _build, fmpac_mock, _spy = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    fmpac_mock.assert_not_called()


def test_config_fmpac_conflicts_with_slot2() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot2", "game2.rom"], app_cfg=AppConfig(fmpac=True))
    assert code != 0
    assert "--fmpac" in err and "--slot2" in err


def test_config_fmpac_conflicts_with_config_slot2() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(fmpac=True, slot2="game2.rom"))
    assert code != 0
    assert "--fmpac" in err and "--slot2" in err


# ---------------------------------------------------------------------------
# slot2
# ---------------------------------------------------------------------------

def test_config_slot2_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(slot2="game2.rom"))
    assert build_mock.call_args.kwargs["cartridge2"] is not None


def test_cli_slot2_used_when_given() -> None:
    # This only proves a CLI-only --slot2 loads a slot 2 cartridge (bytes are
    # indistinguishable from the config case since _run_main's Path.read_bytes
    # patch returns fixed bytes regardless of path). CLI-over-config
    # precedence for the fmpac conflict specifically (i.e. CLI slot2 wins even
    # when config also sets fmpac) is exercised by
    # test_config_fmpac_conflicts_with_slot2.
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot2", "game2.rom"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge2"] is not None


def test_no_slot2_cartridge_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge2"] is None


# ---------------------------------------------------------------------------
# mapper2
# ---------------------------------------------------------------------------

def test_config_mapper2_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(mapper2="Konami"))
    assert build_mock.call_args.kwargs["mapper2"] == "Konami"


def test_cli_mapper2_overrides_config() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper2", "ASCII8"],
        app_cfg=AppConfig(mapper2="Konami"))
    assert build_mock.call_args.kwargs["mapper2"] == "ASCII8"


def test_builtin_mapper2_auto_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper2"] == "auto"


# ---------------------------------------------------------------------------
# frame_skip
# ---------------------------------------------------------------------------

def test_config_frame_skip_used_when_cli_omitted() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(frame_skip=False))
    assert run_mock.call_args.kwargs["frame_skip"] == "none"


def test_cli_frame_skip_overrides_config() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--frame-skip", "auto"],
        app_cfg=AppConfig(frame_skip=False))
    assert run_mock.call_args.kwargs["frame_skip"] == "auto"


def test_builtin_frame_skip_auto_when_neither_set() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert run_mock.call_args.kwargs["frame_skip"] == "auto"


# ---------------------------------------------------------------------------
# keyboard_joystick.buttons -> InputState.joy_map, threaded to build_machine
# ---------------------------------------------------------------------------

def test_keyboard_joy_map_passed_to_build_machine() -> None:
    from msx.input import JOY_MAP, _K_j

    cfg = AppConfig(keyboard_joystick_buttons={"trigger_a": "j"})
    _c, _o, _e, _run, build_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
    joy_map = build_mock.call_args.kwargs["joy_map"]
    assert joy_map[_K_j] == (0, 4)
    assert joy_map is not JOY_MAP


def test_default_keyboard_joy_map_passed_when_no_override() -> None:
    from msx.input import JOY_MAP

    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["joy_map"] is JOY_MAP


# ---------------------------------------------------------------------------
# rpc
# ---------------------------------------------------------------------------

def test_config_rpc_enabled_starts_server() -> None:
    with patch("msx.rpc_server.DebugServer") as rpc_mock:
        _run_main(["--machine", "cbios_msx1"],
                  app_cfg=AppConfig(rpc_enabled=True, rpc_socket="/tmp/cfg.sock"))
    rpc_mock.assert_called_once()
    assert rpc_mock.call_args.kwargs["sock_path"] == "/tmp/cfg.sock"


def test_cli_rpc_socket_overrides_config_socket() -> None:
    with patch("msx.rpc_server.DebugServer") as rpc_mock:
        _run_main(["--machine", "cbios_msx1", "--rpc", "--rpc-socket", "/tmp/cli.sock"],
                  app_cfg=AppConfig(rpc_enabled=True, rpc_socket="/tmp/cfg.sock"))
    assert rpc_mock.call_args.kwargs["sock_path"] == "/tmp/cli.sock"


# ---------------------------------------------------------------------------
# gamepad map / turbo threaded to frontend
# ---------------------------------------------------------------------------

def test_gamepad_map_and_turbo_passed_to_run() -> None:
    cfg = AppConfig(turbo_hz=30.0, gamepad_buttons={"trigger_a": "leftshoulder"})
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
    kwargs = run_mock.call_args.kwargs
    assert kwargs["turbo_period"] == 2
    direct, _turbo = kwargs["gamepad_map"]
    assert direct[9] == 4  # leftshoulder (index 9) → Trigger A (bit 4)
