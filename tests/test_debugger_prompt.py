"""Tests for msx.debugger.prompt — Debugger REPL command handlers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from msx.cpu.registers import Registers
from msx.debugger.prompt import Debugger
from msx.mapper import FlatMapper
from msx.ram_mapper import RamMapper
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
        lines = [line for line in out.strip().splitlines() if line.strip()]
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
        lines = [line for line in out.strip().splitlines() if line.strip()]
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
        lines = [line for line in out.strip().splitlines() if line.strip()]
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
        lines = [line for line in out.strip().splitlines() if line.strip()]
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
        lines = [line for line in out.strip().splitlines() if line.strip()]
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


# ---------------------------------------------------------------------------
# Slot inspector commands (9.1 - 9.5)
# ---------------------------------------------------------------------------

def _make_slot_memory(
    slot_register: int = 0xD4,
    sub_slot_enabled: bool = False,
    sub_slot_reg: int = 0x00,
    rom_name: str = "cbios_main.rom",
    sub0_rom_name: str = "",
    with_ram_mapper: bool = False,
) -> MagicMock:
    mem = MagicMock()
    mem.slot_register = slot_register
    mem.sub_slot_enabled = sub_slot_enabled
    mem.sub_slot_reg = sub_slot_reg
    mem.rom = b"\x00" * 32768
    mem.rom_name = rom_name
    mem.sub0_rom = b"\x00" * 16384 if sub0_rom_name else None
    mem.sub0_rom_name = sub0_rom_name
    mem._mapper = FlatMapper(None)
    mem._mapper2 = FlatMapper(None)
    mem.ram_mapper = RamMapper() if with_ram_mapper else None
    return mem


def _make_slot_machine(
    slot_register: int = 0xD4,
    sub_slot_enabled: bool = False,
    sub_slot_reg: int = 0x00,
    rom_name: str = "cbios_main.rom",
    sub0_rom_name: str = "",
    with_ram_mapper: bool = False,
) -> MagicMock:
    m = _make_machine()
    m.memory = _make_slot_memory(
        slot_register=slot_register,
        sub_slot_enabled=sub_slot_enabled,
        sub_slot_reg=sub_slot_reg,
        rom_name=rom_name,
        sub0_rom_name=sub0_rom_name,
        with_ram_mapper=with_ram_mapper,
    )
    return m


class TestSlotActive:
    def test_sl_decodes_primary_slots(self, capsys):
        # slot_register=0xD4 = 0b11_01_01_00 → P0=0, P1=1, P2=1, P3=3
        m = _make_slot_machine(slot_register=0xD4)
        Debugger(m)._cmd_slot_active()
        out = capsys.readouterr().out
        # Data rows contain "P0"/"P1"/"P2"/"P3"
        assert "P0" in out
        assert "P1" in out
        assert "P2" in out
        assert "P3" in out
        # P0 should have primary=0, P3 should have primary=3
        for line in out.splitlines():
            if "P0" in line and "0000" in line:
                assert " 0 " in line or line.strip().split()[2] == "0"
            if "P3" in line and "C000" in line:
                assert " 3 " in line or line.strip().split()[2] == "3"

    def test_sl_no_secondary_on_msx1(self, capsys):
        m = _make_slot_machine(sub_slot_enabled=False)
        Debugger(m)._cmd_slot_active()
        out = capsys.readouterr().out
        # Sec column shows "-" for all data rows (at least 4 dashes in data portion)
        data_lines = [
            line
            for line in out.splitlines()
            if "0000" in line or "4000" in line or "8000" in line or "C000" in line
        ]
        assert len(data_lines) == 4
        for line in data_lines:
            cols = line.split()
            assert cols[3] == "-"  # Sec column

    def test_sl_ram_mapper_bank_shown(self, capsys):
        # P3 = slot 3, sub 2 or 3 with ram_mapper
        # slot_register: P3=3 → 0b11xxxxxx = 0xC0 | lower = use 0xD4 (P3=3)
        m = _make_slot_machine(
            slot_register=0xD4,
            sub_slot_enabled=True,
            sub_slot_reg=0xA8,  # P0=0,P1=2,P2=2,P3=2 (sub slots for slot 3)
            sub0_rom_name="cbios_sub.rom",
            with_ram_mapper=True,
        )
        Debugger(m)._cmd_slot_active()
        out = capsys.readouterr().out
        assert "seg=" in out


class TestSlotTree:
    def test_st_msx2_shows_expanded_slot3(self, capsys):
        m = _make_slot_machine(
            sub_slot_enabled=True,
            sub_slot_reg=0xA4,
            sub0_rom_name="cbios_sub.rom",
            with_ram_mapper=True,
        )
        Debugger(m)._cmd_slot_tree()
        out = capsys.readouterr().out
        assert "[EXPANDED]" in out
        assert "secondary-select(raw)=A4h" in out
        assert "page-map" in out

    def test_st_msx1_no_expanded(self, capsys):
        m = _make_slot_machine(sub_slot_enabled=False)
        Debugger(m)._cmd_slot_tree()
        out = capsys.readouterr().out
        assert "[EXPANDED]" not in out
        assert "page-map" not in out


# ---------------------------------------------------------------------------
# sl — cartridge ROM mapper bank column
# ---------------------------------------------------------------------------

class TestSlotRomMapperBank:
    @staticmethod
    def _rom16(pages: int):
        return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(16384)])

    @staticmethod
    def _rom8(pages: int):
        return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(8192)])

    def test_ascii16_window_bank_and_offset(self):
        from msx.debugger.prompt import _rom_mapper_bank_info
        from msx.mapper import Ascii16Mapper
        m = Ascii16Mapper(self._rom16(8))
        # power-on: both windows bank 0
        assert _rom_mapper_bank_info(m, 1) == "bank 0 @00000-03FFF"
        assert _rom_mapper_bank_info(m, 2) == "bank 0 @00000-03FFF"
        # switch window 1 (0x8000) to bank 3
        m.write(0x7000, 3)
        assert _rom_mapper_bank_info(m, 2) == "bank 3 @0C000-0FFFF"

    def test_ascii8_shows_both_windows_per_page(self):
        from msx.debugger.prompt import _rom_mapper_bank_info
        from msx.mapper import Ascii8Mapper
        m = Ascii8Mapper(self._rom8(8))
        m.write(0x6000, 1)  # window 0 (0x4000) -> bank 1
        m.write(0x6800, 2)  # window 1 (0x6000) -> bank 2
        assert _rom_mapper_bank_info(m, 1) == "w0=b1@02000  w1=b2@04000"

    def test_flat_mapper_has_no_bank(self):
        from msx.debugger.prompt import _rom_mapper_bank_info
        from msx.mapper import FlatMapper
        assert _rom_mapper_bank_info(FlatMapper(b"\x00" * 32768), 1) is None

    def test_sl_bank_uses_rom_mapper_for_cartridge(self):
        from types import SimpleNamespace

        from msx.debugger.prompt import _sl_bank
        from msx.mapper import Ascii16Mapper
        mem = SimpleNamespace(
            _mapper=Ascii16Mapper(self._rom16(8)), _mapper2=None, ram_mapper=None
        )
        assert _sl_bank(mem, 1, None, 2) == "bank 0 @00000-03FFF"

    def test_sl_bank_ram_mapper_segment_unchanged(self):
        from types import SimpleNamespace

        from msx.debugger.prompt import _sl_bank
        from msx.ram_mapper import RamMapper
        rm = RamMapper()
        rm.banks[3] = 2
        mem = SimpleNamespace(_mapper=None, _mapper2=None, ram_mapper=rm)
        assert _sl_bank(mem, 3, 2, 3) == "seg=2"

    def test_sl_bank_dash_for_bios(self):
        from types import SimpleNamespace

        from msx.debugger.prompt import _sl_bank
        mem = SimpleNamespace(_mapper=None, _mapper2=None, ram_mapper=None)
        assert _sl_bank(mem, 0, None, 0) == "-"


# ---------------------------------------------------------------------------
# ce / cd — mapper bank-switch trace
# ---------------------------------------------------------------------------

def _make_mapper_machine(mapper):
    from msx.mapper import FlatMapper
    m = _make_machine()
    m.memory = MagicMock()
    m.memory._mapper = mapper
    m.memory._mapper2 = FlatMapper(None)
    m.cpu.instruction_pc = 0x402E
    m.cycle_count = 45231
    m.vdp._frame_count = 3
    return m


class TestMapperTrace:
    @staticmethod
    def _rom16(pages: int):
        return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(16384)])

    def test_ce_enables_and_logs_switch(self, capsys):
        from msx.mapper import Ascii16Mapper
        mapper = Ascii16Mapper(self._rom16(8))
        m = _make_mapper_machine(mapper)
        Debugger(m)._cmd_mapper_trace_enable()
        capsys.readouterr()  # discard the "enabled" line
        mapper.write(0x7000, 1)  # window 1: 0 -> 1
        out = capsys.readouterr().out
        assert "MAP_BANK win=1 00h->01h addr=7000h" in out
        assert "PC=402E" in out

    def test_cd_disables(self, capsys):
        from msx.mapper import Ascii16Mapper
        mapper = Ascii16Mapper(self._rom16(8))
        m = _make_mapper_machine(mapper)
        dbg = Debugger(m)
        dbg._cmd_mapper_trace_enable()
        dbg._cmd_mapper_trace_disable()
        capsys.readouterr()
        mapper.write(0x7000, 2)
        assert capsys.readouterr().out == ""

    def test_ce_no_mapper_message(self, capsys):
        from msx.mapper import FlatMapper
        m = _make_mapper_machine(FlatMapper(b"\x00" * 32768))
        Debugger(m)._cmd_mapper_trace_enable()
        out = capsys.readouterr().out
        assert "no bank-switching ROM mapper" in out


# ---------------------------------------------------------------------------
# bh / bs — crash-signature auto-break commands
# ---------------------------------------------------------------------------

class TestBreakHaltDi:
    def test_bh_toggles_on(self, capsys):
        m = _make_machine()
        m._break_halt_di = False
        Debugger(m)._cmd_break_halt()
        m.set_break_halt_di.assert_called_once_with(True)
        assert "enabled" in capsys.readouterr().out

    def test_bh_toggles_off(self, capsys):
        m = _make_machine()
        m._break_halt_di = True
        Debugger(m)._cmd_break_halt()
        m.set_break_halt_di.assert_called_once_with(False)
        assert "disabled" in capsys.readouterr().out


class TestBreakSp:
    def test_bs_no_args_uses_machine_ram_range(self, capsys):
        m = _make_machine()
        m.memory.main_ram_range.return_value = (0x8000, 0xFFFF)
        Debugger(m)._cmd_break_sp([])
        m.set_sp_range.assert_called_once_with((0x8000, 0xFFFF))
        out = capsys.readouterr().out
        assert "8000h-FFFFh" in out and "auto" in out

    def test_bs_off_disables(self, capsys):
        m = _make_machine()
        Debugger(m)._cmd_break_sp(["off"])
        m.set_sp_range.assert_called_once_with(None)
        assert "disabled" in capsys.readouterr().out

    def test_bs_explicit_range(self, capsys):
        m = _make_machine()
        Debugger(m)._cmd_break_sp(["c000", "ffff"])
        m.set_sp_range.assert_called_once_with((0xC000, 0xFFFF))

    def test_bs_swaps_reversed_range(self, capsys):
        m = _make_machine()
        Debugger(m)._cmd_break_sp(["ffff", "c000"])
        m.set_sp_range.assert_called_once_with((0xC000, 0xFFFF))


# ---------------------------------------------------------------------------
# g / so — targeted execution control
# ---------------------------------------------------------------------------

class TestGoto:
    def test_g_sets_temp_breakpoint_and_resumes(self, capsys):
        m = _make_machine()
        assert Debugger(m)._cmd_goto(["8031"]) is True
        m.set_temp_breakpoint.assert_called_once_with(0x8031)
        assert "8031h" in capsys.readouterr().out

    def test_g_no_args_does_not_resume(self, capsys):
        m = _make_machine()
        assert Debugger(m)._cmd_goto([]) is False
        m.set_temp_breakpoint.assert_not_called()
        assert "Usage" in capsys.readouterr().out

    def test_g_invalid_addr_does_not_resume(self, capsys):
        m = _make_machine()
        assert Debugger(m)._cmd_goto(["zz"]) is False
        m.set_temp_breakpoint.assert_not_called()


class TestStepOut:
    def test_so_records_current_sp(self, capsys):
        m = _make_machine()
        m.cpu.registers.SP = 0xF2EC
        Debugger(m)._cmd_step_out()
        m.set_step_out.assert_called_once_with(0xF2EC)
        assert "F2EC" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# wl — watchpoint list (exercises the _current_entries closure)
# ---------------------------------------------------------------------------

class TestWatchList:
    def test_wl_empty(self, capsys):
        m = _make_machine()
        m._watch_read = set()
        m._watch_write = set()
        Debugger(m)._cmd_watch(["l"])
        out = capsys.readouterr().out
        assert "no watchpoints" in out

    def test_wl_lists_read_write_and_rw_modes(self, capsys):
        m = _make_machine()
        m._watch_read = {0x1234}
        m._watch_write = {0x5678, 0x1234}
        Debugger(m)._cmd_watch(["l"])
        out = capsys.readouterr().out
        # 0x1234 is in both sets -> "rw"; 0x5678 write-only -> "w"; sorted ascending
        assert "1234h [rw]" in out
        assert "5678h [w]" in out
        assert out.index("1234h") < out.index("5678h")
