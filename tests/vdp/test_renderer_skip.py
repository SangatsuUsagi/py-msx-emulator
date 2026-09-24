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
