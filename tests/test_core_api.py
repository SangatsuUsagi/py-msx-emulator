"""machine-core "Host and frontend reach the core only through the core API"
(the machine half), "Core inspection queries return integers and bytes" and
"Inspection queries do not change emulated state"."""
from __future__ import annotations

import inspect
import typing
from dataclasses import fields

import pytest

from msx.boundary import (
    MAX_BREAKPOINTS,
    MAX_WATCHPOINTS,
    STATUS_BAD_ARGUMENT,
    STATUS_OK,
    STOP_NONE,
    STOP_WATCHPOINT,
    TRACE_CAPACITY,
    TRACE_DEBUG,
    TRACE_EVENT_LOST,
    TRACE_EVENT_VDP_PORT99,
    TRACE_MAPPER,
    TRACE_VDP,
    VDP_KIND_TMS9918A,
    VDP_KIND_V9938,
    WATCH_READ,
    WATCH_WRITE,
    CpuRegisters,
    SlotInfo,
    SlotLayout,
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
    m.set_watchpoints(
        [(0xC000, WATCH_READ | WATCH_WRITE), (0x8000, WATCH_READ), (0x9000, WATCH_WRITE)]
    )
    m.set_break_halt_di(True)
    assert m.breakpoints() == (0x0100, 0x4000)
    assert m.watchpoints() == (
        (0x8000, WATCH_READ), (0x9000, WATCH_WRITE), (0xC000, WATCH_READ | WATCH_WRITE)
    )
    assert m.break_halt_di() is True


def test_watchpoints_round_trip_as_bit_flags() -> None:
    # machine-core "Watchpoints round-trip as bit flags".
    m = _msx1()
    status = m.set_watchpoints([(0xC000, WATCH_READ), (0xD000, WATCH_READ | WATCH_WRITE)])
    assert status == STATUS_OK
    assert m.watchpoints() == ((0xC000, WATCH_READ), (0xD000, WATCH_READ | WATCH_WRITE))


@pytest.mark.parametrize("kind", [0, 4, WATCH_READ | 8, -1])
def test_an_invalid_watch_kind_is_rejected(kind: int) -> None:
    # machine-core "An invalid watch kind is rejected".
    m = _msx1()
    m.set_watchpoints([(0xC000, WATCH_WRITE)])
    status = m.set_watchpoints([(0x8000, WATCH_READ), (0x9000, kind)])
    assert status == STATUS_BAD_ARGUMENT
    assert m.watchpoints() == ((0xC000, WATCH_WRITE),)
    assert m.last_error_text() == f"invalid watch kind {kind}".encode()


def test_a_later_success_clears_the_message() -> None:
    # machine-core "A later success clears the message".
    m = _msx1()
    assert m.set_watchpoints([(0xC000, 0)]) == STATUS_BAD_ARGUMENT
    assert m.last_error_text() != b""
    assert m.set_watchpoints([(0xC000, WATCH_READ)]) == STATUS_OK
    assert m.last_error_text() == b""


def test_status_flags_survive_the_vdp_query() -> None:
    for m in (_msx1(), _msx2()):
        m.vdp.status |= 0x80  # S#0 F: frame interrupt flag
        assert m.vdp_info().status[0] & 0x80
        assert m.vdp_info().status[0] & 0x80
        assert m.io.read_port(0x99) & 0x80


def test_memory_read_does_not_hit_a_watchpoint() -> None:
    m = _msx1()
    m.set_watchpoints([(0xC000, WATCH_READ)])
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
    m.slot_layout()
    for prim in range(4):
        for page in range(4):
            m.slot_info(prim, -1, page)
            m.slot_info(prim, page, page)


def test_queries_leave_the_snapshot_unchanged() -> None:
    for m in (_msx1(), _msx2()):
        for _ in range(3):
            m.step_frame(InputFrame(), render_video=True)
        before = m.save_state()
        cycles, frame = m.cycle_count, m.frame_number()
        _run_every_query(m)
        assert m.save_state() == before
        assert (m.cycle_count, m.frame_number()) == (cycles, frame)


# -- machine-core "The core API reports the slot layout" ---------------------

def test_an_msx1_machine_has_no_expanded_slot() -> None:
    m = _msx1()
    m.io.write_port(0xA8, 0xF0)
    assert m.slot_layout() == SlotLayout(primary=0xF0, expanded=0, secondary=bytes(4))


def test_an_msx2_machine_reports_its_expanded_slot_3() -> None:
    m = _msx2()
    m.io.write_port(0xA8, 0xC0)  # page 3 in slot 3, so 0xFFFF reaches it
    m.write_memory(0xFFFF, b"\xa0")
    layout = m.slot_layout()
    assert layout.expanded & 0x08
    assert layout.secondary[3] == 0xA0


# -- machine-core "The core API describes one slot" --------------------------

def test_a_bank_switching_cartridge_page_shows_its_bank() -> None:
    m = make_machine(_ROM, cartridge=bytes(128 * 1024), mapper="ASCII16")
    m.io.write_port(0xA8, 0xD4)  # pages 1 and 2 in slot 1
    m.write_memory(0x7000, b"\x00")  # window 1 (0x8000) -> bank 0
    info = m.slot_info(1, -1, 2)
    assert b"Ascii16" in info.content
    assert info.bank == b"bank 0 @00000-03FFF"


def test_the_main_rom_reports_its_size() -> None:
    info = _msx1().slot_info(0, -1, -1)
    assert info.size_kb == 32


def test_an_invalid_slot_is_rejected() -> None:
    m = _msx1()
    assert m.slot_info(4, -1, -1) == SlotInfo(content=b"", bank=b"", size_kb=-1)
    assert b"4" in m.last_error_text()
    assert m.slot_info(0, -1, -1).size_kb == 32
    assert m.last_error_text() == b""


@pytest.mark.parametrize("args", [(0, 4, -1), (0, -2, -1), (0, -1, 4), (0, -1, -2), (-1, -1, -1)])
def test_every_slot_argument_outside_its_range_is_rejected(args: tuple[int, int, int]) -> None:
    m = _msx1()
    assert m.slot_info(*args) == SlotInfo(content=b"", bank=b"", size_kb=-1)
    assert m.last_error_text() != b""


# -- machine-core "The core API sets and reports the sprite display flag" ----

def test_hidden_sprites_are_reported() -> None:
    for m in (_msx1(), _msx2()):
        assert m.sprites_hidden() is False
        m.set_sprites_hidden(True)
        assert m.sprites_hidden() is True
        m.set_sprites_hidden(False)
        assert m.sprites_hidden() is False


def test_a_saved_state_does_not_carry_the_flag() -> None:
    for m in (_msx1(), _msx2()):
        shown = m.save_state()
        m.set_sprites_hidden(True)
        assert m.save_state() == shown


# -- machine-core "The core API records trace events" ------------------------

def _ascii16_machine() -> Machine:
    return make_machine(_ROM, cartridge=bytes(128 * 1024), mapper="ASCII16")


def test_the_enabled_trace_kinds_round_trip() -> None:
    m = _ascii16_machine()
    assert m.trace_kinds() == 0
    kinds = TRACE_VDP | TRACE_MAPPER | TRACE_DEBUG
    assert m.set_trace_kinds(kinds) == STATUS_OK
    assert m.trace_kinds() == kinds


def test_an_unknown_trace_kind_is_rejected() -> None:
    m = _msx1()
    m.set_trace_kinds(TRACE_VDP)
    assert m.set_trace_kinds(0x08) == STATUS_BAD_ARGUMENT
    assert m.trace_kinds() == TRACE_VDP
    assert m.last_error_text() == b"invalid trace kinds 8"


def test_the_mapper_trace_kind_needs_a_bank_switching_mapper() -> None:
    assert _msx1().trace_sources() == TRACE_VDP | TRACE_DEBUG
    assert _ascii16_machine().trace_sources() == TRACE_VDP | TRACE_MAPPER | TRACE_DEBUG


def test_a_trace_kind_that_is_off_records_nothing() -> None:
    m = _ascii16_machine()
    m.io.write_port(0xA8, 0xD4)  # pages 1 and 2 in slot 1
    m.io.write_port(0x99, 0x01)
    m.io.write_port(0x99, 0x80)
    m.io.read_port(0xA8)
    m.memory.write(0x7000, 3)  # window 1 -> bank 3
    assert m.memory._mapper.debug_bank_info(2) == "bank 3 @0C000-0FFFF"
    assert m.drain_trace() == ()


def test_the_saved_state_does_not_carry_the_trace_kinds() -> None:
    for m in (_msx1(), _msx2()):
        off = m.save_state()
        m.set_trace_kinds(TRACE_VDP | TRACE_DEBUG)
        assert m.save_state() == off


def test_a_drain_empties_the_trace_buffer() -> None:
    m = _msx1_ram_high()
    # LD A,01h / OUT (99h),A / LD A,80h / OUT (99h),A at 0xC000 (RAM).
    m.write_memory(0xC000, bytes((0x3E, 0x01, 0xD3, 0x99, 0x3E, 0x80, 0xD3, 0x99)))
    m.cpu.registers.PC = 0xC000
    m.set_trace_kinds(TRACE_VDP)
    for _ in range(4):
        m.step()
    events = m.drain_trace()
    assert [(e.kind, e.a, e.pc) for e in events] == [
        (TRACE_EVENT_VDP_PORT99, 0x01, 0xC002),
        (TRACE_EVENT_VDP_PORT99, 0x80, 0xC006),
    ]
    assert events[0].cycle < events[1].cycle
    assert m.drain_trace() == ()


def test_a_full_trace_buffer_reports_the_lost_events() -> None:
    m = _msx1()
    m.set_trace_kinds(TRACE_VDP)
    for i in range(TRACE_CAPACITY + 3):
        m.io.write_port(0x99, i & 0xFF)
    events = m.drain_trace()
    assert len(events) == TRACE_CAPACITY + 1
    assert all(e.kind == TRACE_EVENT_VDP_PORT99 for e in events[:-1])
    assert (events[-1].kind, events[-1].a) == (TRACE_EVENT_LOST, 3)
    assert m.drain_trace() == ()


# -- machine-core "The core API has no callback into the host" ---------------

def _core_api_type_hints() -> dict[str, dict[str, object]]:
    """The resolved type hints of every CoreApi member, by member name."""
    import msx.boundary
    import msx.input
    import msx.machine
    import msx.machine_spec
    import msx.vdp._geometry

    names: dict[str, object] = {}
    for module in (msx.boundary, msx.input, msx.machine, msx.machine_spec, msx.vdp._geometry):
        names.update(vars(module))
    result: dict[str, dict[str, object]] = {}
    for name, member in vars(CoreApi).items():
        func = member.fget if isinstance(member, property) else member
        if name.startswith("_") or not callable(func):
            continue
        result[name] = typing.get_type_hints(func, globalns=names)
    return result


def test_no_core_api_member_takes_or_returns_none() -> None:
    # machine-core "No core API member takes or returns None".
    hints = _core_api_type_hints()
    assert "slot_info" in hints and "read_media" in hints
    for name, member_hints in hints.items():
        for arg, hint in member_hints.items():
            assert hint is not type(None) and type(None) not in typing.get_args(hint), (
                f"CoreApi.{name} {arg}: {hint}"
            )


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
    m.set_watchpoints([(0x0001, WATCH_READ)])  # the NOP sled reads it as an opcode
    out = m.step_frame(InputFrame(), render_video=False)
    assert out.stop_reason == STOP_WATCHPOINT
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_the_build_takes_no_logger_or_tracer() -> None:
    # machine-core "The build takes no logger or tracer".
    from msx.machine_loader import build_machine, create_machine

    for build in (create_machine, build_machine):
        assert list(inspect.signature(build).parameters) == ["machine_config"]


# -- machine-core "Every control operation returns a status" -----------------

def test_a_reset_returns_status_ok() -> None:
    assert _msx1().reset() == STATUS_OK


def test_a_memory_write_wraps_and_returns_status_ok(monkeypatch) -> None:
    m = _msx1()
    writes: list[tuple[int, int]] = []
    # Memory has __slots__: patch the class for this test.
    monkeypatch.setattr(type(m.memory), "write", lambda _self, a, v: writes.append((a, v)))
    assert m.write_memory(0xFFFF, bytes((0x12, 0x34))) == STATUS_OK
    assert writes == [(0xFFFF, 0x12), (0x0000, 0x34)]


def test_a_break_request_returns_status_ok() -> None:
    assert _msx1().request_break() == STATUS_OK


def test_the_other_control_operations_return_status_ok() -> None:
    m = _msx1()
    assert m.apply_input(InputFrame()) == STATUS_OK
    assert m.set_break_halt_di(True) == STATUS_OK
    assert m.set_sprites_hidden(True) == STATUS_OK


# -- machine-core "The core API limits breakpoints and watchpoints to four" --

def test_a_fifth_breakpoint_is_rejected() -> None:
    m = _msx1()
    four = [0x1000, 0x2000, 0x3000, 0x4000]
    assert len(four) == MAX_BREAKPOINTS
    assert m.set_breakpoints(four) == STATUS_OK
    assert m.set_breakpoints(four + [0x5000]) == STATUS_BAD_ARGUMENT
    assert m.breakpoints() == tuple(four)
    assert m.last_error_text() == b"5 breakpoints, at most 4"


def test_a_fifth_watchpoint_is_rejected() -> None:
    m = _msx1()
    m.set_watchpoints([(0xC000, WATCH_WRITE)])
    five = [(0xC000 + i, WATCH_READ) for i in range(MAX_WATCHPOINTS + 1)]
    assert m.set_watchpoints(five) == STATUS_BAD_ARGUMENT
    assert m.watchpoints() == ((0xC000, WATCH_WRITE),)


# -- machine-core "The break setters reject a bad value and take -1 for none" -

def test_an_address_outside_16_bits_is_rejected() -> None:
    m = _msx1()
    m.set_temp_breakpoint(0x4000)
    assert m.set_temp_breakpoint(0x10000) == STATUS_BAD_ARGUMENT
    assert m._temp_breakpoint == 0x4000
    assert b"0x10000" in m.last_error_text()


@pytest.mark.parametrize("call", [
    lambda m: m.set_breakpoints([0x10000]),
    lambda m: m.set_breakpoints([-1]),
    lambda m: m.set_watchpoints([(0x10000, WATCH_READ)]),
    lambda m: m.set_step_out(0x10000),
    lambda m: m.set_temp_breakpoint(-2),
    lambda m: m.set_sp_range(0xC000, 0x10000),
    lambda m: m.set_sp_range(-2, 0xF000),
])
def test_every_address_setter_rejects_a_value_outside_16_bits(call) -> None:
    m = _msx1()
    assert call(m) == STATUS_BAD_ARGUMENT
    assert m.breakpoints() == () and m.watchpoints() == ()
    assert m._stepout_sp is None and m._sp_range is None


def test_a_frame_number_below_minus_1_is_rejected() -> None:
    m = _msx1()
    m.set_frame_breakpoint(10)
    assert m.set_frame_breakpoint(-2) == STATUS_BAD_ARGUMENT
    assert m._frame_breakpoint == 10


def test_an_inverted_sp_range_is_rejected() -> None:
    m = _msx1()
    m.set_sp_range(0xC000, 0xF000)
    assert m.set_sp_range(0xF000, 0xC000) == STATUS_BAD_ARGUMENT
    assert m._sp_range == (0xC000, 0xF000)


def test_minus_1_clears_a_setting() -> None:
    m = _msx1()
    assert m.set_sp_range(0xC000, 0xF000) == STATUS_OK
    assert m.set_sp_range(-1, -1) == STATUS_OK
    assert m.set_temp_breakpoint(0x4000) == STATUS_OK
    assert m.set_temp_breakpoint(-1) == STATUS_OK
    assert m.set_frame_breakpoint(10) == STATUS_OK
    assert m.set_frame_breakpoint(-1) == STATUS_OK
    assert m._sp_range is None and m._temp_breakpoint is None
    assert m._frame_breakpoint is None
    assert not m._break_conditions_active()


@pytest.mark.parametrize("ends", [(-1, 0xF000), (0xC000, -1)])
def test_one_minus_1_end_is_rejected(ends: tuple[int, int]) -> None:
    m = _msx1()
    m.set_sp_range(0xC000, 0xF000)
    assert m.set_sp_range(*ends) == STATUS_BAD_ARGUMENT
    assert m._sp_range == (0xC000, 0xF000)


def test_a_success_after_a_rejection_clears_the_message() -> None:
    m = _msx1()
    assert m.set_step_out(0x10000) == STATUS_BAD_ARGUMENT
    assert m.last_error_text() != b""
    assert m.set_step_out(0xF000) == STATUS_OK
    assert m.last_error_text() == b""
