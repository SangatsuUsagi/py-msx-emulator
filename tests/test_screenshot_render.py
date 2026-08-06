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
    assert (w, h) == (256, OUTPUT_H)
    assert len(rgb) == w * h * 3
