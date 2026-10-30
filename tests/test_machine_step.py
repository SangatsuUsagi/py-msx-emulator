"""machine-core "A single step runs inside the current frame" and "A single
step ignores the break settings"."""
from __future__ import annotations

import pytest

from msx.boundary import STOP_BREAKPOINT, STOP_NONE, STOP_REQUESTED, FrameOutput
from msx.input import InputFrame
from msx.machine import Machine
from msx.vdp.v9938 import V9938
from tests.factories import make_machine, make_machine_msx2, watch_scanlines

# A loop in ROM that reaches INC D about every 88 scanlines, so a breakpoint
# on it lands in the middle of a frame (as in tests/test_machine_stop.py):
#   0000 DI / LD D,0
#   0003 LD BC,0300h
#   0006 DEC BC / LD A,B / OR C / JR NZ,0006h
#   000B INC D                                  (the target)
#   000C JR 0003h
_TARGET = 0x000B
_LOOP_ROM = bytes([
    0xF3, 0x16, 0x00,
    0x01, 0x00, 0x03,
    0x0B, 0x78, 0xB1, 0x20, 0xFB,
    0x14,
    0x18, 0xF5,
]).ljust(32768, b"\x00")


def _msx1() -> Machine:
    return make_machine(rom=_LOOP_ROM)


def _msx2() -> Machine:
    return make_machine_msx2(_LOOP_ROM, bytes(0x4000))


_MACHINES = [pytest.param(_msx1, id="msx1"), pytest.param(_msx2, id="msx2")]


def _step_frame(m: Machine) -> FrameOutput:
    return m.step_frame(InputFrame(), render_video=True)


def _stopped(make) -> Machine:
    """A machine stopped at _TARGET in the middle of its first frame, with
    the breakpoint cleared again."""
    m = make()
    m.set_breakpoints([_TARGET])
    assert not _step_frame(m).frame_complete
    m.set_breakpoints([])
    return m


@pytest.mark.parametrize("make", _MACHINES)
def test_a_step_advances_the_cycle_count(make) -> None:
    m = _stopped(make)
    before = m.cycle_count
    n = m.step()
    assert n > 0
    assert m.cycle_count - before == n


@pytest.mark.parametrize("make", _MACHINES)
def test_steps_keep_the_stop_and_continue_equivalence(make) -> None:
    m, ref = _stopped(make), make()
    for _ in range(20):
        m.step()
    out = _step_frame(m)
    assert out.frame_complete
    _step_frame(ref)
    assert m.cycle_count == ref.cycle_count
    assert m.frame_number() == ref.frame_number()
    assert m.save_state() == ref.save_state()


def test_steps_cross_a_scanline_end() -> None:
    m = _stopped(_msx2)
    vdp = m.vdp
    assert isinstance(vdp, V9938)
    begun: list[int] = []
    watch_scanlines(vdp, begun.append)
    line = m._frame_line
    line_end = (line + 1) * m.cycles_per_frame // m.lines_per_frame
    for _ in range(1000):  # bounded: a regression must fail, not hang
        if m._frame_total >= line_end:
            break
        m.step()
    assert m._frame_total >= line_end
    # The instruction that reached the line end has run; the line ends
    # before the next instruction, which is the next step.
    assert line not in begun
    m.step()
    assert begun[0] == line


def test_a_step_at_the_frame_end_completes_the_frame() -> None:
    m = _stopped(_msx1)
    frame = m.frame_number()
    n = 0
    for _ in range(10000):  # bounded: a regression must fail, not hang
        if m.frame_number() != frame:
            break
        n = m.step()
    assert m.frame_number() == frame + 1
    # The step that completed the frame ran its instruction at the start of
    # the next frame.
    assert m._frame_active
    assert m._frame_line == 0
    assert m._frame_total == n


def test_a_step_over_a_breakpoint_runs_it() -> None:
    m = _msx1()
    m.set_breakpoints([_TARGET])
    assert _step_frame(m).stop_reason == STOP_BREAKPOINT
    assert m.cpu_registers().PC == _TARGET  # the breakpoint is still set
    n = m.step()
    assert n > 0
    assert m.cpu_registers().PC == _TARGET + 1  # INC D ran
    assert m.last_stop().reason == STOP_NONE


def test_a_break_request_survives_a_step() -> None:
    m = _stopped(_msx1)
    m.request_break()
    assert m.step() > 0
    assert _step_frame(m).stop_reason == STOP_REQUESTED
