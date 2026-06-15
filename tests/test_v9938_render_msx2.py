"""Tests for V9938 MSX2-specific screen modes: SCREEN 4–8."""
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import grb332_to_rgb, render_frame


def _enable(vdp: V9938) -> None:
    vdp.regs[1] |= 0x40  # BL bit: enable display


# ---------------------------------------------------------------------------
# SCREEN 5 (Graphic 4): 4-bpp palette-index bitmap
# ---------------------------------------------------------------------------

def _set_screen5(vdp: V9938) -> None:
    """Set mode bits for SCREEN 5 (M3=1, M4=1): R#0=0x06."""
    _enable(vdp)
    vdp.regs[0] = 0x06  # M3=bit1, M4=bit2


def test_screen5_buffer_size_192() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    assert len(render_frame(vdp)) == 256 * 192


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

    buf = render_frame(vdp)
    assert buf[0] == 5   # pixel 0: high nibble


def test_screen5_low_nibble_is_right_pixel() -> None:
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0x5A  # high nibble=5, low nibble=10

    buf = render_frame(vdp)
    assert buf[1] == 10  # pixel 1: low nibble


def test_screen5_palette_indices_in_buffer() -> None:
    """Buffer values are palette indices 0–15, not RGB."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00
    # Fill first 4 pixels: high nibbles=0x1,0x3, low nibbles=0x2,0x4
    vdp.vram[0] = 0x12   # pixels 0=1, 1=2
    vdp.vram[1] = 0x34   # pixels 2=3, 3=4

    buf = render_frame(vdp)
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

    buf = render_frame(vdp)
    assert buf[0] == 0x0A   # high nibble of 0xAB
    assert buf[1] == 0x0B   # low nibble


def test_screen5_second_row_offset() -> None:
    """Row 1 starts at base + 128 (128 bytes per row at 4bpp)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[2] = 0x00  # base = 0
    vdp.vram[128] = 0xCD  # row 1, first byte: pixel(1,0)=0xC, pixel(1,1)=0xD

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 0x0C
    assert buf[1 * 256 + 1] == 0x0D


# ---------------------------------------------------------------------------
# SCREEN 8 (Graphic 7): 8-bpp GRB332 direct colour
# ---------------------------------------------------------------------------

def _set_screen8(vdp: V9938) -> None:
    """Set mode bits for SCREEN 8 (M3+M4+M5): R#0=0x0E."""
    _enable(vdp)
    vdp.regs[0] = 0x0E  # M3=bit1, M4=bit2, M5=bit3


def test_screen8_buffer_size_192() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    assert len(render_frame(vdp)) == 256 * 192


def test_screen8_raw_vram_byte_in_buffer() -> None:
    """render_frame SCREEN 8 output contains raw GRB332 bytes from VRAM."""
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x00  # base = 0
    vdp.vram[0] = 0b11111011  # GRB332: G=7,R=6,B=3

    buf = render_frame(vdp)
    assert buf[0] == 0b11111011


def test_screen8_second_pixel() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[1] = 0x24  # second pixel

    buf = render_frame(vdp)
    assert buf[1] == 0x24


def test_screen8_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen8(vdp)
    vdp.regs[2] = 0x40  # G7: 64KB pages, bit6=1 → base = 0x10000

    vdp.vram[0x0000]  = 0xFF   # decoy at offset 0 — must NOT appear
    vdp.vram[0x10000] = 0x42   # first pixel at actual base

    buf = render_frame(vdp)
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
    assert g == 7 * 36
    assert r == 0
    assert b == 0


def test_grb332_red_channel_only() -> None:
    # 0b00011100: G=0, R=7, B=0
    r, g, b = grb332_to_rgb(0b00011100)
    assert r == 7 * 36
    assert g == 0
    assert b == 0


def test_grb332_blue_channel_only() -> None:
    # 0b00000011: G=0, R=0, B=3 (max 2-bit)
    r, g, b = grb332_to_rgb(0b00000011)
    assert b == 3 * 85   # = 255
    assert g == 0
    assert r == 0


def test_grb332_all_channels_max() -> None:
    # 0xFF: G=7, R=7, B=3
    r, g, b = grb332_to_rgb(0xFF)
    assert r == 7 * 36   # = 252
    assert g == 7 * 36   # = 252
    assert b == 3 * 85   # = 255


def test_grb332_example_byte() -> None:
    # 0b11111011: G=7, R=6, B=3
    r, g, b = grb332_to_rgb(0b11111011)
    assert r == 6 * 36   # = 216
    assert g == 7 * 36   # = 252
    assert b == 3 * 85   # = 255


# ---------------------------------------------------------------------------
# SCREEN 4 (Graphic 3): same tile background as SCREEN 2
# ---------------------------------------------------------------------------

def _set_screen4(vdp: V9938) -> None:
    """SCREEN 4 (Graphic 3): M4=1 only, R#0=0x04."""
    _enable(vdp)
    vdp.regs[0] = 0x04  # M4=bit2


def test_screen4_buffer_size_192() -> None:
    vdp = V9938()
    _set_screen4(vdp)
    assert len(render_frame(vdp)) == 256 * 192


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

    buf = render_frame(vdp)
    assert buf[0] == 6


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5): 2-bpp, 512 virtual width → 256 output
# ---------------------------------------------------------------------------

def _set_screen6(vdp: V9938) -> None:
    """SCREEN 6 (Graphic 5): M5=1 only, R#0=0x08."""
    _enable(vdp)
    vdp.regs[0] = 0x08  # M5=bit3


def test_screen6_buffer_size_192() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    assert len(render_frame(vdp)) == 256 * 192


def test_screen6_buffer_size_212_when_ln_set() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[9] = 0x80
    assert len(render_frame(vdp)) == 256 * 212


def test_screen6_even_output_pixel_from_high_pair() -> None:
    """Output pixel 0 (even) samples virtual pixel 0 — bits 7:6 of first byte."""
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x00
    # bits 7:6 = 0b11 = colour 3; bits 5:4 = 0b10 = colour 2; etc.
    vdp.vram[0] = 0b11_10_01_00

    buf = render_frame(vdp)
    assert buf[0] == 3   # output pixel 0: bits 7:6


def test_screen6_odd_output_pixel_from_second_pair() -> None:
    """Output pixel 1 (odd) samples virtual pixel 2 — bits 3:2 of first byte."""
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0b11_10_01_00

    buf = render_frame(vdp)
    assert buf[1] == 1   # output pixel 1: bits 3:2


def test_screen6_second_output_byte() -> None:
    """Output pixels 2 and 3 come from VRAM byte 1."""
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0x00
    vdp.vram[1] = 0b10_01_11_00

    buf = render_frame(vdp)
    assert buf[2] == 2   # bits 7:6 of byte 1
    assert buf[3] == 3   # bits 3:2 of byte 1


def test_screen6_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen6(vdp)
    vdp.regs[2] = 0x20   # bits[6:5]=01 → page 1 → base = 0x8000

    vdp.vram[0x0000] = 0x00   # decoy — must not appear
    vdp.vram[0x8000] = 0b11_00_00_00   # pixel 0 = colour 3

    buf = render_frame(vdp)
    assert buf[0] == 3


# ---------------------------------------------------------------------------
# SCREEN 7 (Graphic 6): 4-bpp, 512 virtual width → 256 output
# ---------------------------------------------------------------------------

def _set_screen7(vdp: V9938) -> None:
    """SCREEN 7 (Graphic 6): M3+M5, R#0=0x0A."""
    _enable(vdp)
    vdp.regs[0] = 0x0A  # M3=bit1, M5=bit3


def test_screen7_buffer_size_192() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    assert len(render_frame(vdp)) == 256 * 192


def test_screen7_output_pixel_is_high_nibble() -> None:
    """Each output pixel takes the high nibble of its VRAM byte."""
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0xAB   # high nibble=0xA=10, low nibble=0xB (ignored)

    buf = render_frame(vdp)
    assert buf[0] == 0x0A


def test_screen7_second_pixel_independent_byte() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[0] = 0xAB
    vdp.vram[1] = 0xCD   # output pixel 1 = high nibble of byte 1

    buf = render_frame(vdp)
    assert buf[1] == 0x0C


def test_screen7_vram_base_from_r2() -> None:
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x40   # G6: 64KB pages, bit6=1 → base = 0x10000

    vdp.vram[0x0000]  = 0xFF   # decoy
    vdp.vram[0x10000] = 0x5E   # high nibble = 5

    buf = render_frame(vdp)
    assert buf[0] == 5


def test_screen7_second_row_offset() -> None:
    """Row 1 starts at base + 256 bytes (256 bytes per row)."""
    vdp = V9938()
    _set_screen7(vdp)
    vdp.regs[2] = 0x00
    vdp.vram[256] = 0x70   # row 1, first byte: high nibble = 7

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 7
