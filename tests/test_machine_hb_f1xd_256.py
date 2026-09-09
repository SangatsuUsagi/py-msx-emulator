"""hb_f1xd_256: a hypothetical HB-F1XD variant with a 256 KB RAM mapper in
slot 3 instead of the real machine's flat 64 KB RAM.

Reuses HB-F1XD's real BIOS/SUB-ROM/FDC configuration verbatim (see
config/machines/hb_f1xd_256.yaml's header comment and
openspec/changes/archive/2026-09-09-hb-f1xd-256-machine/design.md) -- only slot 3's RAM
strategy differs. No real HB-F1XD variant with this configuration exists;
this machine's only purpose is to give MSX-DOS2 the RAM-mapper environment
it requires. Mirrors tests/test_machine_hb_f1xd.py's structure.
"""
from __future__ import annotations

from pathlib import Path

from msx.machine_loader import (
    MachineSpec,
    build_machine,
    load_device_registry,
    load_machine_spec,
)

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"


def _spec() -> MachineSpec:
    registry = load_device_registry(_CONFIG)
    return load_machine_spec("hb_f1xd_256", _CONFIG, registry, _ROOT)


def test_loader_resolves_ram_mapper() -> None:
    spec = _spec()
    assert spec.generation == "msx2"
    assert spec.has_ram_mapper is True
    assert spec.ram_mapper_size_kb == 256
    assert spec.flat_ram_subslot is None
    assert spec.sub_rom_entry is not None
    assert spec.fdc is not None


def test_build_wires_256kb_ram_mapper() -> None:
    spec = _spec()
    machine = build_machine(
        spec,
        bios_override=bytes(32768),
        extrom_override=bytes(16384),
        disk_rom_override=bytes(16384),
    )
    assert machine.memory.flat_ram_subslot is None
    assert machine.memory.ram_mapper is not None
    assert len(machine.memory.ram_mapper.ram) == 262144


def test_ram_mapper_read_write_through_machine_memory() -> None:
    spec = _spec()
    machine = build_machine(
        spec,
        bios_override=bytes(32768),
        extrom_override=bytes(16384),
        disk_rom_override=bytes(16384),
    )
    mem = machine.memory
    mem.set_slot_register(0xFF)  # all pages -> slot 3
    mem.set_sub_slot_reg(0xFF)  # every page -> sub-slot 3 (RAM mapper)
    mem.write(0xC000, 0x3C)
    assert mem.read(0xC000) == 0x3C


def test_sub_rom_and_fdc_still_wired_alongside_ram_mapper() -> None:
    """The FDC + RAM mapper coexistence this machine relies on
    (openspec/changes/archive/2026-09-09-slot3-fdc-ram-mapper-coexistence)."""
    spec = _spec()
    machine = build_machine(
        spec,
        bios_override=bytes(32768),
        extrom_override=bytes(16384),
        disk_rom_override=bytes(16384),
    )
    mem = machine.memory
    mem.set_slot_register(0xFF)
    mem.set_sub_slot_reg(0x00)  # every page -> sub-slot 0 (SUB ROM + FDC)
    assert mem.read(0x0000) != 0xFF  # SUB ROM page 0 (bios_override content)
    assert machine.fdc is not None


def test_shares_non_ram_configuration_with_real_hb_f1xd() -> None:
    """hb_f1xd.yaml and hb_f1xd_256.yaml differ only in slot 3's RAM
    strategy -- main ROM, SUB ROM, and FDC resolve identically."""
    registry = load_device_registry(_CONFIG)
    real = load_machine_spec("hb_f1xd", _CONFIG, registry, _ROOT)
    virtual = load_machine_spec("hb_f1xd_256", _CONFIG, registry, _ROOT)

    assert virtual.main_rom_entry == real.main_rom_entry
    assert virtual.sub_rom_entry == real.sub_rom_entry
    assert virtual.fdc == real.fdc
    assert virtual.sub_rom_subslot == real.sub_rom_subslot
    assert virtual.fdc_subslot == real.fdc_subslot
