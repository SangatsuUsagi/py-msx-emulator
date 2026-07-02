from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP

# Layout:
#   name table   at 0x1800  (R2=0x06)
#   pattern gen  at 0x0000  (R4=0x03: base=0x0000, R4[1:0]=0b11 → no band aliasing)
#   colour table at 0x2000  (R3=0xFF: base=0x2000, R3[6:0]=0x7F → no band aliasing)
_NAME  = 0x1800
_PAT   = 0x0000
_COLOR = 0x2000


def make_g2_vdp() -> VDP:
    vdp = VDP()
    vdp.regs[0] = 0x02   # M3=1 → Graphic 2
    vdp.regs[1] = 0x40   # BL=1 (display on)
    vdp.regs[2] = 0x06   # name table at 0x1800
    vdp.regs[4] = 0x03   # pattern gen at 0x0000; R4[1:0]=0b11 → independent bands
    vdp.regs[3] = 0xFF   # colour table at 0x2000; R3[6:0]=0x7F → independent bands
    vdp.regs[7] = 0x01   # backdrop = colour 1
    return vdp


def test_g2_frame_dimensions() -> None:
    buf = render_frame(make_g2_vdp())
    assert len(buf) == 256 * 192


def test_g2_single_tile_colour_row() -> None:
    vdp = make_g2_vdp()
    # Put tile 0x00 at name table position (0,0)
    vdp.vram[_NAME + 0] = 0x00
    # Pattern row 0 for tile 0x00 in band 0: all bits set
    vdp.vram[_PAT + 0x00 * 8 + 0] = 0xFF
    # Colour row 0 for tile 0x00 in band 0: fg=15 (white), bg=1 (black)
    vdp.vram[_COLOR + 0x00 * 8 + 0] = 0xF1

    buf = render_frame(vdp)

    # All 8 pixels in top-left tile row 0 should be fg=15
    for px in range(8):
        assert buf[px] == 15, f"pixel {px} expected 15 got {buf[px]}"


def test_g2_per_row_colour() -> None:
    vdp = make_g2_vdp()
    vdp.vram[_NAME + 0] = 0x00
    # Row 0: all set, fg=15; Row 1: all clear → bg=2
    vdp.vram[_PAT + 0] = 0xFF
    vdp.vram[_COLOR + 0] = 0xF2   # fg=15, bg=2 for row 0
    vdp.vram[_PAT + 1] = 0x00
    vdp.vram[_COLOR + 1] = 0x32   # fg=3, bg=2 for row 1

    buf = render_frame(vdp)

    assert buf[0 * 256 + 0] == 15  # row 0, all fg
    assert buf[1 * 256 + 0] == 2   # row 1, all bg (clear bits)


def test_g2_band_boundary_row8() -> None:
    vdp = make_g2_vdp()
    # Name table row 8 → band 1
    # Put tile 0x01 at row 8, col 0
    vdp.vram[_NAME + 8 * 32 + 0] = 0x01
    # Band 1 offset = 0x800
    # Pattern for tile 0x01 in band 1, row 0:
    vdp.vram[_PAT + 0x800 + 0x01 * 8 + 0] = 0xFF
    # Colour for tile 0x01 in band 1, row 0: fg=7
    vdp.vram[_COLOR + 0x800 + 0x01 * 8 + 0] = 0x71

    buf = render_frame(vdp)

    # Display row 64 = tile row 8, pixel row 0 within tile
    assert buf[64 * 256 + 0] == 7


def test_g2_band_two_row16() -> None:
    vdp = make_g2_vdp()
    vdp.vram[_NAME + 16 * 32 + 0] = 0x02
    # Band 2 offset = 0x1000
    vdp.vram[_PAT + 0x1000 + 0x02 * 8 + 0] = 0xFF
    vdp.vram[_COLOR + 0x1000 + 0x02 * 8 + 0] = 0xA1  # fg=10

    buf = render_frame(vdp)

    assert buf[128 * 256 + 0] == 10


def test_g2_full_pixel_count() -> None:
    buf = render_frame(make_g2_vdp())
    assert len(buf) == 256 * 192


def test_g2_pattern_band_aliasing_r4_zero() -> None:
    # R4[1:0]=0 collapses all 3 band pattern tables onto band0 (0x0000-0x07FF).
    # Writing patterns only to band0 must render correctly for band1 and band2 rows.
    vdp = make_g2_vdp()
    vdp.regs[4] = 0x00   # R4[1:0]=0b00 → all bands alias to band0 patterns
    vdp.regs[3] = 0x9F   # R3[6:0]=0x1F → col_mask=0x7FF, bands alias to col band0
    # Write pattern for tile 0x01 in band0 only (row 0: all bits set)
    vdp.vram[_PAT + 0x01 * 8 + 0] = 0xFF
    # Write color for tile 0x01 in col band0 (fg=5, bg=1)
    vdp.vram[_COLOR + 0x01 * 8 + 0] = 0x51
    # Place tile 0x01 in band1 row (row 8) and band2 row (row 16)
    vdp.vram[_NAME + 8 * 32 + 0] = 0x01
    vdp.vram[_NAME + 16 * 32 + 0] = 0x01

    buf = render_frame(vdp)

    # Band1 row (y=64) and band2 row (y=128) must use band0 pattern and colour
    assert buf[64 * 256 + 0] == 5, f"band1 row got {buf[64*256+0]}, expected 5"
    assert buf[128 * 256 + 0] == 5, f"band2 row got {buf[128*256+0]}, expected 5"
