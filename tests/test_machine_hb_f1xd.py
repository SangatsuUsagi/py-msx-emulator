"""HB-F1XD machine: loader resolution, flat-RAM wiring, and a bare-boot gate.

The bare-boot test needs the real HB-F1XD ROMs (not committed) and is skipped
when they are absent. The loader/wiring tests use synthetic ROM overrides and
always run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.machine_loader import build_machine, load_device_registry, load_machine_spec

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"
_ROM_DIR = _ROOT / "roms" / "hb_f1xd"
_REQUIRED_ROMS = ("hb-f1xd_basic-bios2.rom", "hb-f1xd_msx2sub.rom")
_HAVE_ROMS = all((_ROM_DIR / name).exists() for name in _REQUIRED_ROMS)


def _spec():
    registry = load_device_registry(_CONFIG)
    return load_machine_spec("hb_f1xd", _CONFIG, registry, _ROOT)


def test_loader_resolves_flat_ram_subslot() -> None:
    spec = _spec()
    assert spec.generation == "msx2"
    assert spec.flat_ram_subslot == 3
    assert spec.flat_ram_size_kb == 64
    assert spec.has_ram_mapper is False
    assert spec.sub_rom_entry is not None


def test_build_wires_flat_64k_ram() -> None:
    spec = _spec()
    machine = build_machine(
        spec, bios_override=bytes(32768), extrom_override=bytes(16384)
    )
    assert machine.memory.flat_ram_subslot == 3
    assert machine.memory.ram_mapper is None
    assert len(machine.memory.ram) == 65536


def test_flat_ram_read_write_through_machine_memory() -> None:
    spec = _spec()
    machine = build_machine(
        spec, bios_override=bytes(32768), extrom_override=bytes(16384)
    )
    mem = machine.memory
    mem.slot_register = 0xFF   # all pages -> slot 3
    mem.sub_slot_reg = 0xC0    # page 3 -> sub-slot 3 (flat RAM)
    mem.write(0xC000, 0x3C)
    assert mem.read(0xC000) == 0x3C


@pytest.mark.skipif(not _HAVE_ROMS, reason="real HB-F1XD ROMs not present")
def test_bare_hb_f1xd_boots_to_basic() -> None:
    """GATE: with real BIOS+SUB ROMs and no floppy, boot reaches MSX BASIC.

    Proxy for "BASIC ready": the VDP display is enabled and the CPU is executing
    in the slot-0 BIOS/BASIC ROM (PC < 0x8000) rather than hung. Confirms the
    flat 64 KB RAM in sub-slot 3 is usable as system RAM.
    """
    spec = _spec()
    machine = build_machine(spec)
    for _ in range(180):  # ~3 s of emulated time
        machine.run_frame()
    assert machine.vdp.regs[1] & 0x40, "VDP display never enabled (boot stalled)"
    assert machine.cpu.registers.PC < 0x8000, "CPU not executing in BIOS/BASIC ROM"
