"""diag-vdp-tracer "The VDP records its port writes as trace events"."""
from __future__ import annotations

from msx.boundary import (
    TRACE_EVENT_VDP_PORT9B,
    TRACE_EVENT_VDP_PORT99,
    TRACE_VDP,
    TraceEvent,
)
from msx.trace import TraceLog
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP


def _trace(kinds: int = TRACE_VDP) -> TraceLog:
    return TraceLog(kinds=kinds, _get_pc=lambda: 0x1234, _get_cycle=lambda: 9999,
                    _get_frame=lambda: 3)


def test_v9938_without_the_trace_operates_unchanged() -> None:
    vdp = V9938()
    trace = _trace(kinds=0)
    vdp._trace = trace
    vdp.write_port(0x99, 0x02)  # R#0 keeps bits 1-6 only (0x7E)
    vdp.write_port(0x99, 0x80)
    assert vdp.regs[0] == 0x02
    assert trace.drain() == ()


def test_a_register_write_records_its_port_events() -> None:
    vdp = V9938()
    trace = _trace()
    vdp._trace = trace
    vdp.write_port(0x99, 0xAB)
    vdp.write_port(0x99, 0x80)
    assert trace.drain() == (
        TraceEvent(TRACE_EVENT_VDP_PORT99, 9999, 3, 0x1234, 0xAB),
        TraceEvent(TRACE_EVENT_VDP_PORT99, 9999, 3, 0x1234, 0x80),
    )


def test_port_9b_event_carries_pre_increment_r17() -> None:
    vdp = V9938()
    trace = _trace()
    vdp._trace = trace
    vdp.regs[17] = 0x22  # pointer 34, AII 0 (auto-increment on)
    vdp.write_port(0x9B, 0xFF)
    assert trace.drain() == (
        TraceEvent(TRACE_EVENT_VDP_PORT9B, 9999, 3, 0x1234, 0xFF, 0x22),
    )
    assert vdp.regs[17] == 0x23


def test_the_tms9918a_records_its_port_99_writes() -> None:
    vdp = VDP()
    trace = _trace()
    vdp._trace = trace
    vdp.write_port(0x99, 0x02)
    vdp.write_port(0x99, 0x80)
    assert [(e.kind, e.a) for e in trace.drain()] == [
        (TRACE_EVENT_VDP_PORT99, 0x02),
        (TRACE_EVENT_VDP_PORT99, 0x80),
    ]
