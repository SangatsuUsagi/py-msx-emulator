"""machine-core: "A break stops step_frame and the next call continues the
frame", "The host requests a break", "A watchpoint stops after the accessing
instruction", "A resume does not re-break at once" and "reset and a state
load drop a stopped frame"."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from msx.boundary import (
    STOP_BREAKPOINT,
    STOP_NONE,
    STOP_REQUESTED,
    STOP_WATCHPOINT,
    WATCH_WRITE,
    FrameOutput,
)
from msx.cpu.z80 import Z80
from msx.input import InputFrame
from msx.machine import Machine
from tests.factories import make_machine, make_machine_msx2

# A loop in ROM that reaches INC D about every 88 scanlines (three times a
# frame), so a breakpoint on it lands in the middle of a frame:
#   0000 DI / LD D,0
#   0003 LD BC,0300h
#   0006 DEC BC / LD A,B / OR C / JR NZ,0006h   (26 T-states a pass)
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

# JP 4000h; at 4000h select slot 3 (RAM) for pages 2-3, store 5Ah at C000h
# from the instruction at 4006h, then loop at 4009h.
_STORE_ROM = bytearray(32768)
_STORE_ROM[0x0000:0x0003] = bytes([0xC3, 0x00, 0x40])
_STORE_ROM[0x4000:0x400B] = bytes([
    0x3E, 0xF0, 0xD3, 0xA8, 0x3E, 0x5A, 0x32, 0x00, 0xC0, 0x18, 0xFE,
])


def _msx1(rom: bytes = _LOOP_ROM) -> Machine:
    return make_machine(rom=bytes(rom))


def _msx2(rom: bytes = _LOOP_ROM) -> Machine:
    return make_machine_msx2(bytes(rom), bytes(0x4000))


_MACHINES = [pytest.param(_msx1, id="msx1"), pytest.param(_msx2, id="msx2")]


def _step(m: Machine) -> FrameOutput:
    return m.step_frame(InputFrame(), render_video=True)


def _run_to_completion(m: Machine) -> tuple[FrameOutput, int]:
    """Call step_frame until the frame completes; return the completing
    output and the number of calls that stopped."""
    stops = 0
    out = _step(m)
    while not out.frame_complete:
        stops += 1
        out = _step(m)
    return out, stops


@pytest.mark.parametrize("make", _MACHINES)
def test_breakpoint_returns_before_the_instruction_runs(make):
    m = make()
    m.set_breakpoints([_TARGET])
    out = _step(m)
    assert out.stop_reason == STOP_BREAKPOINT
    assert not out.frame_complete
    regs = m.cpu_registers()
    assert regs.PC == _TARGET
    assert regs.DE >> 8 == 0  # INC D has not run


@pytest.mark.parametrize("make", _MACHINES)
def test_the_stopped_frame_continues_where_it_stopped(make):
    m, ref = make(), make()
    m.set_breakpoints([_TARGET])
    frame = m.frame_number()
    out = _step(m)
    assert not out.frame_complete
    assert m.frame_number() == frame
    assert out.video_width == 0 and out.video_pixels == b"" and out.audio == b""
    m.set_breakpoints([])
    out = _step(m)
    assert out.frame_complete and out.stop_reason == STOP_NONE
    assert m.frame_number() == frame + 1
    assert len(out.audio) == m.samples_per_frame() * 2
    assert out.video_width == 256 and len(out.video_pixels) == 256 * 212 * 3
    # The two calls ran exactly the cycles of one uninterrupted frame.
    _step(ref)
    assert m.cycle_count == ref.cycle_count


@pytest.mark.parametrize("make", _MACHINES)
def test_two_stops_in_one_frame_make_one_frame(make):
    m = make()
    m.set_breakpoints([_TARGET])
    frame = m.frame_number()
    out, stops = _run_to_completion(m)
    assert stops >= 2
    assert m.frame_number() == frame + 1
    assert len(out.audio) == m.samples_per_frame() * 2


@pytest.mark.parametrize("make", _MACHINES)
def test_same_snapshot_with_and_without_a_stop(make):
    m, ref = make(), make()
    m.set_breakpoints([_TARGET])
    for _ in range(3):
        _run_to_completion(m)
        _step(ref)
    m.set_breakpoints([])
    assert m.save_state() == ref.save_state()


def test_frame_breakpoint_stops_after_the_frame_completes():
    m = _msx1()
    m.set_frame_breakpoint(m.frame_number() + 1)
    out = _step(m)
    assert out.stop_reason == STOP_BREAKPOINT
    assert out.frame_complete


@pytest.mark.parametrize("make", _MACHINES)
def test_break_request_stops_the_frame(make):
    m = make()
    m.request_break()
    before = m.cycle_count
    out = _step(m)
    assert out.stop_reason == STOP_REQUESTED
    assert not out.frame_complete
    assert m.last_stop().reason == STOP_REQUESTED
    assert m.cycle_count == before  # stopped before the first segment


@pytest.mark.parametrize("make", _MACHINES)
def test_break_request_during_a_frame_stops_at_vertical_blanking(make):
    # The request arrives while the first segment runs (as from a signal
    # handler); the frame stops before its second segment.
    m = make()
    orig_step = Z80.step
    requested = []

    def step_and_request(cpu: Z80) -> int:
        if cpu.registers.PC == _TARGET and not requested:
            requested.append(True)
            m.request_break()
        return orig_step(cpu)

    with patch.object(Z80, "step", step_and_request):
        out = _step(m)
    assert requested
    assert out.stop_reason == STOP_REQUESTED and not out.frame_complete
    assert m._frame_line == m.vdp.vblank_start_line + 1
    out = _step(m)
    assert out.frame_complete and out.stop_reason == STOP_NONE


def test_break_request_is_honored_once():
    m = _msx1()
    m.request_break()
    assert not _step(m).frame_complete
    out = _step(m)
    assert out.frame_complete and out.stop_reason == STOP_NONE


def test_watched_write_is_stored_before_the_stop():
    m = _msx1(_STORE_ROM)
    m.set_watchpoints([(0xC000, WATCH_WRITE)])
    out = _step(m)
    assert out.stop_reason == STOP_WATCHPOINT
    assert m.cpu_registers().PC == 0x4009
    assert m.read_memory(0xC000, 1) == b"\x5a"


def test_single_step_records_a_watchpoint_hit():
    m = _msx1(_STORE_ROM)
    m.set_watchpoints([(0xC000, WATCH_WRITE)])
    while m.cpu_registers().PC != 0x4006:
        m.step()
    m.step()
    assert m.cpu_registers().PC == 0x4009
    stop = m.last_stop()
    assert (stop.reason, stop.watch_addr, stop.watch_access) == (
        STOP_WATCHPOINT, 0xC000, WATCH_WRITE,
    )


def test_continue_past_the_breakpoint_it_stopped_at():
    m = _msx1()
    m.set_breakpoints([_TARGET])
    _step(m)
    before = m.cycle_count
    out = _step(m)
    # The next stop is the following pass through the loop, not the same PC
    # again before INC D runs.
    assert out.stop_reason == STOP_BREAKPOINT
    assert m.cpu_registers().DE >> 8 == 1
    assert m.cycle_count - before > 10000


def test_the_breakpoint_still_hits_the_next_time():
    m = _msx1()
    m.set_breakpoints([_TARGET])
    _step(m)
    _step(m)
    assert m.cpu_registers().PC == _TARGET
    assert m.last_stop().reason == STOP_BREAKPOINT


@pytest.mark.parametrize("drop", ["reset", "load"])
def test_a_reset_or_load_after_a_stop_starts_a_new_frame(drop):
    m = _msx1()
    saved = m.save_state()
    m.set_breakpoints([_TARGET])
    assert not _step(m).frame_complete
    m.set_breakpoints([])
    if drop == "reset":
        m.reset()
    else:
        m.load_state(saved)
    before = m.cycle_count
    out = _step(m)
    assert out.frame_complete
    assert m.cycle_count - before >= m.cycles_per_frame
