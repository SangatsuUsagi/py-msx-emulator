from __future__ import annotations

import pytest

from msx.debug.logger import DebugLogger
from msx.io import IOBus


def make_io(logger: DebugLogger | None = None, pc: int = 0x0041) -> IOBus:
    io = IOBus(_logger=logger, _get_pc=lambda: pc)
    return io


# ---------------------------------------------------------------------------
# OUT (write_port) logging
# ---------------------------------------------------------------------------

def test_out_logs_port_value_pc(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    io = make_io(logger=logger, pc=0x0041)
    io.write_port(0xA8, 0xE0)
    captured = capsys.readouterr()
    assert "[IO]  OUT  port=A8  val=E0  PC=0041" in captured.err


def test_out_no_log_without_logger(capsys: pytest.CaptureFixture[str]) -> None:
    io = make_io()
    io.write_port(0xA8, 0xE0)
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# IN (read_port) logging
# ---------------------------------------------------------------------------

def test_in_logs_returned_value(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    io = make_io(logger=logger, pc=0x003F)
    io.register_read(0xA8, 0xAB, lambda p: 0xE0)
    io.read_port(0xA8)
    captured = capsys.readouterr()
    assert "[IO]  IN   port=A8  val=E0  PC=003F" in captured.err


def test_in_unregistered_port_logs_ff(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    io = make_io(logger=logger, pc=0x0010)
    result = io.read_port(0x00)
    assert result == 0xFF
    captured = capsys.readouterr()
    assert "val=FF" in captured.err


def test_in_no_log_without_logger(capsys: pytest.CaptureFixture[str]) -> None:
    io = make_io()
    io.read_port(0xA8)
    captured = capsys.readouterr()
    assert captured.err == ""
