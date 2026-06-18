"""Tests for V9938 begin_scanline() and line-interrupt match."""
from msx.vdp.v9938 import V9938


def make_vdp() -> V9938:
    vdp = V9938()
    vdp.regs[1] |= 0x40  # BL
    return vdp


# ---------------------------------------------------------------------------
# display_line tracking
# ---------------------------------------------------------------------------

def test_display_line_advances() -> None:
    vdp = make_vdp()
    vdp.begin_scanline(5)
    assert vdp.display_line == 5


def test_display_line_reset_at_frame_start() -> None:
    vdp = make_vdp()
    vdp.begin_scanline(10)
    vdp.begin_scanline(0)
    assert vdp.display_line == 0


# ---------------------------------------------------------------------------
# FH set on R#19 match
# ---------------------------------------------------------------------------

def test_fh_set_on_r19_match() -> None:
    vdp = make_vdp()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert vdp._status1 & 0x01


def test_fh_not_set_on_mismatch() -> None:
    vdp = make_vdp()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(9)
    assert not (vdp._status1 & 0x01)


def test_r23_shifts_effective_irq_line() -> None:
    vdp = make_vdp()
    vdp.regs[19] = 15
    vdp.regs[23] = 5
    vdp.begin_scanline(10)  # effective = (15-5)&0xFF = 10
    assert vdp._status1 & 0x01


def test_fh_persists_until_s1_read() -> None:
    vdp = make_vdp()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    vdp.begin_scanline(11)
    assert vdp._status1 & 0x01


def test_fh_not_set_outside_active_display() -> None:
    vdp = make_vdp()
    vdp.regs[19] = 200
    vdp.regs[23] = 0
    # display_height = 192 by default
    vdp.begin_scanline(200)
    assert not (vdp._status1 & 0x01)


# ---------------------------------------------------------------------------
# IE1 gate
# ---------------------------------------------------------------------------

def test_irq_asserted_when_ie1_set_and_line_match() -> None:
    vdp = make_vdp()
    vdp.regs[0] |= 0x10  # IE1
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert vdp.irq_pending()


def test_irq_not_asserted_when_ie1_clear() -> None:
    vdp = make_vdp()
    # IE1 clear (regs[0] & 0x10 == 0)
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert not vdp.irq_pending()


def test_fh_set_regardless_of_ie1() -> None:
    vdp = make_vdp()
    # IE1 clear
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert vdp._status1 & 0x01


# ---------------------------------------------------------------------------
# VBlank: F set at display_height
# ---------------------------------------------------------------------------

def test_vblank_f_set_at_display_height_192() -> None:
    vdp = make_vdp()
    vdp.begin_scanline(192)
    assert vdp.status & 0x80


def test_vblank_f_set_at_display_height_212() -> None:
    vdp = make_vdp()
    vdp.regs[9] = 0x80  # LN → 212 lines
    vdp.begin_scanline(212)
    assert vdp.status & 0x80


def test_vblank_irq_when_ie0_at_vblank() -> None:
    vdp = make_vdp()
    vdp.regs[1] |= 0x20  # IE0
    vdp.begin_scanline(192)
    assert vdp.irq_pending()


# ---------------------------------------------------------------------------
# reg_write_log cleared at frame start
# ---------------------------------------------------------------------------

def test_reg_write_log_cleared_at_frame_start() -> None:
    vdp = make_vdp()
    vdp._reg_write_log.append((10, 7, 0x01))
    vdp.begin_scanline(0)
    assert vdp._reg_write_log == []
