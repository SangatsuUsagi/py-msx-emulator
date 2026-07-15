"""Tests for the bytes-returning PNG encoder used by the RPC screen.capture."""
from __future__ import annotations

import io

from PIL import Image

from msx.screenshot import encode_rgb24_png

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_encode_returns_png_signature() -> None:
    data = encode_rgb24_png(bytes(4 * 3), 2, 2)
    assert data[:8] == _PNG_SIGNATURE


def test_encode_round_trips_dimensions_and_pixels() -> None:
    # 2x1 image: one red pixel, one green pixel.
    rgb = bytes([255, 0, 0, 0, 255, 0])
    data = encode_rgb24_png(rgb, 2, 1)

    img = Image.open(io.BytesIO(data))
    assert img.size == (2, 1)
    assert img.mode == "RGB"
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert img.getpixel((1, 0)) == (0, 255, 0)


def test_encode_accepts_bytearray() -> None:
    data = encode_rgb24_png(bytearray([1, 2, 3]), 1, 1)
    assert data[:8] == _PNG_SIGNATURE
