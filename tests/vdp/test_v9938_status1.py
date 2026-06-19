"""Tests for V9938 S#1 (status register 1) and irq_pending()."""
from msx.vdp.v9938 import V9938


def make_vdp() -> V9938:
    vdp = V9938()
    vdp.regs[1] |= 0x40  # BL
    return vdp


# ---------------------------------------------------------------------------
# S#1: FH read/clear
# ---------------------------------------------------------------------------

def test_s1_fh_read_returns_1_when_set() -> None:
    vdp = make_vdp()
    vdp._status1 = 0x01
    vdp.regs[15] = 1
    result = vdp.read_port(0x99)
    assert result & 0x01


def test_s1_fh_cleared_after_read() -> None:
    vdp = make_vdp()
    vdp._status1 = 0x01
    vdp.regs[15] = 1
    vdp.read_port(0x99)
    assert not (vdp._status1 & 0x01)


def test_s1_read_does_not_affect_s0_f_flag() -> None:
    vdp = make_vdp()
    vdp.status |= 0x80
    vdp._status1 = 0x01
    vdp.regs[15] = 1
    vdp.read_port(0x99)
    assert vdp.status & 0x80


def test_s0_read_does_not_affect_s1_fh() -> None:
    vdp = make_vdp()
    vdp._status1 = 0x01
    vdp.regs[15] = 0
    vdp.read_port(0x99)
    assert vdp._status1 & 0x01


def test_s1_vdp_id_bits_zero_when_fh_clear() -> None:
    vdp = make_vdp()
    vdp._status1 = 0x00
    vdp.regs[15] = 1
    result = vdp.read_port(0x99)
    assert result == 0x00


# ---------------------------------------------------------------------------
# irq_pending: all source combinations
# ---------------------------------------------------------------------------

def test_irq_pending_true_ie0_and_f() -> None:
    vdp = make_vdp()
    vdp.regs[1] |= 0x20  # IE0
    vdp.status |= 0x80
    assert vdp.irq_pending()


def test_irq_pending_true_ie1_and_fh() -> None:
    vdp = make_vdp()
    vdp.regs[0] |= 0x10  # IE1
    vdp._status1 |= 0x01  # FH
    assert vdp.irq_pending()


def test_irq_pending_true_both_sources() -> None:
    vdp = make_vdp()
    vdp.regs[1] |= 0x20
    vdp.regs[0] |= 0x10
    vdp.status |= 0x80
    vdp._status1 |= 0x01
    assert vdp.irq_pending()


def test_irq_pending_false_no_flags() -> None:
    vdp = make_vdp()
    assert not vdp.irq_pending()


def test_irq_deasserts_after_s0_read() -> None:
    vdp = make_vdp()
    vdp.regs[1] |= 0x20
    vdp.status |= 0x80
    assert vdp.irq_pending()
    vdp.regs[15] = 0
    vdp.read_port(0x99)
    assert not vdp.irq_pending()


def test_irq_deasserts_after_s1_read() -> None:
    vdp = make_vdp()
    vdp.regs[0] |= 0x10
    vdp._status1 |= 0x01
    assert vdp.irq_pending()
    vdp.regs[15] = 1
    vdp.read_port(0x99)
    assert not vdp.irq_pending()


def test_irq_cache_updates_on_ie0_register_write() -> None:
    # The machine loop reads the cached vdp._irq; enabling IE0 via a register
    # write while F is already set must refresh the cache immediately.
    vdp = make_vdp()
    vdp.status |= 0x80          # F (VBlank) already pending
    vdp._update_irq()
    assert not vdp._irq         # IE0 not set yet
    vdp.write_port(0x99, 0x40 | 0x20)  # data = BL|IE0
    vdp.write_port(0x99, 0x80 | 1)     # commit to R#1
    assert vdp._irq             # cache refreshed → IRQ pending


def test_irq_cache_updates_on_ie1_register_write() -> None:
    vdp = make_vdp()
    vdp._status1 |= 0x01        # FH pending
    vdp._update_irq()
    assert not vdp._irq         # IE1 not set yet
    vdp.write_port(0x99, 0x10)         # data = IE1 (R#0 bit4)
    vdp.write_port(0x99, 0x80 | 0)     # commit to R#0
    assert vdp._irq


def test_irq_stays_when_one_source_remains() -> None:
    vdp = make_vdp()
    vdp.regs[1] |= 0x20
    vdp.regs[0] |= 0x10
    vdp.status |= 0x80
    vdp._status1 |= 0x01
    # Clear only F (S#0 read)
    vdp.regs[15] = 0
    vdp.read_port(0x99)
    assert vdp.irq_pending()  # FH still pending


# ---------------------------------------------------------------------------
# S#2: HR (horizontal retrace, bit5) and VR (vertical retrace, bit6)
# ---------------------------------------------------------------------------

def test_s2_hr_set_late_in_scanline() -> None:
    vdp = make_vdp()
    vdp.regs[15] = 2
    vdp._line_cycle = 220  # past the display, in horizontal blanking
    assert vdp.read_port(0x99) & 0x20  # HR


def test_s2_hr_clear_early_in_scanline() -> None:
    vdp = make_vdp()
    vdp.regs[15] = 2
    vdp._line_cycle = 10  # active display region
    assert not (vdp.read_port(0x99) & 0x20)


def test_s2_hr_advances_with_tick_and_resets_each_line() -> None:
    vdp = make_vdp()
    vdp.regs[15] = 2
    vdp.tick(220)
    assert vdp.read_port(0x99) & 0x20  # HR set after enough T-states
    vdp.begin_scanline(5)
    assert not (vdp.read_port(0x99) & 0x20)  # reset at the new scanline


def test_s2_hr_preserves_command_bits() -> None:
    vdp = make_vdp()
    vdp._status2 = 0x81  # CE + TR
    vdp._line_cycle = 220
    vdp.regs[15] = 2
    assert vdp.read_port(0x99) == 0x81 | 0x20 | 0x0C  # +HR, +reserved bits 2,3
    assert vdp._status2 == 0x81  # underlying field unchanged


def test_s2_vr_set_during_vblank() -> None:
    vdp = make_vdp()
    vdp.regs[15] = 2
    vdp.begin_scanline(200)  # >= 192 → vertical blanking
    assert vdp.read_port(0x99) & 0x40  # VR


def test_s2_vr_clear_during_active_display() -> None:
    vdp = make_vdp()
    vdp.regs[15] = 2
    vdp.begin_scanline(100)  # active display
    assert not (vdp.read_port(0x99) & 0x40)
