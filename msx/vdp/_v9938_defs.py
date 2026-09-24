"""Definitions shared by the V9938 chip (msx/vdp/v9938.py) and its renderer
(msx/vdp/v9938_renderer.py).

A leaf module, so the chip can import the renderer at module level without an
import cycle (the renderer needs these at load time, and the chip only under
TYPE_CHECKING).
"""
from __future__ import annotations

from dataclasses import dataclass

# S#2 bit 1: current field (even/odd), toggles once per frame. Computed by
# v9938_renderer._update_field_parity into the render status (once per render)
# and committed only by render_frame -- the renderer owns frame-parity tracking
# for interlace mode.
_S2_EO = 0x02


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
