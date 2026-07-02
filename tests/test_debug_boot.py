from __future__ import annotations

import pytest

from msx.debug.logger import DebugLogger
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.vdp.vdp import VDP

# ---------------------------------------------------------------------------
# Slot register write → [BOOT] log
# ---------------------------------------------------------------------------

def test_slot_register_write_logged(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    logger.on_slot_register_write(0x00, 0xE0, pc=0)
    captured = capsys.readouterr()
    assert "[BOOT]" in captured.err
    assert "0x00" in captured.err
    assert "0xE0" in captured.err


def test_slot_register_direct_write_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    # Production PPI writes mem.slot_register directly (msx/ppi.py:22); that
    # plain field assignment emits no diagnostic output on its own.
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    mem.slot_register = 0xE0
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# VDP R1 BL transition → [BOOT] log
# ---------------------------------------------------------------------------

def test_vdp_display_enable_logged(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    vdp = VDP(_logger=logger)
    # Write register 1 with BL=1 (0x40) via port 0x99 two-byte sequence
    vdp.write_port(0x99, 0x40)  # low byte
    vdp.write_port(0x99, 0x81)  # 0x80 | reg 1
    captured = capsys.readouterr()
    assert "[BOOT]" in captured.err
    assert "display enabled" in captured.err


def test_vdp_mode_change_logged(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    vdp = VDP(_logger=logger)
    # Set M1=1 (Text mode) via R1: value 0x10 = M1 bit
    vdp.write_port(0x99, 0x10)  # low byte
    vdp.write_port(0x99, 0x81)  # 0x80 | reg 1
    captured = capsys.readouterr()
    assert "[BOOT]" in captured.err
    assert "Text" in captured.err


def test_vdp_no_log_without_logger(capsys: pytest.CaptureFixture[str]) -> None:
    vdp = VDP()
    vdp.write_port(0x99, 0x40)
    vdp.write_port(0x99, 0x81)
    captured = capsys.readouterr()
    assert captured.err == ""
