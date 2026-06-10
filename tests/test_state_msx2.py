"""Tests for MSX2 save/load state functionality."""
from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from msx.machine import make_machine, make_machine_msx2
from msx.state import CURRENT_FORMAT_VERSION, load_state, save_state

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000
_RGB_MSX1 = bytearray(256 * 192 * 3)
_RGB_MSX2 = bytearray(256 * 192 * 3)


@pytest.fixture()
def saves_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


# ---------------------------------------------------------------------------
# MSX2 round-trip
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_vram(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.vram[0x100] = 0xAB
    machine.vdp.vram[0x1FFFF] = 0xCD
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.vram[0x100] = 0x00
    machine.vdp.vram[0x1FFFF] = 0x00
    load_state(machine)

    assert machine.vdp.vram[0x100] == 0xAB
    assert machine.vdp.vram[0x1FFFF] == 0xCD


def test_msx2_roundtrip_palette(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.palette[3] = 0b101_011_110
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.palette[3] = 0
    load_state(machine)

    assert machine.vdp.palette[3] == 0b101_011_110


def test_msx2_roundtrip_ram_mapper_banks(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.ram_mapper.banks[0] = 5
    machine.memory.ram_mapper.banks[2] = 7
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.ram_mapper.banks[0] = 0
    machine.memory.ram_mapper.banks[2] = 0
    load_state(machine)

    assert machine.memory.ram_mapper.banks[0] == 5
    assert machine.memory.ram_mapper.banks[2] == 7


def test_msx2_roundtrip_ram_mapper_ram(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.ram_mapper.ram[0x8000] = 0x77
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.ram_mapper.ram[0x8000] = 0x00
    load_state(machine)

    assert machine.memory.ram_mapper.ram[0x8000] == 0x77


# ---------------------------------------------------------------------------
# machine_type mismatch raises ValueError
# ---------------------------------------------------------------------------

def test_msx1_load_into_msx2_raises(saves_dir):
    msx1 = make_machine(_ROM)
    save_state(msx1, _RGB_MSX1, "test")

    msx2 = make_machine_msx2(_ROM, _EXTROM)
    with pytest.raises(ValueError, match="machine type"):
        load_state(msx2)


def test_msx2_load_into_msx1_raises(saves_dir):
    msx2 = make_machine_msx2(_ROM, _EXTROM)
    save_state(msx2, _RGB_MSX2, "test")

    msx1 = make_machine(_ROM)
    with pytest.raises(ValueError, match="machine type"):
        load_state(msx1)


# ---------------------------------------------------------------------------
# format_version 1 rejected
# ---------------------------------------------------------------------------

def test_old_format_version_rejected(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    with open(state_path, "rb") as f:
        snap = pickle.load(f)
    snap.format_version = 1
    with open(state_path, "wb") as f:
        pickle.dump(snap, f)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# sub_slot_reg round-trip
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_sub_slot_reg(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.sub_slot_reg = 0xA5
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.sub_slot_reg = 0x00
    load_state(machine)

    assert machine.memory.sub_slot_reg == 0xA5


def test_msx1_sub_slot_reg_none_in_snapshot(saves_dir):
    """MSX1 snapshots store sub_slot_reg=None and don't touch Memory.sub_slot_reg."""
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    with open(state_path, "rb") as f:
        snap = pickle.load(f)
    assert snap.sub_slot_reg is None


# ---------------------------------------------------------------------------
# format_version 2 rejected (version bump to 3)
# ---------------------------------------------------------------------------

def test_format_version_2_rejected(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    with open(state_path, "rb") as f:
        snap = pickle.load(f)
    snap.format_version = 2
    with open(state_path, "wb") as f:
        pickle.dump(snap, f)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# MSX1 save/load unaffected
# ---------------------------------------------------------------------------

def test_msx1_roundtrip_unaffected(saves_dir):
    machine = make_machine(_ROM)
    machine.cpu.registers.A = 0x55
    machine.memory.ram[10] = 0x99
    save_state(machine, _RGB_MSX1, "test")

    machine.cpu.registers.A = 0x00
    machine.memory.ram[10] = 0x00
    load_state(machine)

    assert machine.cpu.registers.A == 0x55
    assert machine.memory.ram[10] == 0x99
