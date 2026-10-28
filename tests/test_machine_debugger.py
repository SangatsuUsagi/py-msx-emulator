"""Tests for Machine debugger integration (breakpoints, KeyboardInterrupt)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from msx.boundary import (
    STOP_BREAKPOINT,
    STOP_NONE,
    STOP_WATCHPOINT,
    WATCH_WRITE,
    StopInfo,
)
from msx.input import InputFrame
from tests.factories import make_machine

_NOP_ROM = bytes(32768)
_EXTROM = bytes(32768)


# ---------------------------------------------------------------------------
# set_breakpoints
# ---------------------------------------------------------------------------

class TestSetBreakpoints:
    def test_empty_set(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([])
        assert m._breakpoints == frozenset()

    def test_single_address(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([0xC000])
        assert m._breakpoints == frozenset([0xC000])

    def test_multiple_addresses(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([0x0000, 0xC000, 0xD000])
        assert m._breakpoints == frozenset([0x0000, 0xC000, 0xD000])

    def test_max_4_truncated(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([0x1000, 0x2000, 0x3000, 0x4000, 0x5000])
        assert len(m._breakpoints) == 4
        assert 0x5000 not in m._breakpoints

    def test_stored_as_frozenset(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([0xBEEF])
        assert isinstance(m._breakpoints, frozenset)

    def test_replace_existing(self):
        m = make_machine(rom=_NOP_ROM)
        m.set_breakpoints([0x1000])
        m.set_breakpoints([0x2000])
        assert m._breakpoints == frozenset([0x2000])


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------

class TestDefaultState:
    def test_no_debugger_by_default(self):
        m = make_machine(rom=_NOP_ROM)
        assert m._debugger is None

    def test_attach_debugger_is_what_a_break_enters(self):
        m = make_machine(rom=_NOP_ROM)
        dbg = MagicMock()
        m.attach_debugger(dbg)
        assert m.enter_debugger_if_attached() is True
        dbg.enter.assert_called_once_with()
        m.attach_debugger(None)
        assert m.enter_debugger_if_attached() is False

    def test_empty_breakpoints_by_default(self):
        m = make_machine(rom=_NOP_ROM)
        assert m._breakpoints == frozenset()


# ---------------------------------------------------------------------------
# KeyboardInterrupt handling
# ---------------------------------------------------------------------------

class TestKeyboardInterrupt:
    def test_ctrl_c_calls_debugger_enter(self):
        m = make_machine(rom=_NOP_ROM)
        dbg = MagicMock()
        m._debugger = dbg

        # Patch Z80.step via the class to raise KeyboardInterrupt once
        call_count = [0]
        original_step = m.cpu.__class__.step

        def step_raising(self_cpu):
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt
            return original_step(self_cpu)

        with patch.object(m.cpu.__class__, "step", step_raising):
            m.run_frame()
        dbg.enter.assert_called_once()

    def test_ctrl_c_propagates_without_debugger(self):
        m = make_machine(rom=_NOP_ROM)
        assert m._debugger is None

        call_count = [0]
        original_step = m.cpu.__class__.step

        def step_raising(self_cpu):
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt
            return original_step(self_cpu)

        with patch.object(m.cpu.__class__, "step", step_raising):
            with pytest.raises(KeyboardInterrupt):
                m.run_frame()


# ---------------------------------------------------------------------------
# Breakpoint triggers debugger
# ---------------------------------------------------------------------------

class TestBreakpointTrigger:
    def test_breakpoint_at_pc_calls_enter(self):
        # ROM at 0x0000 is all NOPs, so PC starts at 0x0000
        m = make_machine(rom=_NOP_ROM)
        dbg = MagicMock()
        m._debugger = dbg
        m.set_breakpoints([0x0000])

        # After enter() returns, PC advances; subsequent steps won't hit 0x0000 again
        # unless we loop. Just run one frame and verify enter was called.
        m.run_frame()
        assert dbg.enter.call_count >= 1

    def test_no_overhead_without_breakpoints(self):
        """Empty _breakpoints takes the fast path: debugger.enter never called."""
        m = make_machine(rom=_NOP_ROM)
        dbg = MagicMock()
        m._debugger = dbg
        # _breakpoints is empty — fast path used, enter should not be called
        m.run_frame()
        dbg.enter.assert_not_called()


# ---------------------------------------------------------------------------
# Last stop (machine-core "The last stop is reported as a flat record")
# ---------------------------------------------------------------------------

def _store_rom() -> bytes:
    """JP 4000h; at 4000h select slot 3 (RAM) for pages 2-3, store 5Ah at
    C000h from the instruction at 4006h, then loop at 4009h."""
    rom = bytearray(32768)
    rom[0x0000:0x0003] = bytes([0xC3, 0x00, 0x40])  # JP 4000h
    rom[0x4000:0x400B] = bytes([
        0x3E, 0xF0,        # 4000: LD A,F0h
        0xD3, 0xA8,        # 4002: OUT (A8h),A
        0x3E, 0x5A,        # 4004: LD A,5Ah
        0x32, 0x00, 0xC0,  # 4006: LD (C000h),A
        0x18, 0xFE,        # 4009: JR 4009h
    ])
    return bytes(rom)


class TestLastStop:
    def test_watchpoint_hit_names_the_access(self):
        m = make_machine(rom=_store_rom())
        m.set_watchpoints([(0xC000, "w")])
        m.step_frame(InputFrame(), render_video=False)
        assert m.last_stop() == StopInfo(
            STOP_WATCHPOINT, 0x4006, 0xC000, 0x5A, WATCH_WRITE
        )

    def test_complete_frame_clears_the_stop(self):
        m = make_machine(rom=_store_rom())
        m.set_breakpoints([0x4000])
        m.step_frame(InputFrame(), render_video=False)
        assert m.last_stop() == StopInfo(STOP_BREAKPOINT, 0x4000)
        m.set_breakpoints([])
        m.step_frame(InputFrame(), render_video=False)
        assert m.last_stop().reason == STOP_NONE

    def test_single_step_records_a_watchpoint_hit(self):
        m = make_machine(rom=_store_rom())
        m.set_watchpoints([(0xC000, "w")])
        while m.cpu_registers().PC != 0x4006:
            m.step()
        m.step()
        assert m.cpu_registers().PC == 0x4009
        stop = m.last_stop()
        assert (stop.reason, stop.watch_addr, stop.watch_access) == (
            STOP_WATCHPOINT, 0xC000, WATCH_WRITE,
        )
        m.step()
        assert m.last_stop().reason == STOP_NONE
