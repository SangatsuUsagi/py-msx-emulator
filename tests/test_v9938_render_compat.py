"""Tests for msx.vdp.v9938_renderer: SCREEN 0–3 TMS9918A-compatible rendering."""
from msx.vdp._v9938_defs import _RegChange
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame
from tests.render_geometry import active_region


def _active(vdp: V9938) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(render_frame(vdp), vdp.display_height)




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
    assert len(render_frame(vdp)) == 256 * 212


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
#
# The core fg/bg-from-colour-table and colour-0-substitutes-to-backdrop
# behaviour is covered, for both this renderer and TMS9918A's, by
# tests/vdp/test_renderer_g1.py (parametrized over both backends via the
# `flavor` fixture -- see tests/vdp/conftest.py). Only the V9938-specific
# 128 KB-range extension stays here.
# ---------------------------------------------------------------------------

def test_screen1_name_and_pattern_tables_above_16k() -> None:
    """GRAPHIC1's name/pattern tables use the full V9938 128 KB range (R#2 7
    bits, R#4 6 bits), the same fix GRAPHIC2/3 already had
    (test_screen4_name_table_above_16k / test_g3_pattern_base_uses_r4_bits_5_2)
    -- found by an allium weed pass while investigating
    logs/msx-21-hb-f1xd-select-screen-text-missing.md: this codebase's own
    G1 colour table was already on the wide (R#10/&0x1FFFF) form while
    name/pattern were still the narrow TMS9918A-only (16 KB) form."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[2] = 0x30   # name_base = (0x30 & 0x7F) << 10 = 0xC000
    vdp.regs[3] = 0x80   # col_base = 0x2000
    vdp.regs[4] = 0x11   # pat_base = (0x11 & 0x3F) << 11 = 0x8800

    vdp.vram[0xC000] = 0      # name[0,0] = tile 0 (read from 0xC000, not 0x0000)
    vdp.vram[0x2000] = 0x41   # colour group 0: fg=4, bg=1
    for py in range(8):
        vdp.vram[0x8800 + py] = 0xFF   # pattern tile 0 (read from 0x8800, not 0x0800)

    buf = _active(vdp)
    assert buf[0] == 4


# ---------------------------------------------------------------------------
# SCREEN 2 (GRAPHIC2): per-row per-tile colour
#
# The core per-row fg/bg-from-colour-table and colour-0-substitutes-to-
# backdrop behaviour is covered, for both this renderer and TMS9918A's, by
# tests/vdp/test_renderer_g2.py (parametrized over both backends). Only the
# V9938-specific third-mirroring behaviour (no TMS9918A equivalent, since
# TMS9918A lacks R#10/full-R#2/wide-R#4) stays here.
# ---------------------------------------------------------------------------

def test_screen2_band1_2_alias_band0_when_r4_r3_low_bits_clear() -> None:
    """R#4 bits 1:0 clear (and R#3 bits 6:5 clear) collapse the 2nd/3rd-third
    pattern/colour tables onto the 1st third (msx.org/wiki/SCREEN_2's
    documented "reused" behaviour) -- a real-ROM regression
    (logs/msx-21-hb-f1xd-select-screen-text-missing.md): a title/menu screen
    defining only one 2 KB font+colour third rendered blank on tile rows
    8-23 because the code read the (uninitialised) 2nd/3rd-third VRAM
    directly instead of mirroring the 1st third's real data."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x02   # GRAPHIC2
    vdp.regs[2] = 0x06   # name_base = 0x1800
    vdp.regs[3] = 0x9F   # col_base = 0x2000; bits 6:5 clear -> band1/2 colour aliases band0
    vdp.regs[4] = 0x00   # pat_base = 0x0000; bits 1:0 clear -> band1/2 pattern aliases band0

    # Only band0 (0x0000-0x07FF pattern, 0x2000-0x27FF colour) has real data,
    # for tile 0 -- the name table's rows 8 and 16 already default to tile 0
    # without any write, since fresh VRAM is zero-filled.
    vdp.vram[0x0000] = 0xFF   # tile 0, row 0: solid
    vdp.vram[0x2000] = 0x65   # tile 0, row 0 colour: fg=6, bg=5
    # band1/2's own VRAM (0x0800.../0x1000...) is left at its zero-initialised
    # default (no pattern bits set, colour nibbles 0 -> backdrop).

    buf = _active(vdp)
    assert buf[8 * 8 * 256] == 6    # band1 row (tile row 8, y=64): mirrored fg
    assert buf[16 * 8 * 256] == 6   # band2 row (tile row 16, y=128): mirrored fg


def test_screen2_band1_2_independent_when_r4_r3_low_bits_set() -> None:
    """The mirroring in the test above is conditional: with R#4 bits 1:0 and
    R#3 bits 6:5 set, band1/2 keep their own independent pattern/colour data
    (the normal 3-distinct-fonts SCREEN 2 layout is unaffected)."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x02   # GRAPHIC2
    vdp.regs[2] = 0x06   # name_base = 0x1800
    vdp.regs[3] = 0xFF   # col_base = 0x2000; bits 6:5 set -> independent bands
    vdp.regs[4] = 0x03   # pat_base = 0x0000; bits 1:0 set -> independent bands

    # All three thirds use tile 0 (the name table's rows 0/8/16 all default to
    # tile 0), each with its own distinct pattern+colour data.
    vdp.vram[0x0000] = 0xFF   # band0 tile 0, row 0: solid, fg=6/bg=5
    vdp.vram[0x2000] = 0x65
    vdp.vram[0x0800] = 0xFF   # band1 tile 0, row 0: solid, fg=3/bg=1 (different colour)
    vdp.vram[0x2800] = 0x31
    vdp.vram[0x1000] = 0xFF   # band2 tile 0, row 0: solid, fg=9/bg=2
    vdp.vram[0x3000] = 0x92

    buf = _active(vdp)
    assert buf[8 * 8 * 256] == 3    # band1 row: its own colour, not band0's
    assert buf[16 * 8 * 256] == 9   # band2 row: its own colour, not band0's


# ---------------------------------------------------------------------------
# Sprites (mode 1)
#
# Basic pixel placement, priority, 5th-sprite/coincidence flags, EC, and
# colour-0-transparent-and-still-collides-on-TMS9918A are covered, for both
# this renderer and TMS9918A's, by tests/vdp/test_sprites.py (parametrized
# over both backends). Only the V9938-specific TP (R#8 bit 5) behaviour --
# which has no TMS9918A equivalent (colour 0 always collides there) --
# stays here.
# ---------------------------------------------------------------------------

def _write_sat_entry(vdp: V9938, idx: int, y: int, x: int, pat: int, color: int) -> None:
    """Write one 4-byte SAT entry."""
    sat_base = (vdp.regs[5] & 0x7F) << 7
    base = (sat_base + idx * 4) & 0x3FFF
    vdp.vram[base]     = y & 0xFF
    vdp.vram[base + 1] = x & 0xFF
    vdp.vram[base + 2] = pat & 0xFF
    vdp.vram[base + 3] = color & 0x0F


def test_sprite_colour_zero_does_not_collide_at_default_tp() -> None:
    """TP (R#8 bit 5) defaults to 0 (colour 0 transparent): a colour-0
    sprite must not set the coincidence flag, matching real V9938 hardware
    (openMSX canSpriteColor0Collide() is false here) and unchanged from
    before this fix."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[5] = 0x0E  # SAT at 0x0700
    vdp.regs[6] = 0x00  # SPG at 0x0000
    sat_base = (vdp.regs[5] & 0x7F) << 7

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0, color=0)    # transparent
    _write_sat_entry(vdp, 1, y=0, x=0, pat=1, color=10)   # overlapping, opaque
    vdp.vram[0x0000] = 0x80  # pattern 0, row 0
    vdp.vram[0x0008] = 0x80  # pattern 1, row 0
    vdp.vram[(sat_base + 2 * 4) & 0x3FFF] = 0xD0  # terminator

    buf = _active(vdp)

    assert not (vdp.status & 0x20), "colour 0 must not collide at TP=0"
    assert buf[1 * 256 + 0] == 10  # colour-0 sprite itself is still not painted


def test_sprite_colour_zero_collides_when_tp_set() -> None:
    """With TP=1 (R#8 bit 5 set, colour 0 not transparent), a colour-0 sprite
    both sets the coincidence flag and paints as palette entry 0 -- it is an
    ordinary opaque colour, so the higher-priority sprite 0 wins the pixel
    (openMSX SpriteConverter::drawMode1 only skips colour 0 when TP is clear)."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[5] = 0x0E  # SAT at 0x0700
    vdp.regs[6] = 0x00  # SPG at 0x0000
    vdp.regs[8] |= 0x20  # TP
    sat_base = (vdp.regs[5] & 0x7F) << 7

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0, color=0)    # transparent, but TP set
    _write_sat_entry(vdp, 1, y=0, x=0, pat=1, color=10)   # overlapping, opaque
    vdp.vram[0x0000] = 0x80  # pattern 0, row 0
    vdp.vram[0x0008] = 0x80  # pattern 1, row 0
    vdp.vram[(sat_base + 2 * 4) & 0x3FFF] = 0xD0  # terminator

    buf = _active(vdp)

    assert vdp.status & 0x20, "colour 0 must collide when TP=1"
    assert buf[1 * 256 + 0] == 0  # higher-priority colour-0 sprite is opaque at TP=1


# ---------------------------------------------------------------------------
# Colour code 0 follows TP (R#8 bit 5) in the pattern and text modes
# (V9938 data book, R#8 TP): TP=0 -> the backdrop shows, TP=1 -> palette 0.
# ---------------------------------------------------------------------------

def _text_vdp(r1: int, fg_bg: int, tp: bool) -> V9938:
    vdp = V9938()
    vdp.regs[1] = r1
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x01
    vdp.regs[7] = fg_bg
    vdp.regs[8] = 0x20 if tp else 0x00
    vdp.vram[0x0000] = 0x00
    vdp.vram[0x0800] = 0xFF
    return vdp


def test_text1_fg_zero_shows_backdrop_when_tp_clear() -> None:
    buf = _active(_text_vdp(0x50, 0x04, tp=False))
    assert buf[8] == 4


def test_text1_fg_zero_is_palette_0_when_tp_set() -> None:
    buf = _active(_text_vdp(0x50, 0x04, tp=True))
    assert buf[8] == 0


def test_text2_fg_zero_shows_backdrop_when_tp_clear() -> None:
    vdp = _text_vdp(0x50, 0x04, tp=False)
    vdp.regs[0] = 0x04                 # M4 with M1 -> TEXT2
    vdp.regs[2] = 0x03                 # TEXT2 name table at 0x0000
    buf = render_frame(vdp)
    assert 4 in buf and 0 not in buf


def _g1_vdp(tp: bool) -> V9938:
    vdp = V9938()
    vdp.regs[1] = 0x40                 # GRAPHIC1, display on
    vdp.regs[2] = 0x06                 # name table 0x1800
    vdp.regs[3] = 0x80                 # colour table 0x2000
    vdp.regs[4] = 0x00                 # pattern generator 0x0000
    vdp.regs[7] = 0x04                 # backdrop 4
    vdp.regs[8] = 0x22 if tp else 0x02 # SPD set: no sprites
    vdp.vram[0x2000] = 0x10            # fg=1, bg=0
    return vdp                         # pattern 0 is all zero -> background


def test_g1_colour0_is_palette_0_when_tp_set() -> None:
    assert _active(_g1_vdp(tp=True))[0] == 0


def test_g1_colour0_shows_backdrop_when_tp_clear() -> None:
    assert _active(_g1_vdp(tp=False))[0] == 4


def test_g2_and_multicolor_colour0_follow_tp() -> None:
    g2 = _g1_vdp(tp=True)
    g2.regs[0] = 0x02                  # GRAPHIC2
    g2.regs[3] = 0xFF
    g2.regs[4] = 0x03
    assert _active(g2)[0] == 0
    mc = _g1_vdp(tp=True)
    mc.regs[1] = 0x48                  # MULTICOLOR
    mc.vram[0x0000] = 0x10             # left block colour 1, right block colour 0
    assert _active(mc)[4] == 0


def test_text1_nonzero_fg_is_unchanged_when_tp_set() -> None:
    buf = _active(_text_vdp(0x50, 0x94, tp=True))
    assert buf[8] == 9
    assert buf[0] == 4                 # left margin: background, not palette 0


def test_text2_fg_zero_is_palette_0_when_tp_set() -> None:
    vdp = _text_vdp(0x50, 0x04, tp=True)
    vdp.regs[0] = 0x04                 # M4 with M1 -> TEXT2
    vdp.regs[2] = 0x03                 # TEXT2 name table at 0x0000
    buf = active_region(render_frame(vdp), vdp.display_height)
    assert buf[16] == 0                # first character pixel (16 px margin)
    assert buf[0] == 4                 # margin stays the background


def test_g1_fg_nibble_zero_follows_tp() -> None:
    for tp, expected in ((False, 4), (True, 0)):
        vdp = _g1_vdp(tp)
        vdp.vram[0x2000] = 0x01        # fg=0, bg=1
        vdp.vram[0x0000] = 0xFF        # pattern 0 row 0: all foreground
        assert _active(vdp)[0] == expected


def test_g2_colour0_shows_backdrop_when_tp_clear() -> None:
    g2 = _g1_vdp(tp=False)
    g2.regs[0] = 0x02                  # GRAPHIC2
    g2.regs[3] = 0xFF
    g2.regs[4] = 0x03
    assert _active(g2)[0] == 4


def test_g3_colour0_follows_tp() -> None:
    # GRAPHIC3 (SCREEN 4) shares GRAPHIC2's background renderer.
    for tp, expected in ((False, 4), (True, 0)):
        g3 = _g1_vdp(tp)
        g3.regs[0] = 0x04              # M4 alone -> GRAPHIC3
        g3.regs[3] = 0xFF
        g3.regs[4] = 0x03
        assert _active(g3)[0] == expected


def test_multicolor_colour0_shows_backdrop_when_tp_clear() -> None:
    mc = _g1_vdp(tp=False)
    mc.regs[1] = 0x48                  # MULTICOLOR
    mc.vram[0x0000] = 0x10             # left block colour 1, right block colour 0
    buf = _active(mc)
    assert buf[0] == 1
    assert buf[4] == 4


def test_g7_byte_zero_ignores_tp() -> None:
    # GRAPHIC7 pixels are raw GRB332 bytes: no colour-0 substitution at all.
    for tp in (False, True):
        vdp = V9938()
        vdp.regs[0] = 0x0E             # M5+M4+M3 -> GRAPHIC7
        vdp.regs[1] = 0x40
        vdp.regs[7] = 0x44
        vdp.regs[8] = 0x22 if tp else 0x02
        assert _active(vdp)[0] == 0


def test_banded_frame_applies_tp_per_band() -> None:
    # TP set mid-frame (effective from line 10): the top band keeps the
    # backdrop for colour 0, the lower band shows palette 0.
    vdp = _g1_vdp(tp=False)
    vdp._frame_start_regs = vdp.regs[:]
    vdp._frame_start_palette = vdp.palette[:]
    vdp.regs[8] = 0x22
    vdp._reg_write_log = [_RegChange(9, 8, 0x22)]
    buf = _active(vdp)
    assert buf[5 * 256] == 4
    assert buf[50 * 256] == 0


def test_text1_name_and_pattern_tables_above_16k() -> None:
    """TEXT1's name/pattern tables use the full V9938 128 KB range, the same
    fix as GRAPHIC1/GRAPHIC2/GRAPHIC3 above -- found by the same allium weed
    pass (logs/msx-21-hb-f1xd-select-screen-text-missing.md)."""
    vdp = V9938()
    vdp.regs[1] = 0x50  # TEXT1
    vdp.regs[2] = 0x30  # name_base = (0x30 & 0x7F) << 10 = 0xC000
    vdp.regs[4] = 0x11  # pat_base  = (0x11 & 0x3F) << 11 = 0x8800
    vdp.regs[7] = 0x32  # fg=3, bg=2
    vdp.vram[0xC000] = 0x00
    vdp.vram[0x8800] = 0xFF

    buf = _active(vdp)
    assert buf[8] == 3


# ---------------------------------------------------------------------------
# MULTICOLOR (SCREEN 3): 128 KB-range table addressing
#
# 4x4 block addressing and colour-0-substitutes-to-backdrop are covered, for
# both this renderer and TMS9918A's, by tests/vdp/test_renderer_mc.py
# (parametrized over both backends). Only the V9938-specific 128 KB-range
# extension stays here.
# ---------------------------------------------------------------------------

def test_mc_name_and_pattern_tables_above_16k() -> None:
    """MULTICOLOUR's name/pattern tables use the full V9938 128 KB range, the
    same fix as GRAPHIC1/2/3 and TEXT1 above -- found by the same allium weed
    pass (logs/msx-21-hb-f1xd-select-screen-text-missing.md)."""
    vdp = V9938()
    vdp.regs[1] = 0x48   # MULTICOLOR
    vdp.regs[2] = 0x30   # name_base = (0x30 & 0x7F) << 10 = 0xC000
    vdp.regs[4] = 0x11   # pat_base  = (0x11 & 0x3F) << 11 = 0x8800
    vdp.vram[0xC000] = 0x00      # tile 0
    vdp.vram[0x8800] = 0xF7      # top half: left=15, right=7

    buf = _active(vdp)
    assert buf[0] == 15
    assert buf[4] == 7


# ---------------------------------------------------------------------------
# GRAPHIC 2/3 pattern-generator base uses R#4 bits 5:2 (A16-A13), not just bit2.
# Regression for an MSX2 SCREEN 4 title: R#4=0x13 → pattern base 0x8000; the old
# (R#4 & 0x04) << 11 form gave 0x0000 and garbled the background.
# ---------------------------------------------------------------------------

def test_g3_pattern_base_uses_r4_bits_5_2() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x04          # SCREEN 4 (G3): M4=1, M3=0, M5=0
    vdp.regs[1] = 0x40          # BL on
    vdp.regs[8] = 0x02          # SPD (R#8 bit 1): disable sprites (VRAM is not a valid SAT)
    vdp.regs[2] = 0x00          # name table 0x0000
    vdp.regs[4] = 0x13          # pattern base (0x13 & 0x3C) << 11 = 0x8000
    vdp.regs[3] = 0x80          # colour base (0x80 & 0x80) << 6 = 0x2000
    vdp.regs[10] = 0x00
    for n in range(768):        # every cell -> tile 0
        vdp.vram[0x0000 + n] = 0x00
    for py in range(8):
        vdp.vram[0x8000 + py] = 0xFF   # pattern generator at 0x8000: solid tile 0
        vdp.vram[0x2000 + py] = 0xF1   # colour: fg=15, bg=1

    buf = _active(vdp)

    # Pattern read from 0x8000 (not 0x0000, which is the zero-filled name table)
    assert buf[5 * 256 + 5] == 15


def test_screen2_pattern_base_bit2_still_zero() -> None:
    # Standard SCREEN 2 layout (R#4=0x00) keeps the pattern generator at 0x0000
    # under the R#4 bits 5:2 mask, unchanged from before.
    vdp = V9938()
    vdp.regs[0] = 0x02          # SCREEN 2 (M3=1)
    vdp.regs[1] = 0x40
    vdp.regs[8] = 0x02          # SPD (R#8 bit 1): disable sprites
    vdp.regs[2] = 0x06          # name 0x1800
    vdp.regs[4] = 0x00          # pattern base 0x0000
    vdp.regs[3] = 0x80          # colour 0x2000
    vdp.vram[0x1800] = 0x00     # tile 0
    vdp.vram[0x0000] = 0xFF     # pattern row 0 solid
    vdp.vram[0x2000] = 0x65     # fg=6, bg=5
    buf = _active(vdp)
    assert buf[0] == 6
