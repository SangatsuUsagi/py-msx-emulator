"""diag-mapper-tracer: the ROM mappers' bank-change trace events and their
MAP_BANK text (host/trace.py)."""
from __future__ import annotations

from host.trace import map_bank_line
from msx.boundary import TRACE_MAPPER
from msx.clock import CoreClock
from msx.mapper import Ascii8Mapper, Ascii16Mapper, KonamiMapper
from msx.trace import TraceLog


def _rom16(pages: int) -> bytes:
    return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(16384)])


def _attach(mapper, kinds: int = TRACE_MAPPER) -> TraceLog:
    trace = TraceLog(kinds=kinds, _clock=CoreClock(cycle=45231, frame=3, instruction_pc=0x402E))
    mapper._trace = trace
    return trace


def _text(trace: TraceLog) -> list[str]:
    return [map_bank_line(e) for e in trace.drain()]


def test_mapper_kind_off_records_nothing() -> None:
    m = Ascii16Mapper(_rom16(8))
    trace = _attach(m, kinds=0)
    m.write(0x7000, 1)
    assert trace.drain() == ()


def test_bank_change_records_one_event() -> None:
    m = Ascii16Mapper(_rom16(8))
    trace = _attach(m)
    m.write(0x7000, 1)  # window 1: bank 0 -> 1
    assert _text(trace) == [
        "CY=0000045231 FR=000003 PC=402E MAP_BANK win=1 00h->01h addr=7000h",
    ]


def test_noop_write_same_bank_records_nothing() -> None:
    m = Ascii16Mapper(_rom16(8))
    trace = _attach(m)
    m.write(0x7000, 0)  # window 1 already bank 0
    assert trace.drain() == ()


def test_behavior_identical_with_the_trace_on() -> None:
    a = Ascii16Mapper(_rom16(8))
    b = Ascii16Mapper(_rom16(8))
    _attach(b)
    for addr, val in [(0x6000, 2), (0x7000, 3), (0x6000, 5)]:
        a.write(addr, val)
        b.write(addr, val)
    assert a._banks == b._banks
    assert a.read(0x4000) == b.read(0x4000)
    assert a.read(0x8000) == b.read(0x8000)


def test_ascii8_window_index_reported() -> None:
    m = Ascii8Mapper(bytes([(p if i == 0 else 0) for p in range(8) for i in range(8192)]))
    trace = _attach(m)
    m.write(0x6800, 2)  # window 1 (0x6000 zone): bank 0 -> 2
    assert "MAP_BANK win=1 00h->02h addr=6800h" in _text(trace)[0]


def test_konami_window_and_address() -> None:
    rom = bytes([(p if i == 0 else 0) for p in range(8) for i in range(8192)])
    m = KonamiMapper(rom)
    trace = _attach(m)
    m.write(0x8000, 5)  # window 2: bank 2 default -> 5
    assert "MAP_BANK win=2 02h->05h addr=8000h" in _text(trace)[0]


# diag-mapper-tracer "ROM mappers record bank changes as trace events".

def test_a_bank_changing_write_records_an_event() -> None:
    from msx.boundary import TRACE_EVENT_MAP_BANK, TRACE_MAPPER
    from tests.factories import make_machine

    m = make_machine(bytes(32 * 1024), cartridge=_rom16(8), mapper="ASCII16")
    m.set_trace_kinds(TRACE_MAPPER)
    m.io.write_port(0xA8, 0xD4)  # pages 1 and 2 in slot 1
    m.memory.write(0x7000, 2)  # page 1 is slot 1: window 1, bank 0 -> 2
    events = m.drain_trace()
    assert [(e.kind, e.a, e.b, e.c, e.d) for e in events] == [
        (TRACE_EVENT_MAP_BANK, 1, 0, 2, 0x7000),
    ]


def test_a_mapper_with_the_kind_off_behaves_identically() -> None:
    from tests.factories import make_machine

    m = make_machine(bytes(32 * 1024), cartridge=_rom16(8), mapper="ASCII16")
    m.io.write_port(0xA8, 0xD4)  # pages 1 and 2 in slot 1
    m.memory.write(0x7000, 2)
    assert m.memory._mapper.debug_bank_info(2) == "bank 2 @08000-0BFFF"
    assert m.drain_trace() == ()


def test_no_event_when_the_write_does_not_change_the_bank() -> None:
    # diag-mapper-tracer "No record when the write does not change the bank".
    from msx.boundary import TRACE_MAPPER
    from tests.factories import make_machine

    m = make_machine(bytes(32 * 1024), cartridge=_rom16(8), mapper="ASCII16")
    m.set_trace_kinds(TRACE_MAPPER)
    m.io.write_port(0xA8, 0xD4)  # pages 1 and 2 in slot 1
    m.memory.write(0x7000, 0)  # window 1 already selects bank 0
    assert m.drain_trace() == ()
