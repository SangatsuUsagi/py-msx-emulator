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


# -- format details (ported from the tests of the old Tracer) -------------------

def test_cycle_pc_and_value_are_padded_uppercase_hex() -> None:
    line = _lines([_p99(0xAB), _p99(0x80, pc=0xABCF, cycle=9)])[0]
    assert line == "CY=0000000009 FR=000000 PC=ABCF VDP_REG R#00=ABh"


def test_two_consecutive_register_writes() -> None:
    lines = _lines([_p99(0x10), _p99(0x80), _p99(0x1E), _p99(0x82)])
    assert [ln[-8:] for ln in lines] == ["R#00=10h", "R#02=1Eh"]


def test_a_command_parameter_register_is_a_vdp_reg_line() -> None:
    assert _lines([_p99(0x00), _p99(0xA4, pc=0x4084, cycle=45291)]) == [
        "CY=0000045291 FR=000000 PC=4084 VDP_REG R#36=00h",
    ]


def test_unwritten_command_parameters_show_dashes() -> None:
    assert _lines([_p99(0xC0), _p99(0xAE)])[-1].count("'--'") == 14


def test_port_9b_line_carries_the_frame() -> None:
    assert "FR=000005" in _lines([_p9b(0x7F, 0x02, frame=5)])[0]


def test_port_9b_command_parameter_is_buffered() -> None:
    lines = _lines([_p9b(0x7F, 0x24), _p9b(0xC0, 0x2E)])
    assert lines[0].endswith("VDP_REG R#36=7Fh  ;port 9Bh")
    assert "VDP_CMD HMMV/IMP  (C0h)" in lines[1] and "'7F'" in lines[1]
    assert lines[1].endswith(";port 9Bh")


def test_port_9b_uses_the_low_6_bits_of_r17() -> None:
    assert "R#02=ABh" in _lines([_p9b(0xAB, 0x42)])[0]


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


# -- tidy-review-findings F1 and R3 -------------------------------------------------

def test_a_drain_writes_stderr_and_the_log_once(capsys) -> None:
    from msx.boundary import TRACE_EVENT_IO_WRITE

    class _Core:
        def drain_trace(self):
            return (
                TraceEvent(TRACE_EVENT_IO_WRITE, 0, 0, 0x1234, 0x98, 0x55, 0x1234),
                TraceEvent(TRACE_EVENT_IO_WRITE, 0, 0, 0x1236, 0x99, 0x80, 0x1236),
                TraceEvent(TRACE_EVENT_LOST, 0, 0, 0, 3),
            )

    class _CountingFile(io.StringIO):
        writes = 0

        def write(self, s: str) -> int:
            type(self).writes += 1
            return super().write(s)

    log = _CountingFile()
    out = TraceOutput()
    out.set_debug_output(log)
    out.drain(_Core())
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert len(lines) == 3 and lines[-1] == "trace: 3 events lost"
    assert "OUT" in lines[0] and "OUT" in lines[1]
    assert log.getvalue().splitlines() == lines[:2]
    assert _CountingFile.writes == 1


_OLD_LOP_NAMES = [
    "IMP", "AND", "OR", "XOR", "NOT", "????", "????", "????",
    "TIMP", "TAND", "TOR", "TXOR", "TNOT", "????", "????", "????",
]
_OLD_CMD_NAMES = [
    "ABRT", "????", "????", "????", "POINT", "PSET", "SRCH", "LINE",
    "LMMV", "LMMM", "LMCM", "LMMC", "HMMV", "HMMM", "YMMM", "HMMC",
]


def test_the_shared_name_tables_keep_the_trace_text() -> None:
    for value in range(256):
        line = _lines([_p99(value), _p99(0x80 | 46)])[0]
        name, lop = _OLD_CMD_NAMES[value >> 4], _OLD_LOP_NAMES[value & 0xF]
        assert f"VDP_CMD {name:<4s}/{lop:<4s} ({value:02X}h)" in line


def test_the_shared_name_tables_keep_the_debugger_text() -> None:
    from host.debugger.prompt import _decode_cmr

    old_log = {0: "IMP", 1: "AND", 2: "OR", 3: "XOR", 4: "NOT"}
    for value in range(256):
        cmd_code, log_code = value >> 4, value & 0x07
        cmd = _OLD_CMD_NAMES[cmd_code] if _OLD_CMD_NAMES[cmd_code] != "????" \
            else f"CMD{cmd_code:X}"
        log = old_log.get(log_code, f"LOG{log_code}") + ("/T" if value & 0x08 else "")
        assert _decode_cmr(value) == (cmd, log)
