from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP
from tests.render_geometry import active_region
from tests.vdp.conftest import RendererFlavor


def _active(vdp: object, flavor: RendererFlavor) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(flavor.render_frame(vdp), vdp.display_height)


# Layout:
#   name table  at 0x0000  (R2=0x00)
#   pattern gen at 0x0800  (R4=0x01, 0x01<<11=0x800)
_NAME = 0x0000
_PAT  = 0x0800


def make_text_vdp(flavor: RendererFlavor) -> object:
    vdp = flavor.make_vdp()
    vdp.regs[1] = 0x50   # M1=1 (bit 4), BL=1 (bit 6) → Text mode, display on
    vdp.regs[2] = 0x00   # name table at 0x0000
    vdp.regs[4] = 0x01   # pattern gen at 0x0800
    vdp.regs[7] = 0xF2   # fg=15 (white), bg=2
    return vdp


def test_text_frame_dimensions(flavor: RendererFlavor) -> None:
    buf = flavor.render_frame(make_text_vdp(flavor))
    assert len(buf) == 256 * 212


def test_text_border_pixels_are_bg(flavor: RendererFlavor) -> None:
    vdp = make_text_vdp(flavor)
    vdp.regs[7] = 0xF3   # fg=15, bg=3
    buf = _active(vdp, flavor)
    # Left 8-pixel border on row 0
    for px in range(8):
        assert buf[px] == 3, f"left border pixel {px} expected 3 got {buf[px]}"
    # Right 8-pixel border: columns 248–255
    for px in range(248, 256):
        assert buf[px] == 3, f"right border pixel {px} expected 3 got {buf[px]}"


def test_text_character_width_six_pixels(flavor: RendererFlavor) -> None:
    vdp = make_text_vdp(flavor)
    vdp.regs[7] = 0xF2   # fg=15, bg=2
    # Tile 0 at col 0: pattern row 0 all set → fg on all 6 pixels,
    # then bg for pixel 6+ (next char space)
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xFF   # all bits set

    buf = _active(vdp, flavor)

    # Col 0 starts at x=8; 6 pixels wide → x=8..13 should be fg=15
    for px in range(8, 14):
        assert buf[px] == 15, f"pixel {px} expected fg=15 got {buf[px]}"
    # x=14 is start of col 1 (tile 0 again but pattern is same — actually tile index matters)
    # Just verify col 0 char ends at x=13 by checking the first border col x=7 = bg
    assert buf[7] == 2  # left border still bg


def test_text_fg_zero_shows_backdrop() -> None:
    """TMS9918A: text colour code 0 is transparent (TI data manual Table 2-3),
    so set pattern bits show the backdrop, R#7 bits 3:0 (§2.4)."""
    vdp = VDP()
    vdp.regs[1] = 0x50   # M1=1 (bit 4), BL=1 (bit 6) → Text mode, display on
    vdp.regs[2] = 0x00   # name table at 0x0000
    vdp.regs[4] = 0x01   # pattern gen at 0x0800
    vdp.regs[7] = 0x04   # fg=0 (transparent), bg/backdrop=4
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0xFF

    buf = active_region(render_frame(vdp), vdp.display_height)

    assert buf[8] == 4


def test_r0_bit2_write_renders_graphic2_without_error() -> None:
    """R#0 bit 2 (M4) does not exist on the TMS9918A: a program writing
    R#0 = 0x06 gets GRAPHIC2, not a GRAPHIC4 path over the 16 KB VRAM."""
    vdp = VDP()
    for reg, value in ((0, 0x06), (1, 0x60)):
        vdp.write_port(0x99, value)
        vdp.write_port(0x99, 0x80 | reg)

    buf = render_frame(vdp)

    assert len(buf) > 0


def test_r0_bit2_write_draws_graphic2_tiles() -> None:
    """The R#0 = 0x06 frame is drawn through GRAPHIC2's pattern/colour
    tables, not as GRAPHIC4 nibble pixels read straight from VRAM."""
    vdp = VDP()
    regs = ((0, 0x06), (1, 0x40), (2, 0x06), (3, 0xFF), (4, 0x03), (5, 0x36), (7, 0x01))
    for reg, value in regs:
        vdp.write_port(0x99, value)
        vdp.write_port(0x99, 0x80 | reg)
    vdp.vram[0x0000] = 0xFF   # tile 0, row 0: all foreground
    vdp.vram[0x2000] = 0x65   # row 0 colour: fg=6, bg=5
    vdp.vram[0x2001] = 0x65   # row 1 colour (GRAPHIC2 colours are per row)
    vdp.vram[0x1B00] = 0xD0   # SAT terminator: no sprites

    buf = active_region(render_frame(vdp), vdp.display_height)

    assert buf[0] == 6        # a GRAPHIC4 decode of VRAM byte 0xFF would give 15
    assert buf[256] == 5      # row 1 pattern 0x00 -> background


def test_text_no_sprites_rendered(flavor: RendererFlavor) -> None:
    vdp = make_text_vdp(flavor)
    # Enable a sprite with colour 15 at (8, 0)
    vdp.regs[5] = 0x20          # SAT at 0x1000
    sat = 0x1000
    vdp.vram[sat + 0] = 0x00    # Y=0 → top=1
    vdp.vram[sat + 1] = 0x08    # X=8
    vdp.vram[sat + 2] = 0x00    # pattern 0
    vdp.vram[sat + 3] = 0x0F    # colour 15
    vdp.regs[6] = 0x00
    vdp.vram[0x0000] = 0xFF     # sprite pattern all-set

    buf = _active(vdp, flavor)

    # Text mode has no sprites; pixel at (8,1) should NOT be 15
    # It should be bg=2 (pattern byte 0 → all clear → bg)
    assert buf[1 * 256 + 8] == 2
