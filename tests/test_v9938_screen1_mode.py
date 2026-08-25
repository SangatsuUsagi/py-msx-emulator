"""V9938 regression tests for SCREEN 1-3 (GRAPHIC1/GRAPHIC2/MULTICOLOR) and
sprite mode 1, covering a set of real hardware-fidelity gaps found during the
vdp-renderer allium distill/weed pass: sprite mode 1 previously ignored R#23
vertical scroll and per-band R#8 SPD entirely (ghost/blanked sprites across a
banded split screen), its sprite-pattern-table read was truncated to 16 KB
(V9938 supports the full 128 KB range via R#6, same as sprite mode 2), and the
GRAPHIC1/MULTICOLOR background tile planes ignored R#23 while GRAPHIC2 (which
shares this file's rendering with GRAPHIC3) already honoured it.
"""
from msx.vdp.v9938 import V9938, _RegChange
from msx.vdp.v9938_renderer import render_frame
from tests.render_geometry import active_region


def _active(vdp: V9938) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(render_frame(vdp), vdp.display_height)


def _set_g1(vdp: V9938) -> None:
    vdp.regs[0] = 0x00  # M1-M5 clear -> GRAPHIC1
    vdp.regs[1] |= 0x40  # BL: enable display


def _set_mc(vdp: V9938) -> None:
    vdp.regs[0] = 0x00
    vdp.regs[1] = 0x48  # BL | M2 -> MULTICOLOR


# SAT at 0x1000 (R#5=0x20, (0x20 & 0x7F) << 7 = 0x1000), sprite pattern
# generator at 0x0000 (R#6=0x00). Sprite mode 1 layout: SAT entry is
# (Y, X, pattern, attr), attr low nibble = colour, bit 7 = EC.
_SAT = 0x1000


def _write_sat_entry(vdp: V9938, idx: int, y: int, x: int, pat: int, attr: int) -> None:
    vdp.vram[_SAT + idx * 4 + 0] = y & 0xFF
    vdp.vram[_SAT + idx * 4 + 1] = x & 0xFF
    vdp.vram[_SAT + idx * 4 + 2] = pat & 0xFF
    vdp.vram[_SAT + idx * 4 + 3] = attr & 0xFF


# ---------------------------------------------------------------------------
# A2: sprite mode 1 honours R#23 vertical scroll (previously ignored)
# ---------------------------------------------------------------------------

def test_sprite_mode1_honours_vertical_scroll() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20  # SAT at 0x1000
    vdp.regs[6] = 0x00  # SPT at 0x0000
    vdp.regs[23] = 20  # vscroll: VRAM line N displays at screen line N-20
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost pixel set
    _write_sat_entry(vdp, 0, y=29, x=0, pat=0, attr=0x0F)  # y_top=30 (VRAM space)
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)  # terminator

    buf = _active(vdp)

    # VRAM y_top=30 displays at screen line 30-20=10 under vscroll=20.
    assert buf[10 * 256 + 0] == 15, "sprite must appear at the scrolled screen line"
    assert buf[30 * 256 + 0] != 15, "sprite must NOT appear at its unscrolled VRAM line"


# ---------------------------------------------------------------------------
# A1: sprite mode 1 honours per-band R#8 SPD in a banded (split-screen) render
# ---------------------------------------------------------------------------

def test_sprite_mode1_spd_applied_per_band_not_tallest_band() -> None:
    """A status-bar split screen that blanks sprites (SPD=1) only in a short
    top band, with sprites enabled (SPD=0) in the taller bottom band, must not
    show a ghost sprite in the top band just because the bottom band is
    taller and its SPD value used to win for the whole pass."""
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20
    vdp.regs[6] = 0x00
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost pixel set
    _write_sat_entry(vdp, 0, y=4, x=0, pat=0, attr=0x0F)  # y_top=5, in the top band
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    # Top band (short, lines 0-9): SPD=1 (sprites blanked, status bar).
    # Bottom band (tall, lines 10-191): SPD=0 (sprites enabled, play area).
    vdp._frame_start_regs = vdp.regs[:]
    vdp._frame_start_regs[8] = 0x02  # SPD=1 in the top band
    vdp.regs[8] = 0x00  # live/bottom-band regs: SPD=0
    vdp._frame_start_palette = vdp.palette[:]
    vdp._reg_write_log = [_RegChange(9, 8, 0x00)]  # effective line 10

    buf = _active(vdp)

    assert buf[5 * 256 + 0] != 15, "sprite in the SPD=1 top band must be blanked"


def test_sprite_mode1_spd_enabled_band_still_shows_sprite() -> None:
    """Inverse of the above: a sprite in the taller SPD=0 band must still
    render even though the shorter band ahead of it has SPD=1 -- this failed
    the other way before the fix (whole-pass SPD taken from the tallest band,
    which used to always be the SPD=0 band in the other test's shape, but the
    bug was symmetric: whichever band vdp.regs happened to hold won)."""
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20
    vdp.regs[6] = 0x00
    vdp.vram[0] = 0x80
    _write_sat_entry(vdp, 0, y=49, x=0, pat=0, attr=0x0F)  # y_top=50, in the bottom band
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    vdp._frame_start_regs = vdp.regs[:]
    vdp._frame_start_regs[8] = 0x02  # SPD=1 in the top band
    vdp.regs[8] = 0x00  # SPD=0 in the bottom band
    vdp._frame_start_palette = vdp.palette[:]
    vdp._reg_write_log = [_RegChange(9, 8, 0x00)]  # effective line 10

    buf = _active(vdp)

    assert buf[50 * 256 + 0] == 15, "sprite in the SPD=0 bottom band must still render"


# ---------------------------------------------------------------------------
# A4: sprite mode 1's pattern table addresses the full 128 KB VRAM (R#6),
# not truncated to 16 KB
# ---------------------------------------------------------------------------

def test_sprite_mode1_pattern_table_beyond_16kb() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20  # SAT at 0x1000 (within 16 KB, unaffected)
    vdp.regs[6] = 0x10  # SPT base = 0x10 << 11 = 0x8000 (beyond 16 KB)
    vdp.vram[0x8000] = 0x80  # pattern 0, row 0, at the >16KB pattern table
    vdp.vram[0x0000] = 0x00  # the wrapped (16KB-masked) address must NOT be read
    _write_sat_entry(vdp, 0, y=9, x=0, pat=0, attr=0x0F)  # y_top=10
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    buf = _active(vdp)

    assert buf[10 * 256 + 0] == 15, "sprite pattern must be read from the real >16KB address"


# ---------------------------------------------------------------------------
# A3: GRAPHIC1 and MULTICOLOR backgrounds honour R#23 vertical scroll
# (GRAPHIC2 already did; these two previously did not)
# ---------------------------------------------------------------------------

def test_graphic1_background_honours_vertical_scroll() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[2] = 0x00  # name table at 0x0000
    vdp.regs[4] = 0x00  # pattern generator at 0x0000
    vdp.regs[3] = 0x00  # colour table at 0x0000
    vdp.regs[10] = 0x00
    vdp.regs[23] = 8  # scroll by exactly one tile row

    # Tile 1 at name-table row 1, col 0 (VRAM row 1 = screen row 0 after an
    # 8-line scroll); tile 1's colour group is tile // 8 == 0 (tiles 0-7
    # share one colour byte, at col_base + 0).
    vdp.vram[32] = 1  # name table row 1, col 0 -> tile index 1
    vdp.vram[0] = 0xF1  # colour table entry for tile group 0: fg=15, bg=1
    vdp.vram[1 * 8 + 0] = 0x80  # pattern for tile 1, row 0: leftmost pixel set

    buf = _active(vdp)

    assert buf[0] == 15, "row 1's tile must display at screen row 0 after an 8px scroll"


# ---------------------------------------------------------------------------
# A5: scalar (non-banded) SPD still applies as a whole-pass sprite disable
# when no per-line schedule is given -- the fallback has_per_line_sprite_
# schedule(runs) branches to in RenderSpritesMode1's requires clause.
# ---------------------------------------------------------------------------

def test_sprite_mode1_spd_disables_whole_pass_without_schedule() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20  # SAT at 0x1000
    vdp.regs[6] = 0x00  # SPT at 0x0000
    vdp.regs[8] = 0x02  # SPD=1, no banding -> whole-pass disable
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost pixel set
    _write_sat_entry(vdp, 0, y=4, x=0, pat=0, attr=0x0F)  # y_top=5
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    buf = _active(vdp)

    assert buf[5 * 256 + 0] != 15, "SPD=1 with no per-line schedule must blank sprites"


def test_sprite_mode1_spd_clear_allows_sprites_without_schedule() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20
    vdp.regs[6] = 0x00
    vdp.regs[8] = 0x00  # SPD=0
    vdp.vram[0] = 0x80
    _write_sat_entry(vdp, 0, y=4, x=0, pat=0, attr=0x0F)  # y_top=5
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    buf = _active(vdp)

    assert buf[5 * 256 + 0] == 15, "SPD=0 with no per-line schedule must render sprites normally"


def test_multicolour_background_honours_vertical_scroll() -> None:
    vdp = V9938()
    _set_mc(vdp)
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x00
    vdp.regs[23] = 8  # scroll by exactly one tile row

    vdp.vram[32] = 1  # name table row 1, col 0 -> tile index 1
    # MULTICOLOR pattern byte for tile 1, seg=(row&3)*2 with row=1 -> seg=2,
    # top half (py>>2==0) -> pattern index tile*8 + seg + 0 = 8 + 2 = 10.
    vdp.vram[10] = 0xF0  # left nibble (colour 15) / right nibble (colour 0)

    buf = _active(vdp)

    assert buf[0] == 15, "row 1's block must display at screen row 0 after an 8px scroll"


# ---------------------------------------------------------------------------
# Open Question #3 (now resolved): sprite mode 1's SAT base honours R#11's
# 128 KB VRAM extension, matching sprite mode 2 (previously ignored R#11
# entirely and was masked to 16 KB).
# ---------------------------------------------------------------------------

def test_sprite_mode1_sat_base_honours_r11_128kb_extension() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x00
    vdp.regs[11] = 0x01  # R#11 bit 0 -> SAT base = (1 << 15) | (0 << 7) = 0x8000
    vdp.regs[6] = 0x01  # SPT at 0x0800 (kept away from the 0x0000 trap below)
    sat = 0x8000
    vdp.vram[sat + 0] = 9  # Y=9 -> y_top=10
    vdp.vram[sat + 1] = 0  # X=0
    vdp.vram[sat + 2] = 0  # pattern 0
    vdp.vram[sat + 3] = 0x0F  # colour 15
    vdp.vram[sat + 4] = 0xD0  # terminator (sprite index 1)
    vdp.vram[0x0800] = 0x80  # pattern 0, row 0: leftmost pixel set
    # If the old 16KB-wrapped address (0x8000 & 0x3FFF == 0) were read
    # instead, this terminator byte would end the SAT scan at sprite 0.
    vdp.vram[0x0000] = 0xD0

    buf = _active(vdp)

    assert buf[10 * 256 + 0] == 15, "SAT must be read from the real >16KB address (R#11 extension)"


# ---------------------------------------------------------------------------
# Open Question #4 (now resolved): TEXT1 and sprite mode 1 both extend to
# the full 212 native lines at R#9 LN=1, matching every other screen mode
# (real hardware's active display period is mode-independent -- see
# VDP::getNumberOfLines() in openMSX). Both were previously hard-clamped to
# 192 lines.
# ---------------------------------------------------------------------------

def test_text1_renders_beyond_192_lines_at_ln1() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x00
    vdp.regs[1] = 0x50  # BL | M1 -> TEXT1
    vdp.regs[9] = 0x80  # LN=1 -> 212 native lines
    vdp.regs[2] = 0x00  # name table at 0x0000
    vdp.regs[4] = 0x00  # pattern generator at 0x0000
    vdp.regs[7] = 0xF0  # fg=15, bg=0

    row = 25  # tile row 25 -> scanline 200, beyond the old 24-row (192-line) cap
    scan = row * 8
    vdp.vram[row * 40 + 0] = 1  # name table: tile index 1 at (row, col 0)
    vdp.vram[1 * 8 + 0] = 0x80  # pattern for tile 1, character row 0: leftmost pixel set

    buf = _active(vdp)

    assert buf[scan * 256 + 8] == 15, "TEXT1 must render past line 192 at LN=1"


def test_sprite_mode1_renders_beyond_192_lines_at_ln1() -> None:
    vdp = V9938()
    _set_g1(vdp)
    vdp.regs[5] = 0x20
    vdp.regs[6] = 0x00
    vdp.regs[9] = 0x80  # LN=1 -> 212 native lines
    vdp.vram[0] = 0x80
    _write_sat_entry(vdp, 0, y=199, x=0, pat=0, attr=0x0F)  # y_top=200
    _write_sat_entry(vdp, 1, y=0xD0, x=0, pat=0, attr=0)

    buf = _active(vdp)

    assert buf[200 * 256 + 0] == 15, "sprite mode 1 must render past line 192 at LN=1"
