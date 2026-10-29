"""The build wires the trace buffer the --debug log uses (diag-debug-logger
"The debug kind reaches every logging component")."""
from __future__ import annotations

from msx.boundary import TRACE_DEBUG
from tests.factories import make_machine

ROM = bytes(32768)


def test_the_build_wires_the_trace_buffer() -> None:
    machine = make_machine(rom=ROM)
    assert machine.io._trace is machine._trace
    assert machine.vdp._trace is machine._trace
    # The CPU gets it only while TRACE_DEBUG is on.
    assert machine.cpu._trace is None
    machine.set_trace_kinds(TRACE_DEBUG)
    assert machine.cpu._trace is machine._trace


def test_the_build_wires_get_pc() -> None:
    machine = make_machine(rom=ROM)
    assert machine.io._get_pc is not None
    # _get_pc returns the CPU's PC register
    machine.cpu.registers.PC = 0x1234
    assert machine.io._get_pc() == 0x1234
