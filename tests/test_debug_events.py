"""diag-debug-logger: the debug events the core records while TRACE_DEBUG is
on (the instruction history, undefined opcodes, I/O, VDP mode registers,
hangs)."""
from __future__ import annotations

from msx.boundary import (
    TRACE_DEBUG,
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_HANG_HALT_DI,
    TRACE_EVENT_HANG_PC_LOOP,
    TRACE_EVENT_UNDEFINED_OP,
    TRACE_EVENT_VDP_MODE_REG,
    TRACE_HISTORY_SIZE,
)
from msx.input import InputFrame
from msx.machine import Machine
from msx.trace import TraceLog
from tests.factories import make_machine, make_machine_msx2


def _machine(code: bytes) -> Machine:
    """An MSX1 machine whose ROM starts with `code` at 0x0000."""
    return make_machine(code + bytes(32 * 1024 - len(code)))


def _kinds(events) -> list[int]:
    return [e.kind for e in events]


# -- "Circular trace buffer records last 64 instructions" ---------------------

def test_buffer_filled_and_oldest_entry_discarded() -> None:
    trace = TraceLog()
    for pc in range(TRACE_HISTORY_SIZE + 6):
        trace.push_history(pc, 0x00)
    trace.record_hang(TRACE_EVENT_HANG_PC_LOOP, 0x1234)
    events = trace.drain()
    history = [e.a for e in events if e.kind == TRACE_EVENT_CPU_HISTORY]
    assert history == list(range(6, TRACE_HISTORY_SIZE + 6))
    assert (events[-1].kind, events[-1].a) == (TRACE_EVENT_HANG_PC_LOOP, 0x1234)


def test_a_hang_carries_the_last_64_instructions() -> None:
    m = _machine(bytes((0x18, 0xFE)))  # JR $
    m.set_trace_kinds(TRACE_DEBUG)
    m.step_frame(InputFrame(), render_video=False)
    events = m.drain_trace()
    assert _kinds(events) == [TRACE_EVENT_CPU_HISTORY] * 64 + [TRACE_EVENT_HANG_PC_LOOP]
    assert all((e.a, e.b) == (0x0000, 0x18) for e in events[:-1])


def test_no_buffer_without_debug() -> None:
    m = _machine(bytes((0x18, 0xFE)))
    for _ in range(10):
        m.step()
    assert m.cpu._trace is None
    assert m._trace._history_len == 0


# -- "Log undefined opcode via DebugLogger" -------------------------------------

def test_undefined_opcode_silent_without_debug(capsys) -> None:
    m = _machine(bytes((0xED, 0x00, 0x18, 0xFE)))
    m.step()
    assert m.cpu.registers.PC == 0x0002  # execution continues
    assert m.drain_trace() == ()
    assert capsys.readouterr() == ("", "")


def test_the_debug_kind_records_an_undefined_opcode() -> None:
    m = _machine(bytes((0xED, 0x00, 0x18, 0xFE)))
    m.set_trace_kinds(TRACE_DEBUG)
    m.step()
    events = m.drain_trace()
    assert [(e.kind, e.a, e.b) for e in events] == [(TRACE_EVENT_UNDEFINED_OP, 0x0000, 0x00)]


# -- "Log every I/O port read and write" ----------------------------------------

def test_no_io_log_without_debug(capsys) -> None:
    m = _machine(b"")
    m.io.write_port(0xA8, 0xE0)
    m.io.read_port(0xA8)
    assert m.drain_trace() == ()
    assert capsys.readouterr() == ("", "")


# -- hangs ------------------------------------------------------------------------

def test_pc_loop_hang_not_triggered_by_halt() -> None:
    # EI / HALT: halted with interrupts on (the VDP raises none: IE0 is off).
    m = _machine(bytes((0xFB, 0x76)))
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(3):
        m.step_frame(InputFrame(), render_video=False)
    assert TRACE_EVENT_HANG_PC_LOOP not in _kinds(m.drain_trace())


def test_normal_halt_not_flagged() -> None:
    m = _machine(bytes((0xFB, 0x76)))
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(3):
        m.step_frame(InputFrame(), render_video=False)
    assert TRACE_EVENT_HANG_HALT_DI not in _kinds(m.drain_trace())


def test_a_halt_with_interrupts_off_is_recorded_each_frame() -> None:
    m = _machine(bytes((0xF3, 0x76)))  # DI / HALT
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(2):
        m.step_frame(InputFrame(), render_video=False)
    hangs = [e for e in m.drain_trace() if e.kind == TRACE_EVENT_HANG_HALT_DI]
    assert [e.a for e in hangs] == [0x0002, 0x0002]


def test_a_pc_loop_is_recorded_once_per_run_of_repeats() -> None:
    m = _machine(bytes((0x18, 0xFE)))
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(3):
        m.step_frame(InputFrame(), render_video=False)
    assert _kinds(m.drain_trace()).count(TRACE_EVENT_HANG_PC_LOOP) == 1


def test_the_pc_loop_count_stops_at_the_threshold() -> None:
    from msx.machine import HANG_PC_REPEAT_THRESHOLD

    m = _machine(bytes((0x18, 0xFE)))
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(3):
        m.step_frame(InputFrame(), render_video=False)
    assert m._pc_repeat == HANG_PC_REPEAT_THRESHOLD
    assert _kinds(m.drain_trace()).count(TRACE_EVENT_HANG_PC_LOOP) == 1


# -- "The debug kind reaches every logging component" ----------------------------

def test_the_debug_kind_records_a_vdp_register_write() -> None:
    m = make_machine_msx2(bytes(32 * 1024), bytes(0x4000))
    m.set_trace_kinds(TRACE_DEBUG)
    m.io.write_port(0x99, 0x40)
    m.io.write_port(0x99, 0x81)
    events = [e for e in m.drain_trace() if e.kind == TRACE_EVENT_VDP_MODE_REG]
    assert len(events) == 1
    assert events[0].a == 1 and events[0].b & 0x40


def test_turning_the_kind_off_stops_the_history() -> None:
    m = _machine(bytes((0x00, 0x00, 0x00, 0x18, 0xFE)))
    m.set_trace_kinds(TRACE_DEBUG)
    for _ in range(3):
        m.step()
    assert m._trace._history_len == 3
    m.set_trace_kinds(0)
    assert m.cpu._trace is None
    m.step()
    assert m._trace._history_len == 3
    m.set_trace_kinds(TRACE_DEBUG)
    assert m._trace._history_len == 0
