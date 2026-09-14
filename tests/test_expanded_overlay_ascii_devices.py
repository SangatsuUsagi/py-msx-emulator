"""Expanded-overlay `ascii8`/`ascii16` sub-slot device tests: YAML parsing
(device recognized, rom: block required), build_machine wiring (an
Ascii8Mapper/Ascii16Mapper instance banked exactly as `--mapper ASCII8`/
`ASCII16` behaves for a normal cartridge slot), and the missing-ROM error.
Mirrors tests/test_cli_hbi_j1.py's structure for the expanded-overlay case.
See openspec/changes/add-ascii-expanded-subslot-devices.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    _RomEntry,
    build_machine,
    load_extension_overlay,
)
from msx.mapper import Ascii8Mapper, Ascii16Mapper

_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _msx2_spec() -> MachineSpec:
    # An expanded extension overlay has no MSX1 counterpart (see
    # cart-extension-overlay's "Expanded extension overlay requires an MSX2
    # base machine" Requirement), hence MSX2 here. bios_override/
    # extrom_override supply the main and SUB ROM bytes directly, so no
    # real slot 0/slot 3 ROM files are needed. Mirrors
    # tests/test_cli_hbi_j1.py's own _msx2_spec.
    return MachineSpec(
        name="test_msx2",
        machine_id="test_msx2",
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=False,
    )


def _page_marked_rom(page_size: int, page_count: int) -> bytes:
    """A ROM whose each page's first byte is its own page index -- lets a
    bank-register write be observed through read()."""
    rom = bytearray(page_size * page_count)
    for page in range(page_count):
        rom[page * page_size] = page & 0xFF
    return bytes(rom)


# ---------------------------------------------------------------------------
# YAML parsing: device recognized, rom: block required (task 1)
# ---------------------------------------------------------------------------

def test_ascii8_subslot_yaml_parses(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    rom_dir = config_dir / "extensions" / "roms"
    rom_dir.mkdir(parents=True)
    (rom_dir / "game.rom").write_bytes(bytes(32768))
    _write(
        config_dir / "extensions" / "test_ascii8.yaml",
        """\
        schema_version: 1
        id: test_ascii8
        overlay: true
        shape: expanded
        slot: 2
        rom_base: extensions/roms
        subslots:
          0:
            device: ascii8
            rom:
              file: game.rom
              size_kb: 32
        """,
    )
    overlay = load_extension_overlay("test_ascii8", config_dir, tmp_path)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert overlay.subslots[0].device == "ascii8"
    assert overlay.subslots[0].rom_entry is not None
    assert overlay.subslots[0].rom_entry.file == "game.rom"


def test_ascii16_subslot_yaml_parses(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    rom_dir = config_dir / "extensions" / "roms"
    rom_dir.mkdir(parents=True)
    (rom_dir / "game.rom").write_bytes(bytes(65536))
    _write(
        config_dir / "extensions" / "test_ascii16.yaml",
        """\
        schema_version: 1
        id: test_ascii16
        overlay: true
        shape: expanded
        slot: 2
        rom_base: extensions/roms
        subslots:
          0:
            device: ascii16
            rom:
              file: game.rom
              size_kb: 64
        """,
    )
    overlay = load_extension_overlay("test_ascii16", config_dir, tmp_path)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert overlay.subslots[0].device == "ascii16"
    assert overlay.subslots[0].rom_entry is not None
    assert overlay.subslots[0].rom_entry.file == "game.rom"


def test_ascii8_subslot_without_rom_block_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "bad.yaml",
        """\
        schema_version: 1
        id: bad
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ascii8}
        """,
    )
    with pytest.raises(MachineLoadError, match="'rom' entry"):
        load_extension_overlay("bad", config_dir, tmp_path)


def test_ascii16_subslot_without_rom_block_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "bad.yaml",
        """\
        schema_version: 1
        id: bad
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ascii16}
        """,
    )
    with pytest.raises(MachineLoadError, match="'rom' entry"):
        load_extension_overlay("bad", config_dir, tmp_path)


# ---------------------------------------------------------------------------
# build_machine wiring: Ascii8Mapper/Ascii16Mapper construction and dispatch
# (task 2)
# ---------------------------------------------------------------------------

def test_build_machine_wires_ascii8_subslot(tmp_path: Path) -> None:
    rom_dir = tmp_path / "ascii8_rom"
    rom_dir.mkdir()
    (rom_dir / "game.rom").write_bytes(_page_marked_rom(8192, 8))
    overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="ascii8", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="game.rom", size_kb=64, pages=[]),
            ),
        },
    )
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=overlay,
    )
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, Ascii8Mapper)
    assert sub0.read(0x4000) == 0x00  # window 0, bank 0 (power-on state)

    sub0.write(0x6000, 5)  # window 0's control register -> bank 5
    assert sub0.read(0x4000) == 5


def test_build_machine_wires_ascii16_subslot(tmp_path: Path) -> None:
    rom_dir = tmp_path / "ascii16_rom"
    rom_dir.mkdir()
    (rom_dir / "game.rom").write_bytes(_page_marked_rom(16384, 4))
    overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="ascii16", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="game.rom", size_kb=64, pages=[]),
            ),
        },
    )
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=overlay,
    )
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, Ascii16Mapper)
    assert sub0.read(0x8000) == 0x00  # window 1, bank 0 (power-on state)

    sub0.write(0x7000, 3)  # window 1's control register -> bank 3
    assert sub0.read(0x8000) == 3


# ---------------------------------------------------------------------------
# Missing ROM asset: MachineLoadError naming the specific file (task 2.3)
# ---------------------------------------------------------------------------

def test_missing_ascii8_subslot_rom_raises_naming_file(tmp_path: Path) -> None:
    rom_dir = tmp_path / "ascii8_rom"
    rom_dir.mkdir()
    overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="ascii8", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="missing.rom", size_kb=64, pages=[]),
            ),
        },
    )
    with pytest.raises(MachineLoadError, match="missing.rom"):
        build_machine(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay=overlay,
        )


def test_missing_ascii16_subslot_rom_raises_naming_file(tmp_path: Path) -> None:
    rom_dir = tmp_path / "ascii16_rom"
    rom_dir.mkdir()
    overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="ascii16", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="missing.rom", size_kb=64, pages=[]),
            ),
        },
    )
    with pytest.raises(MachineLoadError, match="missing.rom"):
        build_machine(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay=overlay,
        )
