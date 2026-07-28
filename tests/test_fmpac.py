"""FM-PAC cartridge device: ROM banking, magic-unlock SRAM, enable register,
memory-mapped + I/O-port OPLL routing, and machine wiring (loader + slot-2
dispatch). Ground truth: openMSX MSXFmPac.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.fmpac import SRAM_SIZE, FmPac
from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _FmPacOverlay,
    _RomEntry,
    build_machine,
    load_fmpac_overlay,
)
from msx.opll import Opll

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"
_BANK_SIZE = 0x4000


def _bank_rom(num_banks: int = 4) -> bytes:
    """64 KB ROM where each 16 KB bank is filled with its bank index byte."""
    buf = bytearray()
    for bank in range(num_banks):
        buf.extend(bytes([bank & 0xFF] * _BANK_SIZE))
    return bytes(buf)


@pytest.fixture()
def fmpac() -> FmPac:
    return FmPac(rom=_bank_rom(), opll=Opll())


# ---------------------------------------------------------------------------
# ROM bank switching
# ---------------------------------------------------------------------------

def test_default_bank_after_construction(fmpac: FmPac) -> None:
    assert fmpac.read(0x4000) == 0


def test_bank_select_changes_visible_rom(fmpac: FmPac) -> None:
    fmpac.write(0x7FF7, 0x02)
    assert fmpac.read(0x4000) == 2


def test_bank_register_masks_to_two_bits(fmpac: FmPac) -> None:
    fmpac.write(0x7FF7, 0xFF)
    assert fmpac.read(0x7FF7) == 0x03


# ---------------------------------------------------------------------------
# SRAM magic unlock
# ---------------------------------------------------------------------------

def test_sram_hidden_by_default(fmpac: FmPac) -> None:
    assert fmpac.read(0x4000) == 0  # ROM bank 0, not SRAM


def test_magic_values_unlock_sram(fmpac: FmPac) -> None:
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x69)
    fmpac.write(0x4000, 0xAB)
    assert fmpac.read(0x4000) == 0xAB


def test_wrong_magic_keeps_sram_hidden(fmpac: FmPac) -> None:
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x00)
    assert fmpac.read(0x4000) == 0  # still ROM


def test_write_protect_blocks_magic_registers(fmpac: FmPac) -> None:
    fmpac.write(0x7FF6, 0x10)  # write-protect bit
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x69)
    assert fmpac.read(0x4000) == 0  # SRAM did not enable


def test_magic_registers_readback_when_enabled(fmpac: FmPac) -> None:
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x69)
    assert fmpac.read(0x5FFE) == 0x4D
    assert fmpac.read(0x5FFF) == 0x69


# ---------------------------------------------------------------------------
# Enable register
# ---------------------------------------------------------------------------

def test_enable_register_masks_value(fmpac: FmPac) -> None:
    fmpac.write(0x7FF6, 0xFF)
    assert fmpac.read(0x7FF6) == 0x11


# ---------------------------------------------------------------------------
# Memory-mapped OPLL register access (always active, independent of enable)
# ---------------------------------------------------------------------------

def test_memory_mapped_opll_write_reaches_register(fmpac: FmPac) -> None:
    fmpac.write(0x7FF4, 0x10)
    fmpac.write(0x7FF5, 0x99)
    assert fmpac.opll.read_reg(0x10) == 0x99


def test_memory_mapped_opll_write_independent_of_enable(fmpac: FmPac) -> None:
    fmpac.write(0x7FF4, 0x11)  # enable bit 0x01 clear
    fmpac.write(0x7FF5, 0x42)
    assert fmpac.opll.read_reg(0x11) == 0x42


# ---------------------------------------------------------------------------
# I/O-port OPLL access (gated by enable bit 0x01)
# ---------------------------------------------------------------------------

def test_io_write_gated_by_enable_bit(fmpac: FmPac) -> None:
    fmpac.write_port(0x7C, 0x10)
    fmpac.write_port(0x7D, 0x55)
    assert fmpac.opll.read_reg(0x10) == 0


def test_io_write_reaches_opll_when_enabled(fmpac: FmPac) -> None:
    fmpac.write(0x7FF6, 0x01)
    fmpac.write_port(0x7C, 0x10)
    fmpac.write_port(0x7D, 0x55)
    assert fmpac.opll.read_reg(0x10) == 0x55


def test_io_read_returns_ff(fmpac: FmPac) -> None:
    assert fmpac.read_port(0x7C) == 0xFF
    assert fmpac.read_port(0x7D) == 0xFF


# ---------------------------------------------------------------------------
# Window bounds
# ---------------------------------------------------------------------------

def test_out_of_window_read_returns_ff(fmpac: FmPac) -> None:
    assert fmpac.read(0x8000) == 0xFF
    assert fmpac.read(0x3FFF) == 0xFF


def test_out_of_window_write_ignored(fmpac: FmPac) -> None:
    fmpac.write(0x8000, 0x12)  # no crash, no observable effect
    assert fmpac.read(0x8000) == 0xFF


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def test_reset_clears_state(fmpac: FmPac) -> None:
    fmpac.write(0x7FF7, 0x03)
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x69)
    fmpac.reset()
    assert fmpac.read(0x7FF7) == 0
    assert fmpac.read(0x4000) == 0  # SRAM disabled again -> ROM bank 0


# ---------------------------------------------------------------------------
# SRAM file persistence
# ---------------------------------------------------------------------------

def test_sram_save_and_load_round_trip(tmp_path: Path) -> None:
    src = FmPac(rom=_bank_rom(), opll=Opll())
    src.write(0x5FFE, 0x4D)
    src.write(0x5FFF, 0x69)
    src.write(0x4000, 0xAB)
    path = tmp_path / "fmpac.sram"
    src.save_sram(path)
    data = path.read_bytes()
    assert len(data) == SRAM_SIZE

    dst = FmPac(rom=_bank_rom(), opll=Opll())
    dst.load_sram(data)
    dst.write(0x5FFE, 0x4D)
    dst.write(0x5FFF, 0x69)
    assert dst.read(0x4000) == 0xAB


def test_load_sram_ignores_wrong_size(fmpac: FmPac) -> None:
    fmpac.load_sram(b"\x00" * 100)
    assert fmpac.sram == bytearray(SRAM_SIZE)


# ---------------------------------------------------------------------------
# Overlay loader (config/machines/fmpac.yaml)
# ---------------------------------------------------------------------------

def test_load_fmpac_overlay_parses_yaml() -> None:
    overlay = load_fmpac_overlay(_CONFIG, _ROOT)
    assert overlay.slot == 2
    assert overlay.rom_entry.file == "fmpac.rom"
    assert overlay.sram_save_path == Path("saves/sram/fmpac.sram")


# ---------------------------------------------------------------------------
# Machine wiring: slot-2 dispatch via a built machine (synthetic ROMs)
# ---------------------------------------------------------------------------

def _make_fmpac_overlay(tmp_path: Path) -> _FmPacOverlay:
    (tmp_path / "fmpac.rom").write_bytes(_bank_rom())
    return _FmPacOverlay(
        rom_base_dir=tmp_path,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        slot=2,
        sram_save_path=tmp_path / "fmpac.sram",
    )


def _msx1_spec(tmp_path: Path) -> MachineSpec:
    (tmp_path / "main.rom").write_bytes(bytes(32768))
    return MachineSpec(
        name="test_msx1",
        generation="msx1",
        rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=None,
        has_ram_mapper=False,
        ram_size_kb=64,
        has_v9938=False,
        has_rtc=False,
    )


def test_build_wires_fmpac_as_slot2_device(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    machine = build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)
    assert machine.fmpac is not None
    assert machine.memory._mapper2 is machine.fmpac
    assert machine.fmpac_sram_save_path == overlay.sram_save_path


def test_slot2_dispatch_reads_fmpac_rom(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    machine = build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)
    mem = machine.memory
    mem.slot_register = 0x08  # page 1 (0x4000-0x7FFF) -> slot 2
    assert mem.read(0x4000) == 0  # FM-PAC ROM bank 0 marker byte


def test_machine_io_ports_gated_by_enable(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    machine = build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)
    mem = machine.memory
    mem.slot_register = 0x08
    assert machine.fmpac is not None

    machine.io.write_port(0x7C, 0x10)
    machine.io.write_port(0x7D, 0x99)
    assert machine.fmpac.opll.read_reg(0x10) == 0

    mem.write(0x7FF6, 0x01)  # enable I/O-port OPLL access
    machine.io.write_port(0x7C, 0x10)
    machine.io.write_port(0x7D, 0x99)
    assert machine.fmpac.opll.read_reg(0x10) == 0x99


def test_build_machine_loads_existing_fmpac_sram(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    saved = bytearray(SRAM_SIZE)
    saved[0] = 0xEE
    overlay.sram_save_path.write_bytes(bytes(saved))

    machine = build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)
    assert machine.fmpac is not None
    machine.fmpac.write(0x5FFE, 0x4D)
    machine.fmpac.write(0x5FFF, 0x69)
    assert machine.fmpac.read(0x4000) == 0xEE


def test_missing_fmpac_rom_raises(tmp_path: Path) -> None:
    overlay = _FmPacOverlay(
        rom_base_dir=tmp_path,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        slot=2,
        sram_save_path=tmp_path / "fmpac.sram",
    )
    with pytest.raises(MachineLoadError, match="fmpac.rom"):
        build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)


def test_machine_reset_resets_fmpac(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    machine = build_machine(_msx1_spec(tmp_path), fmpac_overlay=overlay)
    assert machine.fmpac is not None
    machine.fmpac.write(0x7FF7, 0x03)
    machine.reset()
    assert machine.fmpac.read(0x7FF7) == 0
