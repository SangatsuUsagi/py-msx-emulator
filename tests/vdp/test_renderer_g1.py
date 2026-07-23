from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP
from tests.render_geometry import active_region


def _active(vdp: VDP) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(render_frame(vdp), vdp.display_height)


# Layout used by all G1 tests:
#   name table  at 0x3800  (R2=0x0E, 0x0E<<10=0x3800)
#   pattern gen at 0x0000  (R4=0x00)
#   colour table at 0x0800  (R3=0x20, 0x20<<6=0x800)
_NAME  = 0x3800
_PAT   = 0x0000
_COLOR = 0x0800


def make_g1_vdp() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x40        # BL=1 (display on), G1 mode
    vdp.regs[2] = 0x0E        # name table at 0x3800
    vdp.regs[4] = 0x00        # pattern gen at 0x0000
    vdp.regs[3] = 0x20        # colour table at 0x0800
    vdp.regs[7] = 0x01        # backdrop = colour 1
    return vdp


def test_blank_screen_when_bl_clear() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x00  # BL=0 → blank
    vdp.regs[7] = 0x05  # border = colour 5
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    assert all(b == 5 for b in buf)


def test_blank_screen_correct_size() -> None:
    buf = render_frame(VDP())
    assert len(buf) == 256 * 212


def test_g1_single_tile_pattern() -> None:
    vdp = make_g1_vdp()
    # Tile index 0x41 at name[0,0]; set pattern row 0 = 0b10101010 (alternating)
    vdp.vram[_NAME + 0] = 0x41
    vdp.vram[_PAT + 0x41 * 8 + 0] = 0b10101010
    # Colour group 0x41 // 8 = 8: fg=15 (white), bg=1 (black)
    vdp.vram[_COLOR + 0x41 // 8] = 0xF1

    buf = _active(vdp)

    row0 = buf[0:8]
    # Bits 7,5,3,1 set → pixels 0,2,4,6 = fg=15; pixels 1,3,5,7 = bg=1
    assert row0[0] == 15
    assert row0[1] == 1
    assert row0[2] == 15
    assert row0[3] == 1


def test_g1_colour_table_fg_bg() -> None:
    vdp = make_g1_vdp()
    vdp.vram[_NAME + 0] = 0x00         # tile 0 at position (0,0)
    vdp.vram[_PAT + 0] = 0xFF           # all bits set → all fg
    vdp.vram[_COLOR + 0] = 0xF0         # fg=15, bg=0 (transparent→backdrop=1)

    buf = _active(vdp)
    assert buf[0] == 15   # set bit → fg


def test_g1_transparent_bg_uses_backdrop() -> None:
    vdp = make_g1_vdp()
    vdp.regs[7] = 0x04                  # backdrop = colour 4
    vdp.vram[_NAME + 0] = 0x00
    vdp.vram[_PAT + 0] = 0x00           # all bits clear → all bg
    vdp.vram[_COLOR + 0] = 0xF0         # bg=0 (transparent)

    buf = _active(vdp)
    assert buf[0] == 4   # transparent bg → backdrop


def test_g1_frame_dimensions() -> None:
    buf = render_frame(make_g1_vdp())
    assert len(buf) == 256 * 212


def test_g1_all_tiles_rendered() -> None:
    vdp = make_g1_vdp()
    # Fill all name table entries with tile 1; set tile 1 pattern all-ones (all fg)
    for i in range(32 * 24):
        vdp.vram[_NAME + i] = 0x01
    vdp.vram[_PAT + 0x01 * 8 + 0] = 0xFF
    vdp.vram[_COLOR + 0] = 0xF1   # tile group 0 (tiles 0-7): fg=15

    buf = _active(vdp)
    # Top-left 8 pixels of row 0 should all be fg=15
    for px in range(8):
        assert buf[px] == 15
    # 32nd tile column starts at x=248; last pixel at x=255
    assert buf[248] == 15


def test_output_padded_to_212_with_border_rows() -> None:
    """MSX1 render_frame pads the 192-line frame to 256x212 with 10 border rows
    (R#7 low nibble) on top and bottom."""
    vdp = make_g1_vdp()
    vdp.regs[7] = 0x03  # backdrop / border = colour 3
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    assert all(b == 3 for b in buf[:10 * 256])
    assert all(b == 3 for b in buf[(10 + 192) * 256:])
