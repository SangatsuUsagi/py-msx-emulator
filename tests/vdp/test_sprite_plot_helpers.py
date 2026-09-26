"""Unit tests for the explicit-argument sprite plot helpers.

renderer._plot_sprite_pixel (TMS9918A), v9938_renderer._plot_mode1_row and
v9938_renderer._plot_mode2_row replaced per-call closures; these pin the
contract the full-frame sprite tests exercise indirectly: clipping at x < 0
and x >= 256 (no wrap), coincidence reporting, first-opaque-claim priority,
MAG, 512-wide doubling, CC (OR) mode, and the first colliding x.
"""
from __future__ import annotations

from msx.vdp.renderer import _plot_sprite_pixel
from msx.vdp.v9938_renderer import _SPRITE_CLAIM, _plot_mode1_row, _plot_mode2_row

_W = 256


def _planes(width: int = _W, lines: int = 2) -> tuple[bytearray, bytearray, bytearray]:
    return bytearray(width * lines), bytearray(width * lines), bytearray(width * lines)


# ---------------------------------------------------------------------------
# TMS9918A _plot_sprite_pixel
# ---------------------------------------------------------------------------

def test_tms_plot_clips_both_edges_without_touching_anything() -> None:
    buf, painted, touched = _planes()
    row = _W                                  # line 1
    assert _plot_sprite_pixel(buf, painted, touched, row, -1, 5) is False
    assert _plot_sprite_pixel(buf, painted, touched, row, _W, 5) is False
    assert not any(buf) and not any(painted) and not any(touched)


def test_tms_plot_draws_edges_and_reports_coincidence_on_second_touch() -> None:
    buf, painted, touched = _planes()
    row = _W
    assert _plot_sprite_pixel(buf, painted, touched, row, 0, 5) is False
    assert _plot_sprite_pixel(buf, painted, touched, row, _W - 1, 5) is False
    assert buf[row] == 5 and buf[row + _W - 1] == 5
    assert _plot_sprite_pixel(buf, painted, touched, row, 0, 9) is True
    assert buf[row] == 5                      # first opaque claim keeps the pixel


def test_tms_transparent_pixel_collides_but_does_not_claim() -> None:
    buf, painted, touched = _planes()
    assert _plot_sprite_pixel(buf, painted, touched, 0, 3, 0) is False
    assert buf[3] == 0 and not painted[3] and touched[3]
    assert _plot_sprite_pixel(buf, painted, touched, 0, 3, 7) is True
    assert buf[3] == 7                        # lower-priority opaque shows through


# ---------------------------------------------------------------------------
# V9938 _plot_mode1_row
# ---------------------------------------------------------------------------

def _row1(
    pixels: bytes, x_pos: int, *, scale: int = 1, color: int = 5,
    planes: tuple[bytearray, bytearray, bytearray] | None = None,
) -> tuple[int, bytearray, bytearray, bytearray]:
    buf, painted, touched = planes if planes is not None else _planes(lines=1)
    hit = _plot_mode1_row(buf, painted, touched, pixels, x_pos, scale, 0, color=color)
    return hit, buf, painted, touched


def test_mode1_row_clips_both_edges_without_wrap() -> None:
    _, buf, _, touched = _row1(bytes([1, 1, 1]), -2)
    assert [buf[i] for i in range(2)] == [5, 0]
    assert touched[0] and not touched[1] and not any(touched[_W - 4:])
    _, buf, _, _ = _row1(bytes([1, 1, 1]), _W - 1)
    assert buf[_W - 1] == 5 and not any(buf[:4])


def test_mode1_row_mag_doubles_each_dot() -> None:
    _, buf, _, _ = _row1(bytes([1, 0, 1]), 10, scale=2)
    assert [i for i in range(_W) if buf[i]] == [10, 11, 14, 15]


def test_mode1_row_colour_0_claims_the_pixel() -> None:
    # Colour 0 only reaches the helper when TP makes it opaque: it claims.
    planes = _planes(lines=1)
    planes[0][4] = 0xEE
    hit, buf, painted, _ = _row1(bytes([1]), 4, color=0, planes=planes)
    assert hit == -1 and buf[4] == 0 and painted[4]
    hit, buf, _, _ = _row1(bytes([1]), 4, color=9, planes=planes)
    assert hit == 4 and buf[4] == 0


def test_mode1_row_reports_the_first_colliding_x() -> None:
    planes = _planes(lines=1)
    planes[2][13] = 1
    planes[2][15] = 1
    hit, _, _, _ = _row1(bytes([1] * 8), 10, planes=planes)
    assert hit == 13


# ---------------------------------------------------------------------------
# V9938 _plot_mode2_row
# ---------------------------------------------------------------------------

def _row(
    pixels: bytes, x_pos: int, *, scale: int = 1, screen_scale: int = 1,
    collides: bool = True, or_mode: bool = False, color: int = 7,
    sprite_buf: bytearray | None = None, touched: bytearray | None = None,
) -> tuple[int, bytearray, bytearray, list[int]]:
    width = _W * screen_scale
    if sprite_buf is None:
        sprite_buf = bytearray(width)
    if touched is None:
        touched = bytearray(width)
    drawn: list[int] = []
    hit = _plot_mode2_row(
        sprite_buf, touched, drawn, pixels, x_pos, scale, 0, screen_scale,
        collides=collides, or_mode=or_mode, color=color,
    )
    return hit, sprite_buf, touched, drawn


def test_mode2_row_clips_left_and_right_without_wrap() -> None:
    _, left, _, _ = _row(bytes([1, 1, 1, 1]), -2)
    assert [left[i] for i in range(3)] == [7 | _SPRITE_CLAIM, 7 | _SPRITE_CLAIM, 0]
    assert not any(left[_W - 4:])

    _, right, _, _ = _row(bytes([1, 1, 1, 1]), _W - 2)
    assert right[_W - 2] and right[_W - 1]
    assert not any(right[:4])


def test_mode2_row_mag_doubles_each_dot() -> None:
    _, buf, _, drawn = _row(bytes([1, 0, 1]), 10, scale=2)
    lit = [i for i in range(_W) if buf[i]]
    assert lit == [10, 11, 14, 15]
    assert sorted(drawn) == lit


def test_mode2_row_mag_clips_half_a_dot_at_the_right_edge() -> None:
    _, buf, _, _ = _row(bytes([1]), _W - 1, scale=2)
    assert buf[_W - 1] and not buf[0]


def test_mode2_row_512_wide_covers_both_output_pixels() -> None:
    _, buf, touched, drawn = _row(bytes([1]), 10, screen_scale=2)
    assert [i for i in range(2 * _W) if buf[i]] == [20, 21]
    assert touched[20] and touched[21]
    assert sorted(drawn) == [20, 21]


def test_mode2_row_512_wide_clips_at_sprite_plane_x_256() -> None:
    _, buf, _, _ = _row(bytes([1, 1]), _W - 1, screen_scale=2)
    assert [i for i in range(2 * _W) if buf[i]] == [2 * _W - 2, 2 * _W - 1]


def test_mode2_row_reports_first_colliding_sprite_plane_x() -> None:
    touched = bytearray(_W)
    touched[13] = 1
    touched[15] = 1
    hit, _, _, _ = _row(bytes([1] * 8), 10, touched=touched)
    assert hit == 13


def test_mode2_row_512_wide_reports_sprite_plane_x_not_screen_x() -> None:
    touched = bytearray(2 * _W)
    touched[2 * 12 + 1] = 1                   # right half of sprite-plane dot 12
    hit, _, _, _ = _row(bytes([1] * 4), 10, screen_scale=2, touched=touched)
    assert hit == 12


def test_mode2_row_no_collision_returns_minus_one() -> None:
    hit, _, _, _ = _row(bytes([1, 1]), 0)
    assert hit == -1


def test_mode2_row_non_colliding_sprite_neither_reports_nor_marks() -> None:
    touched = bytearray(_W)
    touched[5] = 1
    hit, _, after, _ = _row(bytes([1, 1]), 5, collides=False, touched=touched)
    assert hit == -1
    assert after[6] == 0


def test_mode2_row_or_mode_ors_into_a_claimed_pixel() -> None:
    sprite_buf = bytearray(_W)
    sprite_buf[3] = 0x04 | _SPRITE_CLAIM
    _, buf, _, drawn = _row(bytes([1]), 3, or_mode=True, color=0x03, sprite_buf=sprite_buf)
    assert buf[3] == 0x07 | _SPRITE_CLAIM
    assert drawn == []                        # already listed by its first claim


def test_mode2_row_without_or_mode_first_claim_wins() -> None:
    sprite_buf = bytearray(_W)
    sprite_buf[3] = 0x04 | _SPRITE_CLAIM
    _, buf, _, _ = _row(bytes([1]), 3, color=0x03, sprite_buf=sprite_buf)
    assert buf[3] == 0x04 | _SPRITE_CLAIM


def test_mode2_row_colour_0_still_claims() -> None:
    _, buf, _, drawn = _row(bytes([1]), 3, color=0)
    assert buf[3] == _SPRITE_CLAIM
    assert drawn == [3]
