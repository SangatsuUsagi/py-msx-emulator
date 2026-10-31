"""Tests for crash-signature auto-break conditions (debugger bh / bs)."""
from __future__ import annotations

from msx.boundary import STOP_BREAKPOINT, STOP_NONE
from msx.input import InputFrame
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper
from tests.factories import make_machine


def _build(rom: bytes):
    """Build an MSX1 machine with the conventional slot layout (RAM at page 3).

    make_machine resets with slot_register=0x00 (all BIOS, no RAM), which leaves
    the stack non-functional; 0xD4 maps page 3 (0xC000-0xFFFF) to slot 3 RAM.
    """
    m = make_machine(rom=rom)
    m.memory.set_slot_register(0xD4)
    return m


def _run(machine) -> int:
    """Run step_frame once; return its stop reason."""
    return machine.step_frame(InputFrame(), render_video=False).stop_reason


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


def test_main_ram_range_falls_back_to_slot0_ram_when_slot3_empty() -> None:
    """Sony HB-10 shape: slot 3 has no RAM at all (self.ram is zero-length),
    so main_ram_range() must report slot0_ram's own page window instead of
    the degenerate (0x10000, 0xFFFF) it would otherwise compute."""
    mem = Memory(
        rom=bytes(32768), ram=bytearray(0),
        _mapper=FlatMapper(None), slot0_ram=bytearray(0x4000), slot0_ram_page=3,
    )
    assert mem.main_ram_range() == (0xC000, 0xFFFF)


# ---------------------------------------------------------------------------
# bh — break on HALT with interrupts disabled
# ---------------------------------------------------------------------------

def test_bh_breaks_on_halt_with_di() -> None:
    m = _build(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    m.set_break_halt_di(True)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.halted and not m.cpu.iff1


def test_bh_disabled_does_not_break_on_halt() -> None:
    m = _build(rom=bytes([0xF3, 0x76] + [0x00] * 32766))  # DI ; HALT
    # bh not enabled: the frame completes without a stop
    assert _run(m) == STOP_NONE
    assert m.cpu.halted


def test_bh_does_not_break_on_halt_with_interrupts_enabled() -> None:
    # EI ; HALT — interrupts enabled, so HALT is a normal wait, not a dead hang.
    m = _build(rom=bytes([0xFB, 0x76] + [0x00] * 32766))
    m.set_break_halt_di(True)
    assert _run(m) == STOP_NONE


# ---------------------------------------------------------------------------
# bs — break when SP leaves the valid RAM range
# ---------------------------------------------------------------------------

def test_bs_breaks_when_sp_enters_rom_window() -> None:
    # LD SP,7252h ; HALT  — SP jumps into the ROM window.
    m = _build(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    m.set_sp_range(0xC000, 0xFFFF)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.registers.SP == 0x7252


def test_bs_within_range_does_not_break() -> None:
    # LD SP,F000h ; HALT — SP stays inside the RAM range.
    m = _build(rom=bytes([0x31, 0x00, 0xF0, 0x76] + [0x00] * 32764))
    m.set_sp_range(0xC000, 0xFFFF)
    assert _run(m) == STOP_NONE


def test_set_sp_range_none_disables() -> None:
    m = _build(rom=bytes([0x31, 0x52, 0x72, 0x76] + [0x00] * 32764))
    m.set_sp_range(0xC000, 0xFFFF)
    m.set_sp_range(-1, -1)
    assert _run(m) == STOP_NONE


# ---------------------------------------------------------------------------
# g — run to a one-shot temporary breakpoint
# ---------------------------------------------------------------------------

def test_g_runs_to_address() -> None:
    m = _build(rom=bytes(32768))  # all NOP
    m.set_temp_breakpoint(0x0003)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.registers.PC == 0x0003
    assert m._temp_breakpoint == -1  # one-shot: cleared on hit


def test_g_does_not_modify_permanent_breakpoints() -> None:
    m = _build(rom=bytes(32768))
    m.set_breakpoints([0x1000])
    m.set_temp_breakpoint(0x0003)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.registers.PC == 0x0003
    assert m._breakpoints == frozenset([0x1000])


# ---------------------------------------------------------------------------
# so — step out
# ---------------------------------------------------------------------------

def test_so_returns_to_caller() -> None:
    # 0000 CALL 0006 ; 0003 HALT ; 0006 RET
    rom = bytes([0xCD, 0x06, 0x00, 0x76, 0x00, 0x00, 0xC9] + [0x00] * (32768 - 7))
    m = _build(rom=rom)
    # Run into the subroutine first.
    m.set_temp_breakpoint(0x0006)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.registers.PC == 0x0006
    sp_inside = m.cpu.registers.SP
    # Step out: should break after RET, back at the caller (0x0003).
    m.set_step_out(sp_inside)
    assert _run(m) == STOP_BREAKPOINT
    assert m.cpu.registers.PC == 0x0003
    assert m.cpu.registers.SP > sp_inside


# ---------------------------------------------------------------------------
# gf — frame breakpoint
# ---------------------------------------------------------------------------

def test_frame_breakpoint_fires_at_target_frame() -> None:
    m = _build(rom=bytes(32768))  # all NOPs
    m.set_frame_breakpoint(2)
    assert _run(m) == STOP_NONE
    assert m.vdp.frame_count == 1
    assert _run(m) == STOP_BREAKPOINT
    assert m.vdp.frame_count == 2


def test_frame_breakpoint_does_not_fire_before_target() -> None:
    m = _build(rom=bytes(32768))
    m.set_frame_breakpoint(2)
    assert _run(m) == STOP_NONE  # frame 1 — should not fire
    assert m.vdp.frame_count == 1


def test_frame_breakpoint_clears_after_firing() -> None:
    m = _build(rom=bytes(32768))
    m.set_frame_breakpoint(1)
    assert _run(m) == STOP_BREAKPOINT
    assert m._frame_breakpoint == -1
    # Running further frames must not re-trigger the cleared breakpoint.
    assert _run(m) == STOP_NONE
    assert _run(m) == STOP_NONE
    assert m._stepout_sp == -1
