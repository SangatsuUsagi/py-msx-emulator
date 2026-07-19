"""Tests for V9938 begin_scanline() and line-interrupt match."""
from msx.vdp.v9938 import V9938, _RegChange


def make_vdp() -> V9938:
    vdp = V9938()
    vdp.regs[1] |= 0x40  # BL
    return vdp


def make_vdp_ie1() -> V9938:
    """VDP with the line interrupt enabled (IE1). FH is gated by IE1 to match
    openMSX (only raises irqHorizontal when IE1 is set, VDP.cc:412)."""
    vdp = make_vdp()
    vdp.regs[0] |= 0x10  # IE1
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
    vdp = make_vdp_ie1()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert vdp._status1 & 0x01


def test_fh_not_set_on_mismatch() -> None:
    vdp = make_vdp_ie1()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(9)
    assert not (vdp._status1 & 0x01)


def test_r23_shifts_effective_irq_line() -> None:
    vdp = make_vdp_ie1()
    vdp.regs[19] = 15
    vdp.regs[23] = 5
    vdp.begin_scanline(10)  # effective = (15-5)&0xFF = 10
    assert vdp._status1 & 0x01


def test_fh_persists_until_s1_read() -> None:
    vdp = make_vdp_ie1()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    vdp.begin_scanline(11)
    assert vdp._status1 & 0x01


def test_fh_set_outside_active_display() -> None:
    # The line interrupt counts the whole field: R#19 may target a line in the
    # border/vblank region (here 200, beyond the 192-line active display).
    vdp = make_vdp_ie1()
    vdp.regs[19] = 200
    vdp.regs[23] = 0
    vdp.begin_scanline(200)
    assert vdp._status1 & 0x01


def test_fh_not_set_above_8bit_line_range() -> None:
    # Lines >= 256 can never match the 8-bit R#19 compare.
    vdp = make_vdp_ie1()
    vdp.regs[19] = 0
    vdp.regs[23] = 0
    vdp.begin_scanline(256)
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


def test_fh_not_set_when_ie1_clear() -> None:
    # FH (S#1 bit0) is gated by IE1: with IE1 clear the raster compare must not
    # latch FH, matching openMSX (irqHorizontal raised only when IE1, VDP.cc:412).
    # A split program that parks the compare with IE1 clear would otherwise fire
    # a spurious interrupt when the vblank ISR re-enables IE1.
    vdp = make_vdp()
    # IE1 clear
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert not (vdp._status1 & 0x01)


def test_ie1_falling_edge_clears_fh() -> None:
    # Clearing IE1 via an R#0 write resets a pending FH (openMSX VDP.cc:1182),
    # so a later re-enable of IE1 cannot fire on the stale flag.
    vdp = make_vdp_ie1()
    vdp.regs[19] = 10
    vdp.regs[23] = 0
    vdp.begin_scanline(10)
    assert vdp._status1 & 0x01
    # Write R#0 with IE1 cleared (control-port two-byte sequence: data, then
    # register-select with bit7 set).
    vdp.write_port(0x99, vdp.regs[0] & ~0x10)
    vdp.write_port(0x99, 0x80 | 0)
    assert not (vdp._status1 & 0x01)


def test_parked_compare_with_ie1_clear_does_not_fire_on_reenable() -> None:
    # Regression for the split-screen bottom-border interrupt: a program parks
    # the line compare while IE1 is clear, the raster crosses the parked line
    # (no FH, no IRQ), then the vblank ISR re-enables IE1 at a non-matching line.
    # No spurious interrupt must result (openMSX cancels the parked compare).
    vdp = make_vdp_ie1()
    vdp.regs[23] = 0
    # Split ISR: disable IE1 and park the compare in the bottom border.
    vdp.write_port(0x99, vdp.regs[0] & ~0x10)  # R#0 data: IE1 clear
    vdp.write_port(0x99, 0x80 | 0)             # select R#0
    vdp.regs[19] = 214                         # parked target (bottom border)
    vdp.begin_scanline(214)                    # raster crosses parked line
    assert not (vdp._status1 & 0x01)           # FH must NOT latch (IE1 clear)
    assert not vdp.irq_pending()
    # Vblank ISR re-enables IE1 at a line that no longer matches the compare.
    vdp.begin_scanline(215)
    vdp.write_port(0x99, vdp.regs[0] | 0x10)   # R#0 data: IE1 set
    vdp.write_port(0x99, 0x80 | 0)             # select R#0
    assert not (vdp._status1 & 0x01)           # no stale FH to fire on
    assert not vdp.irq_pending()


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
    vdp._reg_write_log.append(_RegChange(10, 7, 0x01))
    vdp.begin_scanline(0)
    assert vdp._reg_write_log == []
