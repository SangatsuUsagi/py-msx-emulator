"""CLI wiring for the embedded RPC server (--rpc / --rpc-socket)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from msx.machine import Machine

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str], rpc_mock=None):
    """Run main() with the SDL frontend and (optionally) DebugServer patched."""
    patches = [
        patch.object(sys, "argv", [".", *argv]),
        patch("builtins.print"),
        patch.object(Path, "exists", lambda self: True),
        patch.object(Path, "read_bytes", lambda self: b"\x00" * 32768),
        patch("frontend.sdl2_frontend.run"),
        patch("msx.debugger.prompt.Debugger"),
    ]
    if rpc_mock is not None:
        patches.append(patch("msx.rpc_server.DebugServer", rpc_mock))
    for p in patches:
        p.start()
    try:
        spec = importlib.util.spec_from_file_location("_emulator_main_rpc", _MAIN_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        try:
            mod.main()
        except SystemExit:
            pass
    finally:
        for p in reversed(patches):
            p.stop()


def test_rpc_flag_starts_server_with_pause_hook() -> None:
    rpc_mock = MagicMock()
    with patch.object(Machine, "set_pause_hook") as set_hook:
        _run_main(["--machine", "cbios_msx1", "--rpc"], rpc_mock=rpc_mock)

    rpc_mock.assert_called_once()
    # Default socket path when --rpc-socket is omitted.
    _args, kwargs = rpc_mock.call_args
    assert kwargs.get("sock_path") == "/tmp/py_msx_emu.sock"
    instance = rpc_mock.return_value
    instance.start.assert_called_once()
    instance.stop.assert_called_once()
    set_hook.assert_called_once_with(instance.on_pause)


def test_rpc_socket_override() -> None:
    rpc_mock = MagicMock()
    _run_main(
        ["--machine", "cbios_msx1", "--rpc", "--rpc-socket", "/tmp/custom.sock"],
        rpc_mock=rpc_mock,
    )
    _args, kwargs = rpc_mock.call_args
    assert kwargs.get("sock_path") == "/tmp/custom.sock"


def test_no_rpc_flag_does_not_start_server() -> None:
    rpc_mock = MagicMock()
    _run_main(["--machine", "cbios_msx1"], rpc_mock=rpc_mock)
    rpc_mock.assert_not_called()
