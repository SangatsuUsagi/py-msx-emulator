"""Shared RGB24 → PNG writer for save-states and screenshots.

Lives in the core so both the SDL frontend and the debugger can capture the
screen without the debugger having to reach up into the frontend layer.
"""
from __future__ import annotations

import datetime
import io
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image as _PIL_Image

if TYPE_CHECKING:
    from msx.vdp.vdp import VdpDevice


def render_current_rgb24(vdp: "VdpDevice") -> tuple[bytes, int, int]:
    """Render the *current* VDP state to RGB24 without perturbing frame state.

    Picks the V9938 or TMS renderer and returns (rgb_bytes, width, height).
    A render re-runs the sprite scan (S#0 5S/C/index, V9938 collision
    coordinates), and on the TMS9918A raises F and the /INT level, on the
    V9938 recomputes S#2's EO bit; all of that is saved and restored so a
    capture never changes what the program reads next. frame_count (and irq
    on the V9938) are not written by a render and are restored only as a
    guard.
    Shared by the debugger REPL and the RPC adapter so both capture the frame
    identically.

    Both renderers always emit a constant OUTPUT_H-row frame (a native height
    shorter than OUTPUT_H is centred with border padding — see _geometry.py),
    so the height is always OUTPUT_H, never vdp.display_height.
    """
    from msx.vdp._geometry import OUTPUT_H
    from msx.vdp.renderer import render_frame
    from msx.vdp.v9938 import V9938
    from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
    from msx.vdp.vdp import VDP

    saved_fc = vdp.frame_count
    saved_status = vdp.status
    saved_irq = vdp.irq
    if isinstance(vdp, V9938):
        saved_col = (vdp.collision_x, vdp.collision_y)
        saved_s2 = vdp._status2
        try:
            idx = render_frame_v9938(vdp)
        finally:
            vdp.collision_x, vdp.collision_y = saved_col
            vdp._status2 = saved_s2
            vdp.frame_count = saved_fc
            vdp.status = saved_status
            vdp.irq = saved_irq
    else:
        assert isinstance(vdp, VDP)
        try:
            idx = render_frame(vdp)
        finally:
            vdp.frame_count = saved_fc
            vdp.status = saved_status
            vdp.irq = saved_irq
    return vdp.to_rgb24(idx), vdp.display_width, OUTPUT_H


def scale_rgb24(
    rgb_buf: bytes | bytearray, width: int, height: int, out_w: int, out_h: int
) -> bytes:
    """Nearest-neighbour resize an RGB24 buffer, returning packed bytes."""
    img = _PIL_Image.frombytes("RGB", (width, height), bytes(rgb_buf))
    return img.resize((out_w, out_h), _PIL_Image.Resampling.NEAREST).tobytes()


def encode_rgb24_png(rgb_buf: bytes | bytearray, width: int, height: int) -> bytes:
    """Encode a width×height RGB24 buffer as PNG and return the bytes.

    Args:
        rgb_buf: Packed RGB24 pixel data (3 bytes per pixel, row-major).
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        The encoded PNG file as a bytes object.
    """
    img = _PIL_Image.frombytes("RGB", (width, height), bytes(rgb_buf))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_rgb24_png(
    rgb_buf: bytes | bytearray, width: int, height: int, path: Path
) -> None:
    """Encode a width×height RGB24 buffer as a PNG at `path` (creating parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_rgb24_png(rgb_buf, width, height))


def save_screenshot(rgb_buf: bytes | bytearray, width: int, height: int) -> Path:
    """Write the frame to a timestamped PNG in saves/screenshots/ and return its path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path("saves") / "screenshots" / f"screenshot_{stamp}.png"
    write_rgb24_png(rgb_buf, width, height, path)
    print(f"screenshot saved: {path}")
    return path
