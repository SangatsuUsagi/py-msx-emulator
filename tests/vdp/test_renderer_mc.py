from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP

# Layout:
#   name table  at 0x3800  (R2=0x0E)
#   pattern gen at 0x0000  (R4=0x00)
_NAME = 0x3800
_PAT  = 0x0000


def make_mc_vdp() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x48   # M2=1 (bit 3), BL=1 (bit 6) → Multicolor mode
    vdp.regs[2] = 0x0E   # name table at 0x3800
    vdp.regs[4] = 0x00   # pattern gen at 0x0000
    vdp.regs[7] = 0x01   # backdrop = colour 1
    return vdp


def test_mc_frame_dimensions() -> None:
    buf = render_frame(make_mc_vdp())
    assert len(buf) == 256 * 192


def test_mc_block_left_right_colour() -> None:
    vdp = make_mc_vdp()
    vdp.vram[_NAME + 0] = 0x00   # tile 0 at (0, 0)
    # Pattern row 0: high nibble=15 (left 4px), low nibble=7 (right 4px)
    vdp.vram[_PAT + 0] = 0xF7

    buf = render_frame(vdp)

    for px in range(4):
        assert buf[px] == 15, f"left px {px} expected 15 got {buf[px]}"
    for px in range(4, 8):
        assert buf[px] == 7, f"right px {px} expected 7 got {buf[px]}"


def test_mc_top_bottom_half_bytes() -> None:
    # MULTICOLOR: within an 8x8 cell the top 4 scanlines use pattern byte
    # (row&3)*2 and the bottom 4 use +1; each nibble is a solid 4x4 block, so
    # the colour is constant across all 4 scanlines of its half (NOT per-line).
    vdp = make_mc_vdp()
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xF7   # top half: left=15, right=7
    vdp.vram[_PAT + 1] = 0x3A   # bottom half: left=3, right=10

    buf = render_frame(vdp)

    for scan in range(4):       # top half → PAT+0
        assert buf[scan * 256 + 0] == 15
        assert buf[scan * 256 + 4] == 7
    for scan in range(4, 8):    # bottom half → PAT+1
        assert buf[scan * 256 + 0] == 3
        assert buf[scan * 256 + 4] == 10


def test_mc_char_row_selects_byte_pair() -> None:
    # The pattern byte pair is (row & 3)*2, so character row 1 (scanlines 8-15)
    # uses PAT+2 / PAT+3, not PAT+0 / PAT+1.
    vdp = make_mc_vdp()
    vdp.vram[_NAME + 1 * 32 + 0] = 0x00   # tile 0 at (col 0, char row 1)
    vdp.vram[_PAT + 2] = 0x5C             # row 1 top half: left=5, right=12

    buf = render_frame(vdp)

    assert buf[8 * 256 + 0] == 5    # scanline 8 (char row 1, top half), left
    assert buf[8 * 256 + 4] == 12   # right


def test_mc_transparent_colour_uses_backdrop() -> None:
    vdp = make_mc_vdp()
    vdp.regs[7] = 0x04              # backdrop = colour 4
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0x00      # both nibbles = 0 (transparent)

    buf = render_frame(vdp)

    assert buf[0] == 4   # transparent → backdrop
    assert buf[4] == 4


def test_mc_full_grid_dimensions() -> None:
    buf = render_frame(make_mc_vdp())
    assert len(buf) == 256 * 192
