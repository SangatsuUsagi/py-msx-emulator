"""Golden-baseline regression test for existing machine YAML resolution.

Phase 5 of MSX-MUSIC (FM-PAC) support investigated unifying existing machine
YAMLs onto the slot-resident-device schema introduced by the FM-PAC overlay
(config/machines/fmpac.yaml). Finding: the only structurally shared piece —
the `rom: {file, size_kb, pages, sha1}` block — is already parsed by one
function (`_parse_rom_entry`) across every use site (slot 0 main/logo ROM,
the MSX2 slot 3 sub ROM, the FDC's DISK ROM, and the FM-PAC overlay's ROM);
see its docstring in msx/machine_loader.py. The FM-PAC overlay's top-level
`slot: N + rom_base + rom + sram` shape has no natural counterpart in a base
machine's full `slots:` tree, so no existing machine YAML needed changes.

This test locks in the current resolved MachineSpec for every existing
machine id as a golden baseline, guarding future machine_loader.py or YAML
refactors against silently changing resolved behaviour.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.machine_loader import MachineSpec, load_device_registry, load_machine_spec

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"

# (machine_id, expected fields). `fdc` is (controller, drives) or None.
_GOLDEN: list[dict[str, object]] = [
    {
        "id": "cbios_msx1",
        "name": "Generic MSX1 (C-BIOS, International)",
        "generation": "msx1",
        "main_rom_file": "cbios_main_msx1.rom",
        "logo_rom_file": "cbios_logo_msx1.rom",
        "sub_rom_file": None,
        "has_ram_mapper": False,
        "has_v9938": False,
        "has_rtc": False,
        "keyboard_type": "int",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx1_br",
        "name": "Generic MSX1 (C-BIOS, Brazil)",
        "generation": "msx1",
        "main_rom_file": "cbios_main_msx1_br.rom",
        "logo_rom_file": "cbios_logo_msx1.rom",
        "sub_rom_file": None,
        "has_ram_mapper": False,
        "has_v9938": False,
        "has_rtc": False,
        "keyboard_type": "int",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx1_eu",
        "name": "Generic MSX1 (C-BIOS, Europe)",
        "generation": "msx1",
        "main_rom_file": "cbios_main_msx1_eu.rom",
        "logo_rom_file": "cbios_logo_msx1.rom",
        "sub_rom_file": None,
        "has_ram_mapper": False,
        "has_v9938": False,
        "has_rtc": False,
        "keyboard_type": "int",
        "cycles_per_frame": 71_364,
        "lines_per_frame": 313,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx1_jp",
        "name": "Generic MSX1 (C-BIOS, Japan)",
        "generation": "msx1",
        "main_rom_file": "cbios_main_msx1_jp.rom",
        "logo_rom_file": "cbios_logo_msx1.rom",
        "sub_rom_file": None,
        "has_ram_mapper": False,
        "has_v9938": False,
        "has_rtc": False,
        "keyboard_type": "jp",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx2",
        "name": "Generic MSX2 (C-BIOS, International)",
        "generation": "msx2",
        "main_rom_file": "cbios_main_msx2.rom",
        "logo_rom_file": "cbios_logo_msx2.rom",
        "sub_rom_file": "cbios_sub.rom",
        "has_ram_mapper": True,
        "has_v9938": True,
        "has_rtc": True,
        "keyboard_type": "int",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx2_br",
        "name": "Generic MSX2 (C-BIOS, Brazil)",
        "generation": "msx2",
        "main_rom_file": "cbios_main_msx2_br.rom",
        "logo_rom_file": "cbios_logo_msx2.rom",
        "sub_rom_file": "cbios_sub.rom",
        "has_ram_mapper": True,
        "has_v9938": True,
        "has_rtc": True,
        "keyboard_type": "int",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx2_eu",
        "name": "Generic MSX2 (C-BIOS, Europe)",
        "generation": "msx2",
        "main_rom_file": "cbios_main_msx2_eu.rom",
        "logo_rom_file": "cbios_logo_msx2.rom",
        "sub_rom_file": "cbios_sub.rom",
        "has_ram_mapper": True,
        "has_v9938": True,
        "has_rtc": True,
        "keyboard_type": "int",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "cbios_msx2_jp",
        "name": "Generic MSX2 (C-BIOS, Japan)",
        "generation": "msx2",
        "main_rom_file": "cbios_main_msx2_jp.rom",
        "logo_rom_file": "cbios_logo_msx2.rom",
        "sub_rom_file": "cbios_sub.rom",
        "has_ram_mapper": True,
        "has_v9938": True,
        "has_rtc": True,
        "keyboard_type": "jp",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": None,
        "fdc": None,
    },
    {
        "id": "hb_f1xd",
        "name": "Sony HB-F1XD (MSX2, Japan)",
        "generation": "msx2",
        "main_rom_file": "hb-f1xd_basic-bios2.rom",
        "logo_rom_file": None,
        "sub_rom_file": "hb-f1xd_msx2sub.rom",
        "has_ram_mapper": False,
        "has_v9938": True,
        "has_rtc": True,
        "keyboard_type": "jp",
        "cycles_per_frame": 59_659,
        "lines_per_frame": 262,
        "flat_ram_subslot": 3,
        "fdc": ("wd2793", 1),
    },
]

_DEVICE_IO_PORTS_MSX1_INT = {
    "ppi8255": (0xA8, 0xAB), "vdp_tms9918a": (0x98, 0x99), "psg_ay8910": (0xA0, 0xA2),
}
_DEVICE_IO_PORTS_MSX2_WITH_RAM_MAPPER = {
    "ppi8255": (0xA8, 0xAB), "vdp_v9938": (0x98, 0x9B), "psg_ay8910": (0xA0, 0xA2),
    "rtc_rp5c01": (0xB4, 0xB5), "memory_mapper_standard": (0xFC, 0xFF),
}
_DEVICE_IO_PORTS_HB_F1XD = {
    "ppi8255": (0xA8, 0xAB), "vdp_v9938": (0x98, 0x9B), "psg_ay8910": (0xA0, 0xA2),
    "rtc_rp5c01": (0xB4, 0xB5),
}


def _spec(machine_id: str) -> MachineSpec:
    registry = load_device_registry(_CONFIG)
    return load_machine_spec(machine_id, _CONFIG, registry, _ROOT)


@pytest.mark.parametrize("golden", _GOLDEN, ids=lambda g: g["id"])
def test_resolved_spec_matches_golden_baseline(golden: dict[str, object]) -> None:
    spec = _spec(golden["id"])  # type: ignore[arg-type]

    assert spec.name == golden["name"]
    assert spec.generation == golden["generation"]
    assert spec.main_rom_entry.file == golden["main_rom_file"]
    assert spec.main_rom_entry.size_kb == 32
    assert spec.main_rom_entry.pages == [0, 1]
    logo_file = spec.logo_rom_entry.file if spec.logo_rom_entry is not None else None
    assert logo_file == golden["logo_rom_file"]
    sub_file = spec.sub_rom_entry.file if spec.sub_rom_entry is not None else None
    assert sub_file == golden["sub_rom_file"]
    assert spec.has_ram_mapper == golden["has_ram_mapper"]
    assert spec.has_v9938 == golden["has_v9938"]
    assert spec.has_rtc == golden["has_rtc"]
    assert spec.keyboard_type == golden["keyboard_type"]
    assert spec.cycles_per_frame == golden["cycles_per_frame"]
    assert spec.lines_per_frame == golden["lines_per_frame"]
    assert spec.m1_wait_states == 1
    assert spec.flat_ram_subslot == golden["flat_ram_subslot"]
    if golden["fdc"] is None:
        assert spec.fdc is None
    else:
        assert spec.fdc is not None
        controller, drives = golden["fdc"]  # type: ignore[misc]
        assert spec.fdc.controller == controller
        assert spec.fdc.drives == drives


def test_msx1_international_device_io_ports() -> None:
    spec = _spec("cbios_msx1")
    assert spec.device_io_ports == _DEVICE_IO_PORTS_MSX1_INT


def test_msx2_international_device_io_ports() -> None:
    spec = _spec("cbios_msx2")
    assert spec.device_io_ports == _DEVICE_IO_PORTS_MSX2_WITH_RAM_MAPPER


def test_hb_f1xd_device_io_ports() -> None:
    spec = _spec("hb_f1xd")
    assert spec.device_io_ports == _DEVICE_IO_PORTS_HB_F1XD
