"""Tests for TEXT2 (SCREEN 0, WIDTH 80): MSX-DOS's MODE 80."""

from msx.debugger.prompt import _decode_screen_mode
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame
from tests.render_geometry import active_region


def _active(vdp: V9938) -> bytearray:
    """render_frame() output with the constant output-height border padding
    stripped, so pixel-position assertions use native scanline coordinates."""
    return active_region(render_frame(vdp), vdp.display_height)


def _enable_text2(vdp: V9938) -> None:
    vdp.regs[0] = 0x04  # M4 (bit2)
    vdp.regs[1] = 0x50  # M1 (bit4) + BL (bit6) -> TEXT2 with M4 above


# ---------------------------------------------------------------------------
# Colour / name-table / pattern-table rendering
# ---------------------------------------------------------------------------


def test_text2_colours_from_r7_at_column_0_and_80() -> None:
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[2] = 0x00  # name table at 0x0000
    vdp.regs[4] = 0x01  # pattern gen at 0x0800
    vdp.regs[7] = 0x32  # fg=3, bg=2
    vdp.vram[0] = 0x00  # char 0 at row 0, col 0
    vdp.vram[40] = 0x00  # char 0 at row 0, col 40 (unreachable by TEXT1's 40 cols)
    vdp.vram[0x0800] = 0xFF  # pattern row 0 all set

    buf = _active(vdp)
    assert buf[16] == 3  # col 0 -> 16 px left margin
    assert buf[16 + 40 * 6] == 3  # col 40 -> only reachable with 80-column layout


def test_text2_name_table_uses_r2_bits_6_2_only() -> None:
    """R#2 = 0x33 must resolve the same name-table base as R#2 = 0x30
    (0xC000): bits 0-1 are masked off by the 4 KB alignment."""
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[2] = 0x33
    vdp.regs[4] = 0x00  # pattern gen at 0x0000
    vdp.regs[7] = 0x32  # fg=3, bg=2

    vdp.vram[0xC000] = 1  # correct (masked) name-table address: tile 1
    vdp.vram[0xCC00] = 2  # wrong (unmasked, R#2&0x7F) address: tile 2
    vdp.vram[1 * 8] = 0xFF  # tile 1 pattern row 0: all fg
    vdp.vram[2 * 8] = 0x00  # tile 2 pattern row 0: all bg

    buf = _active(vdp)
    assert buf[16] == 3  # fg -> read tile 1 from 0xC000, not tile 2 from 0xCC00


# ---------------------------------------------------------------------------
# Sprites are suppressed in TEXT2
# ---------------------------------------------------------------------------


def test_text2_draws_no_sprites() -> None:
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x00
    vdp.regs[7] = 0x32  # fg=3, bg=2 (border also reads bg=2)
    vdp.regs[5] = 0x0E  # SAT at 0x0700
    vdp.regs[6] = 0x01  # SPG at 0x0800

    sat_base = (vdp.regs[5] & 0x7F) << 7
    base = sat_base & 0x3FFF
    vdp.vram[base] = 0  # y=0 -> sprite mode 1 would appear at scan line 1
    vdp.vram[base + 1] = 0  # x=0
    vdp.vram[base + 2] = 0  # pattern 0
    vdp.vram[base + 3] = 7  # colour 7
    vdp.vram[(base + 4) & 0x3FFF] = 0xD0  # terminate SAT after sprite 0
    vdp.vram[0x0800] = 0x80  # pattern 0, row 0: leftmost bit set

    buf = _active(vdp)
    assert buf[1 * 512 + 0] == 2  # bg/border, not the sprite's colour 7


# ---------------------------------------------------------------------------
# display_width / display_height geometry
# ---------------------------------------------------------------------------


def test_text2_display_width_is_512() -> None:
    vdp = V9938()
    _enable_text2(vdp)
    assert vdp.display_width == 512


def test_text2_212_line_variant_renders_only_top_half_of_final_row() -> None:
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[9] = 0x80  # LN -> 212 lines (26.5 text rows)
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x00
    vdp.regs[7] = 0x32  # fg=3, bg=2

    # Row 26 (scanlines 208-215) is the final, half-visible row at 212 lines:
    # only scanlines 208-211 (py 0-3) fall inside [0, 212).
    vdp.vram[26 * 80] = 1  # tile 1 at row 26, col 0
    for py in range(4):
        vdp.vram[1 * 8 + py] = 0xFF  # visible half: all fg
    for py in range(4, 8):
        vdp.vram[1 * 8 + py] = 0x00  # invisible half: would be all bg

    buf = render_frame(vdp)
    assert len(buf) == 512 * 212
    active = active_region(buf, vdp.display_height)
    assert active[208 * 512 + 16] == 3
    assert active[211 * 512 + 16] == 3


# ---------------------------------------------------------------------------
# Debugger's `_decode_screen_mode`
# ---------------------------------------------------------------------------


def test_decode_screen_mode_text2() -> None:
    # Exact register values from the MSX-DOS `MODE 80` repro that surfaced this bug.
    assert _decode_screen_mode(0x04, 0x70) == "SCREEN0 (TEXT2/80col)"


# ---------------------------------------------------------------------------
# VRAM addressing range and sprite status-flag side effects (allium propagate)
# ---------------------------------------------------------------------------


def test_text2_name_table_reaches_high_vram_via_wide_mask() -> None:
    """TEXT2's name-table read masks with & 0x1FFFF (full 128 KB), wider than
    TEXT1's & 0x3FFF. Placing the name table beyond the 64 KB boundary (a
    16-bit mask would still pass a base like 0xC000, so that alone would not
    catch a narrower mask) confirms the wide mask is actually exercised, not
    just R#2's 5-bit decode."""
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[2] = 0x7C  # name base = (0x7C & 0x7C) << 10 = 0x1F000 (126976)
    vdp.regs[4] = 0x00  # pattern gen at 0x0000
    vdp.regs[7] = 0x32  # fg=3, bg=2

    name_base = 0x1F000
    vdp.vram[name_base] = 1  # tile 1 at row 0, col 0
    vdp.vram[1 * 8] = 0xFF  # tile 1 pattern row 0: all fg

    buf = _active(vdp)
    assert buf[16] == 3  # fg -> the high-VRAM (>64 KB) name-table byte was read


def test_text2_reports_no_sprite_overflow_or_collision_status() -> None:
    """Even with a SAT that would set both 5S (5th sprite on a line) and C
    (collision) in a sprite-drawing mode, TEXT2 must leave S#0's sprite flags
    untouched: sprites_active excludes text2 (allium/v9938.allium's
    RenderFrame), so the sprite scan that sets vdp.status never runs at all --
    not just "no sprite pixels", but no status side effect either."""
    vdp = V9938()
    _enable_text2(vdp)
    vdp.regs[2] = 0x00
    vdp.regs[4] = 0x00
    vdp.regs[7] = 0x32  # fg=3, bg=2
    vdp.regs[5] = 0x0E  # SAT at 0x0700 (sprite mode 1 addressing)
    vdp.regs[6] = 0x00  # SPT at 0x0000

    sat_base = (vdp.regs[5] & 0x7F) << 7
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost bit set (opaque)
    for i in range(5):
        base = sat_base + i * 4
        vdp.vram[base] = 0  # Y=0 -> would appear at scan line 1
        vdp.vram[base + 1] = 0  # X=0: every sprite overlaps -> would collide
        vdp.vram[base + 2] = 0  # pattern 0
        vdp.vram[base + 3] = i + 1  # non-zero colour: opaque, collision-eligible
    vdp.vram[sat_base + 5 * 4] = 0xD0  # terminate SAT after the 5th sprite

    render_frame(vdp)

    assert vdp.status == 0  # untouched entirely: 5S, C and the overflow index all stay clear
