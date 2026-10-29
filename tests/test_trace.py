"""host/trace.py: the VDP and mapper trace text from the core's events
(diag-vdp-tracer "Tracer class produces canonical VDP Trace Log records",
diag-mapper-tracer "MapperTracer produces canonical MAP_BANK records")."""
from __future__ import annotations

import io

from host.trace import TraceOutput, VdpTraceFormatter, map_bank_line
from msx.boundary import (
    TRACE_EVENT_LOST,
    TRACE_EVENT_MAP_BANK,
    TRACE_EVENT_VDP_PORT9B,
    TRACE_EVENT_VDP_PORT99,
    TRACE_MAPPER,
    TRACE_VDP,
    TraceEvent,
)


def _p99(value: int, pc: int = 0, cycle: int = 0, frame: int = 0) -> TraceEvent:
    return TraceEvent(TRACE_EVENT_VDP_PORT99, cycle, frame, pc, value)


def _p9b(value: int, r17: int, pc: int = 0, cycle: int = 0, frame: int = 0) -> TraceEvent:
    return TraceEvent(TRACE_EVENT_VDP_PORT9B, cycle, frame, pc, value, r17)


def _lines(events: list[TraceEvent]) -> list[str]:
    f = VdpTraceFormatter()
    out = []
    for e in events:
        line = f.feed(e)
        if line is not None:
            out.append(line)
    return out


# -- VDP -------------------------------------------------------------------------

def test_vdp_tracer_disabled_by_default_produces_no_output() -> None:
    # A first byte alone completes no write.
    assert _lines([_p99(0x99)]) == []


def test_vdp_reg_record_for_control_register_includes_fr_field() -> None:
    assert _lines([_p99(0x80, pc=0x407C, cycle=45231),
                   _p99(0x81, pc=0x407E, cycle=45233, frame=3)]) == [
        "CY=0000045233 FR=000003 PC=407E VDP_REG R#01=80h",
    ]


def test_fr_defaults_to_000000_when_frame_not_supplied() -> None:
    assert "FR=000000" in _lines([_p99(0x01), _p99(0x80)])[0]


def test_a_vram_address_setup_is_not_a_register_write() -> None:
    assert _lines([_p99(0x00), _p99(0x40)]) == []


def test_vdp_cmd_record_emitted_on_r46_write() -> None:
    f = VdpTraceFormatter()
    lines = []
    for e in (_p99(0x00), _p99(0x80 | 36), _p99(0x01), _p99(0x80 | 37),
              _p99(0xC0), _p99(0x80 | 46), _p99(0x00), _p99(0x80 | 46)):
        line = f.feed(e)
        if line is not None:
            lines.append(line)
    params = "['--','--','--','--','00','01'" + ",'--'" * 8 + "]"
    assert lines[2] == f"CY=0000000000 FR=000000 PC=0000 VDP_CMD HMMV/IMP  (C0h) R32-45={params}"
    # The parameter buffer is cleared by the command.
    assert lines[3].endswith("R32-45=[" + ",".join(["'--'"] * 14) + "]")


def test_port_9b_indirect_write_emits_port_9bh_suffix() -> None:
    assert _lines([_p9b(0x1E, 0x02, pc=0x40C1, cycle=178432)]) == [
        "CY=0000178432 FR=000000 PC=40C1 VDP_REG R#02=1Eh  ;port 9Bh",
    ]


# -- mapper ------------------------------------------------------------------------

def test_mapper_tracer_disabled_by_default_produces_no_output() -> None:
    out = TraceOutput()
    out.set_map_output(io.StringIO(), "buffer")

    class _Core:
        def drain_trace(self):
            return (_p99(0x01), _p99(0x80))

    out.drain(_Core())
    assert out.map_out.getvalue() == ""


def test_map_bank_record_emitted_on_bank_change() -> None:
    e = TraceEvent(TRACE_EVENT_MAP_BANK, 45231, 3, 0x402E, 1, 1, 0, 0x7000)
    assert map_bank_line(e) == (
        "CY=0000045231 FR=000003 PC=402E MAP_BANK win=1 01h->00h addr=7000h"
    )


# -- the same text as the tracer objects -----------------------------------------

def _old_and_new(writes: list[tuple[int, int]], regs17: int | None = None) -> tuple[str, str]:
    """Run port writes on an MSX2 machine with the old Tracer attached and
    TRACE_VDP on; return (old text, new text)."""
    from msx.vdp.tracer import Tracer, attach_vdp_tracer
    from tests.factories import make_machine_msx2

    m = make_machine_msx2(bytes(32 * 1024), bytes(0x4000))
    old = io.StringIO()
    assert attach_vdp_tracer(m, Tracer(enabled=True, output=old))
    m.set_trace_kinds(TRACE_VDP)
    if regs17 is not None:
        m.vdp.regs[17] = regs17
    for port, value in writes:
        m.cycle_count += 7
        m.io.write_port(port, value)
    out = TraceOutput()
    new = io.StringIO()
    out.set_vdp_output(new, "buffer")
    out.drain(m)
    return old.getvalue(), new.getvalue()


def test_a_register_write_has_the_tracer_text() -> None:
    old, new = _old_and_new([(0x99, 0x02), (0x99, 0x80), (0x99, 0x00), (0x99, 0x40)])
    assert old and new == old


def test_a_command_has_the_tracer_text() -> None:
    writes = []
    for reg, value in ((36, 0x10), (38, 0x20), (40, 0x08), (42, 0x04), (44, 0x55), (46, 0xC0)):
        writes += [(0x99, value), (0x99, 0x80 | reg)]
    old, new = _old_and_new(writes)
    assert "VDP_CMD" in old and new == old


def test_a_port_9b_stream_has_the_tracer_text() -> None:
    old, new = _old_and_new([(0x9B, v) for v in (0x01, 0x02, 0x03, 0x04)], regs17=36)
    assert old.count(";port 9Bh") == 4 and new == old


# -- TraceOutput.drain ------------------------------------------------------------

def test_drain_writes_each_trace_to_its_stream(capsys) -> None:
    from tests.factories import make_machine

    rom = bytes([(p if i == 0 else 0) for p in range(8) for i in range(16384)])
    m = make_machine(bytes(32 * 1024), cartridge=rom, mapper="ASCII16")
    m.set_trace_kinds(TRACE_VDP | TRACE_MAPPER)
    m.io.write_port(0xA8, 0xD4)
    m.io.write_port(0x99, 0x02)
    m.io.write_port(0x99, 0x80)
    m.memory.write(0x7000, 3)
    out = TraceOutput()
    vdp, mapper = io.StringIO(), io.StringIO()
    out.set_vdp_output(vdp, "vdp")
    out.set_map_output(mapper, "map")
    out.drain(m)
    assert vdp.getvalue().count("VDP_REG R#00=02h") == 1
    assert "MAP_BANK win=1 00h->03h addr=7000h" in mapper.getvalue()
    assert "MAP_BANK" not in vdp.getvalue()
    assert capsys.readouterr().err == ""


def test_drain_reports_lost_events_on_stderr(capsys) -> None:
    class _Core:
        def drain_trace(self):
            return (TraceEvent(TRACE_EVENT_LOST, 0, 0, 0, 12),)

    TraceOutput().drain(_Core())
    assert capsys.readouterr().err == "trace: 12 events lost\n"
