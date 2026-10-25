"""The PPI's own state in the save state (machine-state "The PPI state is saved")."""
from __future__ import annotations

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ppi import KEY_CLICK_LEVEL, PPI


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
