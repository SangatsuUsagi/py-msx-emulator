"""Tests for crash-signature auto-break conditions (debugger bh / bs)."""
from __future__ import annotations

import pytest

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper
from tests.factories import make_machine


class _Stop(Exception):
    pass


class _RecordingDebugger:
    """Fake debugger whose enter() aborts run_frame so the test can assert."""

    def enter(self) -> None:
        raise _Stop


def _build(rom: bytes):
    """Build an MSX1 machine with the conventional slot layout (RAM at page 3).

    make_machine resets with slot_register=0x00 (all BIOS, no RAM), which leaves
    the stack non-functional; 0xD4 maps page 3 (0xC000-0xFFFF) to slot 3 RAM.
    """
    m = make_machine(rom=rom)
    m.memory.slot_register = 0xD4
    return m


def _arm(machine):
    machine._debugger = _RecordingDebugger()


# ---------------------------------------------------------------------------
# Memory.main_ram_range — auto-derived bs range
# ---------------------------------------------------------------------------

def test_main_ram_range_msx1_flat_32k() -> None:
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    assert mem.main_ram_range() == (0x8000, 0xFFFF)


def test_main_ram_range_mapper_full_space() -> None:
    mem = Memory(
        rom=bytes(32768), ram=bytearray(32768),
        _mapper=FlatMapper(None), ram_mapper=RamMapper(),
    )
    assert mem.main_ram_range() == (0x0000, 0xFFFF)


# ---------------------------------------------------------------------------
# bh — break on HALT with interrupts disabled
# ---------------------------------------------------------------------------

def test_bh_breaks_on_halt_with_di() -> None:
    m = _build(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    _arm(m)
    m.set_break_halt_di(True)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.halted and not m.cpu.iff1


def test_bh_disabled_does_not_break_on_halt() -> None:
    m = _build(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    _arm(m)
    # bh not enabled: run_frame completes without entering the debugger
    m.run_frame()
    assert m.cpu.halted


def test_bh_does_not_break_on_halt_with_interrupts_enabled() -> None:
    # EI ; HALT — interrupts enabled, so HALT is a normal wait, not a dead hang.
    m = _build(rom=bytes([0xFB, 0x76] + [0x00] * 32766))
    _arm(m)
    m.set_break_halt_di(True)
    m.run_frame()  # must not raise _Stop


# ---------------------------------------------------------------------------
# bs — break when SP leaves the valid RAM range
# ---------------------------------------------------------------------------

def test_bs_breaks_when_sp_enters_rom_window() -> None:
    # LD SP,7252h ; HALT  — SP jumps into the ROM window.
    m = _build(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.SP == 0x7252


def test_bs_within_range_does_not_break() -> None:
    # LD SP,F000h ; HALT — SP stays inside the RAM range.
    m = _build(rom=bytes([0x31, 0x00, 0xF0, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    m.run_frame()  # must not raise


def test_set_sp_range_none_disables() -> None:
    m = _build(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    m.set_sp_range(None)
    m.run_frame()  # must not raise


# ---------------------------------------------------------------------------
# g — run to a one-shot temporary breakpoint
# ---------------------------------------------------------------------------

def test_g_runs_to_address() -> None:
    m = _build(rom=bytes(32768))  # all NOP
    _arm(m)
    m.set_temp_breakpoint(0x0003)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.PC == 0x0003
    assert m._temp_breakpoint is None  # one-shot: cleared on hit


def test_g_does_not_modify_permanent_breakpoints() -> None:
    m = _build(rom=bytes(32768))
    _arm(m)
    m.set_breakpoints([0x1000])
    m.set_temp_breakpoint(0x0003)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.PC == 0x0003
    assert m._breakpoints == frozenset([0x1000])


# ---------------------------------------------------------------------------
# so — step out
# ---------------------------------------------------------------------------

def test_so_returns_to_caller() -> None:
    # 0000 CALL 0006 ; 0003 HALT ; 0006 RET
    rom = bytes([0xCD, 0x06, 0x00, 0x76, 0x00, 0x00, 0xC9] + [0x00] * (32768 - 7))
    m = _build(rom=rom)
    _arm(m)
    # Run into the subroutine first.
    m.set_temp_breakpoint(0x0006)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.PC == 0x0006
    sp_inside = m.cpu.registers.SP
    # Step out: should break after RET, back at the caller (0x0003).
    m.set_step_out(sp_inside)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.PC == 0x0003
    assert m.cpu.registers.SP > sp_inside


# ---------------------------------------------------------------------------
# gf — frame breakpoint
# ---------------------------------------------------------------------------

class _CountingDebugger:
    """Fake debugger that records enter() calls without aborting run_frame."""

    def __init__(self) -> None:
        self.calls = 0

    def enter(self) -> None:
        self.calls += 1


def test_frame_breakpoint_fires_at_target_frame() -> None:
    m = _build(rom=bytes(32768))  # all NOPs
    _arm(m)
    m.set_frame_breakpoint(2)
    m.run_frame()
    assert m.vdp._frame_count == 1
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.vdp._frame_count == 2


def test_frame_breakpoint_does_not_fire_before_target() -> None:
    m = _build(rom=bytes(32768))
    _arm(m)
    m.set_frame_breakpoint(2)
    m.run_frame()  # frame 1 — should not fire
    assert m.vdp._frame_count == 1


def test_frame_breakpoint_clears_after_firing() -> None:
    m = _build(rom=bytes(32768))
    debugger = _CountingDebugger()
    m._debugger = debugger
    m.set_frame_breakpoint(1)
    m.run_frame()
    assert debugger.calls == 1
    assert m._frame_breakpoint is None
    # Running further frames must not re-trigger the cleared breakpoint.
    m.run_frame()
    m.run_frame()
    assert debugger.calls == 1
    assert m._stepout_sp is None
