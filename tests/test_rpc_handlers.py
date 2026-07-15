"""End-to-end tests for the RPC method handlers (phase 3).

A background thread runs a realistic host loop (drain + run_frame when not
paused) with the pause hook wired to the server, so breakpoints, stepping, and
continue_sync behave as they will under the real frontend.
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from msx.rpc_server import DebugServer
from tests.factories import make_machine


class _Client:
    def __init__(self, path: str) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect(path)
        self._buf = b""
        self.notifications: list[dict] = []

    def _readframe(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError("server closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def call(self, method: str, params: dict | None = None) -> dict:
        req: dict = {"id": method, "method": method}
        if params is not None:
            req["params"] = params
        self._sock.sendall((json.dumps(req) + "\n").encode())
        while True:
            frame = self._readframe()
            if "notification" in frame:
                self.notifications.append(frame)
                continue
            return frame

    def result(self, method: str, params: dict | None = None) -> dict:
        resp = self.call(method, params)
        assert "error" not in resp, resp
        return resp["result"]

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def rig(tmp_path) -> Iterator[tuple]:
    machine = make_machine(rom=bytes(32768))  # all NOP
    machine.memory.slot_register = 0xD4  # RAM at page 3 (0xC000+)
    srv = DebugServer(machine, sock_path=str(tmp_path / "h.sock"))
    machine.set_pause_hook(srv.on_pause)
    srv.start()

    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            srv.drain()
            if not srv.pause_state.paused:
                try:
                    machine.run_frame(skip_render=True)
                except BaseException:  # noqa: BLE001 — keep the test loop alive
                    pass
            else:
                time.sleep(0.001)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

    client = _Client(str(srv._sock_path))
    banner = client._readframe()
    assert banner["notification"] == "connected"
    try:
        yield machine, srv, client
    finally:
        client.close()
        stop.set()
        thread.join(timeout=1.0)
        srv.stop()


def _pause(client: _Client) -> None:
    r = client.result("debugger.pause")
    assert r["paused"] is True


# ── CPU ──────────────────────────────────────────────────────────────────────

def test_get_registers_while_running(rig) -> None:
    _machine, _srv, client = rig
    regs = client.result("cpu.get_registers")["registers"]
    assert "PC" in regs and regs["PC"].startswith("0x")


def test_step_requires_paused(rig) -> None:
    _machine, _srv, client = rig
    resp = client.call("cpu.step")
    assert resp["error"]["code"] == 1


def test_pause_then_step_nop(rig) -> None:
    _machine, _srv, client = rig
    _pause(client)
    r = client.result("cpu.step")
    assert r["mnemonic"] == "NOP"
    assert r["t_states"] == 4
    assert "registers" in r


def test_breakpoint_hit_via_continue_sync(rig) -> None:
    _machine, _srv, client = rig
    _pause(client)
    bp = client.result("debug.set_breakpoint", {"address": "0x0100"})
    assert bp["address"] == "0x0100"
    r = client.result("cpu.continue_sync", {"timeout_ms": 3000})
    assert r["paused"] is True
    assert r["reason"] == "breakpoint"
    assert r["pc"] == "0x0100"


def test_continue_sync_timeout(rig) -> None:
    _machine, _srv, client = rig
    _pause(client)
    r = client.result("cpu.continue_sync", {"timeout_ms": 100})
    assert r["paused"] is False
    assert r["reason"] == "timeout"


def test_list_and_remove_breakpoint(rig) -> None:
    _machine, _srv, client = rig
    bp = client.result("debug.set_breakpoint", {"address": "C000"})
    listed = client.result("debug.list_breakpoints")["breakpoints"]
    assert any(b["id"] == bp["id"] for b in listed)
    assert client.result("debug.remove_breakpoint", {"id": bp["id"]})["removed"] is True
    listed2 = client.result("debug.list_breakpoints")["breakpoints"]
    assert all(b["id"] != bp["id"] for b in listed2)


# ── Memory ───────────────────────────────────────────────────────────────────

def test_memory_write_requires_paused(rig) -> None:
    _machine, _srv, client = rig
    resp = client.call("memory.write", {"address": "0xC000", "data": "3E"})
    assert resp["error"]["code"] == 1


def test_memory_write_read_roundtrip(rig) -> None:
    _machine, _srv, client = rig
    _pause(client)
    w = client.result("memory.write", {"address": "0xC000", "data": "3E 01 C9"})
    assert w["written"] == 3
    r = client.result("memory.read", {"address": "0xC000", "length": 3})
    assert r["data"] == "3E 01 C9"


def test_disassemble(rig) -> None:
    _machine, _srv, client = rig
    _pause(client)
    client.result("memory.write", {"address": "0xC000", "data": "3E 01 C9"})
    instrs = client.result("memory.disassemble", {"address": "0xC000", "count": 2})["instructions"]
    assert instrs[0]["address"] == "0xC000"
    assert "LD" in instrs[0]["mnemonic"]
    assert instrs[1]["mnemonic"] == "RET"


def test_read_vram(rig) -> None:
    _machine, _srv, client = rig
    r = client.result("memory.read_vram", {"address": "0x0000", "length": 4})
    assert len(r["data"].split()) == 4


def test_invalid_address(rig) -> None:
    _machine, _srv, client = rig
    resp = client.call("memory.read", {"address": "ZZZZ", "length": 1})
    assert resp["error"]["code"] == -32602


# ── VDP ──────────────────────────────────────────────────────────────────────

def test_vdp_registers_and_status(rig) -> None:
    _machine, _srv, client = rig
    regs = client.result("vdp.get_registers")
    assert regs["type"] in ("V9938", "TMS9918A")
    assert "R0" in regs["registers"]
    status = client.result("vdp.get_status")
    assert status["status"].startswith("0x")


# ── Input ────────────────────────────────────────────────────────────────────

def test_press_and_release_key(rig) -> None:
    machine, _srv, client = rig
    client.result("input.press_key", {"row": 8, "bit": 0})
    assert machine.input.matrix[8] & 1 == 0
    client.result("input.release_key", {"row": 8, "bit": 0})
    assert machine.input.matrix[8] & 1 == 1


def test_press_key_named_auto_release(rig) -> None:
    machine, _srv, client = rig
    r = client.result("input.press_key_named", {"key": "space", "duration_ms": 20})
    assert (r["row"], r["bit"]) == (8, 0)
    assert machine.input.matrix[8] & 1 == 0  # pressed now
    deadline = time.time() + 1.0
    while time.time() < deadline and machine.input.matrix[8] & 1 == 0:
        time.sleep(0.005)
    assert machine.input.matrix[8] & 1 == 1  # auto-released


def test_press_key_named_unknown(rig) -> None:
    _machine, _srv, client = rig
    resp = client.call("input.press_key_named", {"key": "NOPE"})
    assert resp["error"]["code"] == -32602


def test_joystick(rig) -> None:
    machine, _srv, client = rig
    r = client.result("input.joystick", {"port": 1, "right": True, "trigger_a": True})
    assert r["state"]["right"] is True and r["state"]["trigger_a"] is True
    joy = machine.input.joy1
    assert (joy >> 3) & 1 == 0  # right pressed (active low)
    assert (joy >> 4) & 1 == 0  # trigger A pressed
    client.result("input.joystick_release", {"port": 1})
    assert machine.input.joy1 == 0x3F


# ── Screenshot ───────────────────────────────────────────────────────────────

def test_screen_capture(rig) -> None:
    _machine, _srv, client = rig
    r = client.result("screen.capture", {"scale": 1})
    assert r["format"] == "png"
    png = base64.b64decode(r["data"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert r["width"] > 0 and r["height"] > 0


def test_screen_capture_scaled(rig) -> None:
    _machine, _srv, client = rig
    native = client.result("screen.capture", {"scale": 1})
    scaled = client.result("screen.capture", {"scale": 2})
    assert scaled["width"] == native["width"] * 2
    assert scaled["height"] == native["height"] * 2
