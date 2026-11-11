"""The S1985 mapper port latch (msx/mapper_io.py) on the machines that have it."""
from __future__ import annotations

import json

import pytest

from host.machine_config import load_machine_config
from msx import state
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.snapshot_schema import SnapshotMismatchError
from msx.state import StateLoadError
from tests.test_machine_config import _overrides, _spec


def _machine(machine_id: str = "hb_f1xd") -> Machine:
    spec = _spec(machine_id)
    return build_machine(load_machine_config(spec, **_overrides(spec)).config)


# -- memory-ram-mapper "S1985 mapper port latch" ------------------------------


def test_the_latch_reads_back_five_bits_with_bit_7_set() -> None:
    io = _machine().io
    for value, expected in ((0x00, 0x80), (0x01, 0x81), (0x1F, 0x9F), (0x20, 0x80),
                            (0xFF, 0x9F)):
        io.write_port(0xFC, value)
        assert io.read_port(0xFC) == expected, hex(value)


def test_each_port_has_its_own_latch() -> None:
    io = _machine().io
    for port, value in ((0xFC, 3), (0xFD, 2), (0xFE, 1), (0xFF, 0)):
        io.write_port(port, value)
    io.write_port(0xFD, 0x23)
    assert [io.read_port(port) for port in (0xFC, 0xFD, 0xFE, 0xFF)] == [
        0x83, 0x83, 0x81, 0x80,
    ]


def test_with_no_ram_mapper_a_write_switches_no_memory() -> None:
    machine = _machine()
    assert machine.memory.ram_mapper is None
    memory = machine.memory
    memory.set_slot_register(0xFF)  # every page in slot 3
    memory.set_sub_slot_reg(0xFF)  # every page in sub-slot 3 (plain RAM)
    machine.io.write_port(0xFD, 1)
    memory.write(0x4000, 0x11)
    machine.io.write_port(0xFD, 2)
    memory.write(0x4000, 0x22)
    machine.io.write_port(0xFD, 1)
    assert memory.read(0x4000) == 0x22


def test_with_a_ram_mapper_the_latch_answers_the_read() -> None:
    machine = _machine("fs_a1f")
    assert machine.memory.ram_mapper is not None
    machine.io.write_port(0xFE, 0x23)
    assert machine.io.read_port(0xFE) == 0x83
    assert machine.memory.ram_mapper.banks[2] == 3


def test_the_latches_are_0_after_a_reset() -> None:
    machine = _machine()
    machine.io.write_port(0xFF, 0x1F)
    machine.reset()
    assert machine.io.read_port(0xFF) == 0x80


def test_the_latches_survive_a_save_state() -> None:
    machine = _machine()
    machine.io.write_port(0xFE, 0x05)
    other = _machine()
    state.load_state(other, state.save_state(machine))
    assert other.io.read_port(0xFE) == 0x85


# -- machine-state "The mapper port latch state is saved" ----------------------


@pytest.mark.parametrize("latches", [[0, 0, 256, 0], [0, 0, 0]])
def test_a_latch_value_out_of_range_is_rejected(latches: list[int]) -> None:
    machine = _machine()
    decoded = json.loads(state.save_state(machine))
    decoded["mapper_latch"] = latches
    before = state.save_state(machine)
    with pytest.raises(StateLoadError, match="mapper_latch"):
        state.load_state(machine, json.dumps(decoded).encode("utf-8"))
    assert state.save_state(machine) == before


def test_a_state_without_a_latch_does_not_load_on_an_s1985_machine() -> None:
    machine = _machine()
    decoded = json.loads(state.save_state(machine))
    decoded["mapper_latch"] = None
    before = state.save_state(machine)
    with pytest.raises(SnapshotMismatchError, match="mapper port latch"):
        state.load_state(machine, json.dumps(decoded).encode("utf-8"))
    assert state.save_state(machine) == before
