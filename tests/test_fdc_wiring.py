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


def test_unknown_controller_rejected() -> None:
    sub0 = {"fdc": {"rom": {"file": "d.rom", "size_kb": 16, "pages": [1]},
                    "controller": "bogus"}}
    with pytest.raises(MachineLoadError, match="controller"):
        _parse_fdc(sub0, "test")


def test_unknown_connection_style_rejected() -> None:
    sub0 = {"fdc": {"rom": {"file": "d.rom", "size_kb": 16, "pages": [1]},
                    "controller": "wd2793", "connection_style": "bogus"}}
    with pytest.raises(MachineLoadError, match="connection_style"):
        _parse_fdc(sub0, "test")


def test_fdc_block_missing_rom_rejected() -> None:
    with pytest.raises(MachineLoadError, match="rom"):
        _parse_fdc({"fdc": {"controller": "wd2793"}}, "test")


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
    mem.slot_register = 0xFF  # all pages -> slot 3
    mem.sub_slot_reg = 0x00   # all pages -> sub-slot 0
    assert mem.read(0x4000) == 0xC3        # DISK ROM byte 0
    mem.write(0x7FF8, 0x80)                # COMMAND = READ SECTOR (routed via Memory)
    assert (mem.read(0x7FF8) & 0x80) != 0  # FDC STATUS: NOT_READY (no disk), no crash
    # SUB ROM in page 0 is still served, and the FDC is not consulted there.
    assert mem.read(0x0000) == 0xAB


def test_disc1_mounts_into_drive_a(tmp_path: Path) -> None:
    _make_roms(tmp_path)
    dsk = tmp_path / "game.dsk"
    dsk.write_bytes(bytes(_2DD))
    machine = build_machine(_fdc_spec(tmp_path), disc1=dsk)
    assert machine.fdc is not None
    assert machine.fdc.drives[0].has_disk is True
    assert isinstance(machine.fdc.drives[0].image, DskDiskImage)
