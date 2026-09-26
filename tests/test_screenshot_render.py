"""Tests for render_current_rgb24()'s reported (width, height).

The renderer always emits a constant OUTPUT_H-row frame (192-line content is
centred with border padding), but render_current_rgb24() used to infer width
by dividing the buffer length by vdp.display_height — wrong whenever
display_height != OUTPUT_H (the common 192-line case), corrupting the pixel
grid for any caller (debugger `ss`, RPC screen.capture) that trusts the
returned dimensions.
"""
from __future__ import annotations

from msx.screenshot import render_current_rgb24
from msx.vdp._geometry import OUTPUT_H
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP


def test_v9938_192_line_mode_reports_output_height() -> None:
    vdp = V9938()  # R#9 bit7 clear -> 192-line native mode
    assert vdp.display_height == 192

    rgb, w, h = render_current_rgb24(vdp)
    assert (w, h) == (vdp.display_width, OUTPUT_H)
    assert len(rgb) == w * h * 3


def test_v9938_212_line_mode_reports_output_height() -> None:
    vdp = V9938()
    vdp.regs[9] |= 0x80  # LN=1 -> native 212 lines, already == OUTPUT_H
    assert vdp.display_height == OUTPUT_H

    rgb, w, h = render_current_rgb24(vdp)
    assert (w, h) == (vdp.display_width, OUTPUT_H)
    assert len(rgb) == w * h * 3


def test_tms9918a_reports_output_height() -> None:
    vdp = VDP()  # always native 192 lines, no LN concept
    assert vdp.display_height == 192

    rgb, w, h = render_current_rgb24(vdp)
    assert (w, h) == (vdp.display_width, OUTPUT_H)
    assert len(rgb) == w * h * 3


# A capture must not change emulated state: the render re-runs the sprite
# scan (5S/C/index, collision coordinates), the TMS9918A's VBlank finalize
# (F, /INT) and the V9938's S#2 EO parity.

def test_tms9918a_capture_leaves_s0_and_irq_unchanged() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x60               # BL=1, IE0=1
    vdp.regs[5] = 0x20               # SAT at 0x1000
    for i in range(5):               # five sprites on one line, two overlapping
        vdp.vram[0x1000 + i * 4 + 1] = i * 4
    vdp.vram[0x1000 + 5 * 4] = 0xD0
    vdp.vram[0] = 0xFF               # sprite pattern 0 row 0
    vdp.status = 0x00

    render_current_rgb24(vdp)

    assert vdp.status == 0x00
    assert vdp.irq is False
    assert vdp.frame_count == 0


def test_v9938_capture_leaves_sprite_status_unchanged() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x06               # GRAPHIC4, sprite mode 2
    vdp.regs[1] = 0x40
    vdp.regs[5] = 0x77
    sat = 0x77 << 7
    for i in range(9):               # nine overlapping sprites on one line
        vdp.vram[sat + i * 4] = 0
    vdp.vram[sat + 9 * 4] = 216
    vdp.vram[0] = 0xFF
    vdp.status = 0x00
    vdp.collision_x = 0x12
    vdp.collision_y = 0x34
    vdp._status2 = 0x0C
    vdp.frame_count = 1              # odd frame: a render would flip EO

    vdp.irq = True                   # restored as-is, not re-derived

    render_current_rgb24(vdp)

    assert vdp.status == 0x00
    assert (vdp.collision_x, vdp.collision_y) == (0x12, 0x34)
    assert vdp._status2 == 0x0C
    assert vdp.frame_count == 1
    assert vdp.irq is True
