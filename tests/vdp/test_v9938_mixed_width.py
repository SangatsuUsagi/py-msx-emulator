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
