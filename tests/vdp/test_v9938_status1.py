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
