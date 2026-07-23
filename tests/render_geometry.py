"""Test helper: strip the constant output-height border padding from a frame.

`render_frame` now always emits a constant `OUTPUT_H`-line buffer (192-line
frames are centred with border rows). Pixel-position tests want native
scanline coordinates, so `active_region` returns just the active picture rows.
"""
from __future__ import annotations

from msx.vdp._geometry import OUTPUT_H, pad_rows


def active_region(buf: bytearray, display_height: int) -> bytearray:
    """Return the native-height active region of a padded frame buffer.

    Args:
        buf: a `render_frame` output buffer (length width * OUTPUT_H).
        display_height: the VDP's native active height (192 or 212).

    Returns:
        The rows holding the active picture, with the top/bottom border padding
        removed (so `buf[y * width + x]` uses native scanline `y`).
    """
    w = len(buf) // OUTPUT_H
    pad = pad_rows(display_height)
    return buf[pad * w:(pad + display_height) * w]
