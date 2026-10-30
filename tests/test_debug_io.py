"""diag-debug-logger "Log every I/O port read and write": the I/O bus
records each access while TRACE_DEBUG is on."""
from __future__ import annotations

from msx.boundary import TRACE_DEBUG, TRACE_EVENT_IO_READ, TRACE_EVENT_IO_WRITE
from msx.io import IOBus
from msx.trace import TraceLog


def make_io(kinds: int = TRACE_DEBUG, pc: int = 0x0041) -> tuple[IOBus, TraceLog]:
    trace = TraceLog(kinds=kinds)
    return IOBus(_trace=trace, _get_pc=lambda: pc), trace


def _events(trace: TraceLog) -> list[tuple[int, int, int, int]]:
    return [(e.kind, e.a, e.b, e.c) for e in trace.drain()]


def test_out_records_port_value_pc() -> None:
    io, trace = make_io(pc=0x0041)
    io.write_port(0xA8, 0xE0)
    assert _events(trace) == [(TRACE_EVENT_IO_WRITE, 0xA8, 0xE0, 0x0041)]


def test_out_no_record_with_the_kind_off() -> None:
    io, trace = make_io(kinds=0)
    io.write_port(0xA8, 0xE0)
    assert _events(trace) == []


def test_in_records_the_returned_value() -> None:
    io, trace = make_io(pc=0x003F)
    io.register_read(0xA8, 0xA8, lambda port: 0xE0)
    assert io.read_port(0xA8) == 0xE0
    assert _events(trace) == [(TRACE_EVENT_IO_READ, 0xA8, 0xE0, 0x003F)]


def test_in_unregistered_port_records_ff() -> None:
    io, trace = make_io(pc=0x0010)
    assert io.read_port(0x55) == 0xFF
    assert _events(trace) == [(TRACE_EVENT_IO_READ, 0x55, 0xFF, 0x0010)]


def test_in_no_record_with_the_kind_off() -> None:
    io, trace = make_io(kinds=0)
    io.read_port(0xA8)
    assert _events(trace) == []
