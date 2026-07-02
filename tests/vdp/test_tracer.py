"""Tests for msx/vdp/tracer.py — VDP register write tracer."""
from __future__ import annotations

import io

from msx.vdp.tracer import Tracer
from msx.vdp.v9938 import V9938

# ---------------------------------------------------------------------------
# Helpers

def _make_tracer() -> tuple[Tracer, io.StringIO]:
    buf = io.StringIO()
    t = Tracer(enabled=True, output=buf)
    return t, buf


def _lines(buf: io.StringIO) -> list[str]:
    return [ln for ln in buf.getvalue().splitlines() if ln]


# ---------------------------------------------------------------------------
# Disabled tracer

def test_disabled_tracer_no_output() -> None:
    buf = io.StringIO()
    t = Tracer(enabled=False, output=buf)
    t.port99_write(0x1234, 1000, 0x01)
    t.port99_write(0x1236, 1004, 0x80)
    t.port9b_write(0x1238, 1008, 0xFF, 0x22)
    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# VDP_REG via port 0x99 — control registers (R#0–R#31)

def test_vdp_reg_control_register_format() -> None:
    t, buf = _make_tracer()
    # Write R#01 = 0x50: first byte = data, second byte = 0x81 (bit7=1, reg=1)
    t.port99_write(0x407C, 45231, 0x50)   # latch
    t.port99_write(0x407E, 45233, 0x81)   # second byte: reg = 0x01
    lines = _lines(buf)
    assert len(lines) == 1
    assert lines[0] == "CY=0000045233 FR=000000 PC=407E VDP_REG R#01=50h"


def test_vdp_reg_cycle_zero_padded_10_digits() -> None:
    t, buf = _make_tracer()
    t.port99_write(0x0000, 7, 0xAB)
    t.port99_write(0x0002, 9, 0x80)  # R#00
    lines = _lines(buf)
    assert lines[0].startswith("CY=0000000009 ")


def test_vdp_reg_fr_field_present_and_zero_padded() -> None:
    t, buf = _make_tracer()
    t.port99_write(0x1000, 1, 0x01)
    t.port99_write(0x1002, 2, 0x80, frame=3)  # R#00, frame=3
    lines = _lines(buf)
    assert "FR=000003" in lines[0]


def test_vdp_reg_fr_field_zero_default() -> None:
    t, buf = _make_tracer()
    t.port99_write(0x1000, 1, 0x01)
    t.port99_write(0x1002, 2, 0x80)  # no frame arg → default 0
    lines = _lines(buf)
    assert "FR=000000" in lines[0]


def test_port9b_fr_field() -> None:
    t, buf = _make_tracer()
    t.port9b_write(0x1000, 500, 0x7F, r17=0x02, frame=5)
    lines = _lines(buf)
    assert "FR=000005" in lines[0]


def test_vdp_reg_pc_4_digit_uppercase_hex() -> None:
    t, buf = _make_tracer()
    t.port99_write(0xABCD, 1, 0x12)
    t.port99_write(0xABCF, 2, 0x80)  # R#00
    lines = _lines(buf)
    assert "PC=ABCF" in lines[0]


def test_vdp_reg_value_uppercase_hex() -> None:
    t, buf = _make_tracer()
    t.port99_write(0x1000, 1, 0xAB)
    t.port99_write(0x1002, 2, 0x80)  # R#00
    lines = _lines(buf)
    assert lines[0].endswith("R#00=ABh")


def test_vdp_reg_second_byte_vram_addr_ignored() -> None:
    """Second byte with bit7=0 is a VRAM address set — no register write."""
    t, buf = _make_tracer()
    t.port99_write(0x1000, 1, 0x34)   # first byte (data)
    t.port99_write(0x1002, 2, 0x40)   # second byte: bit7=0 → VRAM address
    assert _lines(buf) == []


def test_vdp_reg_two_consecutive_writes() -> None:
    t, buf = _make_tracer()
    # R#00 = 0x10
    t.port99_write(0x4000, 100, 0x10)
    t.port99_write(0x4002, 102, 0x80)
    # R#02 = 0x1E
    t.port99_write(0x4004, 200, 0x1E)
    t.port99_write(0x4006, 202, 0x82)
    lines = _lines(buf)
    assert len(lines) == 2
    assert "R#00=10h" in lines[0]
    assert "R#02=1Eh" in lines[1]


# ---------------------------------------------------------------------------
# VDP_REG via port 0x99 — command parameter registers (R#32–R#45)

def test_vdp_reg_cmd_param_register_emitted() -> None:
    t, buf = _make_tracer()
    # R#36 = 0x00: second byte = 0x80 | 36 = 0xA4
    t.port99_write(0x4082, 45289, 0x00)
    t.port99_write(0x4084, 45291, 0xA4)  # reg = 36
    lines = _lines(buf)
    assert len(lines) == 1
    assert lines[0] == "CY=0000045291 FR=000000 PC=4084 VDP_REG R#36=00h"


# ---------------------------------------------------------------------------
# VDP_CMD via port 0x99 — R#46 write

def test_vdp_cmd_hmmv_format() -> None:
    t, buf = _make_tracer()
    # Pre-write R#36=0x00, R#37=0x01
    t.port99_write(0x4082, 100, 0x00)
    t.port99_write(0x4084, 102, 0xA4)  # R#36
    t.port99_write(0x4086, 200, 0x01)
    t.port99_write(0x4088, 202, 0xA5)  # R#37
    # R#46 = 0xC0 (HMMV)
    t.port99_write(0x40A2, 45340, 0xC0)
    t.port99_write(0x40A4, 45342, 0xAE)  # reg = 46
    lines = _lines(buf)
    # First 2 lines = R#36/R#37 VDP_REG; last = VDP_CMD
    cmd_line = lines[-1]
    assert "VDP_CMD HMMV/IMP  (C0h)" in cmd_line
    assert "CY=0000045342" in cmd_line
    assert "PC=40A4" in cmd_line


def test_vdp_cmd_params_written_regs_shown() -> None:
    t, buf = _make_tracer()
    t.port99_write(0, 1, 0x00)
    t.port99_write(0, 2, 0xA4)  # R#36 = 0x00
    t.port99_write(0, 3, 0x01)
    t.port99_write(0, 4, 0xA5)  # R#37 = 0x01
    t.port99_write(0, 5, 0xC0)
    t.port99_write(0, 6, 0xAE)  # R#46
    cmd_line = _lines(buf)[-1]
    assert "'00'" in cmd_line  # R#36
    assert "'01'" in cmd_line  # R#37


def test_vdp_cmd_unwritten_params_shown_as_dashes() -> None:
    t, buf = _make_tracer()
    # Only write R#46 without any param registers
    t.port99_write(0, 1, 0xC0)
    t.port99_write(0, 2, 0xAE)  # R#46
    cmd_line = _lines(buf)[-1]
    # All 14 slots should be '--'
    assert cmd_line.count("'--'") == 14


def test_vdp_cmd_param_buffer_cleared_after_cmd() -> None:
    t, buf = _make_tracer()
    # Write R#36, then issue command
    t.port99_write(0, 1, 0xAB)
    t.port99_write(0, 2, 0xA4)  # R#36 = 0xAB
    t.port99_write(0, 3, 0xC0)
    t.port99_write(0, 4, 0xAE)  # R#46 (first cmd)
    buf.truncate(0)
    buf.seek(0)
    # Issue second command without writing params
    t.port99_write(0, 5, 0x80)
    t.port99_write(0, 6, 0xAE)  # R#46 (second cmd)
    cmd_line = _lines(buf)[-1]
    # R#36 buffer was cleared — should show '--'
    assert cmd_line.count("'--'") == 14


# ---------------------------------------------------------------------------
# port 0x9B — indirect register write

def test_port9b_emits_suffix() -> None:
    t, buf = _make_tracer()
    # r17=0x02 → ptr=2 → R#02
    t.port9b_write(0x40C1, 178432, 0x1E, r17=0x02)
    lines = _lines(buf)
    assert len(lines) == 1
    assert lines[0] == "CY=0000178432 FR=000000 PC=40C1 VDP_REG R#02=1Eh  ;port 9Bh"


def test_port9b_emits_suffix_cycle_format() -> None:
    t, buf = _make_tracer()
    t.port9b_write(0x40C1, 178432, 0x1E, r17=0x02)
    line = _lines(buf)[0]
    assert line.startswith("CY=0000178432 ")
    assert line.endswith("  ;port 9Bh")


def test_port9b_cmd_param_reg_buffered() -> None:
    t, buf = _make_tracer()
    # r17=0x24 → ptr=36 → R#36 (cmd param)
    t.port9b_write(0x1000, 500, 0x7F, r17=0x24)
    lines = _lines(buf)
    assert "VDP_REG R#36=7Fh" in lines[0]
    assert ";port 9Bh" in lines[0]


def test_port9b_r46_emits_vdp_cmd() -> None:
    t, buf = _make_tracer()
    # r17=0x2E → ptr=46 → R#46
    t.port9b_write(0x2000, 999, 0xC0, r17=0x2E)
    lines = _lines(buf)
    assert "VDP_CMD HMMV/IMP  (C0h)" in lines[0]
    assert ";port 9Bh" in lines[0]


def test_port9b_uses_r17_lower6_bits() -> None:
    """r17 bit6 (AII) should not affect the register address."""
    t, buf = _make_tracer()
    # r17=0x42 → lower 6 bits = 0x02 → R#02
    t.port9b_write(0x1000, 1, 0xAB, r17=0x42)
    lines = _lines(buf)
    assert "R#02=ABh" in lines[0]


# ---------------------------------------------------------------------------
# V9938 integration


def _make_v9938_with_tracer() -> tuple[V9938, io.StringIO]:
    buf = io.StringIO()
    t = Tracer(enabled=True, output=buf)
    vdp = V9938(tracer=t, _get_pc=lambda: 0x1234, _get_cycle=lambda: 9999)
    return vdp, buf


def test_v9938_without_tracer_no_exception() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0xAB)
    vdp.write_port(0x99, 0x80)  # R#00 = 0xAB
    assert vdp.regs[0] == 0xAB


def test_v9938_with_tracer_port99_emits_vdp_reg() -> None:
    vdp, buf = _make_v9938_with_tracer()
    vdp.write_port(0x99, 0xAB)
    vdp.write_port(0x99, 0x81)  # R#01 = 0xAB
    lines = _lines(buf)
    assert len(lines) == 1
    assert "VDP_REG R#01=ABh" in lines[0]
    assert "PC=1234" in lines[0]
    assert "CY=0000009999" in lines[0]


def test_v9938_with_tracer_port9b_pre_increment_r17() -> None:
    vdp, buf = _make_v9938_with_tracer()
    vdp.regs[17] = 0x22  # ptr=34 (R#34), AII=0
    vdp.write_port(0x9B, 0xFF)
    lines = _lines(buf)
    assert len(lines) == 1
    assert ";port 9Bh" in lines[0]
    # After hook, auto-increment should have occurred
    assert vdp.regs[17] == 0x23  # ptr incremented to 35
