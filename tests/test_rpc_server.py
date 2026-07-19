"""Transport / dispatch tests for the socket RPC server core (phase 2).

These exercise the framing, error codes, queue+drain bridge, and pause push
without a full frontend loop: a small background thread pumps ``drain()`` to
stand in for the emulator thread.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from msx.rpc_server import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE,
    DebugServer,
)
from tests.factories import make_machine


class _Client:
    """Line-buffered test client over the Unix socket."""

    def __init__(self, path: str) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(3.0)
        self._sock.connect(path)
        self._buf = b""

    def readline(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError("server closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def send(self, obj: dict) -> None:
        self._sock.sendall((json.dumps(obj) + "\n").encode())

    def send_raw(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def server(sock_dir) -> Iterator[DebugServer]:
    machine = make_machine(rom=bytes(32768))
    srv = DebugServer(machine, sock_path=str(sock_dir / "rpc.sock"))
    srv.start()

    stop = threading.Event()

    def _pump() -> None:
        while not stop.is_set():
            srv.drain()
            time.sleep(0.001)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()
    try:
        yield srv
    finally:
        stop.set()
        pump.join(timeout=1.0)
        srv.stop()


def _connect(server: DebugServer) -> _Client:
    client = _Client(str(server._sock_path))
    banner = client.readline()
    assert banner["notification"] == "connected"
    return client


def test_connection_banner(server: DebugServer) -> None:
    client = _Client(str(server._sock_path))
    banner = client.readline()
    assert banner["notification"] == "connected"
    assert banner["emulator"] == "py-msx-emulator"
    assert "version" in banner
    client.close()


def test_status_call_echoes_id(server: DebugServer) -> None:
    client = _connect(server)
    client.send({"id": "abc", "method": "debugger.status"})
    resp = client.readline()
    assert resp["id"] == "abc"
    assert resp["result"]["paused"] is False
    assert resp["result"]["pc"].startswith("0x")
    client.close()


def test_parse_error(server: DebugServer) -> None:
    client = _connect(server)
    client.send_raw(b"{ this is not json }\n")
    resp = client.readline()
    assert resp["error"]["code"] == ERR_PARSE
    client.close()


def test_unknown_method(server: DebugServer) -> None:
    client = _connect(server)
    client.send({"id": "1", "method": "does.not.exist"})
    resp = client.readline()
    assert resp["id"] == "1"
    assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND
    client.close()


def test_invalid_params_type(server: DebugServer) -> None:
    client = _connect(server)
    client.send({"id": "1", "method": "debugger.status", "params": [1, 2, 3]})
    resp = client.readline()
    assert resp["error"]["code"] == ERR_INVALID_PARAMS
    client.close()


def test_pause_sets_state(server: DebugServer) -> None:
    client = _connect(server)
    client.send({"id": "p", "method": "debugger.pause"})
    resp = client.readline()
    assert resp["result"]["paused"] is True
    assert resp["result"]["reason"] == "user_request"
    assert server.pause_state.paused is True
    client.close()


def test_on_pause_pushes_notification(server: DebugServer) -> None:
    client = _connect(server)
    # Simulate a breakpoint hit on the emulator thread.
    server.on_pause("breakpoint", 0xC100)
    frame = client.readline()
    assert frame["notification"] == "paused"
    assert frame["reason"] == "breakpoint"
    assert frame["pc"] == "0xC100"
    assert "AF" in frame["registers"]
    assert server.pause_state.paused is True
    client.close()


def test_registers_shape(server: DebugServer) -> None:
    client = _connect(server)
    server.on_pause("breakpoint", 0x0000)
    frame = client.readline()
    regs = frame["registers"]
    for key in ("AF", "BC", "DE", "HL", "IX", "IY", "SP", "PC",
                "AF'", "BC'", "DE'", "HL'", "I", "R"):
        assert key in regs, f"missing register {key}"
    assert isinstance(regs["IFF1"], bool)
    assert isinstance(regs["IM"], int)
    client.close()


def test_stale_socket_file_replaced(sock_dir) -> None:
    path = sock_dir / "stale.sock"
    path.write_text("not a socket")
    machine = make_machine(rom=bytes(32768))
    srv = DebugServer(machine, sock_path=str(path))
    srv.start()  # must not raise despite the pre-existing file
    try:
        client = _Client(str(path))
        assert client.readline()["notification"] == "connected"
        client.close()
    finally:
        srv.stop()
