"""Tests for the programmatic pause hook that the RPC server installs."""
from __future__ import annotations

import pytest

from tests.factories import make_machine


class _Stop(Exception):
    pass


class _RecordingDebugger:
    """Fake REPL: records whether enter() was called (and aborts if it is)."""

    def __init__(self) -> None:
        self.entered = False

    def enter(self) -> None:
        self.entered = True
        raise _Stop


def _build(rom: bytes):
    m = make_machine(rom=rom)
    m.memory.slot_register = 0xD4  # map RAM at page 3 so the stack works
    return m


def test_pause_hook_invoked_on_breakpoint() -> None:
    m = _build(rom=bytes(32768))  # all NOP
    events: list[tuple[str, int]] = []
    m.set_pause_hook(lambda reason, pc: events.append((reason, pc)))
    m.set_breakpoints([0x0003])
    m.run_frame()
    assert events, "pause hook was not called"
    reason, pc = events[0]
    assert reason == "breakpoint"
    assert pc == 0x0003


def test_pause_hook_takes_priority_over_repl() -> None:
    m = _build(rom=bytes(32768))
    dbg = _RecordingDebugger()
    m._debugger = dbg
    m.set_pause_hook(lambda reason, pc: None)
    m.set_breakpoints([0x0003])
    m.run_frame()  # must not raise _Stop
    assert not dbg.entered, "interactive REPL should be bypassed when a hook is set"


def test_repl_still_used_when_no_hook() -> None:
    m = _build(rom=bytes(32768))
    dbg = _RecordingDebugger()
    m._debugger = dbg
    m.set_breakpoints([0x0003])
    with pytest.raises(_Stop):
        m.run_frame()
    assert dbg.entered


def test_clearing_hook_restores_repl() -> None:
    m = _build(rom=bytes(32768))
    dbg = _RecordingDebugger()
    m._debugger = dbg
    m.set_pause_hook(lambda reason, pc: None)
    m.set_pause_hook(None)
    m.set_breakpoints([0x0003])
    with pytest.raises(_Stop):
        m.run_frame()
    assert dbg.entered
