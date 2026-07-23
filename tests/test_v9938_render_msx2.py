"""Tests for V9938 MSX2-specific screen modes: SCREEN 4–8."""
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import grb332_to_rgb, render_frame
from tests.render_geometry import active_region


def _active(vdp: V9938) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(render_frame(vdp), vdp.display_height)




def _enable(vdp: V9938) -> None:
    vdp.regs[1] |= 0x40  # BL bit: enable display


# ---------------------------------------------------------------------------
# SCREEN 5 (Graphic 4): 4-bpp palette-index bitmap
# ---------------------------------------------------------------------------

def _set_screen5(vdp: V9938) -> None:
    """Set mode bits for SCREEN 5 (M3=1, M4=1): R#0=0x06."""
    _enable(vdp)
    vdp.regs[0] = 0x06  # M3=bit1, M4=bit2


def test_screen5_buffer_size_192_padded_to_212() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    assert len(render_frame(vdp)) == 256 * 212


def test_screen5_buffer_size_212_when_ln_set() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[9] = 0x80
    assert len(render_frame(vdp)) == 256 * 212


def test_screen5_high_nibble_is_left_pixel() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    # R#2=0 → base=0; row 0, pixel 0 comes from high nibble of vram[0]
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0x5A  # high nibble=5 (left), low nibble=A=10 (right)

    buf = _active(vdp)
    assert buf[0] == 5   # pixel 0: high nibble


def test_screen5_low_nibble_is_right_pixel() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0x5A  # high nibble=5, low nibble=10

    buf = _active(vdp)
    assert buf[1] == 10  # pixel 1: low nibble


def test_screen5_palette_indices_in_buffer() -> None:
    """Buffer values are palette indices 0–15, not RGB."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00
    # Fill first 4 pixels: high nibbles=0x1,0x3, low nibbles=0x2,0x4
    vdp.vram[0] = 0x12   # pixels 0=1, 1=2
    vdp.vram[1] = 0x34   # pixels 2=3, 3=4

    buf = _active(vdp)
    assert buf[0] == 1
    assert buf[1] == 2
    assert buf[2] == 3
    assert buf[3] == 4


def test_screen5_vram_base_from_r2() -> None:
    """R#2 bits[6:5] select 32KB page: 0b01→page1→base=0x8000."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x20  # bits[6:5]=01 → page 1 → base = 0x8000

    vdp.vram[0x0000] = 0xFF  # decoy at base 0 — must NOT appear in output
    vdp.vram[0x8000] = 0xAB  # actual base: pixel 0=0xA=10, pixel 1=0xB=11

    buf = _active(vdp)
    assert buf[0] == 0x0A   # high nibble of 0xAB
    assert buf[1] == 0x0B   # low nibble


def test_screen5_second_row_offset() -> None:
    """Row 1 starts at base + 128 (128 bytes per row at 4bpp)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00  # base = 0
    vdp.vram[128] = 0xCD  # row 1, first byte: pixel(1,0)=0xC, pixel(1,1)=0xD

    buf = _active(vdp)
    assert buf[1 * 256 + 0] == 0x0C
    assert buf[1 * 256 + 1] == 0x0D


# ---------------------------------------------------------------------------
# SCREEN 8 (Graphic 7): 8-bpp GRB332 direct colour
# ---------------------------------------------------------------------------

def _set_screen8(vdp: V9938) -> None:
    """Set mode bits for SCREEN 8 (M3+M4+M5): R#0=0x0E."""
    _enable(vdp)
    vdp.regs[0] = 0x0E  # M3=bit1, M4=bit2, M5=bit3


def test_screen8_buffer_size_192_padded_to_212() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    assert len(render_frame(vdp)) == 256 * 212


def test_screen8_raw_vram_byte_in_buffer() -> None:
    """render_frame SCREEN 8 output contains raw GRB332 bytes from VRAM."""
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x00  # base = 0
    vdp.vram[0] = 0b11111011  # GRB332: G=7,R=6,B=3

    buf = _active(vdp)
    assert buf[0] == 0b11111011


def test_screen8_second_pixel() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[1] = 0x24  # second pixel

    buf = _active(vdp)
    assert buf[1] == 0x24


def test_screen8_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x40  # G7: 64KB pages, bit6=1 → base = 0x10000

    vdp.vram[0x0000]  = 0xFF   # decoy at offset 0 — must NOT appear
    vdp.vram[0x10000] = 0x42   # first pixel at actual base

    buf = _active(vdp)
    assert buf[0] == 0x42


# ---------------------------------------------------------------------------
# grb332_to_rgb: GRB332 → (R, G, B) conversion
# ---------------------------------------------------------------------------

def test_grb332_black() -> None:
    r, g, b = grb332_to_rgb(0x00)
    assert r == 0 and g == 0 and b == 0


def test_grb332_green_channel_only() -> None:
    # 0b11100000: G=7, R=0, B=0
    r, g, b = grb332_to_rgb(0b11100000)
    assert g == 255   # 255 * 7 // 7
    assert r == 0
    assert b == 0


def test_grb332_red_channel_only() -> None:
    # 0b00011100: G=0, R=7, B=0
    r, g, b = grb332_to_rgb(0b00011100)
    assert r == 255
    assert g == 0
    assert b == 0


def test_grb332_blue_channel_only() -> None:
    # 0b00000011: G=0, R=0, B=3 (max 2-bit)
    r, g, b = grb332_to_rgb(0b00000011)
    assert b == 255   # openMSX 2-bit blue max
    assert g == 0
    assert r == 0


def test_grb332_all_channels_max() -> None:
    # 0xFF: G=7, R=7, B=3
    r, g, b = grb332_to_rgb(0xFF)
    assert r == 255
    assert g == 255
    assert b == 255


def test_grb332_example_byte() -> None:
    # 0b11111011: G=7, R=6, B=3 → openMSX intensity 255*6//7 = 218
    r, g, b = grb332_to_rgb(0b11111011)
    assert r == 255 * 6 // 7   # = 218
    assert g == 255
    assert b == 255


# ---------------------------------------------------------------------------
# SCREEN 4 (Graphic 3): same tile background as SCREEN 2
# ---------------------------------------------------------------------------

def _set_screen4(vdp: V9938) -> None:
    """SCREEN 4 (Graphic 3): M4=1 only, R#0=0x04."""
    _enable(vdp)
    vdp.regs[0] = 0x04  # M4=bit2


def test_screen4_buffer_size_192_padded_to_212() -> None:
    vdp = V9938()
    _set_screen4(vdp)
    assert len(render_frame(vdp)) == 256 * 212


def test_screen4_renders_g2_tiles() -> None:
    """SCREEN 4 background uses same G2 tile logic as SCREEN 2."""
    vdp = V9938()
    _set_screen4(vdp)

    vdp.regs[2] = 0x01   # name_base = 0x0400
    vdp.regs[3] = 0x80   # col_base  = 0x2000
    vdp.regs[4] = 0x00   # pat_base  = 0x0000

    vdp.vram[0x0400] = 0       # name[0,0] = tile 0
    vdp.vram[0x0000] = 0xFF    # pattern row 0: all fg
    vdp.vram[0x2000] = 0x65    # colour: fg=6, bg=5

    buf = _active(vdp)
    assert buf[0] == 6


def test_screen4_name_table_above_16k() -> None:
    """V9938 GRAPHIC2/3 use the full 7-bit R#2, so the name table may sit above
    the 16 KB an MSX1 4-bit mask would allow. A Konami SCC MegaROM cartridge
    places it at 0xC000 (R#2=0x30); a 4-bit mask would read it from 0x0000 (the
    pattern table) and render garbage."""
    vdp = V9938()
    _set_screen4(vdp)

    vdp.regs[2] = 0x30   # name_base = 0x30 << 10 = 0xC000 (7-bit)
    vdp.regs[3] = 0x80   # col_base  = 0x2000
    vdp.regs[4] = 0x00   # pat_base  = 0x0000

    vdp.vram[0xC000] = 0       # name[0,0] = tile 0 (read from 0xC000, not 0x0000)
    vdp.vram[0x0000] = 0xFF    # pattern row 0: all fg
    vdp.vram[0x2000] = 0x65    # colour: fg=6, bg=5

    buf = _active(vdp)
    assert buf[0] == 6


def test_screen4_vertical_scroll_r23() -> None:
    """R#23 vertical scroll applies to GRAPHIC2/3 (a no-op when R#23 = 0). With
    R#23 = 8, screen line 0 shows VRAM line 8 = name row 1 (a scrolling-playfield
    title sets R#23 to position its playfield)."""
    vdp = V9938()
    _set_screen4(vdp)

    vdp.regs[2] = 0x00   # name_base = 0x0000
    vdp.regs[3] = 0x80   # col_base  = 0x2000
    vdp.regs[4] = 0x00   # pat_base  = 0x0000
    vdp.regs[23] = 8     # scroll up one tile row

    vdp.vram[0x0000] = 0       # name[row 0, col 0] = tile 0 (would show without scroll)
    vdp.vram[0x0020] = 1       # name[row 1, col 0] = tile 1 (shows at line 0 when scrolled)
    vdp.vram[0x0008] = 0xFF    # pattern tile 1 row 0: all fg
    vdp.vram[0x2008] = 0x65    # colour tile 1 row 0: fg=6, bg=5

    buf = _active(vdp)
    assert buf[0] == 6         # line 0 shows the scrolled-in row 1, foreground colour 6


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5): 2-bpp, full 512-pixel width
# ---------------------------------------------------------------------------

def _set_screen6(vdp: V9938) -> None:
    """SCREEN 6 (Graphic 5): M5=1 only, R#0=0x08."""
    _enable(vdp)
    vdp.regs[0] = 0x08  # M5=bit3


def test_screen6_buffer_size_192_padded_to_212() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    assert len(render_frame(vdp)) == 512 * 212


def test_screen6_buffer_size_212_when_ln_set() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[9] = 0x80
    assert len(render_frame(vdp)) == 512 * 212


def test_screen6_four_pixels_per_byte() -> None:
    """At 512 wide each byte yields 4 pixels (2-bpp), MSB pair first."""
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x00
    # bits 7:6 = 0b11 = 3; 5:4 = 0b10 = 2; 3:2 = 0b01 = 1; 1:0 = 0b00 = 0.
    vdp.vram[0] = 0b11_10_01_00

    buf = _active(vdp)
    assert buf[0] == 3
    assert buf[1] == 2
    assert buf[2] == 1
    assert buf[3] == 0


def test_screen6_second_output_byte() -> None:
    """Output pixels 4–7 come from VRAM byte 1."""
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0x00
    vdp.vram[1] = 0b10_01_11_00

    buf = _active(vdp)
    assert buf[4] == 2   # bits 7:6 of byte 1
    assert buf[5] == 1   # bits 5:4
    assert buf[6] == 3   # bits 3:2


def test_screen6_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x20   # bits[6:5]=01 → page 1 → base = 0x8000

    vdp.vram[0x0000] = 0x00   # decoy — must not appear
    vdp.vram[0x8000] = 0b11_00_00_00   # pixel 0 = colour 3

    buf = _active(vdp)
    assert buf[0] == 3


# ---------------------------------------------------------------------------
# SCREEN 7 (Graphic 6): 4-bpp, full 512-pixel width
# ---------------------------------------------------------------------------

def _set_screen7(vdp: V9938) -> None:
    """SCREEN 7 (Graphic 6): M3+M5, R#0=0x0A."""
    _enable(vdp)
    vdp.regs[0] = 0x0A  # M3=bit1, M5=bit3


def test_screen7_buffer_size_192_padded_to_212() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    assert len(render_frame(vdp)) == 512 * 212


def test_screen7_two_pixels_per_byte() -> None:
    """At 512 wide each byte yields 2 pixels: high nibble then low nibble."""
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0xAB   # pixel 0 = 0xA, pixel 1 = 0xB

    buf = _active(vdp)
    assert buf[0] == 0x0A
    assert buf[1] == 0x0B


def test_screen7_second_byte_is_next_two_pixels() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0xAB
    vdp.vram[1] = 0xCD   # pixels 2 and 3

    buf = _active(vdp)
    assert buf[2] == 0x0C
    assert buf[3] == 0x0D


def test_screen7_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x40   # G6: 64KB pages, bit6=1 → base = 0x10000

    vdp.vram[0x0000]  = 0xFF   # decoy
    vdp.vram[0x10000] = 0x5E   # high nibble = 5

    buf = _active(vdp)
    assert buf[0] == 5


def test_screen7_second_row_offset() -> None:
    """Row 1 starts at base + 256 bytes (256 bytes per row); output stride 512."""
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[256] = 0x70   # row 1, first byte: high nibble = 7

    buf = _active(vdp)
    assert buf[1 * 512 + 0] == 7


# ---------------------------------------------------------------------------
# Constant output height: 192-line frames are padded to 212 with border rows
# ---------------------------------------------------------------------------

def test_screen5_192_padded_with_masked_border_rows() -> None:
    """SCREEN 5 (LN=0) output is 256x212 with 10 border rows top and bottom,
    using the R#7 low-nibble palette index as the border colour."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[7] = 0x37  # low nibble 7 → border index 7 (high nibble ignored)
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    # Top 10 rows and bottom 10 rows are border colour 7.
    assert all(b == 7 for b in buf[:10 * 256])
    assert all(b == 7 for b in buf[(10 + 192) * 256:])


def test_screen8_192_padded_with_raw_byte_border_rows() -> None:
    """SCREEN 8 (G7) uses the raw R#7 byte (not the low nibble) as the direct
    GRB332 border colour for the padding rows."""
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[7] = 0xB5  # full byte is the GRB332 border colour
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    assert all(b == 0xB5 for b in buf[:10 * 256])
    assert all(b == 0xB5 for b in buf[(10 + 192) * 256:])


def test_screen5_212_mode_not_padded() -> None:
    """With LN=1 the native height is already 212; no border padding is added."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[9] = 0x80  # LN=1
    vdp.regs[7] = 0x07
    vdp.vram[0] = 0x9A  # pixel (0,0) = 9
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    # Row 0 holds active content (no top border rows inserted).
    assert buf[0] == 9


def test_midframe_ln_toggle_still_212_rows() -> None:
    """A mid-frame R#9 LN toggle (banded render) still yields exactly 212 output
    rows — the constant output height, not doubled or re-padded."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.begin_scanline(0)
    # Toggle LN=1 partway down the frame; live regs end at 212-line mode.
    from msx.vdp.v9938 import _RegChange
    vdp._reg_write_log.append(_RegChange(96, 9, 0x80))
    vdp.regs[9] = 0x80
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
