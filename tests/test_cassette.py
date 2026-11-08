"""io-cassette: the cassette port in the core (edge list, input level, motor,
position, exact read time, reset and saved state), the register 14 bit 7
scenarios of audio-psg, and the tape members of machine-core."""
from __future__ import annotations

import dataclasses
import struct

import pytest

from host.machine_config import load_machine_config
from msx.boundary import (
    STATUS_BAD_ARGUMENT,
    STATUS_BAD_IMAGE,
    STATUS_NO_MEDIUM,
    STATUS_NO_TAPE_PORT,
    STATUS_OK,
    STATUS_STATE_REJECTED,
    TRACE_DEBUG,
    TRACE_EVENT_TAPE_MOTOR,
    TapeInfo,
)
from msx.cassette import CassettePort
from msx.clock import CoreClock
from msx.input import InputLines
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.psg import PSG
from tests.factories import _msx1_spec

# DI; slot 3 RAM on pages 2-3; the PPI mode-set word 0x82 (Port C output,
# latch 0, so the motor starts); select PSG register 14; then read it into a
# 256-byte ring at 0xC000 forever.
_READ_LOOP = bytes([
    0xF3,              # DI
    0x3E, 0xF0,        # LD A,0F0h
    0xD3, 0xA8,        # OUT (0A8h),A
    0x3E, 0x82,        # LD A,82h
    0xD3, 0xAB,        # OUT (0ABh),A
    0x3E, 0x0E,        # LD A,0Eh
    0xD3, 0xA0,        # OUT (0A0h),A
    0x21, 0x00, 0xC0,  # LD HL,0C000h
    0xDB, 0xA2,        # loop: IN A,(0A2h)
    0x77,              # LD (HL),A
    0x2C,              # INC L
    0x18, 0xFA,        # JR loop
])

# As _READ_LOOP up to the register select, then two reads into B and C.
_TWO_READS = bytes([
    0xF3, 0x3E, 0xF0, 0xD3, 0xA8, 0x3E, 0x82, 0xD3, 0xAB, 0x3E, 0x0E, 0xD3, 0xA0,
    0xDB, 0xA2,        # IN A,(0A2h)
    0x47,              # LD B,A
    0xDB, 0xA2,        # IN A,(0A2h)
    0x4F,              # LD C,A
    0x18, 0xFE,        # JR $
])

_IDLE = bytes([0xF3, 0x18, 0xFE])  # DI; JR $


def _rom(code: bytes) -> bytes:
    return code + bytes(32768 - len(code))


def _machine(code: bytes = _IDLE, port: bool = True) -> Machine:
    spec = dataclasses.replace(_msx1_spec(), cassette_port=port)
    return build_machine(load_machine_config(spec, bios_override=_rom(code)).config)


def _edges(intervals: list[int]) -> bytes:
    return struct.pack(f"<{len(intervals)}I", *intervals)


def _varied_tape(count: int = 40000) -> bytes:
    return _edges([200 + (i * 37) % 700 for i in range(count)])


def _deck(intervals: list[int]) -> tuple[CassettePort, CoreClock]:
    clock = CoreClock()
    deck = CassettePort(clock)
    assert deck.mount(_edges(intervals)) is None
    return deck, clock


def _level_at(deck: CassettePort, clock: CoreClock, position: int) -> int:
    deck.set_motor(False)
    deck.seek(position)
    deck.set_motor(True)
    return deck.read_level()


# --- The edge list describes the tape signal ---

def test_the_level_alternates_from_1() -> None:
    deck, clock = _deck([100, 50, 25])
    assert deck.length == 175
    for position in (0, 50, 99):
        assert _level_at(deck, clock, position) == 1
    for position in (100, 149):
        assert _level_at(deck, clock, position) == 0
    for position in (150, 174):
        assert _level_at(deck, clock, position) == 1


@pytest.mark.parametrize("bad", [bytes(6), b"", _edges([10, 0, 5])])
def test_a_bad_list_is_rejected(bad: bytes) -> None:
    deck, _ = _deck([100, 50])
    deck.seek(120)
    assert deck.mount(bad) is not None
    assert deck.length == 150 and deck.position() == 120
    empty = CassettePort(CoreClock())
    assert empty.mount(bad) is not None
    assert not empty.mounted


# --- Register 14 bit 7 reads the tape level ---

def _psg(deck: CassettePort | None, lines: InputLines | None = None) -> PSG:
    psg = PSG(_input=lines if lines is not None else InputLines())
    if deck is not None:
        psg.attach_cassette(deck)
    psg.write_port(0xA0, 14)
    return psg


def test_no_tape_reads_1() -> None:
    assert _psg(CassettePort(CoreClock())).read_port(0xA2) & 0x80 == 0x80


def test_the_motor_off_reads_1() -> None:
    deck, _ = _deck([100, 50, 25])
    deck.seek(120)
    assert _psg(deck).read_port(0xA2) & 0x80 == 0x80


def test_the_motor_on_reads_the_level() -> None:
    deck, _ = _deck([100, 50, 25])
    deck.seek(120)
    deck.set_motor(True)
    assert _psg(deck).read_port(0xA2) & 0x80 == 0


def test_the_end_of_the_tape_reads_1() -> None:
    deck, clock = _deck([100, 50])  # the last interval is at level 0
    deck.set_motor(True)
    psg = _psg(deck)
    clock.cycle = 120
    assert psg.read_port(0xA2) & 0x80 == 0
    for cycle in (150, 151, 10_000):
        clock.cycle = cycle
        assert psg.read_port(0xA2) & 0x80 == 0x80
    assert deck.position() == 150


def test_no_port_reads_0() -> None:
    assert _psg(None).read_port(0xA2) & 0x80 == 0


def test_bits_0_to_6_do_not_change() -> None:
    lines = InputLines()
    lines.joy1 &= ~0x01  # Joy1 Up, JOY_SELECT 0
    deck, _ = _deck([100, 50])
    deck.seek(120)
    deck.set_motor(True)
    value = _psg(deck, lines).read_port(0xA2)
    assert value == 0x7E  # bit 0 = 0, bits 1-6 = 1, bit 7 = tape level 0


def test_register_14_bit_7_is_the_cassette_input() -> None:
    # audio-psg "Register 14 bit 7 is the cassette input".
    clock = CoreClock()
    deck = CassettePort(clock)
    psg = _psg(deck)
    no_tape = psg.read_port(0xA2)
    deck.mount(_edges([100, 50]))
    deck.seek(120)
    deck.set_motor(True)
    level_0 = psg.read_port(0xA2)
    assert no_tape & 0x80 == 0x80 and level_0 & 0x80 == 0
    assert no_tape & 0x7F == level_0 & 0x7F


# --- PPI Port C bit 4 drives the motor ---

def test_a_write_of_bit_4_0_starts_the_motor() -> None:
    machine = _machine()
    ppi, deck = machine.ppi, machine.cassette
    assert ppi is not None and deck is not None
    ppi.write_port(0xAB, 0x82)
    ppi.write_port(0xAA, 0x50)
    assert not deck.motor
    ppi.write_port(0xAA, 0x40)
    assert deck.motor


def test_a_bit_reset_starts_the_motor() -> None:
    machine = _machine()
    ppi, deck = machine.ppi, machine.cassette
    assert ppi is not None and deck is not None
    ppi.write_port(0xAB, 0x82)
    ppi.write_port(0xAA, 0x50)
    ppi.write_port(0xAB, 0x08)  # Port C bit 4 reset
    assert deck.motor
    ppi.write_port(0xAB, 0x09)  # Port C bit 4 set
    assert not deck.motor


def test_input_mode_keeps_the_motor() -> None:
    machine = _machine()
    ppi, deck = machine.ppi, machine.cassette
    assert ppi is not None and deck is not None
    ppi.write_port(0xAB, 0x82)
    ppi.write_port(0xAA, 0x40)
    assert deck.motor
    ppi.write_port(0xAB, 0x9B)
    ppi.write_port(0xAA, 0x50)
    assert deck.motor


def test_a_mode_set_word_drives_the_latch() -> None:
    machine = _machine()
    ppi, deck = machine.ppi, machine.cassette
    assert ppi is not None and deck is not None
    ppi.write_port(0xAB, 0x9B)
    ppi.write_port(0xAA, 0x00)
    assert not deck.motor  # power-on: off, and input mode drives nothing
    ppi.write_port(0xAB, 0x82)
    assert deck.motor


# --- The position advances only while the motor runs ---

def test_motor_off_does_not_advance() -> None:
    machine = _machine()
    deck = machine.cassette
    assert deck is not None
    deck.mount(_varied_tape())
    deck.seek(5000)
    for _ in range(10):
        machine.run_frame()
    assert deck.position() == 5000


def test_motor_on_and_off_adds_the_on_time() -> None:
    deck, clock = _deck([10_000_000])
    deck.set_motor(True)
    clock.cycle += 1000
    deck.set_motor(False)
    clock.cycle += 5000
    deck.set_motor(True)
    clock.cycle += 2000
    assert deck.position() == 3000


def _tape_machine() -> Machine:
    machine = _machine(_READ_LOOP)
    assert machine.cassette is not None
    assert machine.cassette.mount(_varied_tape()) is None
    return machine


def test_host_stepping_does_not_change_the_result() -> None:
    frames = 8
    whole = _tape_machine()
    for _ in range(frames):
        whole.run_frame()
    stepped = _tape_machine()
    for _ in range(frames):
        for _ in range(100):
            stepped.step()
        stepped.run_frame()
    assert stepped.vdp.frame_count == whole.vdp.frame_count == frames
    assert stepped.save_state() == whole.save_state()
    # The loop really read the tape: both levels are in the ring.
    ring = {whole.memory.read(0xC000 + i) & 0x80 for i in range(256)}
    assert ring == {0x00, 0x80}


def test_a_breakpoint_arm_gives_the_same_result() -> None:
    frames = 6
    whole = _tape_machine()
    for _ in range(frames):
        whole.run_frame()
    debug = _tape_machine()
    debug.set_breakpoints([0x7000])  # never reached: forces the debug arm
    for _ in range(frames):
        debug.run_frame()
    assert debug.save_state() == whole.save_state()


# --- A read sees the time of its instruction ---

def test_two_reads_in_one_scanline_see_an_edge_between_them() -> None:
    probe = _machine(_TWO_READS)
    deck = probe.cassette
    assert deck is not None
    deck.mount(_edges([10_000_000]))
    positions: list[int] = []
    read = deck.read_level

    def record() -> int:
        positions.append(deck.position())
        return read()

    deck.read_level = record  # type: ignore[method-assign]
    probe.run_frame()
    first, second = positions
    assert 0 < second - first < 228  # both in one scanline
    machine = _machine(_TWO_READS)
    assert machine.cassette is not None
    machine.cassette.mount(_edges([(first + second) // 2, 10_000_000]))
    machine.run_frame()
    assert machine.cpu.registers.B & 0x80 == 0x80
    assert machine.cpu.registers.C & 0x80 == 0


# --- A reset keeps the tape ---

def test_the_position_survives_a_reset() -> None:
    machine = _machine()
    deck = machine.cassette
    assert deck is not None
    deck.mount(_edges([3_000_000]))
    deck.seek(1_000_000)
    assert machine.reset() == STATUS_OK
    assert deck.mounted and deck.position() == 1_000_000


# --- The tape state is saved / A load checks the tape identity ---

def test_a_load_restores_the_position_and_the_motor() -> None:
    saved = _tape_machine()
    for _ in range(3):
        saved.run_frame()
    data = saved.save_state()
    assert saved.cassette is not None and saved.cassette.motor
    target = _tape_machine()
    assert target.load_state(data) == STATUS_OK
    assert target.cassette is not None
    assert target.cassette.position() == saved.cassette.position()
    assert target.cassette.motor
    for machine in (saved, target):
        machine.run_frame()
    assert target.save_state() == saved.save_state()


def test_another_tape_is_rejected() -> None:
    saved = _tape_machine()
    saved.run_frame()
    data = saved.save_state()
    other = _machine(_READ_LOOP)
    assert other.cassette is not None
    other.cassette.mount(_edges([1234, 5678]))
    before = other.save_state()
    assert other.load_state(data) == STATUS_STATE_REJECTED
    text = other.last_error_text().decode()
    assert "tape mismatch" in text
    assert saved.cassette is not None and other.cassette.identity is not None
    assert saved.cassette.identity is not None
    assert saved.cassette.identity["sha1"] in text
    assert other.cassette.identity["sha1"] in text
    assert other.save_state() == before


@pytest.mark.parametrize("saved_has_tape", [True, False])
def test_a_missing_tape_is_rejected(saved_has_tape: bool) -> None:
    saved = _tape_machine() if saved_has_tape else _machine(_READ_LOOP)
    data = saved.save_state()
    target = _machine(_READ_LOOP) if saved_has_tape else _tape_machine()
    before = target.save_state()
    assert target.load_state(data) == STATUS_STATE_REJECTED
    assert target.save_state() == before


def test_the_state_carries_no_edge_list() -> None:
    import json

    machine = _tape_machine()
    tape = json.loads(machine.save_state())["tape"]
    assert set(tape) == {"identity", "position", "motor"}
    assert set(tape["identity"]) == {"size", "sha1"}


def test_no_port_saves_null() -> None:
    import json

    assert json.loads(_machine(port=False).save_state())["tape"] is None


# --- machine-core: the host mounts, ejects and seeks the tape ---

def test_a_mount_starts_at_position_0() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([5_000_000])) == STATUS_OK
    assert machine.seek_tape(2_000_000) == STATUS_OK
    assert machine.mount_tape(_edges([1_000, 2_000])) == STATUS_OK
    assert machine.tape_info() == TapeInfo(mounted=1, motor=0, position=0, length=3_000)


def test_a_bad_list_keeps_the_old_tape() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([5_000_000])) == STATUS_OK
    assert machine.seek_tape(2_000_000) == STATUS_OK
    assert machine.mount_tape(bytes(6)) == STATUS_BAD_IMAGE
    assert machine.last_error_text() != b""
    assert machine.tape_info() == TapeInfo(mounted=1, motor=0, position=2_000_000,
                                           length=5_000_000)


def test_eject_empties_the_deck() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([5_000_000])) == STATUS_OK
    assert machine.eject_tape() == STATUS_OK
    assert machine.eject_tape() == STATUS_OK
    assert machine.tape_info() == TapeInfo()


def test_no_port() -> None:
    machine = _machine(port=False)
    for status in (machine.mount_tape(_edges([1000])), machine.eject_tape(),
                   machine.seek_tape(0)):
        assert status == STATUS_NO_TAPE_PORT
        assert b"no cassette port" in machine.last_error_text()
    assert machine.tape_info() == TapeInfo()


def test_a_seek_moves_the_position() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([10_000_000])) == STATUS_OK
    assert machine.seek_tape(3_000_000) == STATUS_OK
    assert machine.tape_info().position == 3_000_000


@pytest.mark.parametrize("offset", [-1, 1])
def test_a_seek_outside_the_tape_is_rejected(offset: int) -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([10_000_000])) == STATUS_OK
    assert machine.seek_tape(3_000_000) == STATUS_OK
    target = -1 if offset < 0 else 10_000_000 + 1
    assert machine.seek_tape(target) == STATUS_BAD_ARGUMENT
    assert machine.tape_info().position == 3_000_000


def test_a_seek_with_no_tape() -> None:
    assert _machine().seek_tape(0) == STATUS_NO_MEDIUM


# --- machine-core: the host reads the tape state ---

def _motor_on(machine: Machine) -> None:
    """Start the motor the way the guest does: Port C output, bit 4 = 0."""
    machine.ppi.write_port(0xAB, 0x82)
    machine.ppi.write_port(0xAA, 0x40)


def test_the_status_while_playing() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([10_000_000])) == STATUS_OK
    _motor_on(machine)
    machine.clock.cycle += 3_579_545
    assert machine.tape_info() == TapeInfo(mounted=1, motor=1, position=3_579_545,
                                           length=10_000_000)


def test_the_status_is_a_pure_query() -> None:
    machine = _tape_machine()
    machine.run_frame()
    before = machine.save_state()
    first = machine.tape_info()
    assert machine.tape_info() == first
    assert first.motor == 1 and first.position > 0
    assert machine.save_state() == before


# --- machine-core: the motor change is a trace event ---

def test_motor_on_and_off() -> None:
    machine = _machine()
    ppi = machine.ppi
    ppi.write_port(0xAB, 0x82)
    ppi.write_port(0xAA, 0x50)  # motor off
    assert machine.mount_tape(_edges([10_000_000])) == STATUS_OK
    assert machine.set_trace_kinds(TRACE_DEBUG) == STATUS_OK
    machine.drain_trace()
    ppi.write_port(0xAA, 0x40)  # motor on
    machine.clock.cycle += 1000
    ppi.write_port(0xAA, 0x50)  # motor off
    events = [e for e in machine.drain_trace() if e.kind == TRACE_EVENT_TAPE_MOTOR]
    assert [(e.a, e.b | (e.c << 32), e.d) for e in events] == [(1, 0, 1), (0, 1000, 1)]


def test_a_motor_event_at_a_position_of_2_31_or_more() -> None:
    machine = _machine()
    position = (1 << 32) + (1 << 31) + 5
    assert machine.mount_tape(_edges([0xFFFFFFFF, 0xFFFFFFFF])) == STATUS_OK
    assert machine.seek_tape(position) == STATUS_OK
    assert machine.set_trace_kinds(TRACE_DEBUG) == STATUS_OK
    machine.drain_trace()
    _motor_on(machine)
    (event,) = [e for e in machine.drain_trace() if e.kind == TRACE_EVENT_TAPE_MOTOR]
    assert (event.b, event.c) == (-(1 << 31) + 5, 1)
    assert -(1 << 31) <= event.b < (1 << 31)
    assert (event.b & 0xFFFFFFFF) | (event.c << 32) == position


def test_no_motor_event_without_trace_debug() -> None:
    machine = _machine()
    assert machine.mount_tape(_edges([10_000_000])) == STATUS_OK
    _motor_on(machine)
    assert all(e.kind != TRACE_EVENT_TAPE_MOTOR for e in machine.drain_trace())
