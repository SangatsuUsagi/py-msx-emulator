"""FS-A1F machine: loader resolution and TC8566AF FDC wiring.

The bare-boot path needs the real FS-A1F ROMs (not committed, expected
pre-split per config/machines/fs_a1f.yaml's comment) and is out of scope here
-- these tests use synthetic ROM overrides and always run, mirroring
tests/test_machine_hb_f1xd.py's loader/wiring coverage for the WD2793 path.
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.interface import TC8566AFInterface
from msx.fdc.tc8566af import TC8566AF
from msx.machine_loader import build_machine, load_device_registry, load_machine_spec

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"


def _spec():
    registry = load_device_registry(_CONFIG)
    return load_machine_spec("fs_a1f", _CONFIG, registry, _ROOT)


def test_loader_resolves_tc8566af_fdc() -> None:
    spec = _spec()
    assert spec.generation == "msx2"
    assert spec.fdc is not None
    assert spec.fdc.controller == "tc8566af"
    assert spec.fdc.connection_style == "tc8566af"
    assert spec.fdc.drives == 1
    assert spec.flat_ram_subslot == 0
    assert spec.flat_ram_size_kb == 64
    assert spec.sub_rom_subslot == 1
    assert spec.fdc_subslot == 2


def test_build_wires_tc8566af_interface() -> None:
    spec = _spec()
    machine = build_machine(
        spec,
        bios_override=bytes(32768),
        extrom_override=bytes(16384),
        disk_rom_override=bytes(16384),
    )
    assert machine.fdc is not None
    assert isinstance(machine.fdc, TC8566AFInterface)
    assert isinstance(machine.fdc.controller, TC8566AF)
    assert len(machine.fdc.drives) == 1


def test_disk_rom_visible_and_registers_routed() -> None:
    spec = _spec()
    disk_rom = bytes([0xC9]) + bytes(16383)
    machine = build_machine(
        spec,
        bios_override=bytes(32768),
        extrom_override=bytes(16384),
        disk_rom_override=disk_rom,
    )
    mem = machine.memory
    mem.set_slot_register(0xFF)   # all pages -> slot 3
    # sub_slot_reg bit pairs: page0=bits1:0, page1=bits3:2, page2=bits5:4,
    # page3=bits7:6 (see tests/test_memory_flat_ram.py's module docstring and
    # tests/test_memory_subslot_dispatch_characterization.py's identical
    # 0b00_00_10_00 usage). FDC now resolves to sub-slot 2 (fs_a1f.yaml's real
    # layout), so page 1's field (bits 3:2) needs value 2: 2 << 2 == 0x08.
    mem.set_sub_slot_reg(0x08)    # page 1 -> sub-slot 2 (FDC)
    assert mem.read(0x4000) == 0xC9          # first DISK ROM byte
    mem.write(0x7FFA, 0x00)                  # Main Status Register (read-only, ignored)
    assert mem.read(0x7FFA) & 0x80           # RQM ready, no crash
