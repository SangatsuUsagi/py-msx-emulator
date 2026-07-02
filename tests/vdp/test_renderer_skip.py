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


def test_skip_render_fires_vblank_interrupt(vdp_with_interrupt: VDP) -> None:
    called: list[int] = []
    vdp_with_interrupt.on_interrupt = lambda: called.append(1)
    render_frame(vdp_with_interrupt, skip_render=True)
    assert called == [1]


def test_skip_render_sets_vblank_status_bit(vdp_with_interrupt: VDP) -> None:
    vdp_with_interrupt.status = 0
    render_frame(vdp_with_interrupt, skip_render=True)
    assert vdp_with_interrupt.status & 0x80


def test_normal_render_returns_full_buffer(bare_vdp: VDP) -> None:
    bare_vdp.regs[1] = 0x40  # BL=1
    buf = render_frame(bare_vdp, skip_render=False)
    assert len(buf) == 256 * 192
