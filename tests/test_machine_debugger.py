"""Tests for Machine debugger integration (breakpoints, KeyboardInterrupt)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from tests.factories import make_machine, make_machine_msx2

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
