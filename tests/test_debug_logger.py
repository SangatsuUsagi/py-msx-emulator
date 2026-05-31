from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest

from msx.debug.logger import DebugLogger


def capture_logger(log_path: str | None = None) -> tuple[DebugLogger, StringIO]:
    buf = StringIO()
    logger = DebugLogger(log_path=log_path)
    # redirect stderr
    logger._orig_stderr = sys.stderr  # type: ignore[attr-defined]
    sys.stderr = buf
    return logger, buf


def restore_stderr(logger: DebugLogger) -> None:
    sys.stderr = logger._orig_stderr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Basic emit
# ---------------------------------------------------------------------------

def test_emit_writes_to_stderr() -> None:
    logger, buf = capture_logger()
    logger._emit("BOOT", "test message")
    restore_stderr(logger)
    assert "[BOOT]  test message" in buf.getvalue()


def test_emit_writes_to_file(tmp_path: Path) -> None:
    log_file = str(tmp_path / "debug.log")
    logger, buf = capture_logger(log_path=log_file)
    logger._emit("IO", "port write")
    restore_stderr(logger)
    logger.close()
    content = Path(log_file).read_text()
    assert "[IO]  port write" in content


# ---------------------------------------------------------------------------
# Trace buffer
# ---------------------------------------------------------------------------

def test_trace_buffer_capped_at_64() -> None:
    logger = DebugLogger()
    for i in range(100):
        logger.on_step(i, 0x00)
    assert len(logger.trace_buffer) == 64
    # oldest entries discarded; last entry should be PC=99
    assert logger.trace_buffer[-1] == (99, 0x00)


def test_trace_buffer_empty_initially() -> None:
    logger = DebugLogger()
    assert len(logger.trace_buffer) == 0


# ---------------------------------------------------------------------------
# Hang deduplication
# ---------------------------------------------------------------------------

def test_hang_pc_loop_not_repeated(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_hang_pc_loop(0x1234)
    logger.on_hang_pc_loop(0x1234)
    captured = capsys.readouterr()
    assert captured.err.count("PC-loop") == 1


def test_hang_halt_di_not_repeated(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_hang_halt_di(0x1000)
    logger.on_hang_halt_di(0x1000)
    captured = capsys.readouterr()
    assert captured.err.count("HALT with interrupts disabled") == 1


def test_hang_different_pcs_both_reported(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_hang_pc_loop(0x1000)
    logger.on_hang_pc_loop(0x2000)
    captured = capsys.readouterr()
    assert captured.err.count("PC-loop") == 2


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

def test_close_releases_file(tmp_path: Path) -> None:
    log_file = str(tmp_path / "debug.log")
    logger = DebugLogger(log_path=log_file)
    logger.close()
    assert logger._file is None


# ---------------------------------------------------------------------------
# VDP milestone events
# ---------------------------------------------------------------------------

def test_vdp_bl_transition_logged(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_vdp_reg_write(reg=1, value=0x40, frame=5)  # BL=1
    captured = capsys.readouterr()
    assert "VDP display enabled" in captured.err
    assert "frame=5" in captured.err


def test_vdp_bl_no_duplicate(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_vdp_reg_write(reg=1, value=0x40, frame=1)
    logger.on_vdp_reg_write(reg=1, value=0x60, frame=2)  # BL still 1
    captured = capsys.readouterr()
    assert captured.err.count("VDP display") == 1


# ---------------------------------------------------------------------------
# I/O events
# ---------------------------------------------------------------------------

def test_io_write_format(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_io_write(port=0xA8, value=0xE0, pc=0x0041)
    captured = capsys.readouterr()
    assert "[IO]  OUT  port=A8  val=E0  PC=0041" in captured.err


def test_io_read_format(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_io_read(port=0xA8, value=0xE0, pc=0x003F)
    captured = capsys.readouterr()
    assert "[IO]  IN   port=A8  val=E0  PC=003F" in captured.err
