"""FM-PAC + OPLL save/load state round-trip.

Ground truth for FM-PAC/OPLL register semantics: see tests/test_fmpac.py and
tests/test_opll.py. This file only checks that save_state/load_state
faithfully round-trips FM-PAC device and OPLL synthesis state.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.machine import Machine
from msx.machine_loader import MachineSpec, _FmPacOverlay, _RomEntry, build_machine
from msx.state import load_state, save_state

_RGB = bytearray(256 * 192 * 3)
_BANK_SIZE = 0x4000


def _bank_rom(num_banks: int = 4) -> bytes:
    buf = bytearray()
    for bank in range(num_banks):
        buf.extend(bytes([bank & 0xFF] * _BANK_SIZE))
    return bytes(buf)


def _make_fmpac_overlay(rom_dir: Path) -> _FmPacOverlay:
    rom_dir.mkdir(parents=True, exist_ok=True)
    (rom_dir / "fmpac.rom").write_bytes(_bank_rom())
    return _FmPacOverlay(
        rom_base_dir=rom_dir,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        slot=2,
        sram_save_path=rom_dir / "fmpac.sram",
    )


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


def _fmpac_machine(tmp_path: Path, name: str = "m") -> Machine:
    main_dir = tmp_path / f"{name}_main_rom"
    main_dir.mkdir()
    overlay = _make_fmpac_overlay(tmp_path / f"{name}_fmpac_rom")
    machine = build_machine(_msx1_spec(main_dir), fmpac_overlay=overlay)
    assert machine.fmpac is not None
    return machine


# ---------------------------------------------------------------------------
# FM-PAC device state (SRAM, bank, enable, magic registers)
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_sram(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    machine.fmpac.write(0x5FFE, 0x4D)
    machine.fmpac.write(0x5FFF, 0x69)
    machine.fmpac.write(0x4000, 0xAB)
    save_state(machine, _RGB, "test")

    machine.fmpac.write(0x4000, 0x00)
    load_state(machine)

    assert machine.fmpac.read(0x4000) == 0xAB


def test_roundtrip_preserves_bank_and_enable(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    machine.fmpac.write(0x7FF7, 0x02)  # bank = 2
    machine.fmpac.write(0x7FF6, 0x01)  # enable I/O-port OPLL access
    save_state(machine, _RGB, "test")

    machine.fmpac.write(0x7FF7, 0x00)
    machine.fmpac.write(0x7FF6, 0x00)
    load_state(machine)

    assert machine.fmpac.read(0x7FF7) == 0x02
    assert machine.fmpac.read(0x7FF6) == 0x01


def test_sram_enabled_recomputed_on_load(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    machine.fmpac.write(0x5FFE, 0x4D)
    machine.fmpac.write(0x5FFF, 0x69)
    save_state(machine, _RGB, "test")

    # Fresh machine: SRAM starts disabled until restore recomputes it.
    machine2 = _fmpac_machine(tmp_path, name="m2")
    assert machine2.fmpac is not None
    load_state(machine2)
    machine2.fmpac.write(0x4000, 0x77)
    assert machine2.fmpac.read(0x4000) == 0x77  # write reached SRAM, not ignored as ROM


# ---------------------------------------------------------------------------
# OPLL register file
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_opll_registers(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    opll = machine.fmpac.opll
    opll.write_reg(0x30, 0x1F)
    opll.write_reg(0x10, 0x55)
    opll.write_reg(0x20, 0x16)
    save_state(machine, _RGB, "test")

    opll.reset()
    load_state(machine)

    assert opll.read_reg(0x30) == 0x1F
    assert opll.read_reg(0x10) == 0x55
    assert opll.read_reg(0x20) == 0x16


# ---------------------------------------------------------------------------
# OPLL synthesis state (phase, envelope, feedback, key-edge tracking)
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_opll_synthesis_state(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    opll = machine.fmpac.opll
    opll.write_reg(0x30, 0x10)  # instrument=1, vol=0
    opll.write_reg(0x10, 0x50)
    opll.write_reg(0x20, 0x16)  # block=3, KON=1
    opll.generate_samples(200)  # advance partway into attack/decay

    save_state(machine, _RGB, "test")
    # Continuing from the pre-save state must produce a specific stream.
    expected = bytes(opll.generate_samples(500))

    opll.reset()
    load_state(machine)
    # After a save/reset/load, the very same continuation must be bit-identical
    # — proving the full synthesis state (phase, envelope, feedback) round-trips.
    assert bytes(opll.generate_samples(500)) == expected


def test_roundtrip_preserves_rhythm_state(saves_dir: Path, tmp_path: Path) -> None:
    machine = _fmpac_machine(tmp_path)
    assert machine.fmpac is not None
    opll = machine.fmpac.opll
    opll.write_reg(0x16, 0x40)
    opll.write_reg(0x26, 0x06)
    opll.write_reg(0x0E, 0x21)  # rhythm on + HH key
    opll.write_reg(0x37, 0x00)
    opll.generate_samples(200)

    noise_before = opll._noise
    rhythm_before = opll._rhythm_mode

    save_state(machine, _RGB, "test")
    expected = bytes(opll.generate_samples(500))

    opll.reset()
    load_state(machine)

    assert opll._noise == noise_before
    assert opll._rhythm_mode == rhythm_before
    assert bytes(opll.generate_samples(500)) == expected


# ---------------------------------------------------------------------------
# No FM-PAC: existing save/load behaviour unaffected
# ---------------------------------------------------------------------------

def test_machine_without_fmpac_roundtrips(saves_dir: Path, tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    machine = build_machine(_msx1_spec(main_dir))
    assert machine.fmpac is None
    machine.cpu.registers.A = 0x42
    save_state(machine, _RGB, "test")

    machine.cpu.registers.A = 0x00
    load_state(machine)

    assert machine.cpu.registers.A == 0x42
