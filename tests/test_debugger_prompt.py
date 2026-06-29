"""Tests for msx.debugger.prompt — Debugger REPL command handlers."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from msx.cpu.registers import Registers
from msx.debugger.prompt import Debugger
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP


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
    m.cycle_count = 0
    return m


def _make_tms_machine(pc: int = 0x4000) -> MagicMock:
    """Build a mock Machine with TMS9918A VDP."""
    m = MagicMock()
    regs = Registers()
    regs.PC = pc
    m.cpu.registers = regs
    m.cpu.read_byte = lambda addr: 0x00
    m.cpu.instruction_pc = pc

    vdp = VDP()
    vdp.regs[0] = 0x00
    vdp.regs[1] = 0x00
    vdp.status = 0x00
    m.vdp = vdp

    m._breakpoints = frozenset()
    m.set_breakpoints = lambda addrs: setattr(m, "_breakpoints", frozenset(addrs[:4]))
    m.cycle_count = 0
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

    def test_cmd_regs_shown(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_reg_vdp()
        out = capsys.readouterr().out
        for i in range(32, 47):
            assert f"R#{i}=" in out

    def test_tms9918a_shows_8_registers(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_reg_vdp()
        out = capsys.readouterr().out
        for i in range(8):
            assert f"R#{i}=" in out
        assert "R#8=" not in out


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

    def test_tms9918a_shows_screen_mode(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_vdp_status()
        out = capsys.readouterr().out
        assert "GRAPHIC1" in out or "SCREEN" in out
        assert "V9938 not active" not in out


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
# dv (dump VRAM)
# ---------------------------------------------------------------------------

class TestDumpVram:
    def test_default_128_bytes(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump_vram(["12000"])
        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 8  # 128 / 16

    def test_address_shown_as_5digit(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump_vram(["12000", "10"])
        out = capsys.readouterr().out
        assert "12000:" in out

    def test_vram_wraps_at_128k(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump_vram(["1FFF0", "20"])
        out = capsys.readouterr().out
        assert "1FFF0:" in out

    def test_invalid_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump_vram(["ZZZZZ"])
        out = capsys.readouterr().out
        assert "invalid" in out.lower()

    def test_no_args(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_dump_vram([])
        out = capsys.readouterr().out
        assert "Usage" in out


# ---------------------------------------------------------------------------
# break add / remove / list
# ---------------------------------------------------------------------------

class TestBreak:
    def test_list_empty(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_break(["l"])
        out = capsys.readouterr().out
        assert "no breakpoints" in out

    def test_add_breakpoint(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["a", "C000"])
        assert 0xC000 in m._breakpoints

    def test_list_after_add(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["a", "C000"])
        capsys.readouterr()
        dbg._cmd_break(["l"])
        out = capsys.readouterr().out
        assert "C000" in out

    def test_remove_breakpoint(self, capsys):
        m = _make_machine()
        m._breakpoints = frozenset([0xC000])
        dbg = Debugger(m)
        dbg._cmd_break(["r", "C000"])
        assert 0xC000 not in m._breakpoints

    def test_max_4_enforced(self, capsys):
        m = _make_machine()
        m._breakpoints = frozenset([0x1000, 0x2000, 0x3000, 0x4000])
        dbg = Debugger(m)
        dbg._cmd_break(["a", "5000"])
        out = capsys.readouterr().out
        assert "maximum 4" in out
        assert 0x5000 not in m._breakpoints

    def test_remove_unknown_addr(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        dbg._cmd_break(["r", "DEAD"])
        out = capsys.readouterr().out
        assert "not in" in out

    def test_add_invalid_addr(self, capsys):
        dbg = Debugger(_make_machine())
        dbg._cmd_break(["a", "ZZZZ"])
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
# step
# ---------------------------------------------------------------------------

class TestStep:
    def test_step_calls_machine_step(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        dbg._cmd_step([])
        m.step.assert_called_once()

    def test_step_prints_pc(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        dbg._cmd_step([])
        out = capsys.readouterr().out
        assert "PC=" in out

    def test_s_in_repl(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        inputs = iter(["s", "c"])
        with patch("builtins.input", side_effect=inputs):
            dbg.enter()
        m.step.assert_called_once()

    def test_step_keeps_repl_active(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        inputs = iter(["s", "c"])
        with patch("builtins.input", side_effect=inputs):
            dbg.enter()
        m.step.assert_called_once()

    def test_step_count_arg(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        dbg._cmd_step(["256"])
        assert m.step.call_count == 256

    def test_step_count_via_repl(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        inputs = iter(["s 10", "c"])
        with patch("builtins.input", side_effect=inputs):
            dbg.enter()
        assert m.step.call_count == 10

    def test_step_invalid_count(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        dbg = Debugger(m)
        dbg._cmd_step(["abc"])
        out = capsys.readouterr().out
        assert "invalid" in out.lower()
        m.step.assert_not_called()

    def test_prompt_shows_cyc_frm(self, capsys):
        m = _make_machine(pc=0x4000)
        m.step = MagicMock(return_value=4)
        m.cycle_count = 12345
        m.vdp._frame_count = 60
        dbg = Debugger(m)
        prompts = []
        with patch("builtins.input", side_effect=lambda p="": (prompts.append(p), "c")[1]):
            dbg.enter()
        assert any("cyc=12345" in p for p in prompts)
        assert any("frm=60" in p for p in prompts)


# ---------------------------------------------------------------------------
# unknown command
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def test_unknown_shows_help(self, capsys):
        m = _make_machine()
        dbg = Debugger(m)
        # Simulate one unknown command then 'cont'
        inputs = iter(["foobar", "c"])
        with patch("builtins.input", side_effect=inputs):
            dbg.enter()
        out = capsys.readouterr().out
        assert "Unknown command" in out
        assert "Commands:" in out


# ---------------------------------------------------------------------------
# TMS9918A debugger commands (5.1 - 5.6)
# ---------------------------------------------------------------------------

class TestTMS9918ADebug:
    def test_ds_toggles_disable_sprites_on_tms(self, capsys):
        m = _make_tms_machine()
        dbg = Debugger(m)
        assert m.vdp.debug_disable_sprites is False
        dbg._cmd_disable_sprites()
        assert m.vdp.debug_disable_sprites is True
        dbg._cmd_disable_sprites()
        assert m.vdp.debug_disable_sprites is False

    def test_v_on_tms_shows_screen_mode_no_exception(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_vdp_status()
        out = capsys.readouterr().out
        assert "Screen" in out
        assert "V9938 not active" not in out
        assert "MSX2 only" not in out

    def test_rv_on_tms_shows_r0_through_r7(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_reg_vdp()
        out = capsys.readouterr().out
        for i in range(8):
            assert f"R#{i}=" in out

    def test_dv_on_tms_dumps_without_index_error(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_dump_vram(["0"])
        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 8  # 128 bytes / 16 per row

    def test_te_on_tms_attaches_tracer(self, capsys):
        m = _make_tms_machine()
        dbg = Debugger(m)
        assert m.vdp.tracer is None
        dbg._cmd_trace_enable()
        assert m.vdp.tracer is not None
        assert m.vdp.tracer.enabled is True

    def test_rp_on_tms_prints_no_palette_error(self, capsys):
        dbg = Debugger(_make_tms_machine())
        dbg._cmd_reg_palette()
        out = capsys.readouterr().out
        assert "no programmable palette" in out.lower()
