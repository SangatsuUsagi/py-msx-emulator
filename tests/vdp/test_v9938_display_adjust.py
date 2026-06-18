"""Tests for R#18 display adjust (screen position correction)."""
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import _apply_display_adjust, render_frame


def test_r18_neutral_no_shift() -> None:
    vdp = V9938()
    vdp.regs[18] = 0x00  # both nibbles neutral → offset 0
    buf = bytearray(256 * 192)
    buf[20 * 256 + 10] = 5
    out = _apply_display_adjust(vdp, buf)
    assert out is buf  # identity, no copy


def test_r18_horizontal_shift_right() -> None:
    vdp = V9938()
    vdp.regs[18] = 0x0D  # low nibble D → h_off = (0xD^7)-7 = +3
    w = 256
    buf = bytearray(w * 192)
    buf[20 * w + 10] = 5
    out = _apply_display_adjust(vdp, buf)
    assert out[20 * w + 13] == 5  # moved right by 3
    assert out[20 * w + 10] == 0  # exposed edge = border (0)


def test_r18_horizontal_shift_left() -> None:
    vdp = V9938()
    vdp.regs[18] = 0x02  # low nibble 2 → h_off = (2^7)-7 = -2
    w = 256
    buf = bytearray(w * 192)
    buf[20 * w + 10] = 5
    out = _apply_display_adjust(vdp, buf)
    assert out[20 * w + 8] == 5  # moved left by 2


def test_r18_vertical_shift_down() -> None:
    vdp = V9938()
    vdp.regs[18] = 0xE0  # high nibble E → v_off = (0xE^7)-7 = +2
    w = 256
    buf = bytearray(w * 192)
    buf[20 * w + 10] = 5
    out = _apply_display_adjust(vdp, buf)
    assert out[22 * w + 10] == 5  # moved down by 2


def test_r18_512_mode_scales_horizontal_shift() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x0A  # SCREEN 7 (G6): width 512
    vdp.regs[18] = 0x0F  # low nibble F → h_off = (0xF^7)-7 = +1
    w = 512
    buf = bytearray(w * 192)
    buf[20 * w + 10] = 5
    out = _apply_display_adjust(vdp, buf)
    assert out[20 * w + 12] == 5  # 1 dot → 2 buffer pixels in 512-wide mode


def test_render_frame_applies_r18() -> None:
    vdp = V9938()
    vdp.regs[1] |= 0x40   # BL
    vdp.regs[0] = 0x06    # SCREEN 5 (G4)
    vdp.vram[0] = 0x50    # pixel (0,0) = colour 5
    vdp.regs[18] = 0x0D   # h_off = +3
    buf = render_frame(vdp)
    assert buf[3] == 5
    assert buf[0] == 0    # exposed edge = border
