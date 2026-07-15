"""Tests for the MCP server adapter (tools/mcp_server.py).

Exercises the transport helper and a couple of tool wrappers against a live
DebugServer, plus tool registration.
"""
from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp.types import ImageContent

from msx.rpc_server import DebugServer
from tests.factories import make_machine

_TOOLS = Path(__file__).parent.parent / "tools"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def live(tmp_path) -> Iterator[tuple]:
    machine = make_machine(rom=bytes(32768))
    machine.memory.slot_register = 0xD4
    sock = str(tmp_path / "mcp.sock")
    srv = DebugServer(machine, sock_path=sock)
    machine.set_pause_hook(srv.on_pause)
    srv.start()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            srv.drain()
            if not srv.pause_state.paused:
                try:
                    machine.run_frame(skip_render=True)
                except BaseException:  # noqa: BLE001
                    pass
            else:
                time.sleep(0.001)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

    mod = _load("mcp_server_under_test", "mcp_server.py")
    mod.SOCK_PATH = sock
    try:
        yield mod, machine, srv
    finally:
        stop.set()
        thread.join(timeout=1.0)
        srv.stop()


def test_tools_registered() -> None:
    mod = _load("mcp_server_reg", "mcp_server.py")
    tools = asyncio.run(mod.mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "emulator_status", "cpu_step", "cpu_continue_until_pause",
        "memory_read", "memory_write", "screen_capture", "fdd_swap",
    } <= names


def test_norm_addr() -> None:
    mod = _load("mcp_server_addr", "mcp_server.py")
    assert mod._norm_addr("C000") == "0xC000"
    assert mod._norm_addr("0xC000") == "0xC000"


def test_rpc_roundtrip_status(live) -> None:
    mod, _machine, _srv = live
    r = mod._rpc("debugger.status")
    assert "paused" in r and "pc" in r


def test_rpc_raises_on_emulator_error(live) -> None:
    mod, _machine, _srv = live
    with pytest.raises(RuntimeError, match="Emulator error"):
        mod._rpc("does.not.exist")


def test_rpc_raises_when_unreachable(tmp_path) -> None:
    mod = _load("mcp_server_unreach", "mcp_server.py")
    mod.SOCK_PATH = str(tmp_path / "nope.sock")
    with pytest.raises(RuntimeError, match="Cannot connect"):
        mod._rpc("debugger.status")


def test_status_tool(live) -> None:
    mod, _machine, _srv = live
    out = mod.emulator_status()
    assert out.startswith(("RUNNING", "PAUSED"))


def test_pause_then_step_tool(live) -> None:
    mod, _machine, _srv = live
    mod.emulator_pause()
    out = mod.cpu_step()
    assert "NOP" in out and "T-states" in out


def test_screen_capture_returns_image(live) -> None:
    mod, _machine, _srv = live
    parts = mod.screen_capture(scale=1)
    images = [p for p in parts if isinstance(p, ImageContent)]
    assert images and images[0].mimeType == "image/png"


def test_memory_write_read_tools(live) -> None:
    mod, _machine, _srv = live
    mod.emulator_pause()
    mod.memory_write("0xC000", "3E 01 C9")
    out = mod.memory_read("0xC000", 3)
    assert "3E 01 C9" in out
