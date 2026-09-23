import pytest

from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP


def make_vdp_with_display() -> VDP:
    vdp = VDP()
    vdp.regs[1] = 0x40   # BL=1, G1, IE=0
    return vdp


# ---------------------------------------------------------------------------
# VBlank flag
# ---------------------------------------------------------------------------

def test_vblank_flag_set_after_render() -> None:
    vdp = make_vdp_with_display()
    render_frame(vdp)
    assert vdp.status & 0x80, "VBlank flag (bit 7) must be set after render_frame"


def test_vblank_flag_set_even_when_blanked() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x00   # BL=0 (blank display)
    render_frame(vdp)
    assert vdp.status & 0x80, "VBlank flag must be set even when display is blanked"


def test_vblank_flag_cleared_on_status_read() -> None:
    vdp = make_vdp_with_display()
    render_frame(vdp)
    vdp.read_port(0x99)   # reading status clears bit 7
    assert not (vdp.status & 0x80), "VBlank flag must be cleared after status read"


# ---------------------------------------------------------------------------
# IRQ line: level-based F && IE0 (openspec vdp-interrupts "IRQ line —
# level-based VBlank IRQ (TMS9918A)")
# ---------------------------------------------------------------------------

def _write_reg(vdp: VDP, reg: int, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


def test_irq_asserted_at_vblank_when_ie_set() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60   # BL=1, IE0=1
    render_frame(vdp)
    assert vdp.irq is True


def test_irq_not_asserted_at_vblank_when_ie_clear() -> None:
    vdp = make_vdp_with_display()   # IE0=0
    render_frame(vdp)
    assert vdp.status & 0x80
    assert vdp.irq is False


def test_irq_asserted_on_blank_frame_too() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x20   # BL=0, IE0=1
    render_frame(vdp)
    assert vdp.irq is True


def test_setting_ie_while_f_pending_asserts_irq() -> None:
    vdp = make_vdp_with_display()
    render_frame(vdp)            # F=1 with IE0=0: line stays low
    assert vdp.irq is False
    _write_reg(vdp, 1, 0x60)     # IE0 0->1 while F is still set
    assert vdp.irq is True


def test_clearing_ie_deasserts_irq() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60
    render_frame(vdp)
    _write_reg(vdp, 1, 0x40)     # IE0 1->0
    assert vdp.irq is False


def test_status_read_deasserts_irq() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60
    render_frame(vdp)
    vdp.read_port(0x99)
    assert vdp.irq is False
    _write_reg(vdp, 1, 0x60)     # F is clear now, so re-writing IE0 keeps it low
    assert vdp.irq is False


def test_reset_deasserts_irq() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60
    render_frame(vdp)
    vdp.reset()
    assert vdp.irq is False


@pytest.mark.parametrize("reg", [0, 2, 3, 4, 5, 6, 7])
def test_non_r1_write_leaves_asserted_irq_high(reg: int) -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60
    render_frame(vdp)
    _write_reg(vdp, reg, 0x00)
    assert vdp.irq is True


@pytest.mark.parametrize("reg", [0, 2, 3, 4, 5, 6, 7])
def test_non_r1_write_leaves_low_irq_low(reg: int) -> None:
    # F pending, IE0 clear: a write elsewhere (even one whose value has bit 5
    # set) must not recompute the line from the wrong register.
    vdp = make_vdp_with_display()
    render_frame(vdp)
    _write_reg(vdp, reg, 0x20)
    assert vdp.irq is False


def test_setting_ie_without_f_keeps_irq_low() -> None:
    vdp = make_vdp_with_display()   # F clear, IE0=0
    _write_reg(vdp, 1, 0x60)
    assert vdp.irq is False
