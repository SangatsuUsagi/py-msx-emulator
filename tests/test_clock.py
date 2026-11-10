"""machine-core "Core components read the emulated time from one clock
record" (msx/clock.py)."""
from __future__ import annotations

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.rtc import RTC
from tests.factories import make_machine, make_machine_msx2

_NOP_ROM = bytes(32768)


def test_every_component_shares_the_machines_clock_record() -> None:
    m = make_machine_msx2(_NOP_ROM, bytes(0x4000), cartridge=bytes(131072),
                          mapper="Majutsushi")
    clock = m.clock
    assert m.cpu._clock is clock
    assert m.vdp._clock is clock
    assert m.psg._clock is clock
    assert m.ppi is not None and m.ppi._clock is clock
    assert m.rtc is not None and m.rtc._clock is clock
    assert m.dac is not None and m.dac._clock is clock
    assert m._trace._clock is clock


def test_an_msx1_machine_shares_the_clock_record() -> None:
    m = make_machine(_NOP_ROM)
    clock = m.clock
    assert m.cpu._clock is clock
    assert m.vdp._clock is clock
    assert m.psg._clock is clock
    assert m.ppi is not None and m.ppi._clock is clock
    assert m._trace._clock is clock


def test_the_clock_values_match_the_machines_counters() -> None:
    m = make_machine(_NOP_ROM)
    m.run_frame(skip_render=True)
    pc = m.cpu_registers().PC
    m.step()
    clock = m.clock
    assert clock.cycle == m.cycle_count
    assert clock.frame == m.frame_number() == 1
    assert clock.instruction_pc == pc


def test_a_component_built_alone_keeps_its_own_clock_record() -> None:
    psg = PSG()
    psg.write_port(0xA0, 8)
    psg.write_port(0xA1, 0x0F)
    assert psg._events == [(0, 8, 0x0F)]

    mem = Memory(rom=bytes(32768), ram=bytearray(32768), mapper1=FlatMapper(None))
    ppi = PPI(memory=mem)
    ppi.write_port(0xAA, 0x80)
    assert ppi.click._events[0][0] == 0

    rtc = RTC()
    assert rtc._uncounted_cycles() == 0
    assert psg._clock is not ppi._clock
