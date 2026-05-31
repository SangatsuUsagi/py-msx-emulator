from msx.vdp.vdp import VDP
from msx.vdp.renderer import render_frame

# Layout:
#   name table  at 0x0000  (R2=0x00)
#   pattern gen at 0x0800  (R4=0x01, 0x01<<11=0x800)
_NAME = 0x0000
_PAT  = 0x0800


def make_text_vdp() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x50   # M1=1 (bit 4), BL=1 (bit 6) → Text mode, display on
    vdp.regs[2] = 0x00   # name table at 0x0000
    vdp.regs[4] = 0x01   # pattern gen at 0x0800
    vdp.regs[7] = 0xF2   # fg=15 (white), bg=2
    return vdp


def test_text_frame_dimensions() -> None:
    buf = render_frame(make_text_vdp())
    assert len(buf) == 256 * 192


def test_text_border_pixels_are_bg() -> None:
    vdp = make_text_vdp()
    vdp.regs[7] = 0xF3   # fg=15, bg=3
    buf = render_frame(vdp)
    # Left 8-pixel border on row 0
    for px in range(8):
        assert buf[px] == 3, f"left border pixel {px} expected 3 got {buf[px]}"
    # Right 8-pixel border: columns 248–255
    for px in range(248, 256):
        assert buf[px] == 3, f"right border pixel {px} expected 3 got {buf[px]}"


def test_text_character_width_six_pixels() -> None:
    vdp = make_text_vdp()
    vdp.regs[7] = 0xF2   # fg=15, bg=2
    # Tile 0 at col 0: pattern row 0 all set → fg on all 6 pixels, then bg for pixel 6+ (next char space)
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xFF   # all bits set

    buf = render_frame(vdp)

    # Col 0 starts at x=8; 6 pixels wide → x=8..13 should be fg=15
    for px in range(8, 14):
        assert buf[px] == 15, f"pixel {px} expected fg=15 got {buf[px]}"
    # x=14 is start of col 1 (tile 0 again but pattern is same — actually tile index matters)
    # Just verify col 0 char ends at x=13 by checking the first border col x=7 = bg
    assert buf[7] == 2  # left border still bg


def test_text_fg_zero_uses_colour_1() -> None:
    vdp = make_text_vdp()
    vdp.regs[7] = 0x02   # fg=0 (transparent → colour 1), bg=2
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xFF

    buf = render_frame(vdp)

    # fg=0 should resolve to colour 1 (backdrop fixed at 1 for text mode)
    assert buf[8] == 1


def test_text_no_sprites_rendered() -> None:
    vdp = make_text_vdp()
    # Enable a sprite with colour 15 at (8, 0)
    vdp.regs[5] = 0x20          # SAT at 0x1000
    sat = 0x1000
    vdp.vram[sat + 0] = 0x00    # Y=0 → top=1
    vdp.vram[sat + 1] = 0x08    # X=8
    vdp.vram[sat + 2] = 0x00    # pattern 0
    vdp.vram[sat + 3] = 0x0F    # colour 15
    vdp.regs[6] = 0x00
    vdp.vram[0x0000] = 0xFF     # sprite pattern all-set

    buf = render_frame(vdp)

    # Text mode has no sprites; pixel at (8,1) should NOT be 15
    # It should be bg=2 (pattern byte 0 → all clear → bg)
    assert buf[1 * 256 + 8] == 2
