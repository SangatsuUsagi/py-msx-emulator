"""host/debug_log.py: the --debug log text from the core's TRACE_DEBUG events
(diag-debug-logger)."""
from __future__ import annotations

from host.debug_log import DebugLogFormatter
from msx.boundary import (
    TRACE_DEBUG,
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_HANG_HALT_DI,
    TRACE_EVENT_HANG_PC_LOOP,
    TRACE_EVENT_IO_READ,
    TRACE_EVENT_IO_WRITE,
    TRACE_EVENT_UNDEFINED_OP,
    TRACE_EVENT_VDP_MODE_REG,
    TraceEvent,
)
from msx.input import InputFrame
from msx.machine import Machine
from tests.factories import make_machine, make_machine_msx2


def _ev(kind: int, a: int = 0, b: int = 0, c: int = 0, frame: int = 0) -> TraceEvent:
    return TraceEvent(kind, 0, frame, 0, a, b, c)


def _lines(events: list[TraceEvent]) -> list[str]:
    f = DebugLogFormatter()
    out: list[str] = []
    for e in events:
        out += f.feed(e)
    return out


def _machine_lines(m: Machine, frames: int) -> list[str]:
    m.set_trace_kinds(TRACE_DEBUG)
    f = DebugLogFormatter()
    out: list[str] = []
    for _ in range(frames):
        m.step_frame(InputFrame(), render_video=False)
        for e in m.drain_trace():
            out += f.feed(e)
    return out


def test_category_prefix_present() -> None:
    lines = _lines([_ev(TRACE_EVENT_VDP_MODE_REG, 1, 0x40)])
    assert lines[0].startswith("[BOOT]  ")


def test_buffer_dumped_before_hang_message() -> None:
    m = make_machine(bytes((0x18, 0xFE)) + bytes(32 * 1024 - 2))  # JR $
    lines = _machine_lines(m, 2)
    assert lines[:64] == ["[CPU]  trace  PC=0000  op=18"] * 64
    assert lines[64:] == ["[HANG]  PC-loop detected  PC=0000"]


def test_vdp_display_enable_logged() -> None:
    lines = _lines([_ev(TRACE_EVENT_VDP_MODE_REG, 1, 0xE0, frame=12)])
    assert "[BOOT]  VDP display enabled  R1=0xE0  frame=12" in lines


def test_v9938_register_writes_reach_the_logger_too() -> None:
    for port_9b in (False, True):
        m = make_machine_msx2(bytes(32 * 1024), bytes(0x4000))
        m.set_trace_kinds(TRACE_DEBUG)
        if port_9b:
            m.io.write_port(0x99, 0x01)  # R#17 = 1 (R#1, auto-increment)
            m.io.write_port(0x99, 0x80 | 17)
            m.io.write_port(0x9B, 0x40)
        else:
            m.io.write_port(0x99, 0x40)
            m.io.write_port(0x99, 0x81)
        lines = _lines(list(m.drain_trace()))
        assert any(ln.startswith("[BOOT]  VDP display enabled  R1=0x40") for ln in lines)


def test_vdp_mode_logged_on_change() -> None:
    # M1 off, M2 off, M3 off is G1: R#1 = 0x00 after Text (M1 = 1).
    lines = _lines([
        _ev(TRACE_EVENT_VDP_MODE_REG, 1, 0x10),
        _ev(TRACE_EVENT_VDP_MODE_REG, 1, 0x00),
    ])
    assert "[BOOT]  VDP mode → Text (Screen 0)  R1=0x10" in lines
    assert "[BOOT]  VDP mode → G1 (Screen 1)  R1=0x00" in lines


def test_undefined_opcode_logged_in_debug_mode() -> None:
    assert _lines([_ev(TRACE_EVENT_UNDEFINED_OP, 0x1234, 0x00)]) == [
        "[CPU]  undefined opcode 00  PC=1234",
    ]


def test_out_logged() -> None:
    assert _lines([_ev(TRACE_EVENT_IO_WRITE, 0xA8, 0xE0, 0x0041)]) == [
        "[IO]  OUT  port=A8  val=E0  PC=0041",
    ]


def test_in_logged() -> None:
    assert _lines([_ev(TRACE_EVENT_IO_READ, 0xA8, 0xE0, 0x003F)]) == [
        "[IO]  IN   port=A8  val=E0  PC=003F",
    ]


def test_pc_loop_hang_detected_once() -> None:
    hang = [_ev(TRACE_EVENT_CPU_HISTORY, 0x10, 0x18), _ev(TRACE_EVENT_HANG_PC_LOOP, 0x10)]
    assert _lines(hang + hang) == [
        "[CPU]  trace  PC=0010  op=18",
        "[HANG]  PC-loop detected  PC=0010",
    ]


def test_halt_di_hang_detected_once_per_pc() -> None:
    lines = _lines([
        _ev(TRACE_EVENT_HANG_HALT_DI, 0x0002),
        _ev(TRACE_EVENT_HANG_HALT_DI, 0x0002),
        _ev(TRACE_EVENT_HANG_HALT_DI, 0x0005),
    ])
    assert lines == [
        "[HANG]  HALT with interrupts disabled  PC=0002",
        "[HANG]  HALT with interrupts disabled  PC=0005",
    ]


def test_a_pc_loop_and_a_halt_at_one_pc_are_both_reported() -> None:
    lines = _lines([_ev(TRACE_EVENT_HANG_PC_LOOP, 0x10), _ev(TRACE_EVENT_HANG_HALT_DI, 0x10)])
    assert len(lines) == 2


def test_vdp_display_line_not_repeated_while_bl_stays_set() -> None:
    lines = _lines([
        _ev(TRACE_EVENT_VDP_MODE_REG, 1, 0x40, frame=1),
        _ev(TRACE_EVENT_VDP_MODE_REG, 1, 0x60, frame=2),  # BL still 1
    ])
    assert sum("VDP display" in ln for ln in lines) == 1


def test_hangs_at_different_pcs_are_both_reported() -> None:
    lines = _lines([_ev(TRACE_EVENT_HANG_PC_LOOP, 0x1000), _ev(TRACE_EVENT_HANG_PC_LOOP, 0x2000)])
    assert sum("PC-loop" in ln for ln in lines) == 2
