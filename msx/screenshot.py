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
