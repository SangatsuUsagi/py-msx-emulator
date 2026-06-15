"""Tests for V9938 sprite mode 2 (SCREEN 4–8)."""
from msx.vdp.v9938 import V9938
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
# V9938 sprite mode 2: align attr_reg low 9 bits → colour base = 0x3800,
# SAT = colour_base + 0x200 = 0x3A00.
_SAT_R5 = 0x70
_COL_BASE = 0x3800
_SAT_BASE = _COL_BASE + 0x200


def _write_sat_entry(
    vdp: V9938, idx: int, y: int, x: int, pat: int, attr: int = 0
) -> None:
    base = (_SAT_BASE + idx * 4) & 0x1FFFF
    vdp.vram[base]     = y & 0xFF
    vdp.vram[base + 1] = x & 0xFF
    vdp.vram[base + 2] = pat & 0xFF
    vdp.vram[base + 3] = attr & 0xFF


def _write_col_entry(
    vdp: V9938, sprite_idx: int, line_idx: int, color: int, or_mode: bool = False
) -> None:
    entry = (color & 0x0F) | (0x20 if or_mode else 0)
    vdp.vram[(_COL_BASE + sprite_idx * 16 + line_idx) & 0x1FFFF] = entry


def _terminate_sat(vdp: V9938, after_idx: int) -> None:
    vdp.vram[(_SAT_BASE + after_idx * 4) & 0x1FFFF] = 0xD0


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
    """EC bit (bit 7 of SAT attr) shifts sprite X by −32."""
    vdp = V9938()
    _set_screen5(vdp)
    vdp.regs[5] = _SAT_R5
    vdp.regs[6] = 0x00

    # EC=1, X=64 → effective X = (64 − 32) = 32
    _write_sat_entry(vdp, 0, y=0, x=64, pat=0, attr=0x80)
    _terminate_sat(vdp, after_idx=1)

    _write_col_entry(vdp, 0, 0, color=7)
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
