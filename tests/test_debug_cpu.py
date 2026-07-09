from __future__ import annotations

import pytest

from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.mapper import FlatMapper
from msx.memory import Memory


def make_cpu(opcodes: list[int], logger: DebugLogger | None = None) -> Z80:
    rom = bytes(opcodes + [0x00] * (32768 - len(opcodes)))
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write, _logger=logger)


# ---------------------------------------------------------------------------
# Trace buffer populated on step
# ---------------------------------------------------------------------------

def test_trace_buffer_populated(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    cpu = make_cpu([0x00, 0x00], logger=logger)  # NOP, NOP
    cpu.step()
    assert len(logger.trace_buffer) == 1
    pc, op = logger.trace_buffer[0]
    assert pc == 0x0000
    assert op == 0x00


def test_trace_buffer_no_entry_without_logger() -> None:
    cpu = make_cpu([0x00])
    cpu.step()
    # no logger — just verify no crash


# ---------------------------------------------------------------------------
# Undefined opcode calls logger, not stderr
# ---------------------------------------------------------------------------

def test_undefined_opcode_calls_logger(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    cpu = make_cpu([0xED, 0x00], logger=logger)  # ED 00 is undefined
    cpu.step()
    captured = capsys.readouterr()
    assert "[CPU]  undefined opcode" in captured.err
    assert "00" in captured.err


def test_undefined_opcode_silent_without_logger(capsys: pytest.CaptureFixture[str]) -> None:
    cpu = make_cpu([0xED, 0x00])  # no logger
    cpu.step()
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# No-op when logger is None
# ---------------------------------------------------------------------------

def test_step_no_crash_without_logger() -> None:
    cpu = make_cpu([0x00])  # NOP
    cpu.step()  # should not raise
