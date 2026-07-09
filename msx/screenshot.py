"""Shared RGB24 → PNG writer for save-states and screenshots.

Lives in the core so both the SDL frontend and the debugger can capture the
screen without the debugger having to reach up into the frontend layer.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from PIL import Image as _PIL_Image


def write_rgb24_png(
    rgb_buf: bytes | bytearray, width: int, height: int, path: Path
) -> None:
    """Encode a width×height RGB24 buffer as a PNG at `path` (creating parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = _PIL_Image.frombytes("RGB", (width, height), bytes(rgb_buf))
    img.save(path)


def save_screenshot(rgb_buf: bytes | bytearray, width: int, height: int) -> Path:
    """Write the frame to a timestamped PNG in saves/screenshots/ and return its path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path("saves") / "screenshots" / f"screenshot_{stamp}.png"
    write_rgb24_png(rgb_buf, width, height, path)
    print(f"screenshot saved: {path}")
    return path
