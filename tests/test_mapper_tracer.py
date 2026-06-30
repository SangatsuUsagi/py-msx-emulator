from __future__ import annotations

from io import StringIO

from msx.mapper import Ascii8Mapper, Ascii16Mapper, KonamiMapper
from msx.mapper_tracer import MapperTracer


def _rom16(pages: int) -> bytes:
    return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(16384)])


def _attach(mapper, enabled: bool = True) -> StringIO:
    buf = StringIO()
    mapper._tracer = MapperTracer(enabled=enabled, output=buf)
    mapper._get_pc = lambda: 0x402E
    mapper._get_cycle = lambda: 45231
    mapper._get_frame = lambda: 3
    return buf


def test_disabled_tracer_is_silent() -> None:
    m = Ascii16Mapper(_rom16(8))
    buf = _attach(m, enabled=False)
    m.write(0x7000, 1)
    assert buf.getvalue() == ""


def test_bank_change_emits_one_record() -> None:
    m = Ascii16Mapper(_rom16(8))
    buf = _attach(m)
    m.write(0x7000, 1)  # window 1: bank 0 -> 1
    assert buf.getvalue() == (
        "CY=0000045231 FR=000003 PC=402E MAP_BANK win=1 00h->01h addr=7000h\n"
    )


def test_noop_write_same_bank_emits_nothing() -> None:
    m = Ascii16Mapper(_rom16(8))
    buf = _attach(m)
    m.write(0x7000, 0)  # window 1 already bank 0
    assert buf.getvalue() == ""


def test_behavior_identical_without_tracer() -> None:
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
    buf = _attach(m)
    m.write(0x6800, 2)  # window 1 (0x6000 zone): bank 1 default? no, default 0 -> 2
    assert "MAP_BANK win=1 00h->02h addr=6800h" in buf.getvalue()


def test_konami_window_and_address() -> None:
    rom = bytes([(p if i == 0 else 0) for p in range(8) for i in range(8192)])
    m = KonamiMapper(rom)
    buf = _attach(m)
    m.write(0x8000, 5)  # window 2: bank 2 default -> 5
    assert "MAP_BANK win=2 02h->05h addr=8000h" in buf.getvalue()
