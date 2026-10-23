from __future__ import annotations

import pytest

from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP


@pytest.fixture
def vdp_with_interrupt() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x60  # BL=1 (display enabled), IE=1 (interrupt enable)
    return vdp


def test_skip_render_returns_empty_buffer(vdp_with_interrupt: VDP) -> None:
    buf = render_frame(vdp_with_interrupt, skip_render=True)
    assert len(buf) == 0


def test_skip_render_leaves_f_and_irq_alone(vdp_with_interrupt: VDP) -> None:
    vdp_with_interrupt.status = 0
    render_frame(vdp_with_interrupt, skip_render=True)
    assert not (vdp_with_interrupt.status & 0x80)
    assert vdp_with_interrupt.irq is False


def test_normal_render_returns_full_buffer(bare_vdp: VDP) -> None:
    bare_vdp.regs[1] = 0x40  # BL=1
    buf = render_frame(bare_vdp, skip_render=False)
    assert len(buf) == 256 * 212


def test_begin_vblank_raises_f_and_irq(vdp_with_interrupt: VDP) -> None:
    vdp_with_interrupt.status = 0
    vdp_with_interrupt.begin_vblank()
    assert vdp_with_interrupt.status & 0x80
    assert vdp_with_interrupt.irq is True


def test_begin_vblank_leaves_irq_low_when_ie_clear() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x40  # BL=1, IE0=0
    vdp.begin_vblank()
    assert vdp.status & 0x80
    assert vdp.irq is False


# --- The renderer never raises VBlank: Machine.run_frame calls begin_vblank ---

def test_render_leaves_f_and_irq_alone(vdp_with_interrupt: VDP) -> None:
    vdp_with_interrupt.status = 0
    buf = render_frame(vdp_with_interrupt)
    assert len(buf) == 256 * 212
    assert not (vdp_with_interrupt.status & 0x80)
    assert vdp_with_interrupt.irq is False


def test_blanked_render_leaves_f_clear() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x20  # BL=0 (display off), IE0=1
    buf = render_frame(vdp)
    assert len(buf) == 256 * 212
    assert not (vdp.status & 0x80)
    assert vdp.irq is False


def test_v9938_skip_render_still_updates_the_eo_field_bit() -> None:
    # vdp-renderer "V9938 render_frame skip_render parameter".
    from msx.vdp.v9938 import V9938
    from msx.vdp.v9938_renderer import render_frame as render_frame_v9938

    vdp = V9938()
    vdp.frame_count = 1
    assert render_frame_v9938(vdp, skip_render=True) == bytearray(0)
    assert vdp._status2 & 0x02
    vdp.frame_count = 2
    render_frame_v9938(vdp, skip_render=True)
    assert not vdp._status2 & 0x02


# --- A skipped frame sets the same sprite status as a full render -----------
# vdp-renderer "TMS9918A skip sets the same sprite status as a full render",
# vdp-v9938 / vdp-renderer "skip_render sets the same sprite status".

def _tms_5s_and_collision() -> VDP:
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


def test_tms_skip_sets_the_same_sprite_status_as_a_full_render() -> None:
    full, skipped = _tms_5s_and_collision(), _tms_5s_and_collision()
    render_frame(full)
    render_frame(skipped, skip_render=True)
    assert full.status & 0x60 == 0x60          # the case is not vacuous
    assert skipped.status == full.status


def test_tms_skip_in_text_mode_leaves_status_as_a_full_render() -> None:
    full, skipped = _tms_5s_and_collision(), _tms_5s_and_collision()
    for vdp in (full, skipped):
        vdp.regs[1] |= 0x10              # M1: text mode has no sprites
    render_frame(full)
    render_frame(skipped, skip_render=True)
    assert skipped.status == full.status == 0
