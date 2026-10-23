"""Current-frame capture as RGB24, shared by the debugger and the RPC adapter.

PNG encoding and file writing live in host/screenshot.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.vdp.vdp import VdpDevice


def render_current_rgb24(vdp: "VdpDevice") -> tuple[bytes, int, int]:
    """Render the *current* VDP state to RGB24 without perturbing it.

    The VDP's own render_preview() keeps the capture invisible to the
    program; shared by the debugger REPL and the RPC adapter so both capture
    the frame identically. Returns (rgb_bytes, width, height); both renderers
    emit a constant OUTPUT_H-row frame (see _geometry.py), so the height is
    always OUTPUT_H, never vdp.display_height.
    """
    from msx.vdp._geometry import OUTPUT_H

    rgb = vdp.to_rgb24(vdp.render_preview())
    # The frame's width, which a mid-frame width change can make differ from the
    # live registers' display_width.
    return rgb, len(rgb) // (3 * OUTPUT_H), OUTPUT_H
