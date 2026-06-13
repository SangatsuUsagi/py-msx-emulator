"""Tests for V9938 G4 (SCREEN 5) renderer."""
from __future__ import annotations

from msx.vdp.v9938 import V9938
from msx.vdp.renderer import render_frame, _render_g4


def _make_g4_vdp(ln: bool = False) -> V9938:
    vdp = V9938()
    vdp.regs[0] = 0x06   # M3=1, M4=1 → G4
    vdp.regs[1] = 0x60   # BL=1 (display on)
    if ln:
        vdp.regs[9] = 0x80  # LN=1 → 212 lines
    return vdp


def test_g4_render_frame_size_192() -> None:
    buf = render_frame(_make_g4_vdp())
    assert len(buf) == 256 * 192


def test_g4_render_frame_size_212_with_ln() -> None:
    buf = render_frame(_make_g4_vdp(ln=True))
    assert len(buf) == 256 * 212


def test_g4_even_pixel_high_nibble() -> None:
    vdp = _make_g4_vdp()
    vdp.vram[0] = 0xAB  # even pixel = 0xA, odd pixel = 0xB
    buf = bytearray(256 * 192)
    _render_g4(vdp, buf)
    assert buf[0] == 0xA  # pixel (0,0) = high nibble
    assert buf[1] == 0xB  # pixel (1,0) = low nibble


def test_g4_second_row() -> None:
    vdp = _make_g4_vdp()
    vdp.vram[128] = 0xCD  # first byte of row 1
    buf = bytearray(256 * 192)
    _render_g4(vdp, buf)
    assert buf[256] == 0xC   # pixel (0,1)
    assert buf[256 + 1] == 0xD  # pixel (1,1)


def test_g4_all_background_when_vram_zero() -> None:
    vdp = _make_g4_vdp()
    buf = bytearray(256 * 192)
    _render_g4(vdp, buf)
    assert all(b == 0 for b in buf)


def test_g4_display_base_from_r2() -> None:
    vdp = _make_g4_vdp()
    # R2 bits [5:4] = 01 → display_base = 0x8000
    vdp.regs[2] = 0x10   # bit4=1 → (0x10 >> 4 & 3) << 15 = 1 << 15 = 0x8000
    vdp.vram[0x8000] = 0xEF
    buf = bytearray(256 * 192)
    _render_g4(vdp, buf)
    assert buf[0] == 0xE
    assert buf[1] == 0xF
