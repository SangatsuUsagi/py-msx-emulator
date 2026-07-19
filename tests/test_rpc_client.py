"""Tests for the manual RPC test client (tools/rpc_client.py)."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.rpc_server import DebugServer
from tests.factories import make_machine

_TOOLS = Path(__file__).parent.parent / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / "rpc_client.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_coerce_types() -> None:
    mod = _load("rpc_client_coerce")
    assert mod._coerce("true") is True
    assert mod._coerce("false") is False
    assert mod._coerce("null") is None
    assert mod._coerce("16") == 16
    assert mod._coerce("0xC000") == 0xC000
    assert mod._coerce("SPACE") == "SPACE"


def test_parse_params_kv() -> None:
    mod = _load("rpc_client_kv")
    assert mod._parse_params(["address=0xC000", "length=16"]) == {
        "address": 0xC000, "length": 16,
    }


def test_parse_params_json() -> None:
    mod = _load("rpc_client_json")
    assert mod._parse_params(['{"address": "0xC000"}']) == {"address": "0xC000"}


def test_parse_params_empty() -> None:
    mod = _load("rpc_client_empty")
    assert mod._parse_params([]) is None


def test_parse_params_bad_kv() -> None:
    mod = _load("rpc_client_bad")
    with pytest.raises(ValueError):
        mod._parse_params(["not_a_pair"])


@pytest.fixture
def live_sock(sock_dir) -> Iterator[str]:
    machine = make_machine(rom=bytes(32768))
    sock = str(sock_dir / "cli.sock")
    srv = DebugServer(machine, sock_path=sock)
    srv.start()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            srv.drain()
            time.sleep(0.001)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield sock
    finally:
        stop.set()
        thread.join(timeout=1.0)
        srv.stop()


def test_main_prints_result(live_sock, capsys) -> None:
    mod = _load("rpc_client_main")
    argv = [".", "--socket", live_sock, "debugger.status"]
    with patch.object(sys, "argv", argv):
        mod.main()
    out = json.loads(capsys.readouterr().out)
    assert "result" in out
    assert "paused" in out["result"]


def test_main_unreachable_exits(sock_dir) -> None:
    mod = _load("rpc_client_unreach")
    argv = [".", "--socket", str(sock_dir / "nope.sock"), "debugger.status"]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
