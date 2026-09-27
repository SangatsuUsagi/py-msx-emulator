"""Definitions shared by the V9938 chip (msx/vdp/v9938.py) and its renderer
(msx/vdp/v9938_renderer.py).

A leaf module: both the chip and the renderer import it at load time, and the
renderer imports the chip only under TYPE_CHECKING, so the chip can import the
renderer at module level without an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass

# S#2 bit 1: current field (even/odd), toggles once per frame. Computed by
# v9938_renderer._update_field_parity into the render status (once per render)
# and committed only by render_frame -- the renderer owns frame-parity tracking
# for interlace mode.
_S2_EO = 0x02


# Register-derived geometry, as functions of a register list so the renderer
# can evaluate them for a band's snapshot as well as the live registers (the
# V9938 properties named without the `_of` suffix call these).


def display_height_of(regs: list[int]) -> int:
    """192 lines by default; 212 when R#9 bit 7 (LN) is set."""
    return 212 if (regs[9] & 0x80) else 192


def display_width_of(regs: list[int]) -> int:
    """256 normally; 512 for the wide bitmap modes SCREEN 6 (G5) and
    SCREEN 7 (G6) (M5 set, M4 clear; SCREEN 8/G7, M5+M4, is 256), and for
    TEXT2 (M1 and M4 set, M3 clear)."""
    r0 = regs[0]
    r1 = regs[1]
    m1 = (r1 >> 4) & 1
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1
    is_text2 = bool(m4 and m1 and not m3)
    return 512 if ((m5 and not m4) or is_text2) else 256


def even_odd_enabled_of(regs: list[int]) -> bool:
    """R#9 bit 2 (EO): automatic even/odd page alternation enable."""
    return bool(regs[9] & 0x04)


@dataclass(slots=True)
class _RegChange:
    """A tracked register write, logged per scanline for banded rendering.

    Tagged-union alternative to the old (line, reg, int | tuple) log entry:
    a _RegChange sets regs[reg] = value; a _PaletteChange sets palette[idx] = rgb.
    Consumers dispatch by type (isinstance) rather than a reg == -1 sentinel,
    which maps directly to a Rust enum / C++ variant.
    """

    line: int
    reg: int
    value: int


@dataclass(slots=True)
class _PaletteChange:
    """A palette write, logged per scanline for banded rendering (see _RegChange)."""

    line: int
    idx: int
    rgb: int
