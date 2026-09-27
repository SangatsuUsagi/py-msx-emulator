"""A V9938 frame whose display width changes mid-frame.

Covers openspec vdp-banded-renderer "Per-band display_height and
display_width": the frame is as wide as its widest band, a narrower band's
rows are doubled pixel by pixel, sprite passes never span a width change, and
the screenshot helper reports the rendered frame's width.
"""
from __future__ import annotations

import random

from msx.screenshot import render_current_rgb24
from msx.vdp import v9938_renderer
from msx.vdp._geometry import OUTPUT_H
from msx.vdp._v9938_defs import _RegChange
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame

_G4 = 0x06  # R#0 M3+M4: GRAPHIC 4, 256 dots
_G6 = 0x0A  # R#0 M3+M5: GRAPHIC 6, 512 dots
_CHANGE_LINE = 100  # a write logged at line 100 takes effect from line 101
_PAD = 10  # 192-line frames are centred in the 212-line output


def _vdp(r0: int, sprites: bool = False) -> V9938:
    vdp = V9938()
    rnd = random.Random(7)
    for i in range(len(vdp.vram)):
        vdp.vram[i] = rnd.randrange(256)
    vdp.regs[0] = r0
    vdp.regs[1] = 0x40  # display enabled
    vdp.regs[2] = 0x1F
    vdp.regs[8] = 0x08 if sprites else 0x0A  # SPD set unless sprites wanted
    return vdp


def _single(r0: int, sprites: bool = False) -> bytearray:
    return render_frame(_vdp(r0, sprites))


def _mixed(first: int, second: int, sprites: bool = False) -> V9938:
    vdp = _vdp(first, sprites)
    vdp.begin_scanline(0)
    vdp.regs[0] = second
    vdp._reg_write_log.append(_RegChange(_CHANGE_LINE, 0, second))
    return vdp


def _rows(buf: bytearray, width: int, lo: int, hi: int) -> list[bytes]:
    return [bytes(buf[y * width:(y + 1) * width]) for y in range(lo, hi)]


def _doubled(rows: list[bytes]) -> list[bytes]:
    out = []
    for row in rows:
        wide = bytearray(len(row) * 2)
        wide[0::2] = row
        wide[1::2] = row
        out.append(bytes(wide))
    return out


_SPLIT = _PAD + _CHANGE_LINE + 1  # first output row of the second band
_END = _PAD + 192


def test_wide_to_narrow_switch_renders_a_512_wide_frame() -> None:
    out = render_frame(_mixed(_G6, _G4))

    assert len(out) == 512 * OUTPUT_H
    assert _rows(out, 512, _PAD, _SPLIT) == _rows(_single(_G6), 512, _PAD, _SPLIT)
    assert _rows(out, 512, _SPLIT, _END) == _doubled(_rows(_single(_G4), 256, _SPLIT, _END))


def test_narrow_to_wide_switch_keeps_each_bands_rows() -> None:
    out = render_frame(_mixed(_G4, _G6))

    assert len(out) == 512 * OUTPUT_H
    assert _rows(out, 512, _PAD, _SPLIT) == _doubled(_rows(_single(_G4), 256, _PAD, _SPLIT))
    assert _rows(out, 512, _SPLIT, _END) == _rows(_single(_G6), 512, _SPLIT, _END)


def test_rgb24_conversion_of_a_mixed_frame_has_the_frame_width() -> None:
    vdp = _mixed(_G6, _G4)
    rgb = vdp.to_rgb24(render_frame(vdp))
    assert len(rgb) == 512 * OUTPUT_H * 3


def test_sprite_passes_do_not_span_a_width_change(monkeypatch) -> None:
    vdp = _mixed(_G6, _G4, sprites=True)
    calls: list[tuple[int, int, int]] = []
    real = v9938_renderer._render_sprites_for_mode

    def spy(vdp_arg, regs, st, buf, y_start, y_end, *rest):
        calls.append((regs[0], y_start, y_end))
        real(vdp_arg, regs, st, buf, y_start, y_end, *rest)

    monkeypatch.setattr(v9938_renderer, "_render_sprites_for_mode", spy)
    render_frame(vdp)

    assert [(r0, y0) for r0, y0, _ in calls] == [(_G6, 0), (_G4, _CHANGE_LINE + 1)]
    assert calls[0][2] == _CHANGE_LINE + 1


def test_screenshot_reports_the_rendered_frames_width() -> None:
    vdp = _mixed(_G6, _G4)  # live registers select the 256-dot GRAPHIC 4
    rgb, width, height = render_current_rgb24(vdp)
    assert (width, height) == (512, OUTPUT_H)
    assert len(rgb) == width * height * 3


def test_frame_without_a_width_change_is_unchanged() -> None:
    vdp = _mixed(_G4, _G4)
    vdp.regs[7] = 0x05
    vdp._reg_write_log.append(_RegChange(50, 7, 0x05))
    out = render_frame(vdp)
    assert len(out) == 256 * OUTPUT_H


# ---------------------------------------------------------------------------
# Width runs, SAT segments and the frame-level passes (allium/v9938-renderer
# ComposeBandWidthRuns, MergeSpriteAttributeTableSegments, RenderFramePixels,
# ApplyDisplayAdjust)
# ---------------------------------------------------------------------------

def _banded(r0: int, changes: list[tuple[int, int, int]]) -> V9938:
    """A sprite-enabled frame starting in mode `r0` with logged register writes
    (line, reg, value); each takes effect from line + 1."""
    vdp = _vdp(r0, sprites=True)
    vdp.begin_scanline(0)
    for line, reg, value in changes:
        vdp.regs[reg] = value
        vdp._reg_write_log.append(_RegChange(line, reg, value))
    return vdp


def _spy_sprite_passes(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    real = v9938_renderer._render_sprites_for_mode

    def spy(vdp_arg, regs, st, buf, y_start, y_end, row_vscroll=None, row_spd=None):
        calls.append((list(regs), y_start, y_end, st, row_vscroll, row_spd))
        real(vdp_arg, regs, st, buf, y_start, y_end, row_vscroll, row_spd)

    monkeypatch.setattr(v9938_renderer, "_render_sprites_for_mode", spy)
    return calls


def _band(r0: int, y0: int, y1: int) -> v9938_renderer._Band:
    regs = [0] * 28
    regs[0] = r0
    return v9938_renderer._Band(y0, y1, regs, [0] * 16)


def test_width_runs_are_maximal_runs_of_one_width() -> None:
    bands = [
        _band(_G6, 0, 40), _band(_G6, 40, 80), _band(_G4, 80, 120),
        _band(_G4, 120, 160), _band(_G6, 160, 192),
    ]
    runs = v9938_renderer._width_runs(bands)
    assert [[(b.y0, b.y1) for b in run] for run in runs] == [
        [(0, 40), (40, 80)], [(80, 120), (120, 160)], [(160, 192)],
    ]


def test_bands_are_named_tuples_carrying_their_own_snapshot() -> None:
    vdp = _mixed(_G6, _G4)
    bands = v9938_renderer._build_bands(vdp)
    assert v9938_renderer._Band._fields == ("y0", "y1", "regs", "palette")
    assert [(b.y0, b.y1, b.regs[0]) for b in bands] == [
        (0, _CHANGE_LINE + 1, _G6), (_CHANGE_LINE + 1, 192, _G4),
    ]
    for band in bands:
        assert band.regs is not vdp.regs
        assert band.palette is not vdp.palette
        assert len(band.palette) == len(vdp.palette)


def test_narrow_run_sprites_are_doubled_with_their_background() -> None:
    out = render_frame(_mixed(_G6, _G4, sprites=True))
    with_sprites = _rows(_single(_G4, sprites=True), 256, _SPLIT, _END)
    without_sprites = _rows(_single(_G4), 256, _SPLIT, _END)
    assert with_sprites != without_sprites  # the lower band does draw sprites
    assert _rows(out, 512, _SPLIT, _END) == _doubled(with_sprites)
    assert _rows(out, 512, _PAD, _SPLIT) == _rows(_single(_G6, sprites=True), 512, _PAD, _SPLIT)


def test_sprite_schedule_is_built_once_over_all_runs(monkeypatch) -> None:
    vdp = _mixed(_G6, _G4, sprites=True)
    vdp.regs[23] = 0x10
    vdp._reg_write_log.append(_RegChange(_CHANGE_LINE, 23, 0x10))
    calls = _spy_sprite_passes(monkeypatch)
    render_frame(vdp)

    assert len(calls) == 2
    vscroll, spd = calls[0][4], calls[0][5]
    assert all(c[4] is vscroll and c[5] is spd for c in calls)
    assert len(vscroll) == len(spd) == 192
    assert vscroll[_CHANGE_LINE] == 0 and vscroll[_CHANGE_LINE + 1] == 0x10


def test_render_status_accumulates_across_runs(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    render_frame(_mixed(_G6, _G4, sprites=True))
    assert len(calls) == 2
    assert calls[0][3] is calls[1][3]


def test_single_run_banded_frame_has_one_sprite_pass_over_the_frame(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    out = render_frame(_banded(_G4, [(_CHANGE_LINE, 7, 0x05)]))
    assert len(out) == 256 * OUTPUT_H
    assert [(y0, y1) for _, y0, y1, *_ in calls] == [(0, 192)]


def test_sat_segment_uses_the_tallest_bands_registers(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    # Bands [0, 51), [51, 101), [101, 192): the third is tallest.
    render_frame(_banded(_G4, [(50, 7, 0x01), (100, 7, 0x02)]))
    assert [(regs[7], y0, y1) for regs, y0, y1, *_ in calls] == [(0x02, 0, 192)]


def test_sat_segment_keeps_the_first_of_equally_tall_bands(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    # Bands [0, 64), [64, 128), [128, 192): all 64 lines.
    render_frame(_banded(_G4, [(63, 7, 0x01), (127, 7, 0x02)]))
    assert [(regs[7], y0, y1) for regs, y0, y1, *_ in calls] == [(0x00, 0, 192)]


def test_sat_key_ignores_r11_above_its_low_two_bits(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    render_frame(_banded(_G4, [(95, 11, 0x04)]))
    assert [(y0, y1) for _, y0, y1, *_ in calls] == [(0, 192)]


def test_sat_switch_within_one_run_clips_each_segment(monkeypatch) -> None:
    calls = _spy_sprite_passes(monkeypatch)
    render_frame(_banded(_G4, [(95, 11, 0x01)]))
    assert [(regs[11], y0, y1) for regs, y0, y1, *_ in calls] == [(0x00, 0, 96), (0x01, 96, 192)]


def test_mixed_frame_padding_rows_span_the_frame_width() -> None:
    vdp = _vdp(_G6)
    vdp.regs[7] = 0x03
    vdp.begin_scanline(0)
    vdp.regs[0] = _G4
    vdp._reg_write_log.append(_RegChange(_CHANGE_LINE, 0, _G4))
    out = render_frame(vdp)

    assert len(out) == 512 * OUTPUT_H
    border = bytes([0x03]) * 512
    assert _rows(out, 512, 0, _PAD) == [border] * _PAD
    assert _rows(out, 512, _END, OUTPUT_H) == [border] * (OUTPUT_H - _END)


def test_display_adjust_uses_the_buffer_width_not_the_live_mode() -> None:
    vdp = V9938()
    vdp.regs[0] = _G4  # live registers select a 256-dot mode
    vdp.regs[18] = 0x0F  # +1 dot
    w = 512
    buf = bytearray(w * 192)
    buf[20 * w + 10] = 5
    out = v9938_renderer._apply_display_adjust(vdp, buf)
    assert len(out) == w * 192
    assert out[20 * w + 12] == 5  # 2 buffer pixels per dot in a 512-wide frame
    assert out[20 * w + 10] == 0


def test_display_adjust_shifts_a_doubled_narrow_band_by_two_pixels_per_dot() -> None:
    def frame(r18: int) -> bytearray:
        vdp = _vdp(_G6)
        vdp.regs[18] = r18
        vdp.begin_scanline(0)
        vdp.regs[0] = _G4
        vdp._reg_write_log.append(_RegChange(_CHANGE_LINE, 0, _G4))
        return render_frame(vdp)

    plain = _rows(frame(0x00), 512, _SPLIT, _END)
    shifted = _rows(frame(0x0F), 512, _SPLIT, _END)
    for p, s in zip(plain, shifted):
        assert s[2:] == p[:-2]
        assert s[:2] == b"\x00\x00"
