"""diag-debug-logger: the CPU's instruction history and undefined-opcode
events while TRACE_DEBUG is on."""
from __future__ import annotations

from msx.boundary import (
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_HANG_PC_LOOP,
    TRACE_EVENT_UNDEFINED_OP,
)
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.trace import TraceLog


def make_cpu(opcodes: list[int], trace: TraceLog | None = None) -> Z80:
    rom = bytes(opcodes + [0x00] * (32768 - len(opcodes)))
    mem = Memory(rom=rom, ram=bytearray(32768), mapper1=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write, _trace=trace)


def test_history_populated_on_step() -> None:
    trace = TraceLog()
    cpu = make_cpu([0x00, 0x00], trace)  # NOP, NOP
    cpu.step()
    trace.record_hang(TRACE_EVENT_HANG_PC_LOOP, 0)
    history = [(e.a, e.b) for e in trace.drain() if e.kind == TRACE_EVENT_CPU_HISTORY]
    assert history == [(0x0000, 0x00)]


def test_step_without_a_trace_keeps_no_history() -> None:
    cpu = make_cpu([0x00])
    cpu.step()
    assert cpu._trace is None


def test_undefined_opcode_is_recorded() -> None:
    trace = TraceLog()
    cpu = make_cpu([0xED, 0x00], trace)  # ED 00 is undefined
    cpu.step()
    events = [(e.kind, e.a, e.b) for e in trace.drain()]
    assert events == [(TRACE_EVENT_UNDEFINED_OP, 0x0000, 0x00)]


def test_undefined_opcode_silent_without_a_trace(capsys) -> None:
    cpu = make_cpu([0xED, 0x00])
    cpu.step()
    assert capsys.readouterr() == ("", "")
