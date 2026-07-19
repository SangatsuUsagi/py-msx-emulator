"""RPC fdd.swap mount / eject against a machine with a floppy interface."""
from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793
from msx.rpc_server import DebugServer
from tests.factories import make_machine

_2DD = 737280


class _Client:
    def __init__(self, path: str) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect(path)
        self._buf = b""

    def _readframe(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def result(self, method: str, params: dict) -> dict:
        req = {"id": method, "method": method, "params": params}
        self._sock.sendall((json.dumps(req) + "\n").encode())
        while True:
            frame = self._readframe()
            if "notification" in frame:
                continue
            assert "error" not in frame, frame
            return frame["result"]

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def fdd_rig(tmp_path, sock_dir) -> Iterator[tuple]:
    machine = make_machine(rom=bytes(32768))
    machine.fdc = SonyPhilipsInterface(WD2793(), [DiskDrive()], disk_rom=bytes(16384))
    srv = DebugServer(machine, sock_path=str(sock_dir / "fdd.sock"))
    srv.start()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            srv.drain()
            time.sleep(0.001)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    client = _Client(str(srv._sock_path))
    client._readframe()  # banner
    try:
        yield machine, client, tmp_path
    finally:
        client.close()
        stop.set()
        thread.join(timeout=1.0)
        srv.stop()


def _blank_dsk(path: Path) -> Path:
    path.write_bytes(bytes(_2DD))
    return path


def test_fdd_mount(fdd_rig) -> None:
    machine, client, tmp_path = fdd_rig
    dsk = _blank_dsk(tmp_path / "disk.dsk")
    r = client.result("fdd.swap", {"drive": 1, "path": str(dsk)})
    assert r["mounted"] is True
    assert r["path"] == str(dsk)
    assert machine.fdc.drives[0].image is not None


def test_fdd_eject(fdd_rig) -> None:
    machine, client, tmp_path = fdd_rig
    dsk = _blank_dsk(tmp_path / "disk.dsk")
    client.result("fdd.swap", {"drive": 1, "path": str(dsk)})
    r = client.result("fdd.swap", {"drive": 1, "path": None})
    assert r["mounted"] is False
    assert machine.fdc.drives[0].image is None


def test_fdd_no_interface_errors(sock_dir) -> None:
    machine = make_machine(rom=bytes(32768))  # no FDC
    assert machine.fdc is None
    srv = DebugServer(machine, sock_path=str(sock_dir / "nofdd.sock"))
    resp = srv._dispatch(
        {"id": "1", "method": "fdd.swap", "params": {"drive": 1, "path": "x.dsk"}}
    )
    assert resp["error"]["code"] == 3
