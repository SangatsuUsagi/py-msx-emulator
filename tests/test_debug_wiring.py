from __future__ import annotations

from msx.debug.logger import DebugLogger
from msx.machine import make_machine


ROM = bytes(32768)


def test_make_machine_without_logger_leaves_none() -> None:
    machine = make_machine(rom=ROM)
    assert machine.cpu._logger is None
    assert machine.memory._logger is None
    assert machine.io._logger is None
    assert machine.vdp._logger is None
    assert machine._logger is None


def test_make_machine_wires_logger_to_all_subsystems() -> None:
    logger = DebugLogger()
    machine = make_machine(rom=ROM, logger=logger)
    assert machine.cpu._logger is logger
    assert machine.memory._logger is logger
    assert machine.io._logger is logger
    assert machine.vdp._logger is logger
    assert machine._logger is logger


def test_make_machine_wires_get_pc() -> None:
    logger = DebugLogger()
    machine = make_machine(rom=ROM, logger=logger)
    assert machine.io._get_pc is not None
    # _get_pc should return the CPU's current PC
    machine.cpu.registers.PC = 0x1234
    assert machine.io._get_pc() == 0x1234
