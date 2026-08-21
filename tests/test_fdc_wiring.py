"""FDC config resolution and Memory/Machine wiring."""
from __future__ import annotations

from pathlib import Path

import pytest

from msx.fdc.disk_image import DskDiskImage
from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _FdcDef,
    _parse_fdc,
    _parse_slot3_msx2,
    _RomEntry,
    build_machine,
    load_device_registry,
    load_machine_spec,
)

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"
_2DD = 737280


# --- Loader resolution ---------------------------------------------------

def test_loader_resolves_fdc_from_hb_f1xd() -> None:
    registry = load_device_registry(_CONFIG)
    spec = load_machine_spec("hb_f1xd", _CONFIG, registry, _ROOT)
    assert spec.fdc is not None
    assert spec.fdc.controller == "wd2793"
    assert spec.fdc.connection_style == "sony"
    assert spec.fdc.drives == 1
    assert spec.fdc.disk_rom_entry.file == "hb-f1xd_disk.rom"


def test_cbios_msx2_has_no_fdc() -> None:
    registry = load_device_registry(_CONFIG)
    spec = load_machine_spec("cbios_msx2_jp", _CONFIG, registry, _ROOT)
    assert spec.fdc is None


@pytest.mark.parametrize(
    "machine_id",
    ["cbios_msx2", "cbios_msx2_jp", "cbios_msx2_br", "cbios_msx2_eu", "hb_f1xd", "fs_a1f"],
)
def test_every_existing_msx2_machine_defaults_both_roles_to_subslot_0(machine_id: str) -> None:
    """openspec/changes/parameterize-subslot-index task 5.1's audit, pinned as
    a regression test: every MSX2 machine YAML predating the independent
    SUB-ROM/FDC sub-slot fields declares both (where present) in sub-slot 0,
    so both resolve to the loader's default (0) unchanged. fs_a1f is included
    here even though its real hardware wants sub-slot 1/2 -- it still ships
    the provisional (combined, sub-slot 0) layout until
    parameterize-subslot-index's own follow-up step switches it."""
    registry = load_device_registry(_CONFIG)
    spec = load_machine_spec(machine_id, _CONFIG, registry, _ROOT)
    assert spec.sub_rom_subslot == 0
    assert spec.fdc_subslot == 0


def test_unknown_controller_rejected() -> None:
    sub0 = {"fdc": {"rom": {"file": "d.rom", "size_kb": 16, "pages": [1]},
                    "controller": "bogus"}}
    with pytest.raises(MachineLoadError, match="controller"):
        _parse_fdc(sub0, "test", 0)


def test_unknown_connection_style_rejected() -> None:
    sub0 = {"fdc": {"rom": {"file": "d.rom", "size_kb": 16, "pages": [1]},
                    "controller": "wd2793", "connection_style": "bogus"}}
    with pytest.raises(MachineLoadError, match="connection_style"):
        _parse_fdc(sub0, "test", 0)


def test_mismatched_controller_style_pair_rejected() -> None:
    """Each supported controller/connection_style is individually valid, but
    wd2793+tc8566af isn't a pair _build_fdc knows how to construct."""
    sub0 = {"fdc": {"rom": {"file": "d.rom", "size_kb": 16, "pages": [1]},
                    "controller": "wd2793", "connection_style": "tc8566af"}}
    with pytest.raises(MachineLoadError, match="does not support"):
        _parse_fdc(sub0, "test", 0)


def test_loader_resolves_fdc_from_fs_a1f() -> None:
    registry = load_device_registry(_CONFIG)
    spec = load_machine_spec("fs_a1f", _CONFIG, registry, _ROOT)
    assert spec.fdc is not None
    assert spec.fdc.controller == "tc8566af"
    assert spec.fdc.connection_style == "tc8566af"
    assert spec.fdc.drives == 1
    assert spec.fdc.disk_rom_entry.file == "fs-a1f_disk.rom"


def test_fdc_block_missing_rom_rejected() -> None:
    with pytest.raises(MachineLoadError, match="rom"):
        _parse_fdc({"fdc": {"controller": "wd2793"}}, "test", 0)


def test_sub_rom_and_fdc_default_to_subslot_0_when_combined() -> None:
    """hb_f1xd.yaml's existing shape: SUB ROM content and an fdc: block both
    declared in the same (sub-slot 0) secondary dict -- must resolve both
    indices to 0, unchanged from before this generalisation."""
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {
                "content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}],
                "fdc": {
                    "rom": {"file": "disk.rom", "size_kb": 16, "pages": [1]},
                    "controller": "wd2793",
                    "connection_style": "sony",
                },
            },
            3: {"type": "ram", "size_kb": 64},
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.sub_rom is not None and result.sub_rom_subslot == 0
    assert result.fdc is not None and result.fdc_subslot == 0


def test_sub_rom_and_fdc_resolve_to_independent_subslots() -> None:
    """FS-A1F's real hardware shape: SUB ROM in one secondary slot, the fdc:
    block in a different one -- each resolves to its own declared index."""
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {"type": "ram", "size_kb": 64},
            1: {"content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}]},
            2: {
                "fdc": {
                    "rom": {"file": "disk.rom", "size_kb": 16, "pages": [1]},
                    "controller": "tc8566af",
                    "connection_style": "tc8566af",
                },
            },
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.sub_rom is not None and result.sub_rom_subslot == 1
    assert result.fdc is not None and result.fdc_subslot == 2
    assert result.flat_ram_subslot == 0


# --- Memory / Machine wiring (synthetic ROMs) ----------------------------

def _make_roms(tmp_path: Path) -> None:
    (tmp_path / "main.rom").write_bytes(bytes([0xC9]) + bytes(32768 - 1))
    (tmp_path / "sub.rom").write_bytes(bytes([0xAB]) + bytes(16384 - 1))
    (tmp_path / "disk.rom").write_bytes(bytes([0xC3]) + bytes(16384 - 1))


def _fdc_spec(tmp_path: Path) -> MachineSpec:
    return MachineSpec(
        name="test_fdc",
        generation="msx2",
        rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=True,
        flat_ram_subslot=3,
        flat_ram_size_kb=64,
        fdc=_FdcDef(
            disk_rom_entry=_RomEntry(file="disk.rom", size_kb=16, pages=[1]),
            controller="wd2793",
            connection_style="sony",
            drives=1,
        ),
    )


def test_build_wires_fdc_device(tmp_path: Path) -> None:
    _make_roms(tmp_path)
    machine = build_machine(_fdc_spec(tmp_path))
    assert machine.fdc is not None
    assert machine.memory.fdc is machine.fdc


def test_disk_rom_and_registers_routed_through_memory(tmp_path: Path) -> None:
    _make_roms(tmp_path)
    machine = build_machine(_fdc_spec(tmp_path))
    mem = machine.memory
    mem.set_slot_register(0xFF)  # all pages -> slot 3
    mem.set_sub_slot_reg(0x00)  # all pages -> sub-slot 0
    assert mem.read(0x4000) == 0xC3        # DISK ROM byte 0
    mem.write(0x7FF8, 0x80)                # COMMAND = READ SECTOR (routed via Memory)
    assert (mem.read(0x7FF8) & 0x80) != 0  # FDC STATUS: NOT_READY (no disk), no crash
    # SUB ROM in page 0 is still served, and the FDC is not consulted there.
    assert mem.read(0x0000) == 0xAB


def test_fdd1_mounts_into_drive_a(tmp_path: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    dsk.write_bytes(bytes(_2DD))
    machine = build_machine(_fdc_spec(tmp_path), fdd1=dsk)
    assert machine.fdc is not None
    assert machine.fdc.drives[0].has_disk is True
    assert isinstance(machine.fdc.drives[0].image, DskDiskImage)
