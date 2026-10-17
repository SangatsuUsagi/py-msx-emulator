"""Tests for the PPI key click (openspec/specs/audio-key-click/spec.md)."""

import struct

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ppi import KEY_CLICK_LEVEL, PPI
from tests.factories import make_machine, make_machine_msx2

_NOP_ROM = bytes(32768)


def make_ppi(cycle: int = 0) -> PPI:
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    ppi = PPI(memory=mem)
    ppi._get_cycle = lambda: cycle
    return ppi


def _samples(buf: bytearray | None) -> list[int]:
    assert buf is not None
    return [struct.unpack_from("<h", buf, i * 2)[0] for i in range(len(buf) // 2)]


def test_key_click_level_value() -> None:
    assert KEY_CLICK_LEVEL == 9288


def test_port_aa_write_setting_bit7_is_recorded() -> None:
    ppi = make_ppi(cycle=120)
    ppi.write_port(0xAA, 0x80)
    assert ppi.click._events == [(120, 9288)]


def test_port_ab_bit_set_reset_on_bit7_is_recorded() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x0F)  # bit 7 set
    ppi.write_port(0xAB, 0x0E)  # bit 7 reset
    assert [level for _, level in ppi.click._events] == [9288, 0]


def test_write_leaving_bit7_unchanged_records_nothing() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)
    before = list(ppi.click._events)
    ppi.write_port(0xAA, 0x85)  # row select changes, bit 7 stays 1
    assert ppi.click._events == before


def test_mode_set_word_records_nothing() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)
    before = list(ppi.click._events)
    ppi.write_port(0xAB, 0x82)
    assert ppi.click._events == before


def test_bit7_set_gives_click_level() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)
    assert _samples(ppi.click.generate_samples(2, 0, 100)) == [9288, 9288]


def test_fresh_ppi_click_is_silent() -> None:
    assert make_ppi().click.generate_samples(4, 0, 100) is None


def test_reset_silences_held_click() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)
    ppi.click.generate_samples(4, 0, 100)
    ppi.reset()
    assert ppi.click.generate_samples(2, 0, 100) is None


def test_reset_drops_pending_events() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)
    ppi.reset()
    assert ppi.click._events == []


def test_callback_set_by_build_machine_msx1() -> None:
    m = make_machine(rom=_NOP_ROM)
    assert m.ppi is not None
    assert m.ppi._get_cycle is not None
    m.cycle_count = 1234
    assert m.ppi._get_cycle() == 1234


def test_callback_set_by_build_machine_msx2() -> None:
    m = make_machine_msx2(_NOP_ROM, bytes(0x4000))
    assert m.ppi is not None
    assert m.ppi._get_cycle is not None
    m.cycle_count = 5678
    assert m.ppi._get_cycle() == 5678
