"""SCC-I cartridge (SCCICart) save/load state round-trip.

Ground truth for SCCICart/SCC register semantics: see tests/test_scc_i_cart.py
and tests/test_scc.py. This file only checks that save_state/load_state
faithfully round-trips SCCICart's RAM/bank/mode-register state (via the
generic slot-2 mapper2_kind/mapper2_state mechanism, msx/state.py -- SCCICart
lives in slot 2 as of --extension scc-plus, and is persisted the same way any
flat slot-2 Mapper is, since generalize-slot2-mapper-state) and the carried
SCC chip's own state (via the existing generic scc save-state path).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.machine import Machine
from msx.machine_loader import MachineSpec, _ExtensionOverlay, _RomEntry, build_machine
from msx.state import load_state, save_state

_RGB = bytearray(256 * 192 * 3)


def _msx1_spec(main_rom_dir: Path) -> MachineSpec:
    (main_rom_dir / "main.rom").write_bytes(bytes(32768))
    return MachineSpec(
        name="test_msx1",
        generation="msx1",
        rom_base_dir=main_rom_dir,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=None,
        has_ram_mapper=False,
        ram_size_kb=64,
        has_v9938=False,
        has_rtc=False,
    )


@pytest.fixture()
def saves_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


def _scc_plus_machine(tmp_path: Path, name: str = "m") -> Machine:
    main_dir = tmp_path / f"{name}_main_rom"
    main_dir.mkdir()
    return build_machine(
        _msx1_spec(main_dir), extension_overlay=_ExtensionOverlay(device="scc_i_cart")
    )


# ---------------------------------------------------------------------------
# RAM, bank registers, mode register
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_ram(saves_dir: Path, tmp_path: Path) -> None:
    machine = _scc_plus_machine(tmp_path)
    cart = machine.memory._mapper2
    cart.write(0xBFFE, 0x01)  # window 0 RAM-write
    cart.write(0x4000, 0xAB)
    save_state(machine, _RGB, "test")

    cart.write(0x4000, 0x00)
    load_state(machine)

    assert cart.read(0x4000) == 0xAB


def test_roundtrip_preserves_banks_and_mode_register(saves_dir: Path, tmp_path: Path) -> None:
    machine = _scc_plus_machine(tmp_path)
    cart = machine.memory._mapper2
    cart.write(0x7000, 5)  # window 1 -> block 5
    cart.write(0xBFFE, 0x20)  # Plus mode
    cart.write(0xB000, 0x80)  # window 3 enable bit -> SCC+ window active
    save_state(machine, _RGB, "test")

    cart.write(0x7000, 0)
    cart.write(0xBFFE, 0x00)
    load_state(machine)

    assert cart._banks[1] == 5
    assert cart._mode_register == 0x20
    assert cart._scc_window_base == 0xB800


# ---------------------------------------------------------------------------
# Carried SCC chip state (registers + Plus mode)
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_scc_registers_and_plus_mode(saves_dir: Path, tmp_path: Path) -> None:
    machine = _scc_plus_machine(tmp_path)
    cart = machine.memory._mapper2
    cart.write(0xBFFE, 0x20)  # Plus mode
    cart.write(0xB000, 0x80)  # SCC+ window active
    cart.write(0xB880, 0x22)  # channel 5 waveform byte 0 (Plus-mode offset 0x80)
    save_state(machine, _RGB, "test")

    machine.scc.reset()  # also clears _plus_mode back to Compatible
    load_state(machine)

    assert machine.scc._plus_mode is True
    assert machine.scc.read(0x80) == 0x22


# ---------------------------------------------------------------------------
# Extension mismatch (loading a state saved without --extension scc-plus
# into a machine with it active, or vice versa): as of
# generalize-slot2-mapper-state, this IS a flat slot-2 mapper2_kind mismatch
# and now raises ValueError, mirroring slot 1's existing strict mapper_kind
# check -- a deliberate behavior change from the prior silent-skip
# (msx/state.py's now-removed _restore_scci).
# ---------------------------------------------------------------------------

def test_loading_plain_state_into_scc_plus_machine_raises(
    saves_dir: Path, tmp_path: Path
) -> None:
    plain_dir = tmp_path / "plain_main_rom"
    plain_dir.mkdir()
    plain = build_machine(_msx1_spec(plain_dir))
    save_state(plain, _RGB, "test")

    scc_plus_machine = _scc_plus_machine(tmp_path, name="scc")
    with pytest.raises(ValueError, match="slot 2 mapper mismatch"):
        load_state(scc_plus_machine)


def test_loading_scc_plus_state_into_plain_machine_raises(
    saves_dir: Path, tmp_path: Path
) -> None:
    scc_plus_machine = _scc_plus_machine(tmp_path, name="scc")
    cart = scc_plus_machine.memory._mapper2
    cart.write(0xBFFE, 0x01)  # window 0 RAM-write
    cart.write(0x4000, 0xAB)
    save_state(scc_plus_machine, _RGB, "test")

    plain_dir = tmp_path / "plain_main_rom"
    plain_dir.mkdir()
    plain = build_machine(_msx1_spec(plain_dir))
    with pytest.raises(ValueError, match="slot 2 mapper mismatch"):
        load_state(plain)


# ---------------------------------------------------------------------------
# No --scc-plus: existing save/load behaviour unaffected
# ---------------------------------------------------------------------------

def test_machine_without_scc_plus_roundtrips(saves_dir: Path, tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    machine = build_machine(_msx1_spec(main_dir))
    machine.cpu.registers.A = 0x42
    save_state(machine, _RGB, "test")

    machine.cpu.registers.A = 0x00
    load_state(machine)

    assert machine.cpu.registers.A == 0x42
