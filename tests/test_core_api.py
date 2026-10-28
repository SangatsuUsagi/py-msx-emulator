"""machine-core "Host and frontend reach the core only through the core API"
(the machine half), "Core inspection queries return integers and bytes" and
"Inspection queries do not change emulated state"."""
from __future__ import annotations

import inspect
from dataclasses import fields

from msx.boundary import (
    STOP_NONE,
    STOP_WATCHPOINT,
    VDP_KIND_TMS9918A,
    VDP_KIND_V9938,
    CpuRegisters,
)
from msx.core_api import CoreApi
from msx.input import InputFrame
from msx.machine import Machine
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes(32 * 1024)  # NOPs


def _msx1() -> Machine:
    return make_machine(rom=_ROM)


def _msx1_ram_high() -> Machine:
    """An MSX1 machine with slot 3 (RAM) selected in pages 2 and 3."""
    m = _msx1()
    m.io.write_port(0xA8, 0xF0)
    return m


def _msx2() -> Machine:
    return make_machine_msx2(_ROM, bytes(0x4000))


def test_machine_satisfies_the_core_api() -> None:
    core: CoreApi = _msx1()
    assert isinstance(core, CoreApi)
    assert isinstance(_msx2(), CoreApi)


def test_register_query_returns_every_register() -> None:
    m = _msx1()
    r = m.cpu.registers
    r.AF, r.BC, r.DE, r.HL = 0x1234, 0x2345, 0x3456, 0x4567
    r.AF_, r.BC_, r.DE_, r.HL_ = 0x5678, 0x6789, 0x789A, 0x89AB
    r.IX, r.IY, r.SP, r.PC = 0x9ABC, 0xABCD, 0xBCDE, 0xCDEF
    r.I, r.R = 0x3F, 0x7E
    m.cpu.iff1, m.cpu.iff2, m.cpu.im = True, False, 2
    regs = m.cpu_registers()
    assert regs == CpuRegisters(
        AF=0x1234, BC=0x2345, DE=0x3456, HL=0x4567,
        AF_=0x5678, BC_=0x6789, DE_=0x789A, HL_=0x89AB,
        IX=0x9ABC, IY=0xABCD, SP=0xBCDE, PC=0xCDEF, I=0x3F, R=0x7E,
        IFF1=1, IFF2=0, IM=2,
    )
    for f in fields(regs):
        assert type(getattr(regs, f.name)) is int


def test_memory_read_wraps_at_ffff() -> None:
    m = _msx1_ram_high()
    for addr, value in ((0xFFFE, 0x11), (0x0000, 0x33), (0x0001, 0x44)):
        m.memory.write(addr, value)
    expected = bytes(m.memory.read(a) for a in (0xFFFE, 0xFFFF, 0x0000, 0x0001))
    assert m.read_memory(0xFFFE, 4) == expected
    assert m.read_memory(0xFFFE, 1) == b"\x11"


def test_memory_write_goes_through_the_bus() -> None:
    m = _msx1_ram_high()
    m.write_memory(0xC000, b"\xaa\x55")
    assert (m.memory.read(0xC000), m.memory.read(0xC001)) == (0xAA, 0x55)


def test_vram_read_wraps_at_the_vram_size() -> None:
    for m in (_msx1(), _msx2()):
        size = m.vram_size()
        assert size == len(m.vdp.vram)
        m.vdp.vram[size - 1] = 0x5A
        m.vdp.vram[0] = 0xA5
        assert m.read_vram(size - 1, 2) == b"\x5a\xa5"


def test_vdp_query_matches_the_vdp_kind() -> None:
    info1 = _msx1().vdp_info()
    assert info1.kind == VDP_KIND_TMS9918A
    assert len(info1.regs) == 8
    assert info1.cmd_regs == b"" and info1.palette == ()
    assert len(info1.status) == 1

    m2 = _msx2()
    info2 = m2.vdp_info()
    assert info2.kind == VDP_KIND_V9938
    assert len(info2.regs) == 28
    assert len(info2.cmd_regs) == 15
    assert len(info2.status) == 10
    assert len(info2.palette) == 16
    assert all(0 <= p <= 0x1FF for p in info2.palette)


def test_machine_without_a_floppy_interface_has_no_drives() -> None:
    m = _msx1()
    assert m.fdc is None
    assert m.drive_count() == 0


def test_break_setting_queries_reflect_the_setters() -> None:
    m = _msx1()
    m.set_breakpoints([0x4000, 0x0100])
    m.set_watchpoints([(0xC000, "rw"), (0x8000, "r"), (0x9000, "w")])
    m.set_break_halt_di(True)
    assert m.breakpoints() == (0x0100, 0x4000)
    assert m.watchpoints() == ((0x8000, "r"), (0x9000, "w"), (0xC000, "rw"))
    assert m.break_halt_di() is True


def test_status_flags_survive_the_vdp_query() -> None:
    for m in (_msx1(), _msx2()):
        m.vdp.status |= 0x80  # S#0 F: frame interrupt flag
        assert m.vdp_info().status[0] & 0x80
        assert m.vdp_info().status[0] & 0x80
        assert m.io.read_port(0x99) & 0x80


def test_memory_read_does_not_hit_a_watchpoint() -> None:
    m = _msx1()
    m.set_watchpoints([(0xC000, "r")])
    m.read_memory(0xC000, 1)
    assert m.last_stop().reason == STOP_NONE
    assert not m._stop_pending


def _run_every_query(m: Machine) -> None:
    m.cpu_registers()
    m.read_memory(0x0000, 0x10000)
    m.read_vram(0, m.vram_size())
    m.vdp_info()
    m.frame_number()
    m.drive_count()
    m.main_ram_range()
    m.breakpoints()
    m.watchpoints()
    m.break_halt_di()
    m.render_preview()


def test_queries_leave_the_snapshot_unchanged() -> None:
    for m in (_msx1(), _msx2()):
        for _ in range(3):
            m.step_frame(InputFrame(), render_video=True)
        before = m.save_state()
        cycles, frame = m.cycle_count, m.frame_number()
        _run_every_query(m)
        assert m.save_state() == before
        assert (m.cycle_count, m.frame_number()) == (cycles, frame)


# -- machine-core "The core API has no callback into the host" ---------------

def test_no_core_api_member_takes_a_callback() -> None:
    # Annotations are strings here (from __future__ import annotations), so
    # the check reads them as text: no callable, hook or debugger parameter.
    for name, member in vars(CoreApi).items():
        if name.startswith("_") or not callable(member):
            continue
        assert "hook" not in name and "debugger" not in name, name
        for param in inspect.signature(member).parameters.values():
            note = str(param.annotation)
            for banned in ("Callable", "Hook", "Debugger"):
                assert banned not in note, f"{name}({param.name}: {note})"


def test_a_watchpoint_hit_prints_nothing(capsys) -> None:
    m = _msx1_ram_high()
    m.set_watchpoints([(0x0001, "r")])  # the NOP sled reads it as an opcode
    out = m.step_frame(InputFrame(), render_video=False)
    assert out.stop_reason == STOP_WATCHPOINT
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
