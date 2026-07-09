"""Tests for V9938 _reg_write_log (banded renderer register tracking)."""
from msx.vdp.v9938 import V9938, _PaletteChange, _RegChange


def make_vdp() -> V9938:
    vdp = V9938()
    vdp.regs[1] |= 0x40  # BL
    vdp.display_line = 30
    return vdp


def _write_reg(vdp: V9938, reg: int, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


# ---------------------------------------------------------------------------
# Display-relevant register writes are logged
# ---------------------------------------------------------------------------

def test_display_reg_write_logged_port99() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 7, 0x01)
    assert _RegChange(30, 7, 0x01) in vdp._reg_write_log


def test_display_reg_write_logged_port9b() -> None:
    vdp = make_vdp()
    vdp.regs[17] = 7   # R#17 → auto-inc from reg 7
    vdp.write_port(0x9B, 0x05)
    assert _RegChange(30, 7, 0x05) in vdp._reg_write_log


# ---------------------------------------------------------------------------
# Command-engine register writes are NOT logged
# ---------------------------------------------------------------------------

def test_cmd_reg_write_not_logged() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 36, 0xFF)  # R#36 = CMD register
    assert all(not (isinstance(e, _RegChange) and e.reg == 36) for e in vdp._reg_write_log)


def test_high_display_reg_not_logged_for_cmd_range() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 32, 0xAA)  # R#32 = first cmd reg
    assert all(not (isinstance(e, _RegChange) and e.reg == 32) for e in vdp._reg_write_log)


# ---------------------------------------------------------------------------
# Palette write logged as a _PaletteChange record
# ---------------------------------------------------------------------------

def test_palette_write_logged() -> None:
    vdp = make_vdp()
    vdp.display_line = 50
    vdp.regs[16] = 3  # palette auto-index = 3
    vdp.write_port(0x9A, 0x77)  # first byte (RB)
    vdp.write_port(0x9A, 0x04)  # second byte (G)
    entry = next(e for e in vdp._reg_write_log if isinstance(e, _PaletteChange))
    assert entry.line == 50
    assert entry.idx == 3
    assert entry.rgb == vdp.palette[3]


# ---------------------------------------------------------------------------
# Log cleared at frame start (begin_scanline(0))
# ---------------------------------------------------------------------------

def test_log_cleared_at_frame_start() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 7, 0x01)
    assert len(vdp._reg_write_log) > 0
    vdp.begin_scanline(0)
    assert vdp._reg_write_log == []


def test_log_not_cleared_at_other_scanlines() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 7, 0x01)
    vdp.begin_scanline(5)
    assert len(vdp._reg_write_log) > 0
