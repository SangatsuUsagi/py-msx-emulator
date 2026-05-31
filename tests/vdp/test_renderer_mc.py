from msx.vdp.vdp import VDP
from msx.vdp.renderer import render_frame

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


def test_mc_per_row_colour() -> None:
    vdp = make_mc_vdp()
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xF7   # row 0: left=15, right=7
    vdp.vram[_PAT + 1] = 0x3A   # row 1: left=3, right=10

    buf = render_frame(vdp)

    assert buf[0 * 256 + 0] == 15   # row 0, left block
    assert buf[0 * 256 + 4] == 7    # row 0, right block
    assert buf[1 * 256 + 0] == 3    # row 1, left block
    assert buf[1 * 256 + 4] == 10   # row 1, right block


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
