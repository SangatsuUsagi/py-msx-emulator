"""Tests for msx.vdp.v9938_renderer: SCREEN 0–3 TMS9918A-compatible rendering."""
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame


def _enable(vdp: V9938) -> None:
    vdp.regs[1] |= 0x40  # BL bit: enable display


# ---------------------------------------------------------------------------
# skip_render and frame_count
# ---------------------------------------------------------------------------

def test_skip_render_returns_empty_bytearray() -> None:
    vdp = V9938()
    _enable(vdp)
    assert len(render_frame(vdp, skip_render=True)) == 0


def test_render_frame_does_not_own_frame_count() -> None:
    # Frame counting moved to Machine.run_frame; the renderer no longer touches it.
    vdp = V9938()
    _enable(vdp)
    render_frame(vdp)
    render_frame(vdp, skip_render=True)
    assert vdp._frame_count == 0


# ---------------------------------------------------------------------------
# Buffer size: 256 × display_height
# ---------------------------------------------------------------------------

def test_buffer_size_192_by_default() -> None:
    vdp = V9938()
    _enable(vdp)
    assert len(render_frame(vdp)) == 256 * 192


def test_buffer_size_212_when_ln_set() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[9] = 0x80  # LN → 212 lines
    assert len(render_frame(vdp)) == 256 * 212


# ---------------------------------------------------------------------------
# VBlank: F flag set by begin_scanline (not by render_frame)
# ---------------------------------------------------------------------------

def test_begin_scanline_sets_f_flag_at_display_height() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.begin_scanline(vdp.display_height)
    assert vdp.status & 0x80


def test_irq_pending_true_when_ie0_and_vblank() -> None:
    vdp = V9938()
    vdp.regs[1] = 0x40 | 0x20  # BL + IE0
    vdp.begin_scanline(vdp.display_height)
    assert vdp.irq_pending()


def test_irq_pending_false_when_ie0_clear() -> None:
    vdp = V9938()
    vdp.regs[1] = 0x40  # BL only, IE0 clear
    vdp.begin_scanline(vdp.display_height)
    assert not vdp.irq_pending()


# ---------------------------------------------------------------------------
# SCREEN 1 (GRAPHIC1): tile colour from attribute table
# ---------------------------------------------------------------------------

def test_screen1_foreground_colour_from_attribute_table() -> None:
    vdp = V9938()
    _enable(vdp)
    # R#0=0 (M3=0,M4=0,M5=0), R#1 BL (M1=0,M2=0) → GRAPHIC1
    vdp.regs[2] = 0x01  # name_base: (1 & 0x0F) << 10 = 0x0400
    vdp.regs[3] = 0x80  # col_base:  0x80 << 6 = 0x2000
    vdp.regs[4] = 0x01  # pat_base:  (1 & 0x07) << 11 = 0x0800

    vdp.vram[0x0400] = 0      # name[0,0] = tile 0
    vdp.vram[0x2000] = 0x41   # colour group 0: fg=4, bg=1
    for py in range(8):       # pattern tile 0: all foreground bits
        vdp.vram[0x0800 + py] = 0xFF

    buf = render_frame(vdp)
    assert buf[0] == 4  # foreground colour index 4


def test_screen1_background_colour_from_attribute_table() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[2] = 0x01
    vdp.regs[3] = 0x80
    vdp.regs[4] = 0x01

    vdp.vram[0x0400] = 0      # tile 0
    vdp.vram[0x2000] = 0x41   # fg=4, bg=1
    # pattern all 0x00 (background) — vram zero-initialised

    buf = render_frame(vdp)
    assert buf[0] == 1  # background colour index 1


# ---------------------------------------------------------------------------
# SCREEN 2 (GRAPHIC2): per-row per-tile colour
# ---------------------------------------------------------------------------

def test_screen2_per_row_colour() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x02  # M3=1 (bit 1) → GRAPHIC2

    # name_base=0x0400, pat_base=0x0000, col_base=0x2000
    vdp.regs[2] = 0x01  # (1 & 0x0F) << 10 = 0x0400
    vdp.regs[3] = 0x80  # (0x80 & 0x80) << 6 = 0x2000
    vdp.regs[4] = 0x00  # (0x00 & 0x04) << 11 = 0

    vdp.vram[0x0400] = 0      # name[0,0] = tile 0
    vdp.vram[0x0000] = 0xFF   # pattern tile 0, row 0: all foreground
    vdp.vram[0x2000] = 0x65   # colour tile 0, row 0: fg=6, bg=5

    buf = render_frame(vdp)
    assert buf[0] == 6  # foreground colour index 6


def test_screen2_second_row_has_independent_colour() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x02

    vdp.regs[2] = 0x01
    vdp.regs[3] = 0x80
    vdp.regs[4] = 0x00

    vdp.vram[0x0400] = 0      # tile 0 at (row=0, col=0)
    # Pattern: row 0 all fg, row 1 all fg
    vdp.vram[0x0000] = 0xFF
    vdp.vram[0x0001] = 0xFF
    # Colour: row 0 → fg=6, row 1 → fg=3
    vdp.vram[0x2000] = 0x65
    vdp.vram[0x2001] = 0x35

    buf = render_frame(vdp)
    assert buf[0 * 256 + 0] == 6   # line 0: fg=6
    assert buf[1 * 256 + 0] == 3   # line 1: fg=3


# ---------------------------------------------------------------------------
# Sprites (mode 1)
# ---------------------------------------------------------------------------

def _write_sat_entry(vdp: V9938, idx: int, y: int, x: int, pat: int, color: int) -> None:
    """Write one 4-byte SAT entry."""
    sat_base = (vdp.regs[5] & 0x7F) << 7
    base = (sat_base + idx * 4) & 0x3FFF
    vdp.vram[base]     = y & 0xFF
    vdp.vram[base + 1] = x & 0xFF
    vdp.vram[base + 2] = pat & 0xFF
    vdp.vram[base + 3] = color & 0x0F


def test_sprite_pixel_placed_at_correct_position() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[5] = 0x0E  # SAT at 0x0700
    vdp.regs[6] = 0x01  # SPG at 0x0800

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0, color=7)
    sat_base = (vdp.regs[5] & 0x7F) << 7
    vdp.vram[(sat_base + 4) & 0x3FFF] = 0xD0  # terminate SAT after sprite 0

    vdp.vram[0x0800] = 0x80  # pattern 0, row 0: leftmost bit set

    buf = render_frame(vdp)
    # y=0 → y_top=1; sprite appears at scan line 1, column 0
    assert buf[1 * 256 + 0] == 7


def test_sprite_5th_line_flag() -> None:
    """V9938 sprite mode 1 keeps the TMS 4-per-line limit; the 5th sets S#0 bit 6."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[5] = 0x0E  # SAT at 0x0700
    vdp.regs[6] = 0x00  # SPG at 0x0000

    sat_base = (vdp.regs[5] & 0x7F) << 7  # 0x0700

    # 5 sprites at y=0 (all visible on scan line 1), spread across x
    for i in range(5):
        _write_sat_entry(vdp, i, y=0, x=i * 8, pat=0, color=i + 1)
    vdp.vram[(sat_base + 5 * 4) & 0x3FFF] = 0xD0  # terminate after sprite 4

    render_frame(vdp)

    assert vdp.status & 0x40           # 5th-sprite flag set
    assert (vdp.status & 0x1F) == 4   # index of 5th sprite (0-based = 4)


# ---------------------------------------------------------------------------
# TEXT1 (SCREEN 0) colours come straight from R#7 (no colour-0 substitution)
# ---------------------------------------------------------------------------

def test_text1_colours_from_r7() -> None:
    vdp = V9938()
    vdp.regs[1] = 0x50  # M1 (bit4) + BL (bit6) → TEXT1
    vdp.regs[2] = 0x00  # name table at 0x0000
    vdp.regs[4] = 0x01  # pattern gen at 0x0800
    vdp.regs[7] = 0x32  # fg=3, bg=2
    vdp.vram[0x0000] = 0x00     # char 0 at col 0
    vdp.vram[0x0800] = 0xFF     # pattern row 0 all set

    buf = render_frame(vdp)
    assert buf[8] == 3   # col 0, first text pixel = fg=3


def test_text1_fg_zero_uses_palette_index_0() -> None:
    """V9938 TEXT1 with fg=0 shows palette index 0 directly, NOT colour 1."""
    vdp = V9938()
    vdp.regs[1] = 0x50  # TEXT1
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x01
    vdp.regs[7] = 0x02  # fg=0, bg=2
    vdp.vram[0x0000] = 0x00
    vdp.vram[0x0800] = 0xFF

    buf = render_frame(vdp)
    assert buf[8] == 0   # fg=0 → index 0 (was incorrectly forced to 1)


# ---------------------------------------------------------------------------
# MULTICOLOR (SCREEN 3) — 4x4 block addressing (regression for the per-scanline
# byte bug: only 2 of the 8 pattern bytes per cell are used)
# ---------------------------------------------------------------------------

def test_mc_top_bottom_half_bytes() -> None:
    vdp = V9938()
    vdp.regs[1] = 0x48   # M2 (bit3) → MULTICOLOR, BL (bit6) → display on
    vdp.regs[2] = 0x0E   # name table at 0x3800
    vdp.regs[4] = 0x00   # pattern generator at 0x0000
    vdp.vram[0x3800] = 0x00      # tile 0 at (col 0, char row 0)
    vdp.vram[0x0000] = 0xF7      # top half: left=15, right=7
    vdp.vram[0x0001] = 0x3A      # bottom half: left=3, right=10

    buf = render_frame(vdp)

    for scan in range(4):        # top half → pattern byte +0
        assert buf[scan * 256 + 0] == 15
        assert buf[scan * 256 + 4] == 7
    for scan in range(4, 8):     # bottom half → pattern byte +1
        assert buf[scan * 256 + 0] == 3
        assert buf[scan * 256 + 4] == 10


# ---------------------------------------------------------------------------
# GRAPHIC 2/3 pattern-generator base uses R#4 bits 5:2 (A16-A13), not just bit2.
# Regression for Ultima III (SCREEN 4): R#4=0x13 → pattern base 0x8000; the old
# (R#4 & 0x04) << 11 form gave 0x0000 and garbled the background.
# ---------------------------------------------------------------------------

def test_g3_pattern_base_uses_r4_bits_5_2() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x04          # SCREEN 4 (G3): M4=1, M3=0, M5=0
    vdp.regs[1] = 0x40          # BL on
    vdp.regs[8] = 0x04          # SPD: disable sprites (VRAM is not a valid SAT)
    vdp.regs[2] = 0x00          # name table 0x0000
    vdp.regs[4] = 0x13          # pattern base (0x13 & 0x3C) << 11 = 0x8000
    vdp.regs[3] = 0x80          # colour base (0x80 & 0x80) << 6 = 0x2000
    vdp.regs[10] = 0x00
    for n in range(768):        # every cell -> tile 0
        vdp.vram[0x0000 + n] = 0x00
    for py in range(8):
        vdp.vram[0x8000 + py] = 0xFF   # pattern generator at 0x8000: solid tile 0
        vdp.vram[0x2000 + py] = 0xF1   # colour: fg=15, bg=1

    buf = render_frame(vdp)

    # Pattern read from 0x8000 (not 0x0000, which is the zero-filled name table)
    assert buf[5 * 256 + 5] == 15


def test_screen2_pattern_base_bit2_still_zero() -> None:
    # Standard SCREEN 2 layout (R#4=0x00) keeps the pattern generator at 0x0000
    # under the R#4 bits 5:2 mask, unchanged from before.
    vdp = V9938()
    vdp.regs[0] = 0x02          # SCREEN 2 (M3=1)
    vdp.regs[1] = 0x40
    vdp.regs[8] = 0x04
    vdp.regs[2] = 0x06          # name 0x1800
    vdp.regs[4] = 0x00          # pattern base 0x0000
    vdp.regs[3] = 0x80          # colour 0x2000
    vdp.vram[0x1800] = 0x00     # tile 0
    vdp.vram[0x0000] = 0xFF     # pattern row 0 solid
    vdp.vram[0x2000] = 0x65     # fg=6, bg=5
    buf = render_frame(vdp)
    assert buf[0] == 6
