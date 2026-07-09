from __future__ import annotations

import pytest

from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.io import IOBus
from msx.machine import Machine
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.psg import PSG
from msx.vdp.vdp import VDP


def make_machine(opcodes: list[int], logger: DebugLogger | None = None) -> Machine:
    rom = bytes(opcodes + [0x00] * (32768 - len(opcodes)))
    memory = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None))
    io = IOBus()
    cpu = Z80(read_byte=memory.read, write_byte=memory.write)
    vdp = VDP()
    return Machine(cpu=cpu, vdp=vdp, memory=memory, io=io, psg=PSG(), _logger=logger)


# ---------------------------------------------------------------------------
# PC-loop hang
# ---------------------------------------------------------------------------

def test_pc_loop_triggers_after_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    # Infinite loop: JR -2 (0x18 0xFE) repeats at PC=0x0000 indefinitely
    logger = DebugLogger()
    machine = make_machine([0x18, 0xFE], logger=logger)
    machine.run_frame()
    captured = capsys.readouterr()
    assert "PC-loop detected" in captured.err
    assert "0000" in captured.err


def test_pc_loop_fires_only_once(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    machine = make_machine([0x18, 0xFE], logger=logger)
    machine.run_frame()
    machine.run_frame()
    captured = capsys.readouterr()
    assert captured.err.count("PC-loop detected") == 1


def test_normal_halt_not_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    # HALT with iff1=True is normal idle — must not trigger PC-loop hang
    logger = DebugLogger()
    # 0xFB = EI, 0x76 = HALT
    machine = make_machine([0xFB, 0x76], logger=logger)
    machine.cpu.iff1 = True
    machine.run_frame()
    captured = capsys.readouterr()
    assert "PC-loop detected" not in captured.err


# ---------------------------------------------------------------------------
# HALT+DI hang
# ---------------------------------------------------------------------------

def test_halt_di_hang_detected(capsys: pytest.CaptureFixture[str]) -> None:
    # 0x76 = HALT; iff1 remains False (DI is default)
    logger = DebugLogger()
    machine = make_machine([0x76], logger=logger)
    machine.run_frame()
    captured = capsys.readouterr()
    assert "HALT with interrupts disabled" in captured.err


def test_halt_di_fires_only_once_per_pc(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    machine = make_machine([0x76], logger=logger)
    machine.run_frame()
    machine.run_frame()
    captured = capsys.readouterr()
    assert captured.err.count("HALT with interrupts disabled") == 1


def test_halt_iff1_true_not_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    logger = DebugLogger()
    machine = make_machine([0xFB, 0x76], logger=logger)  # EI + HALT
    machine.run_frame()
    captured = capsys.readouterr()
    assert "HALT with interrupts disabled" not in captured.err
