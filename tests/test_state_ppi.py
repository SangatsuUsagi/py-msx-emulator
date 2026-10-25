"""The PPI's own state in the save state (machine-state "The PPI state is saved")."""
from __future__ import annotations

import dataclasses
import json
import re

import pytest

from host.machine_config import build_machine_from_spec
from msx import state
from msx.machine import Machine
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ppi import KEY_CLICK_LEVEL, PPI, PpiControlRead
from msx.snapshot_schema import SnapshotMismatchError
from msx.state import StateLoadError
from tests.factories import _msx1_spec, make_machine


def _ppi() -> PPI:
    memory = Memory(rom=bytes(0x8000), ram=bytearray(0x8000), _mapper=FlatMapper(None))
    return PPI(memory=memory)


# ---------------------------------------------------------------------------
# PPI.snapshot_state / apply_state
# ---------------------------------------------------------------------------

def test_snapshot_state_round_trips_through_apply_state() -> None:
    saved = _ppi()
    saved.write_port(0xAB, 0x82)
    saved.write_port(0xAA, 0x5A)
    state = saved.snapshot_state()
    assert state == {"port_c": 0x5A, "control": 0x82, "bus_latch": 0x5A ^ 0xFF}

    loaded = _ppi()
    loaded.apply_state(state)
    assert loaded.snapshot_state() == state
    assert loaded.read_port(0xAA) == 0x5A


def test_apply_state_holds_the_click_level_without_an_event() -> None:
    ppi = _ppi()
    ppi.apply_state({"port_c": 0x80, "control": 0x82, "bus_latch": 0xFF})
    samples = ppi.click.generate_samples(4, 0, 100)
    assert samples == KEY_CLICK_LEVEL.to_bytes(2, "little", signed=True) * 4

    ppi.apply_state({"port_c": 0x00, "control": 0x82, "bus_latch": 0xFF})
    assert ppi.click.generate_samples(4, 0, 100) is None


# ---------------------------------------------------------------------------
# machine-state "The PPI state is saved"
# ---------------------------------------------------------------------------

_ROM = bytes(0x8000)


def _bus_latch_machine() -> Machine:
    spec = dataclasses.replace(_msx1_spec(), ppi_control_read=PpiControlRead.BUS_LATCH)
    return build_machine_from_spec(spec, bios_override=_ROM)


def test_a_resumed_machine_has_the_saved_port_c_and_control_word() -> None:
    saved = make_machine(_ROM)
    assert saved.ppi is not None
    saved.ppi.write_port(0xAB, 0x82)
    saved.ppi.write_port(0xAA, 0x5A)
    data = state.save_state(saved)

    loaded = make_machine(_ROM)
    state.load_state(loaded, data)
    assert state.save_state(loaded) == data   # before any port read moves the bus latch
    assert loaded.ppi is not None
    assert loaded.ppi.read_port(0xAA) == 0x5A
    assert loaded.ppi.control == 0x82


def test_the_bus_latch_is_restored() -> None:
    saved = _bus_latch_machine()
    assert saved.ppi is not None
    saved.ppi.write_port(0xA8, 0x03)          # leaves the inverse, 0xFC
    data = state.save_state(saved)

    loaded = _bus_latch_machine()
    state.load_state(loaded, data)
    assert loaded.ppi is not None
    assert loaded.ppi.read_port(0xAB) == 0xFC


def test_restoring_the_click_line_emits_no_click() -> None:
    saved = make_machine(_ROM)
    assert saved.ppi is not None
    saved.ppi.write_port(0xAA, 0x80)
    data = state.save_state(saved)

    loaded = make_machine(_ROM)
    assert loaded.ppi is not None
    assert loaded.ppi.read_port(0xAA) & 0x80 == 0
    state.load_state(loaded, data)
    # The frame's key-click buffer: the held level from its first sample on.
    n = loaded.samples_per_frame()
    click = loaded.ppi.click.generate_samples(n, 0, loaded.cycles_per_frame)
    assert click == KEY_CLICK_LEVEL.to_bytes(2, "little", signed=True) * n


@pytest.mark.parametrize(("field", "value"), [("control", 0x7F), ("port_c", 256)])
def test_an_out_of_range_ppi_value_is_rejected(field: str, value: int) -> None:
    decoded = json.loads(state.save_state(make_machine(_ROM)))
    decoded["ppi"][field] = value
    target = make_machine(_ROM)
    before = state.save_state(target)
    with pytest.raises(StateLoadError, match=re.escape(f"ppi.{field}")):
        state.load_state(target, json.dumps(decoded).encode("utf-8"))
    assert state.save_state(target) == before


def test_a_ppi_wiring_mismatch_is_rejected() -> None:
    decoded = json.loads(state.save_state(make_machine(_ROM)))
    decoded["ppi"] = None
    with pytest.raises(SnapshotMismatchError, match="PPI wiring mismatch"):
        state.load_state(make_machine(_ROM), json.dumps(decoded).encode("utf-8"))
