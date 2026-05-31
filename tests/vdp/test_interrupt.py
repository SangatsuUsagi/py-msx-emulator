from msx.vdp.vdp import VDP
from msx.vdp.renderer import render_frame


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
# Interrupt callback
# ---------------------------------------------------------------------------

def test_callback_fires_when_ie_set() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60   # BL=1, IE=1 (bit 5)
    fired: list[int] = []
    vdp.on_interrupt = lambda: fired.append(1)

    render_frame(vdp)

    assert len(fired) == 1, "interrupt callback must fire once when IE=1"


def test_callback_suppressed_when_ie_clear() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x40   # BL=1, IE=0
    fired: list[int] = []
    vdp.on_interrupt = lambda: fired.append(1)

    render_frame(vdp)

    assert len(fired) == 0, "interrupt callback must not fire when IE=0"


def test_no_callback_when_handler_is_none() -> None:
    vdp = make_vdp_with_display()
    vdp.regs[1] = 0x60   # IE=1
    vdp.on_interrupt = None
    render_frame(vdp)   # must not raise


def test_callback_fires_on_blank_frame_too() -> None:
    vdp = VDP()
    vdp.regs[1] = 0x20   # BL=0, IE=1
    fired: list[int] = []
    vdp.on_interrupt = lambda: fired.append(1)

    render_frame(vdp)

    assert len(fired) == 1, "callback must fire on blank frames when IE=1"
