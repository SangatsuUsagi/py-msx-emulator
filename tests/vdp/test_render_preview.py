"""VDP.render_preview() / V9938.render_preview(): a frame without side effects.

Covers openspec remote-socket-rpc "Screenshot capture method" (the VDP's own
render_preview() restores every field its renderer can change) directly on
each VDP; tests/test_screenshot_render.py covers the render_current_rgb24
wrapper. Each test first shows the plain render_frame() does change the
state, so the preview assertions are not vacuous.
"""
from __future__ import annotations

from msx.vdp._geometry import OUTPUT_H
from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
from msx.vdp.vdp import VDP


def _tms_with_5s_and_collision() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x60               # BL=1, IE0=1
    vdp.regs[5] = 0x20               # SAT at 0x1000
    for i in range(5):               # five sprites on line 1, overlapping
        vdp.vram[0x1000 + i * 4] = 0
        vdp.vram[0x1000 + i * 4 + 1] = i * 4
        vdp.vram[0x1000 + i * 4 + 3] = 0x0F
    vdp.vram[0x1000 + 5 * 4] = 0xD0
    vdp.vram[0] = 0xFF               # sprite pattern 0 row 0
    return vdp


def _v9938_with_9s_and_collision() -> V9938:
    vdp = V9938()
    vdp.regs[0] = 0x06               # GRAPHIC4, sprite mode 2
    vdp.regs[1] = 0x60               # BL=1, IE0=1
    vdp.regs[5] = 0x77
    sat = 0x77 << 7
    for i in range(9):               # nine overlapping sprites on line 1
        vdp.vram[sat + i * 4] = 0
        vdp.vram[sat + i * 4 + 1] = i
    vdp.vram[sat + 9 * 4] = 216
    col = (sat & ~0x3FF) & 0x1FFFF
    for i in range(9):
        vdp.vram[col + i * 16] = 0x0F
    vdp.vram[0] = 0xFF
    return vdp


def test_tms_render_frame_would_change_s0() -> None:
    vdp = _tms_with_5s_and_collision()
    render_frame(vdp)
    assert vdp.status & 0x60 == 0x60           # 5S and C set by a plain render


def test_tms_render_preview_leaves_state_and_returns_a_frame() -> None:
    vdp = _tms_with_5s_and_collision()
    vdp.status = 0x00

    buf = vdp.render_preview()

    assert len(buf) == 256 * OUTPUT_H
    assert vdp.status == 0x00
    assert vdp.irq is False
    assert vdp.frame_count == 0


def test_tms_render_preview_keeps_a_pending_vblank() -> None:
    vdp = _tms_with_5s_and_collision()
    vdp.begin_vblank()                         # F set, IE0 set -> irq high
    before = vdp.status

    vdp.render_preview()

    assert vdp.status == before
    assert vdp.irq is True


def test_v9938_render_frame_would_change_sprite_status() -> None:
    vdp = _v9938_with_9s_and_collision()
    vdp.frame_count = 1
    render_frame_v9938(vdp)
    assert vdp.status & 0x60 == 0x60           # 5S (9th sprite) and C
    assert vdp._status2 & 0x02                 # EO follows odd frame


def test_v9938_render_preview_leaves_state_and_returns_a_frame() -> None:
    vdp = _v9938_with_9s_and_collision()
    vdp.status = 0x00
    vdp.collision_x = 0x12
    vdp.collision_y = 0x34
    vdp._status2 = 0x0C
    vdp.frame_count = 1

    buf = vdp.render_preview()

    assert len(buf) == vdp.display_width * OUTPUT_H
    assert any(buf)
    assert vdp.status == 0x00
    assert (vdp.collision_x, vdp.collision_y) == (0x12, 0x34)
    assert vdp._status2 == 0x0C
    assert vdp.frame_count == 1
    assert vdp.irq is False


def test_v9938_render_preview_restores_state_when_the_render_raises(monkeypatch) -> None:
    from msx.vdp import v9938_renderer

    vdp = _v9938_with_9s_and_collision()
    vdp.collision_x = 0x55
    status_before = vdp.status
    status2_before = vdp._status2

    def boom(v: V9938, skip_render: bool = False) -> bytearray:
        v.status = 0x7F
        v.collision_x = 0
        v._status2 = 0xFF
        raise RuntimeError("render failed")

    monkeypatch.setattr(v9938_renderer, "render_frame", boom)
    try:
        vdp.render_preview()
    except RuntimeError:
        pass
    assert vdp.status == status_before
    assert vdp.collision_x == 0x55
    assert vdp._status2 == status2_before
