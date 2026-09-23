"""Tests for TMS9918A run_frame() scanline splitting at vblank_start_line.

Mirrors tests/test_machine_scanline.py's V9938 coverage: the TMS9918A path now
splits its scanline loop the same way (see openspec/specs/machine-core/spec.md
"V9938 frame is rendered at the start of vertical blanking" and
openspec/specs/vdp-interrupts/spec.md "VBlank flag" / "IRQ on VBlank").
"""
from msx.cpu.z80 import Z80
from msx.machine import CYCLES_PER_FRAME, Machine
from msx.vdp._geometry import ACTIVE_H, OUTPUT_H, pad_rows
from msx.vdp.vdp import VDP
from tests.factories import make_machine

_DUMMY_ROM = bytes(32768)
_MID_DISPLAY_PC = 0x1000  # well inside the pre-render segment (all-NOP ROM)


def _make_msx1() -> Machine:
    return make_machine(_DUMMY_ROM)


# ---------------------------------------------------------------------------
# VBlank flag/interrupt: fires at the display_height boundary, not at the end
# ---------------------------------------------------------------------------

def test_finalize_runs_before_the_post_display_height_segment() -> None:
    """The VBlank flag (and on_interrupt, if IE0) must already be visible while
    the remaining (post-display_height) scanlines of the same run_frame() call
    are still executing -- not only starting with the next run_frame() call."""
    machine = _make_msx1()
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60  # BL=1, IE0=1

    seen_status_mid_frame: list[int] = []

    def on_interrupt() -> None:
        seen_status_mid_frame.append(vdp.status & 0x80)

    vdp.on_interrupt = on_interrupt
    machine.run_frame(skip_render=True)

    assert seen_status_mid_frame == [0x80]


def test_on_interrupt_fires_once_before_post_split_scanlines_run() -> None:
    """A regression guard for the boundary itself: on_interrupt must have run
    by the time run_frame() reaches vblank_start_line, confirmed by the second
    scanline segment's cycle budget still being available afterwards."""
    machine = _make_msx1()
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60  # BL=1, IE0=1

    calls = []
    vdp.on_interrupt = lambda: calls.append(machine.cycle_count)

    machine.run_frame(skip_render=True)

    assert len(calls) == 1
    # display_height (192) of lines_per_frame (262) have run: on_interrupt
    # fired at roughly 192/262 of the frame's cycle budget, with cycles left
    # over for the post-split segment -- not at/after the full cycle budget.
    fraction = calls[0] / CYCLES_PER_FRAME
    assert 0.70 < fraction < 0.78
    assert machine.cycle_count > calls[0]


def test_vblank_isr_vram_write_lands_on_the_next_frame() -> None:
    """VRAM written by the interrupt handler must not appear in the frame that
    was being displayed when the interrupt fired -- same guarantee the V9938
    path already has (tests/test_machine_scanline.py)."""
    machine = _make_msx1()
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    # GRAPHIC1 (SCREEN 1): name table @0, pattern table @0x800, colour table
    # @0x40 -- distinct, non-overlapping addresses.
    vdp.regs[1] = 0x60  # BL=1, IE0=1 (M1=M2=0 -> GRAPHIC1)
    vdp.regs[3] = 1  # colour table base = 1 << 6 = 0x40
    vdp.regs[4] = 1  # pattern table base = (1 & 7) << 11 = 0x800
    vdp.vram[0x40] = 0xF1  # tile-group 0 colour: fg=15, bg=1

    def on_interrupt() -> None:
        # The "ISR": sets pixel (0, 0)'s pattern bit, changing it from bg (1)
        # to fg (15).
        vdp.vram[0x800] = 0x80

    vdp.on_interrupt = on_interrupt

    first = machine.run_frame()
    second = machine.run_frame()

    top_left = pad_rows(ACTIVE_H) * 256
    assert len(first) == 256 * OUTPUT_H
    assert first[top_left] == 1
    assert second[top_left] == 15


# ---------------------------------------------------------------------------
# Pause / Ctrl-C mid-display still stops the frame (TMS9918A now splits too)
# ---------------------------------------------------------------------------

def test_pause_during_active_display_skips_the_rest_of_the_frame() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x40  # BL=1

    machine.set_pause_hook(lambda reason, pc: None)
    machine.set_breakpoints([_MID_DISPLAY_PC])
    machine.run_frame(skip_render=True)

    assert machine.is_paused
    assert machine.cycle_count < machine.cycles_per_frame


def test_ctrl_c_during_active_display_skips_the_vblank_segment() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x40  # BL=1

    calls: list[int] = []
    # Z80 is a slots dataclass (no per-instance __dict__), so `step` can only
    # be overridden at the class level -- restored in `finally`, mirroring
    # how this repo's own investigation tooling patches VDP.write_port.
    orig_step = Z80.step

    def patched_step(self: Z80) -> int:
        if self.registers.PC == _MID_DISPLAY_PC:
            raise KeyboardInterrupt
        return orig_step(self)

    Z80.step = patched_step
    try:
        # A pause hook is what makes _on_frame_interrupt handle Ctrl-C instead
        # of re-raising; no breakpoints, so this runs on the fast loop.
        machine.set_pause_hook(lambda reason, pc: calls.append(pc))
        machine.run_frame(skip_render=True)
    finally:
        Z80.step = orig_step

    assert calls
    assert machine.cycle_count < machine.cycles_per_frame
