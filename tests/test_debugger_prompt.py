"""Tests for msx.debugger.prompt — Debugger REPL command handlers."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from msx.cpu.registers import Registers
from msx.debugger.prompt import Debugger
from msx.vdp.v9938 import V9938


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_machine(pc: int = 0x4000) -> MagicMock:
    """Build a minimal mock Machine with wired cpu.registers and vdp."""
    m = MagicMock()
    regs = Registers()
    regs.PC = pc
    regs.AF = 0x1234  # A=0x12 F=0x34
    regs.BC = 0xABCD
    regs.DE = 0x5678
    regs.HL = 0x9ABC
    regs.IX = 0x1111
    regs.IY = 0x2222
    regs.SP = 0xFFF0
    m.cpu.registers = regs

    # read_byte: returns 0x00 (NOP) everywhere
    m.cpu.read_byte = lambda addr: 0x00

    # V9938 vdp
    vdp = V9938(vram=bytearray(131072))
    vdp.regs[0] = 0x00
    vdp.regs[1] = 0xE0
    vdp.status = 0xA0
    vdp._status2 = 0x03
    m.vdp = vdp

    m._breakpoints = frozenset()
    m.set_breakpoints = lambda addrs: setattr(m, "_breakpoints", frozenset(addrs[:4]))
    return m


# ---------------------------------------------------------------------------
# reg cpu
# ---------------------------------------------------------------------------

class TestRegCpu:
    def test_pc_shown(self, capsys):
        dbg = Debugger(_make_machine(pc=0xC000))
        dbg._cmd_reg_cpu()
        out = capsys.readouterr().out
        assert "PC=C000" in out

    def test_all_pair_registers_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_reg_cpu()
        out = capsys.readouterr().out
        for label in ("AF=", "BC=", "DE=", "HL=", "IX=", "IY=", "SP=", "PC="):
            assert label in out

    def test_flag_bits_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_reg_cpu()
        out = capsys.readouterr().out
        for flag in ("S=", "Z=", "H=", "P/V=", "N=", "C="):
            assert flag in out


# ---------------------------------------------------------------------------
# reg vdp
# ---------------------------------------------------------------------------

class TestRegVdp:
    def test_28_registers_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_reg_vdp()
        out = capsys.readouterr().out
        for i in range(28):
            assert f"R#{i}=" in out

    def test_non_v9938_shows_error(self, capsys):
        m = _make_machine()
        m.vdp = MagicMock(spec=[])   # not a V9938 instance
        dbg = Debugger(m)
        dbg._cmd_reg_vdp()
        out = capsys.readouterr().out
        assert "V9938 not active" in out


# ---------------------------------------------------------------------------
# vdp status
# ---------------------------------------------------------------------------

class TestVdpStatus:
    def test_s0_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_vdp_status()
        out = capsys.readouterr().out
        assert "S#0=A0" in out

    def test_s2_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_vdp_status()
        out = capsys.readouterr().out
        assert "S#2=03" in out

    def test_non_v9938_shows_error(self, capsys):
        m = _make_machine()
        m.vdp = MagicMock(spec=[])
        dbg = Debugger(m)
        dbg._cmd_vdp_status()
        out = capsys.readouterr().out
        assert "V9938 not active" in out


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------

class TestDump:
    def test_default_128_bytes(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump(["F000"])
        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        # 128 bytes / 16 per row = 8 rows
        assert len(lines) == 8

    def test_address_prefix(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump(["C000", "10"])
        out = capsys.readouterr().out
        assert "C000:" in out

    def test_custom_size(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump(["0000", "20"])  # 32 decimal? no — hex 0x20 = 32
        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 2  # 0x20 = 32 bytes / 16 = 2 rows

    def test_invalid_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump(["ZZZZ"])
        out = capsys.readouterr().out
        assert "invalid" in out.lower()

    def test_no_args(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump([])
        out = capsys.readouterr().out
        assert "Usage" in out


# ---------------------------------------------------------------------------
# break add / remove / list
# ---------------------------------------------------------------------------

class TestBreak:
    def test_list_empty(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_break(["list"])
        out = capsys.readouterr().out
        assert "no breakpoints" in out

    def test_add_breakpoint(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["add", "C000"])
        assert 0xC000 in m._breakpoints

    def test_list_after_add(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["add", "C000"])
        capsys.readouterr()
        dbg._cmd_break(["list"])
        out = capsys.readouterr().out
        assert "C000" in out

    def test_remove_breakpoint(self, capsys):
        m = _make_machine()
        m._breakpoints = frozenset([0xC000])
        dbg = Debugger(m)
        dbg._cmd_break(["remove", "C000"])
        assert 0xC000 not in m._breakpoints

    def test_max_4_enforced(self, capsys):
        m = _make_machine()
        m._breakpoints = frozenset([0x1000, 0x2000, 0x3000, 0x4000])
        dbg = Debugger(m)
        dbg._cmd_break(["add", "5000"])
        out = capsys.readouterr().out
        assert "maximum 4" in out
        assert 0x5000 not in m._breakpoints

    def test_remove_unknown_addr(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["remove", "DEAD"])
        out = capsys.readouterr().out
        assert "not in" in out

    def test_add_invalid_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_break(["add", "ZZZZ"])
        out = capsys.readouterr().out
        assert "invalid" in out.lower()


# ---------------------------------------------------------------------------
# disasm
# ---------------------------------------------------------------------------

class TestDisasm:
    def test_10_lines_at_pc(self, capsys):
        dbg = Debugger(_make_machine(pc=0x4000))
        dbg._cmd_disasm([])
        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 10

    def test_starts_at_pc(self, capsys):
        dbg = Debugger(_make_machine(pc=0x4000))
        dbg._cmd_disasm([])
        out = capsys.readouterr().out
        assert "4000" in out

    def test_explicit_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_disasm(["0000"])
        out = capsys.readouterr().out
        assert "0000" in out

    def test_invalid_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_disasm(["ZZZZ"])
        out = capsys.readouterr().out
        assert "invalid" in out.lower()


# ---------------------------------------------------------------------------
# unknown command
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def test_unknown_shows_help(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        # Simulate one unknown command then 'cont'
        inputs = iter(["foobar", "cont"])
        with patch("builtins.input", side_effect=inputs):
            dbg.enter()
        out = capsys.readouterr().out
        assert "Unknown command" in out
        assert "Commands:" in out
