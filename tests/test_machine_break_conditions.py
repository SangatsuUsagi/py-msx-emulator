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
    m = make_machine(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    _arm(m)
    m.set_break_halt_di(True)
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.halted and not m.cpu.iff1


def test_bh_disabled_does_not_break_on_halt() -> None:
    m = make_machine(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    _arm(m)
    # bh not enabled: run_frame completes without entering the debugger
    m.run_frame()
    assert m.cpu.halted


def test_bh_does_not_break_on_halt_with_interrupts_enabled() -> None:
    # EI ; HALT — interrupts enabled, so HALT is a normal wait, not a dead hang.
    m = make_machine(rom=bytes([0xFB, 0x76] + [0x00] * 32766))
    _arm(m)
    m.set_break_halt_di(True)
    m.run_frame()  # must not raise _Stop


# ---------------------------------------------------------------------------
# bs — break when SP leaves the valid RAM range
# ---------------------------------------------------------------------------

def test_bs_breaks_when_sp_enters_rom_window() -> None:
    # LD SP,7252h ; HALT  — SP jumps into the ROM window.
    m = make_machine(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    with pytest.raises(_Stop):
        m.run_frame()
    assert m.cpu.registers.SP == 0x7252


def test_bs_within_range_does_not_break() -> None:
    # LD SP,F000h ; HALT — SP stays inside the RAM range.
    m = make_machine(rom=bytes([0x31, 0x00, 0xF0, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    m.run_frame()  # must not raise


def test_set_sp_range_none_disables() -> None:
    m = make_machine(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    _arm(m)
    m.set_sp_range((0xC000, 0xFFFF))
    m.set_sp_range(None)
    m.run_frame()  # must not raise
