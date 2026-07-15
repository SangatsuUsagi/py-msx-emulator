"""Shared RGB24 → PNG writer for save-states and screenshots.

Lives in the core so both the SDL frontend and the debugger can capture the
screen without the debugger having to reach up into the frontend layer.
"""
from __future__ import annotations

import datetime
import io
from pathlib import Path

from PIL import Image as _PIL_Image


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
