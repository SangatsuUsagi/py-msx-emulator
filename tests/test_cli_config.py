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
        (exit_code, stdout, stderr, run_mock, build_mock, extension_mock, spec_spy).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    run_mock = MagicMock(name="run")
    build_mock = MagicMock(name="build_machine")
    extension_mock = MagicMock(name="load_extension_overlay", return_value=None)
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
        patch("msx.machine_loader.load_extension_overlay", extension_mock),
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
            run_mock, build_mock, extension_mock, spec_spy)


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
# mapper (CLI-only -- no py_emulator.yaml equivalent, see app-config-file spec
# "Cartridge mapper selection is CLI-only")
# ---------------------------------------------------------------------------

def test_cli_mapper_used() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper", "Konami"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper"] == "Konami"


def test_builtin_mapper_auto_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper"] == "auto"


def test_config_mapper_attr_has_no_effect() -> None:
    """Regression: even a stray `mapper` attribute on AppConfig (as a stale
    config-loader stub might carry) must not affect the effective mapper --
    only `--mapper` does."""
    cfg = AppConfig()
    cfg.mapper = "KonamiSCC"  # type: ignore[attr-defined]
    _c, _o, _e, _run, build_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
    assert build_mock.call_args.kwargs["mapper"] == "auto"


# ---------------------------------------------------------------------------
# machine resolution
# ---------------------------------------------------------------------------

def test_config_machine_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, _build, _ext, spec_spy = _run_main(
        [], app_cfg=AppConfig(machine="cbios_msx1"))
    assert spec_spy.call_args.args[0] == "cbios_msx1"


def test_cli_machine_overrides_config() -> None:
    _c, _o, _e, _run, _build, _ext, spec_spy = _run_main(
        ["--machine", "cbios_msx2_jp"], app_cfg=AppConfig(machine="cbios_msx1"))
    assert spec_spy.call_args.args[0] == "cbios_msx2_jp"


def test_default_machine_when_neither_set() -> None:
    _c, _o, _e, _run, _build, _ext, spec_spy = _run_main([], app_cfg=AppConfig())
    assert spec_spy.call_args.args[0] == "cbios_msx2_jp"


# ---------------------------------------------------------------------------
# extension
# ---------------------------------------------------------------------------

def test_config_extension_enables_overlay() -> None:
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(extension2="fmpac"))
    extension_mock.assert_called_once()


def test_cli_bare_extension_is_an_alias_for_extension2() -> None:
    # '--extension' meant slot 2 before --extension1/--extension2 existed;
    # kept as a lower-priority alias for --extension2 (not --extension1) so
    # existing usage keeps its exact prior meaning.
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension", "fmpac"], app_cfg=AppConfig())
    extension_mock.assert_called_once()
    assert extension_mock.call_args_list[0].args[0] == "fmpac"


def test_config_legacy_extension_key_enables_slot2_overlay() -> None:
    # The pre-existing unqualified `extension` key keeps working as an
    # `extension2` alias -- see AppConfig.extension2's docstring.
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(extension2="fmpac"))
    extension_mock.assert_called_once()


def test_no_extension_overlay_by_default() -> None:
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    extension_mock.assert_not_called()


def test_config_extension2_conflicts_with_slot2() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot2", "game2.rom"],
        app_cfg=AppConfig(extension2="fmpac"))
    assert code != 0
    assert "--extension2" in err and "--slot2" in err


def test_config_extension2_conflicts_with_config_slot2() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(extension2="fmpac", slot2="game2.rom"))
    assert code != 0
    assert "--extension2" in err and "--slot2" in err


def test_config_extension2_conflicts_with_mapper2() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper2", "Konami"],
        app_cfg=AppConfig(extension2="scc_plus"))
    assert code != 0
    assert "--extension2" in err and "--mapper2" in err


def test_extension2_none_overrides_config_extension() -> None:
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension2", "none"],
        app_cfg=AppConfig(extension2="fmpac"))
    extension_mock.assert_not_called()


def test_extension2_none_frees_slot2_from_config_extension_conflict() -> None:
    code, _o, err, _run, build_mock, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension2", "none", "--slot2", "game2.rom"],
        app_cfg=AppConfig(extension2="fmpac"))
    assert code == 0
    assert err == ""
    extension_mock.assert_not_called()
    assert build_mock.call_args.kwargs["cartridge2"] is not None


def test_extension2_none_frees_mapper2_from_config_extension_conflict() -> None:
    code, _o, err, _run, build_mock, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension2", "none", "--mapper2", "Konami"],
        app_cfg=AppConfig(extension2="scc_plus"))
    assert code == 0
    assert err == ""
    extension_mock.assert_not_called()
    assert build_mock.call_args.kwargs["mapper2"] == "Konami"


def test_extension2_none_without_config_extension_is_a_no_op() -> None:
    _c, _o, _e, _run, _build, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension2", "none"], app_cfg=AppConfig())
    extension_mock.assert_not_called()


# ---------------------------------------------------------------------------
# extension1 -- flat-shape ids (fmpac, scc_plus) place correctly on slot 1;
# expanded-shape ids are PHASE-3-TEMPORARY rejected (Memory's slot-1
# sub-slot expansion lands in a later phase of openspec/changes/
# slot1-slot2-symmetrization -- see __main__.py's PHASE-3-TEMPORARY guard)
# ---------------------------------------------------------------------------

def test_extension1_flat_shape_accepted() -> None:
    code, _o, err, _run, build_mock, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "fmpac"], app_cfg=AppConfig())
    assert code == 0
    assert err == ""
    build_mock.assert_called_once()
    assert extension_mock.call_args_list[0].args[0] == "fmpac"


def test_config_extension1_flat_shape_accepted() -> None:
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(extension1="fmpac"))
    assert code == 0
    assert err == ""
    build_mock.assert_called_once()


def test_extension1_expanded_shape_accepted() -> None:
    code, _o, err, _run, build_mock, extension_mock, _spy = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "hbi_j1"], app_cfg=AppConfig())
    assert code == 0
    assert err == ""
    build_mock.assert_called_once()
    assert extension_mock.call_args_list[0].args[0] == "hbi_j1"


def test_config_extension1_expanded_shape_accepted() -> None:
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(extension1="memory_512k"))
    assert code == 0
    assert err == ""
    build_mock.assert_called_once()


def test_extension1_conflicts_with_positional_cartridge() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "fmpac", "game.rom"], app_cfg=AppConfig())
    assert code != 0
    assert "--extension1" in err


def test_extension1_conflicts_with_slot1() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "fmpac", "--slot1", "game.rom"],
        app_cfg=AppConfig())
    assert code != 0
    assert "--extension1" in err


def test_extension1_conflicts_with_mapper1() -> None:
    code, _o, err, *_ = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "fmpac", "--mapper1", "Konami"],
        app_cfg=AppConfig())
    assert code != 0
    assert "--extension1" in err


def test_identical_extension_on_both_slots_rejected() -> None:
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--extension1", "fmpac", "--extension2", "fmpac"],
        app_cfg=AppConfig())
    assert code != 0
    assert "--extension1" in err and "--extension2" in err
    build_mock.assert_not_called()


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
    # precedence for the extension conflict specifically (i.e. CLI slot2 wins
    # even when config also sets extension) is exercised by
    # test_config_extension_conflicts_with_slot2.
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot2", "game2.rom"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge2"] is not None


def test_no_slot2_cartridge_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge2"] is None


# ---------------------------------------------------------------------------
# slot1 / mapper1 (--slot1 mirrors --slot2; the positional cartridge argument
# and --mapper remain accepted as --slot1/--mapper1's shorthand and alias)
# ---------------------------------------------------------------------------

def test_cli_slot1_used_when_given() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot1", "game.rom"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge"] is not None


def test_cli_bare_slot_is_an_alias_for_slot1() -> None:
    # '--slot' never existed before --slot1/--slot2; added purely for CLI
    # naming symmetry with --mapper's alias for --mapper1.
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot", "game.rom"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge"] is not None


def test_config_slot1_used_when_cli_omitted() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig(slot1="game.rom"))
    assert build_mock.call_args.kwargs["cartridge"] is not None


def test_positional_cartridge_is_slot1_mapper1_auto_shorthand() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "game.rom"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["cartridge"] is not None
    assert build_mock.call_args.kwargs["mapper"] == "auto"


def test_positional_cartridge_and_slot1_together_rejected() -> None:
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "game.rom", "--slot1", "game.rom"], app_cfg=AppConfig())
    assert code != 0
    assert "--slot1" in err
    build_mock.assert_not_called()


def test_mapper1_alias_for_mapper() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper1", "Konami"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper"] == "Konami"


def test_mapper1_accepts_sram_backed_type() -> None:
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper1", "GameMaster2"], app_cfg=AppConfig())
    assert code == 0
    assert err == ""
    assert build_mock.call_args.kwargs["mapper"] == "GameMaster2"


# ---------------------------------------------------------------------------
# Identical ROM in both slots (openspec/changes/slot1-slot2-symmetrization's
# app-cli "Identical ROM or extension across both slots is rejected")
# ---------------------------------------------------------------------------

def test_identical_cartridge_in_both_slots_rejected() -> None:
    # _run_main's Path.read_bytes patch returns the same fixed bytes
    # regardless of path, so any two cartridge slots both populated collide.
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "game.rom", "--slot2", "game.rom"], app_cfg=AppConfig())
    assert code != 0
    assert "identical" in err.lower()
    build_mock.assert_not_called()


def test_only_slot2_populated_is_not_an_identical_cartridge_conflict() -> None:
    # Slot 1 empty (cartridge is None) short-circuits the identical-cartridge
    # check regardless of what Path.read_bytes would return for slot 2.
    _c, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--slot2", "game2.rom"], app_cfg=AppConfig())
    assert err == ""
    assert build_mock.call_args.kwargs["cartridge2"] is not None


# ---------------------------------------------------------------------------
# mapper2 (CLI-only -- no py_emulator.yaml equivalent, see app-config-file
# spec "Cartridge mapper selection is CLI-only")
# ---------------------------------------------------------------------------

def test_cli_mapper2_used() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper2", "ASCII8"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper2"] == "ASCII8"


def test_cli_mapper2_accepts_konamiscc() -> None:
    # --mapper2 KonamiSCC is a valid argparse choice (VALID_MAPPERS2) and
    # reaches build_machine unchanged; build_machine's own resolution is
    # what actually constructs the SCC chip or rejects a slot-1 conflict
    # (see tests/test_machine.py).
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper2", "KonamiSCC"], app_cfg=AppConfig())
    assert code == 0
    assert err == ""
    assert build_mock.call_args.kwargs["mapper2"] == "KonamiSCC"


def test_cli_mapper2_accepts_rtype_and_fixed_page_types() -> None:
    # --mapper2 R-Type/Page2/0x4000/0x8000 are valid argparse choices
    # (VALID_MAPPERS2): these mapper types touch neither SRAM nor the SCC/DAC
    # audio path, unlike the still-rejected SRAM-backed types (ASCII8SRAM2/8,
    # ASCII16SRAM2/8, KoeiSRAM32, GameMaster2). Actual construction is
    # covered by tests/test_machine.py.
    for mapper2_type in ("R-Type", "Page2", "0x4000", "0x8000"):
        code, _o, err, _run, build_mock, *_ = _run_main(
            ["--machine", "cbios_msx1", "--mapper2", mapper2_type], app_cfg=AppConfig())
        assert code == 0
        assert err == ""
        assert build_mock.call_args.kwargs["mapper2"] == mapper2_type


def test_cli_mapper2_accepts_sram_backed_type() -> None:
    # --mapper1 and --mapper2 now accept an identical VALID_MAPPERS list
    # (openspec/changes/slot1-slot2-symmetrization) -- GameMaster2 (and the
    # other SRAM-backed types) were rejected here before that change.
    code, _o, err, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1", "--mapper2", "GameMaster2"], app_cfg=AppConfig())
    assert code == 0
    assert err == ""
    assert build_mock.call_args.kwargs["mapper2"] == "GameMaster2"


def test_builtin_mapper2_auto_when_neither_set() -> None:
    _c, _o, _e, _run, build_mock, *_ = _run_main(
        ["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert build_mock.call_args.kwargs["mapper2"] == "auto"


def test_config_mapper2_attr_has_no_effect() -> None:
    """Regression: a stray `mapper2` attribute on AppConfig must not affect
    the effective slot 2 mapper -- only `--mapper2` does."""
    cfg = AppConfig()
    cfg.mapper2 = "Konami"  # type: ignore[attr-defined]
    _c, _o, _e, _run, build_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
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

def test_keyboard_joystick_names_passed_to_run() -> None:
    # The frontend resolves the names; build_machine takes no key map.
    cfg = AppConfig(keyboard_joystick_buttons={"trigger_a": "j"})
    _c, _o, _e, run_mock, build_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
    assert run_mock.call_args.kwargs["keyboard_joystick"] == {"trigger_a": "j"}
    assert "joy_map" not in build_mock.call_args.kwargs


def test_default_keyboard_joystick_passed_when_no_override() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert run_mock.call_args.kwargs["keyboard_joystick"] == {}


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


# ---------------------------------------------------------------------------
# fdd1 (drive A disk list)
# ---------------------------------------------------------------------------

def test_config_fdd1_used_when_cli_omitted() -> None:
    _c, _o, _e, run_mock, build_mock, *_ = _run_main(
        ["--machine", "hb_f1xd"], app_cfg=AppConfig(fdd1=["a.dsk", "b.dsk"]))
    assert build_mock.call_args.kwargs["fdd1"] == Path("a.dsk")
    assert run_mock.call_args.kwargs["fdd1_images"] == [Path("a.dsk"), Path("b.dsk")]


def test_cli_fdd1_overrides_config_fdd1() -> None:
    _c, _o, _e, run_mock, build_mock, *_ = _run_main(
        ["--machine", "hb_f1xd", "--fdd1", "c.dsk"],
        app_cfg=AppConfig(fdd1=["a.dsk", "b.dsk"]))
    assert build_mock.call_args.kwargs["fdd1"] == Path("c.dsk")
    assert run_mock.call_args.kwargs["fdd1_images"] == [Path("c.dsk")]


# ---------------------------------------------------------------------------
# hotkeys
# ---------------------------------------------------------------------------

def test_config_hotkeys_passed_to_the_frontend() -> None:
    cfg = AppConfig(hotkeys={"mute": "shift+f8"})
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=cfg)
    assert run_mock.call_args.kwargs["hotkey_bindings"] == cfg.hotkey_bindings()


def test_no_hotkeys_config_passes_the_built_in_table() -> None:
    _c, _o, _e, run_mock, *_ = _run_main(["--machine", "cbios_msx1"], app_cfg=AppConfig())
    assert run_mock.call_args.kwargs["hotkey_bindings"] == AppConfig().hotkey_bindings()
