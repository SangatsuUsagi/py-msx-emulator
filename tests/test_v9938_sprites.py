"""Tests for V9938 sprite mode 2 (SCREEN 4–8)."""
from msx.vdp.v9938 import V9938, _RegChange
from msx.vdp.v9938_renderer import render_frame

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enable(vdp: V9938) -> None:
    vdp.regs[1] |= 0x40  # BL bit: enable display


def _set_screen5(vdp: V9938) -> None:
    _enable(vdp)
    vdp.regs[0] = 0x06  # M3=bit1, M4=bit2


# R#5=0x70, R#11=0 → attr_reg = 0x70 << 7 = 0x3800.
# V9938 sprite mode 2: R#5/R#11 → SAT base (512-byte aligned) = 0x3800;
# colour table at SAT - 0x200 = 0x3600.
_SAT_R5 = 0x70
_SAT_BASE = 0x3800
_COL_BASE = _SAT_BASE - 0x200


def _write_sat_entry(
    vdp: V9938, idx: int, y: int, x: int, pat: int, attr: int = 0
) -> None:
    base = (_SAT_BASE + idx * 4) & 0x1FFFF
    vdp.vram[base]     = y & 0xFF
    vdp.vram[base + 1] = x & 0xFF
    vdp.vram[base + 2] = pat & 0xFF
    vdp.vram[base + 3] = attr & 0xFF


def _write_col_entry(
    vdp: V9938, sprite_idx: int, line_idx: int, color: int,
    or_mode: bool = False, ec: bool = False, ic: bool = False,
) -> None:
    # Per-line colour byte: EC(7) | CC(6) | IC(5) | 0 | colour(3:0).
    entry = color & 0x0F
    if or_mode:
        entry |= 0x40  # CC
    if ic:
        entry |= 0x20  # IC
    if ec:
        entry |= 0x80  # EC
    vdp.vram[(_COL_BASE + sprite_idx * 16 + line_idx) & 0x1FFFF] = entry


def _terminate_sat(vdp: V9938, after_idx: int) -> None:
    # Sprite mode 2 (SCREEN 4-8) terminates the list on Y == 216 (0xD8), not 208.
    vdp.vram[(_SAT_BASE + after_idx * 4) & 0x1FFFF] = 0xD8


# ---------------------------------------------------------------------------
# Sprite mode 2: colour from colour table
# ---------------------------------------------------------------------------

def test_sprite_mode2_colour_from_colour_table() -> None:
    """Per-scanline colour comes from the colour table, not SAT attr bits 3:0."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00  # SPG at 0x0000

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=6)

    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost bit set

    buf = render_frame(vdp)
    # y=0 → y_top=1; sprite at scan line 1, col 0
    assert buf[1 * 256 + 0] == 6


def test_sprite_mode2_terminator_is_216_not_208() -> None:
    """Mode-2 list stops at Y=216 (0xD8); entries after it are not drawn, and a
    sprite at Y=208 (0xD0) is a normal sprite, not a terminator."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # idx0: visible sprite at y_top=1. idx1: terminator (0xD8). idx2: opaque
    # "garbage" after the terminator that must NOT be drawn.
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    vdp.vram[(_SAT_BASE + 1 * 4) & 0x1FFFF] = 0xD8     # terminator
    _write_sat_entry(vdp, 2, y=9, x=0, pat=0)          # y_top=10, would be visible
    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=6)
    _write_col_entry(vdp, sprite_idx=2, line_idx=0, color=7)
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost bit set (shared by both)

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 6           # idx0 before terminator: drawn
    assert buf[10 * 256 + 0] == 0          # idx2 after terminator: not drawn


def test_sprite_mode2_y208_is_not_a_terminator() -> None:
    """A sprite at Y=208 (0xD0) must still render in mode 2 (only 216 stops)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[9] = 0x80  # LN: 212-line mode so y_top=209 is on-screen
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=208, x=0, pat=0)   # y_top=209
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=5)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[209 * 256 + 0] == 5  # 0xD0 is a normal sprite, rendered


def test_sprite_mode2_terminator_chains_lower_priority_sprites() -> None:
    """Undocumented V9938 behaviour: the Y=216 (0xD8) terminator stops the whole
    list — the terminating sprite AND every higher-numbered (lower-priority)
    sprite after it are hidden, even though they have valid positions/colours."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # Sprites 0..2 visible (rows 1,2,3), sprite 3 = terminator, sprites 4..6
    # have valid on-screen positions but must all be suppressed by the chain.
    for idx in range(3):
        _write_sat_entry(vdp, idx, y=idx, x=0, pat=0)       # y_top = idx+1
        _write_col_entry(vdp, sprite_idx=idx, line_idx=0, color=6)
    vdp.vram[(_SAT_BASE + 3 * 4) & 0x1FFFF] = 0xD8          # terminator at sprite 3
    for idx in range(4, 7):
        _write_sat_entry(vdp, idx, y=idx + 6, x=0, pat=0)   # y_top = 11,12,13
        _write_col_entry(vdp, sprite_idx=idx, line_idx=0, color=7)
    vdp.vram[0] = 0x80  # pattern 0, row 0: leftmost bit set

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 6   # sprite 0 (before terminator): drawn
    assert buf[3 * 256 + 0] == 6   # sprite 2 (before terminator): drawn
    for line in (11, 12, 13):      # sprites 4,5,6 (after terminator): all hidden
        assert buf[line * 256 + 0] == 0


def test_sprite_mode2_sat_attr_colour_bits_ignored() -> None:
    """SAT attr bits 3:0 are not used as colour; colour table is authoritative."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # attr has colour bits = 5, but colour table says colour 9
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0, attr=0x05)
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=9)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 9


def test_sprite_mode2_colour_per_scanline() -> None:
    """Colour table provides a different colour for each sprite scanline."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, 0, line_idx=0, color=3)  # scan line 1
    _write_col_entry(vdp, 0, line_idx=1, color=5)  # scan line 2

    # Pattern rows 0 and 1 both have leftmost pixel set
    vdp.vram[0] = 0x80  # pattern 0, row 0
    vdp.vram[1] = 0x80  # pattern 0, row 1

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 3
    assert buf[2 * 256 + 0] == 5


# ---------------------------------------------------------------------------
# Sprite mode 2: early clock (EC) X shift
# ---------------------------------------------------------------------------

def test_sprite_mode2_early_clock_shifts_x_left() -> None:
    """EC bit (bit 7 of the per-line colour byte) shifts sprite X by −32."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # EC=1, X=64 → effective X = (64 − 32) = 32
    _write_sat_entry(vdp, 0, y=0, x=64, pat=0)
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, 0, 0, color=7, ec=True)
    vdp.vram[0] = 0x80  # leftmost pixel

    buf = render_frame(vdp)
    assert buf[1 * 256 + 32] == 7   # pixel at shifted position
    assert buf[1 * 256 + 64] != 7   # original position is not set


def test_sprite_mode2_no_early_clock_without_flag() -> None:
    """Without EC, X is unchanged."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=64, pat=0, attr=0x00)  # EC=0
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, 0, 0, color=7)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 64] == 7   # pixel at original position
    assert buf[1 * 256 + 32] != 7


# ---------------------------------------------------------------------------
# Sprite mode 2: OR mode pixel blending
# ---------------------------------------------------------------------------

def test_sprite_mode2_or_mode_blends_with_higher_priority() -> None:
    """OR-mode sprite colour is OR'd with the already-painted higher-priority colour."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # Sprite 0 (higher priority): colour 3 = 0b0011
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=3, or_mode=False)

    # Sprite 1 (lower priority, OR mode): colour 4 = 0b0100
    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=4, or_mode=True)

    _terminate_sat(vdp, after_idx=2)

    vdp.vram[0] = 0x80  # pattern 0 row 0: leftmost pixel

    buf = render_frame(vdp)
    # 3 | 4 = 7
    assert buf[1 * 256 + 0] == 7


def test_sprite_mode2_no_or_mode_higher_priority_wins() -> None:
    """Without OR mode, a higher-priority sprite blocks a lower-priority one."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=3, or_mode=False)

    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=5, or_mode=False)

    _terminate_sat(vdp, after_idx=2)

    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 3  # sprite 0 colour wins


def test_sprite_mode2_or_mode_sets_coincidence() -> None:
    """Coincidence is flagged when two sprites overlap, even with OR mode."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=3)

    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=4, or_mode=True)

    _terminate_sat(vdp, after_idx=2)
    vdp.vram[0] = 0x80

    render_frame(vdp)
    assert vdp.status & 0x20  # C (coincidence) bit set


# ---------------------------------------------------------------------------
# Sprite mode 2: 8-sprites-per-line limit
# ---------------------------------------------------------------------------

def test_s0_read_clears_5s_and_c_together() -> None:
    """An S#0 read returns F/5S/C once, then clears all three (mask ~0xE0)."""
    vdp = V9938()
    vdp.status = 0x80 | 0x40 | 0x20 | 0x03  # F + 5S + C + sprite index 3
    result = vdp.read_port(0x99)            # R#15 defaults to 0 → S#0
    assert result & 0x40  # 5S reported on this read
    assert result & 0x20  # C reported on this read
    assert not (vdp.status & 0x80)  # F cleared
    assert not (vdp.status & 0x40)  # 5S cleared
    assert not (vdp.status & 0x20)  # C cleared


def test_5s_and_c_do_not_persist_across_frames() -> None:
    """A frame that sets 5S/C, with no S#0 read, must not leak the flags into a
    later frame that has neither a 5th sprite nor a collision."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # Frame 1: 9 sprites on one line → 5S; two overlap → C.
    for i in range(9):
        _write_sat_entry(vdp, i, y=0, x=0, pat=0)
        _write_col_entry(vdp, i, 0, color=i + 1)
    _terminate_sat(vdp, after_idx=9)
    vdp.vram[0] = 0x80
    render_frame(vdp)
    assert vdp.status & 0x40  # 5S set this frame
    assert vdp.status & 0x20  # C set this frame

    # Frame 2: a single sprite, no 5th, no overlap — and no S#0 read between.
    for i in range(9):
        _write_sat_entry(vdp, i, y=216, x=0, pat=0)  # 0xD8 terminator-ish clear
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, 0, 0, color=1)
    render_frame(vdp)
    assert not (vdp.status & 0x40), "5S must reset at frame start"
    assert not (vdp.status & 0x20), "C must reset at frame start"


def test_sprite_mode2_9th_sprite_flag() -> None:
    """V9938 mode 2 allows 8 sprites per line; 9th triggers S#0 bit 6."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    for i in range(9):
        _write_sat_entry(vdp, i, y=0, x=i * 8, pat=0)
        _write_col_entry(vdp, i, 0, color=i + 1)
    _terminate_sat(vdp, after_idx=9)

    render_frame(vdp)

    assert vdp.status & 0x40           # 9th-sprite flag set
    assert (vdp.status & 0x1F) == 8   # index of 9th sprite (0-based = 8)


def test_sprite_mode2_9th_sprite_not_rendered() -> None:
    """The 9th sprite on a line is not rendered."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    for i in range(9):
        _write_sat_entry(vdp, i, y=0, x=i * 8, pat=0)
        _write_col_entry(vdp, i, 0, color=i + 1)
    _terminate_sat(vdp, after_idx=9)

    vdp.vram[0] = 0x80  # leftmost pixel for all patterns

    buf = render_frame(vdp)
    # Sprites 0–7 each occupy one column; sprite 8 is at x=64 but must NOT be rendered
    # Sprite 7 is at x=56, colour=8 → buf[1*256+56] == 8
    assert buf[1 * 256 + 56] == 8   # sprite 7 (last rendered)
    assert buf[1 * 256 + 64] != 9   # sprite 8 (9th, not rendered)


def test_sprite_mode2_ic_suppresses_coincidence() -> None:
    """IC bit (bit 5) on an overlapping sprite suppresses the coincidence flag."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=3)

    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=4, ic=True)  # ignore collision

    _terminate_sat(vdp, after_idx=2)
    vdp.vram[0] = 0x80

    render_frame(vdp)
    assert vdp.status & 0x20 == 0   # coincidence NOT flagged


def test_sprite_mode2_leading_cc_sprite_invisible() -> None:
    """A CC=1 sprite with higher priority than any CC=0 sprite is not drawn;
    the lower-priority CC=0 sprite shows through (not OR'd)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # Sprite 0 (highest priority) is CC=1 → leading, must be invisible.
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=4, or_mode=True)
    # Sprite 1 (lower priority) is the CC=0 base.
    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=3, or_mode=False)
    _terminate_sat(vdp, after_idx=2)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 3   # CC=0 shows; leading CC=1 neither drawn nor OR'd (not 4, not 7)


def test_sprite_mode2_lone_cc_sprite_invisible() -> None:
    """A CC=1 sprite with no CC=0 sprite on the line is entirely invisible."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=5, or_mode=True)  # lone CC=1
    _terminate_sat(vdp, after_idx=1)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] != 5   # not drawn (backdrop shows)


def test_sprite_mode2_cc_after_cc0_still_ors() -> None:
    """A CC=1 sprite that follows a CC=0 sprite still OR-combines (normal use)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _write_col_entry(vdp, 0, 0, color=3, or_mode=False)  # CC=0 base
    _write_sat_entry(vdp, 1, y=0, x=0, pat=0)
    _write_col_entry(vdp, 1, 0, color=4, or_mode=True)   # CC=1 overlay
    _terminate_sat(vdp, after_idx=2)
    vdp.vram[0] = 0x80

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 7   # 3 | 4


def test_sprite_mode2_screen8_uses_fixed_sprite_palette() -> None:
    """SCREEN 8 sprites use the fixed GRAPHIC7 sprite palette, not the
    programmable palette. Colour 8 → fixed GRB332 byte 0x9D (from 0x472),
    NOT the programmable-palette-derived 0x3C."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x0E  # SCREEN 8 (G7): M3+M4+M5
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, 0, 0, color=8)
    vdp.vram[0] = 0x80  # sprite pattern row 0, leftmost pixel

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 0x9D   # fixed GRAPHIC7 sprite palette entry 8
    assert buf[1 * 256 + 0] != 0x3C   # not the programmable-palette result


# ---------------------------------------------------------------------------
# Sprite mode 2: 16×16 sprites
# ---------------------------------------------------------------------------

def _set_16x16(vdp: V9938) -> None:
    vdp.regs[1] |= 0x02  # SI bit: 16×16 sprites


def test_sprite_mode2_16x16_quadrant_layout() -> None:
    """16×16 sprite: N=top-left, N+1=bottom-left, N+2=top-right, N+3=bottom-right."""
    vdp = V9938()
    _set_screen5(vdp)
    _set_16x16(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00  # SPG at 0x0000

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)  # pat & 0xFC = 0
    _terminate_sat(vdp, after_idx=1)

    # Top half = sprite lines 0–7 (screen line 1..8); bottom half = lines 8–15.
    vdp.vram[0 * 8 + 0] = 0x80  # char 0 (top-left)  row0: px 0
    vdp.vram[2 * 8 + 0] = 0x01  # char 2 (top-right) row0: px 7 → sprite px 15
    vdp.vram[1 * 8 + 0] = 0x80  # char 1 (bottom-left)  row0: px 0
    vdp.vram[3 * 8 + 0] = 0x01  # char 3 (bottom-right) row0: px 7 → sprite px 15

    for ln in range(16):
        _write_col_entry(vdp, 0, ln, color=6)

    buf = render_frame(vdp)
    assert buf[1 * 256 + 0] == 6    # top-left  px 0
    assert buf[1 * 256 + 15] == 6   # top-right px 15
    assert buf[9 * 256 + 0] == 6    # bottom-left  px 0  (screen line 9 = sprite row 8)
    assert buf[9 * 256 + 15] == 6   # bottom-right px 15


def test_sprite_mode2_16x16_right_edge_clips_no_wrap() -> None:
    """A 16×16 sprite past the right edge clips; pixels do not wrap to the left."""
    vdp = V9938()
    _set_screen5(vdp)
    _set_16x16(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=248, pat=0)
    _terminate_sat(vdp, after_idx=1)

    vdp.vram[0 * 8 + 0] = 0xFF  # top-left  row0: px 0–7  → screen 248–255
    vdp.vram[2 * 8 + 0] = 0xFF  # top-right row0: px 8–15 → screen 256–263 (off-screen)
    for ln in range(16):
        _write_col_entry(vdp, 0, ln, color=7)

    buf = render_frame(vdp)
    assert buf[1 * 256 + 255] == 7   # last on-screen pixel drawn
    assert buf[1 * 256 + 0] != 7     # right half clipped, not wrapped to the left


def test_sprite_mode2_16x16_early_clock_off_left_edge() -> None:
    """EC pushing a 16×16 sprite fully off the left edge draws nothing (no wrap)."""
    vdp = V9938()
    _set_screen5(vdp)
    _set_16x16(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)  # x=0, EC → -32 → fully off-screen
    _terminate_sat(vdp, after_idx=1)

    vdp.vram[0 * 8 + 0] = 0xFF
    vdp.vram[2 * 8 + 0] = 0xFF
    for ln in range(16):
        _write_col_entry(vdp, 0, ln, color=7, ec=True)

    buf = render_frame(vdp)
    assert 7 not in buf[1 * 256:2 * 256]  # nothing drawn on the line, no wrap


# ---------------------------------------------------------------------------
# Sprite mode 2 in the 512-wide modes (SCREEN 6/7): horizontal doubling
# ---------------------------------------------------------------------------

def test_sprite_mode2_512_mode_doubles_horizontally() -> None:
    """In a 512-wide mode the 256-dot sprite plane is doubled: sprite dot at X
    covers screen columns 2X and 2X+1, in a 512-stride buffer."""
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x08  # SCREEN 6 (G5): 512 wide
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    _write_sat_entry(vdp, 0, y=0, x=10, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, 0, 0, color=7)
    vdp.vram[0] = 0x80  # pattern row 0, leftmost dot

    buf = render_frame(vdp)
    assert len(buf) == 512 * vdp.display_height
    # sprite dot at sx=10 → doubled to columns 20 and 21 on scan line 1
    assert buf[1 * 512 + 20] == 7
    assert buf[1 * 512 + 21] == 7
    assert buf[1 * 512 + 19] != 7


# ---------------------------------------------------------------------------
# SPD (Sprite Disable) — R#8 bit 2
# ---------------------------------------------------------------------------

def test_spd_bit_disables_all_sprites() -> None:
    """R#8 bit 2 (SPD=1) disables sprite rendering entirely."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00
    vdp.regs[8] = 0x04  # SPD=1 (bit 2)

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, 0, 0, color=7)
    vdp.vram[0] = 0xFF  # all pixels set in pattern row 0

    buf = render_frame(vdp)

    # Sprite should not appear on line 1 (y_top=1)
    assert all(buf[1 * 256 + x] != 7 for x in range(8)), \
        "SPD=1: sprites must not render"


def test_spd_bit_clear_allows_sprites() -> None:
    """R#8 bit 2 = 0 (SPD=0) allows normal sprite rendering."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00
    vdp.regs[8] = 0x00  # SPD=0

    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, 0, 0, color=7)
    vdp.vram[0] = 0x80  # bit 7 only → pixel at x=0

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] == 7, "SPD=0: sprite must render normally"




# ---------------------------------------------------------------------------
# Sprite multiplexer / "sprite doubler": mid-screen SAT base (R#5) switch
# ---------------------------------------------------------------------------

def test_sprite_doubler_mid_screen_sat_switch() -> None:
    """A mid-frame R#5 change switches the sprite attribute table base, letting a
    game show a second set of 32 sprites in the lower screen region (e.g. Space
    Manbow). Sprites from the second SAT must appear in the lower region; the old
    single-SAT-per-frame pass dropped them."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[6] = 0x00       # sprite pattern generator at 0x0000
    vdp.vram[0] = 0xFF       # pattern 0, row 0: 8 opaque pixels

    sat_a = 0x3800           # R#5 = 0x70
    sat_b = 0x3C00           # R#5 = 0x78

    def put_sprite(base: int, y: int, color: int) -> None:
        vdp.vram[base] = y          # sprite 0 Y
        vdp.vram[base + 1] = 0      # X
        vdp.vram[base + 2] = 0      # pattern 0
        vdp.vram[base + 3] = 0
        vdp.vram[base + 4] = 0xD8   # terminator
        vdp.vram[(base - 0x200) + 0] = color & 0x0F  # sprite 0, line 0 colour

    put_sprite(sat_a, y=9, color=6)    # appears at screen line 10 (upper region)
    put_sprite(sat_b, y=149, color=7)  # appears at screen line 150 (lower region)

    # Banded frame: start with SAT-A, switch R#5 to SAT-B at line 96 (mid screen).
    vdp._frame_start_regs = vdp.regs[:]
    vdp._frame_start_regs[5] = 0x70
    vdp.regs[5] = 0x78
    vdp._frame_start_palette = vdp.palette[:]
    vdp._reg_write_log = [_RegChange(95, 5, 0x78)]   # effective line 96

    buf = render_frame(vdp)

    assert buf[10 * 256 + 0] == 6, "upper region shows SAT-A sprite"
    assert buf[150 * 256 + 0] == 7, "lower region shows SAT-B sprite (doubler)"


def test_sprite_pass_split_on_vscroll_no_ghost() -> None:
    """A split screen whose regions carry different R#23 (vertical scroll) but
    share one SAT must position each region's sprites with that region's vscroll.
    Sprites belonging to the main region (main vscroll) must not leak into the
    top region, which scrolls differently (the Space Manbow top-border ghost)."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00       # SPG at 0x0000
    vdp.vram[0] = 0xFF       # pattern 0, row 0: 8 opaque pixels

    # Sprite 0 → screen line 5 under the MAIN vscroll (0); under the TOP vscroll
    # (0x28) it maps outside the top region, so real hardware never shows it in
    # lines 0-31. The old single-vscroll pass drew it at line 5 (the ghost).
    _write_sat_entry(vdp, 0, y=4, x=0, pat=0)     # y_top = 5
    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=6)
    # Sprite 1 → screen line 100, a legitimate main-region sprite.
    _write_sat_entry(vdp, 1, y=99, x=0, pat=0)    # y_top = 100
    _write_col_entry(vdp, sprite_idx=1, line_idx=0, color=7)
    _terminate_sat(vdp, after_idx=2)

    # Top region lines 0-30 use vscroll 0x28; R#23 → 0 from line 31 on.
    vdp._frame_start_regs = vdp.regs[:]
    vdp._frame_start_regs[23] = 0x28
    vdp.regs[23] = 0x00
    vdp._frame_start_palette = vdp.palette[:]
    vdp._reg_write_log = [_RegChange(30, 23, 0x00)]   # effective line 31

    buf = render_frame(vdp)

    assert buf[5 * 256 + 0] == 0, "no ghost sprite in the top region"
    assert buf[100 * 256 + 0] == 7, "main-region sprite still drawn"


# ---------------------------------------------------------------------------
# SCREEN 4 (GRAPHIC 3): G2-style tile background + sprite mode 2 composited
# on top. Guards that sprites are drawn OVER the background plane in SCREEN 4.
# ---------------------------------------------------------------------------

def test_screen4_sprite_drawn_over_g3_background() -> None:
    vdp = V9938()
    _enable(vdp)
    vdp.regs[0] = 0x04            # SCREEN 4 (G3): M4=1, M3=0, M5=0
    vdp.regs[5] = _SAT_R5         # SAT 0x3800, colour table 0x3600
    vdp.regs[6] = 0x00            # sprite pattern generator at 0x0000
    # G2/G3 background tables (kept clear of SAT/colour/SPG regions)
    vdp.regs[2] = 0x04            # name base 0x1000
    vdp.regs[4] = 0x04            # pattern base 0x2000
    vdp.regs[10] = 0x02           # colour base 0x8000
    vdp.regs[3] = 0x00
    name_base, pat_base, col_base = 0x1000, 0x2000, 0x8000

    # Background: every cell = tile 0; tile 0 is a solid colour-9 block (first band)
    for n in range(768):
        vdp.vram[name_base + n] = 0x00
    for r in range(8):
        vdp.vram[pat_base + r] = 0xFF     # all pixels set
        vdp.vram[col_base + r] = 0x99     # fg=9, bg=9 → solid colour 9

    # Sprite 0 at (0,0), pattern 0 (row 0 = 8 solid pixels), per-line colour 6
    _write_sat_entry(vdp, 0, y=0, x=0, pat=0)
    _terminate_sat(vdp, after_idx=1)
    _write_col_entry(vdp, sprite_idx=0, line_idx=0, color=6)
    vdp.vram[0] = 0xFF            # sprite pattern 0, row 0: 8 pixels set

    buf = render_frame(vdp)

    # Background shows the tile colour where no sprite covers it
    assert buf[5 * 256 + 100] == 9, "G3 background tile colour"
    assert buf[1 * 256 + 20] == 9, "background beside the sprite"
    # Sprite (y=0 → y_top=1, scanline 1, cols 0-7) is composited OVER the tile
    assert buf[1 * 256 + 0] == 6, "sprite pixel must overwrite the background"
    assert buf[1 * 256 + 7] == 6, "sprite spans 8 px over the background"
