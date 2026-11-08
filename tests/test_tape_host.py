"""The host tape holder (host/tape.py) and the tape surfaces built on it:
the debugger's tape commands (debug-monitor), the tape.* RPC methods
(remote-socket-rpc) and the --debug motor line (diag-debug-logger)."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from host.cas_image import CAS_HEADER, CPU_HZ, parse_cas
from host.debug_log import DebugLogFormatter
from host.debugger.prompt import Debugger
from host.machine_config import load_machine_config
from host.rpc_server import DebugServer, RpcError
from host.tape import TapeDeck, TapeError
from msx.boundary import TRACE_DEBUG, TRACE_EVENT_TAPE_MOTOR, TraceEvent
from msx.machine import Machine
from msx.machine_loader import build_machine
from tests.factories import _msx1_spec

_BINARY = bytes([0xD0] * 10)
_ASCII = bytes([0xEA] * 10)


def _machine(port: bool = True) -> Machine:
    spec = dataclasses.replace(_msx1_spec(), cassette_port=port)
    rom = bytes([0xF3, 0x18, 0xFE]) + bytes(32765)
    return build_machine(load_machine_config(spec, bios_override=rom).config)


def _binary_cas(tmp_path: Path, name: str = "game.cas") -> Path:
    path = tmp_path / name
    path.write_bytes(CAS_HEADER + _BINARY + b"GAME  " + CAS_HEADER + bytes(100))
    return path


def _ascii_cas(tmp_path: Path) -> Path:
    """One ASCII file of three blocks: header, data, data ending in 0x1A."""
    path = tmp_path / "text.cas"
    path.write_bytes(CAS_HEADER + _ASCII + b"TEXT  " + CAS_HEADER + b"A" * 256
                     + CAS_HEADER + b"B" * 10 + b"\x1a")
    return path


def _bad_cas(tmp_path: Path) -> Path:
    path = tmp_path / "bad.cas"
    path.write_bytes(bytes(64))
    return path


# --- host/tape.py ---

def test_the_deck_keeps_the_file_and_the_blocks(tmp_path: Path) -> None:
    deck = TapeDeck(_machine())
    path = _binary_cas(tmp_path)
    deck.load(path)
    status = deck.status()
    assert status.mounted and status.path == path and status.block == 0
    assert [b.type for b in deck.blocks] == ["binary", "data"]
    assert status.length == parse_cas(path.read_bytes()).length


def test_a_failed_load_keeps_the_deck(tmp_path: Path) -> None:
    deck = TapeDeck(_machine())
    path = _binary_cas(tmp_path)
    deck.load(path)
    deck.seek_block(1)
    before = deck.status()
    for bad in (_bad_cas(tmp_path), tmp_path / "missing.cas", tmp_path / "game.wav"):
        with pytest.raises(TapeError):
            deck.load(bad)
        assert deck.status() == before
        assert len(deck.blocks) == 2


def test_a_seek_in_seconds(tmp_path: Path) -> None:
    deck = TapeDeck(_machine())
    deck.load(_binary_cas(tmp_path))
    deck.seek_seconds(2.5)
    assert deck.status().position == int(2.5 * CPU_HZ)
    with pytest.raises(TapeError):
        deck.seek_seconds(-1)
    with pytest.raises(TapeError):
        deck.seek_seconds(10_000)
    assert deck.status().position == int(2.5 * CPU_HZ)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 1e300, 10**400])
def test_a_seek_beyond_the_core_range_is_an_error(tmp_path: Path, value: float) -> None:
    deck = TapeDeck(_machine())
    deck.load(_binary_cas(tmp_path))
    deck.seek_seconds(2.5)
    with pytest.raises(TapeError):
        deck.seek_seconds(value)
    assert deck.status().position == int(2.5 * CPU_HZ)


# --- debug-monitor: tape ---

def _debugger(machine: Machine | None = None) -> Debugger:
    return Debugger(machine if machine is not None else _machine())


def test_tape_shows_the_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dbg = _debugger()
    dbg._cmd_tape(["load", str(_binary_cas(tmp_path))])
    data_start = dbg._tape.blocks[1].start
    dbg._cmd_tape(["seek", f"{data_start / CPU_HZ + 0.5}"])
    capsys.readouterr()
    dbg._cmd_tape([])
    out = capsys.readouterr().out
    length = dbg._tape.status().length_seconds
    position = data_start / CPU_HZ + 0.5
    assert "game.cas" in out and f"{position:.2f} s (block 1)" in out
    assert f"{length:.2f} s" in out and "motor off" in out


def test_tape_shows_an_empty_deck(capsys: pytest.CaptureFixture[str]) -> None:
    _debugger()._cmd_tape([])
    assert "empty" in capsys.readouterr().out


def test_tape_blocks_lists_the_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dbg = _debugger()
    dbg._cmd_tape(["load", str(_binary_cas(tmp_path))])
    capsys.readouterr()
    dbg._cmd_tape(["blocks"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "#0" in lines[0] and "binary" in lines[0] and "GAME" in lines[0]
    assert "16 bytes" in lines[0]
    assert "#1" in lines[1] and "data" in lines[1] and "100 bytes" in lines[1]
    start = dbg._tape.blocks[1].start / CPU_HZ
    assert f"{start:.2f} s" in lines[1]


def test_tape_seek_to_a_block(tmp_path: Path) -> None:
    dbg = _debugger()
    dbg._cmd_tape(["load", str(_ascii_cas(tmp_path))])
    assert len(dbg._tape.blocks) == 3
    dbg._cmd_tape(["seek", "#2"])
    assert dbg._tape.status().position == dbg._tape.blocks[2].start
    assert dbg._tape.status().block == 2


def test_tape_load_with_a_bad_file_keeps_the_tape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dbg = _debugger()
    dbg._cmd_tape(["load", str(_binary_cas(tmp_path))])
    dbg._cmd_tape(["seek", "3"])
    before = dbg._tape.status()
    capsys.readouterr()
    dbg._cmd_tape(["load", str(_bad_cas(tmp_path))])
    assert "no block header" in capsys.readouterr().out
    assert dbg._tape.status() == before


def test_tape_on_a_machine_with_no_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dbg = _debugger(_machine(port=False))
    dbg._cmd_tape(["load", str(_binary_cas(tmp_path))])
    assert "no cassette port" in capsys.readouterr().out
    assert not dbg._tape.status().mounted


def test_tape_eject_rewind_and_bad_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dbg = _debugger()
    dbg._cmd_tape(["rewind"])
    assert "no tape" in capsys.readouterr().out
    dbg._cmd_tape(["load", str(_binary_cas(tmp_path))])
    dbg._cmd_tape(["seek", "2"])
    dbg._cmd_tape(["rewind"])
    assert dbg._tape.status().position == 0
    capsys.readouterr()
    for bad in (["seek", "x"], ["seek", "#x"], ["seek", "#9"], ["frob"]):
        dbg._cmd_tape(bad)
        assert capsys.readouterr().out.startswith("tape: ")
    dbg._cmd_tape(["eject"])
    assert not dbg._tape.status().mounted and dbg._tape.blocks == ()


def test_tape_is_a_repl_command() -> None:
    from host.debugger.prompt import _COMMANDS, _HELP

    assert "tape" in _COMMANDS and "tape" in _HELP


# --- remote-socket-rpc: tape.* ---

def _call(server: DebugServer, method: str, params: dict[str, Any] | None = None) -> Any:
    return server._handlers[method](server, params or {})


def _server(port: bool = True) -> DebugServer:
    return DebugServer(_machine(port), sock_path="/nonexistent/unused.sock")


def test_insert_a_tape(tmp_path: Path) -> None:
    server = _server()
    result = _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    assert result == {"mounted": True, "blocks": 2}
    assert _call(server, "tape.status")["position"] == 0


def test_rpc_seek_to_a_block(tmp_path: Path) -> None:
    server = _server()
    _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    _call(server, "tape.seek", {"block": 1})
    status = _call(server, "tape.status")
    assert status["block"] == 1
    assert status["position"] == server.tape_deck.blocks[1].start


@pytest.mark.parametrize("params", [
    {"seconds": 3, "block": 1}, {}, {"seconds": -1}, {"seconds": "3"}, {"block": 1.5},
    {"block": 7}, {"seconds": 10_000}, {"seconds": float("inf")}, {"seconds": float("nan")},
    {"seconds": 10**400},
])
def test_bad_seek_targets_are_an_error(tmp_path: Path, params: dict[str, Any]) -> None:
    server = _server()
    _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    _call(server, "tape.seek", {"seconds": 1})
    before = _call(server, "tape.status")
    with pytest.raises(RpcError):
        _call(server, "tape.seek", params)
    assert _call(server, "tape.status") == before


def test_a_wav_path_is_an_error(tmp_path: Path) -> None:
    server = _server()
    _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    before = _call(server, "tape.status")
    with pytest.raises(RpcError, match="only CAS"):
        _call(server, "tape.insert", {"path": str(tmp_path / "game.wav")})
    assert _call(server, "tape.status") == before


def test_no_port_and_no_tape_are_errors(tmp_path: Path) -> None:
    with pytest.raises(RpcError, match="no cassette port"):
        _call(_server(port=False), "tape.insert", {"path": str(_binary_cas(tmp_path))})
    with pytest.raises(RpcError, match="no tape"):
        _call(_server(), "tape.rewind")


def test_status_with_no_tape() -> None:
    status = _call(_server(), "tape.status")
    assert status["mounted"] is False and status["path"] is None
    assert status["position"] == 0 and status["length"] == 0
    assert status["block"] is None


def test_blocks_of_a_binary_file(tmp_path: Path) -> None:
    server = _server()
    _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    blocks = _call(server, "tape.blocks")["blocks"]
    assert [(b["number"], b["type"], b["name"]) for b in blocks] == [
        (0, "binary", "GAME"), (1, "data", None),
    ]
    assert blocks[1]["start_seconds"] == blocks[1]["start"] / CPU_HZ
    assert blocks[0]["length"] == 16


def test_the_tape_methods_need_no_pause(tmp_path: Path) -> None:
    server = _server()
    assert not server.pause_state.paused
    _call(server, "tape.insert", {"path": str(_binary_cas(tmp_path))})
    _call(server, "tape.seek", {"seconds": 1})
    _call(server, "tape.eject")
    assert _call(server, "tape.blocks") == {"blocks": []}


# --- diag-debug-logger: the motor line ---

def test_motor_on_is_logged(tmp_path: Path) -> None:
    machine = _machine()
    deck = TapeDeck(machine)
    deck.load(_binary_cas(tmp_path))
    machine.set_trace_kinds(TRACE_DEBUG)
    machine.drain_trace()
    machine.ppi.write_port(0xAB, 0x82)  # the BIOS mode set: latch 0, motor on
    lines: list[str] = []
    formatter = DebugLogFormatter()
    for event in machine.drain_trace():
        lines.extend(formatter.feed(event))
    assert lines == ["[IO]  tape motor on  position=0 (0.000 s)  tape mounted"]


def test_motor_off_with_a_large_position() -> None:
    position = (1 << 32) + 5
    event = TraceEvent(TRACE_EVENT_TAPE_MOTOR, 0, 0, 0, 0, position & 0xFFFFFFFF,
                       position >> 32, 0)
    (line,) = DebugLogFormatter().feed(event)
    assert line == (f"[IO]  tape motor off  position={position} "
                    f"({position / CPU_HZ:.3f} s)  no tape")
