"""Shared display-output geometry: constant frame height and border padding.

Every renderer emits a constant `OUTPUT_H`-line frame so the displayed image
keeps a stable 4:3 geometry regardless of R#9 LN (192 active lines when LN=0,
212 when LN=1). A native-height frame shorter than `OUTPUT_H` is centred, with
`pad_rows(h)` border-coloured rows added above and below.

Both `msx/vdp/renderer.py` (TMS9918A) and `msx/vdp/v9938_renderer.py` (V9938)
share these definitions so the two output paths cannot diverge; the SDL2
frontend reads `OUTPUT_H` for its fixed window/texture height.
"""
from __future__ import annotations

ACTIVE_H = 192  # TMS9918A active height (also V9938 with R#9 LN=0)
OUTPUT_H = 212  # constant emitted frame height (V9938 LN=1 native height)


def pad_rows(native_h: int) -> int:
    """Border rows to add above (and below) a `native_h`-line frame to reach OUTPUT_H."""
    return (OUTPUT_H - native_h) // 2


def pad_to_output_height(buf: bytearray, h: int, w: int, border: int) -> bytearray:
    """Centre a native-height frame buffer in a constant `OUTPUT_H`-row buffer.

    Returns `buf` unchanged when `h >= OUTPUT_H` (already full height). Otherwise
    builds a `w × OUTPUT_H` buffer filled with `border` and copies `buf` into the
    centre rows.

    Args:
        buf: the rendered native-height frame buffer (length `w * h`).
        h: the native active height (192 or 212).
        w: the frame width (256, or 512 for SCREEN 6/7).
        border: the border-colour byte for the top/bottom padding rows.

    Returns:
        A bytearray of length `w * OUTPUT_H`.
    """
    if h >= OUTPUT_H:
        return buf
    out = bytearray([border]) * (w * OUTPUT_H)
    top = pad_rows(h) * w
    assert len(buf) == w * h  # simple slice-assign below would silently resize otherwise
    out[top:top + w * h] = buf
    return out
