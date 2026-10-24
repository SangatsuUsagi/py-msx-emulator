"""openspec/changes/slot1-slot2-symmetrization Phase 6: broader integration
coverage for two independent --extensionN overlays active at once (D5's
"extension x2" claim: this needs no separate mechanism beyond the
per-slot-parameterized overlay wiring D1/D3 already build). The
flat+expanded combination (fmpac + hbi_j1) already has CLI-level coverage
in tests/test_cli_hbi_j1.py; this file covers the combinations that don't
naturally belong to any single extension's own test file: two *expanded*
overlays simultaneously (one per slot), and cross-slot SRAM independence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from host.machine_config import build_machine_from_spec, load_machine_config, sram_paths
from host.media import persist_dirty_media
from host.state_files import load_state, save_state
from msx.boundary import RgbFrame
from msx.machine_loader import build_machine
from msx.machine_spec import (
    ExpandedExtensionOverlay,
    ExtensionOverlay,
    MachineLoadError,
    MachineSpec,
    _ExpandedSubslotDevice,
    _RomEntry,
)
from msx.mapper import FixedPageMapper, HalnoteMapper
from msx.ram_mapper import RamMapper

_HALNOTE_ROM_SIZE = 1048576
_KANJIBASIC_ROM_SIZE = 32768
_KANJIFONT_ROM_SIZE = 262144
_KERNEL_ROM_SIZE = 32768


def _msx2_spec() -> MachineSpec:
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


def _hbi_j1_overlay(rom_dir: Path) -> ExpandedExtensionOverlay:
    (rom_dir / "msx-je.rom").write_bytes(bytes(_HALNOTE_ROM_SIZE))
    (rom_dir / "kanjibasic.rom").write_bytes(bytes(_KANJIBASIC_ROM_SIZE))
    (rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    return ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="halnote", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="msx-je.rom", size_kb=1024, pages=[]),
                sram_save_path=rom_dir / "msx-je.sram",
            ),
            1: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="kanjibasic.rom", size_kb=32, pages=[]),
            ),
        },
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=rom_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )


def _msxdos2_512k_overlay(rom_dir: Path) -> ExpandedExtensionOverlay:
    (rom_dir / "msxd22s.rom").write_bytes(bytes(_KERNEL_ROM_SIZE))
    return ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(device="ram_mapper", size_kb=512),
            1: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="msxd22s.rom", size_kb=32, pages=[]),
            ),
        },
    )


def _fmpac_overlay(rom_dir: Path) -> ExtensionOverlay:
    (rom_dir / "fmpac.rom").write_bytes(bytes(65536))
    return ExtensionOverlay(
        device="fmpac", rom_base_dir=rom_dir,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        sram_save_path=rom_dir / "fmpac.sram",
    )


# ---------------------------------------------------------------------------
# Two expanded overlays at once, one per slot (hbi_j1 has no ram_mapper
# sub-slot, so this does not hit the cross-slot ram_mapper conflict --
# msxdos2_512k does, and is covered separately in test_msxdos2_512k.py)
# ---------------------------------------------------------------------------

def test_two_expanded_overlays_wire_independently_no_cross_talk(tmp_path: Path) -> None:
    hbi_dir = tmp_path / "hbi_j1_rom"
    hbi_dir.mkdir()
    dos2_dir = tmp_path / "msxdos2_rom"
    dos2_dir.mkdir()
    machine = build_machine_from_spec(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay1=_hbi_j1_overlay(hbi_dir),
        extension_overlay2=_msxdos2_512k_overlay(dos2_dir),
    )

    # Slot 1: HBI-J1's halnote + flat Kanji driver/BASIC ROM.
    assert machine.memory.slot1_sub_slot_enabled is True
    assert isinstance(machine.memory._mapper1_subslots[0], HalnoteMapper)
    assert isinstance(machine.memory._mapper1_subslots[1], FixedPageMapper)
    assert machine.halnote_cart is machine.memory._mapper1_subslots[0]

    # Slot 2: msxdos2_512k's RAM mapper + flat kernel ROM -- no cross-talk
    # with slot 1's devices above.
    assert machine.memory.slot2_sub_slot_enabled is True
    assert isinstance(machine.memory._mapper2_subslots[0], RamMapper)
    assert isinstance(machine.memory._mapper2_subslots[1], FixedPageMapper)

    # The Kanji-ROM io_device came only from hbi_j1's overlay (msxdos2_512k
    # declares none) -- reachable regardless of which sub-slot is selected.
    assert machine.kanji is not None
    machine.io.write_port(0xD8, 0x05)
    machine.io.write_port(0xD9, 0x02)
    assert machine.io.read_port(0xD9) == 0x00

    # Slot 2's RAM mapper still responds on the standard memory-mapper ports.
    machine.io.write_port(0xFF, 0x05)
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    assert sub0.banks[3] == 5


def test_two_halnote_overlays_at_once_rejected(tmp_path: Path) -> None:
    # Two HBI-J1-shaped overlays (one per slot) would both claim Machine's
    # single halnote_cart field -- real hardware never has two MSX-JE
    # cartridges attached at once.
    hbi_dir1 = tmp_path / "hbi_j1_rom1"
    hbi_dir1.mkdir()
    hbi_dir2 = tmp_path / "hbi_j1_rom2"
    hbi_dir2.mkdir()
    with pytest.raises(MachineLoadError, match="other cartridge slot"):
        build_machine_from_spec(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay1=_hbi_j1_overlay(hbi_dir1),
            extension_overlay2=_hbi_j1_overlay(hbi_dir2),
        )


def test_two_kanji_rom_io_devices_at_once_rejected(tmp_path: Path) -> None:
    # Two expanded overlays that each declare their own io_device: kanji_rom
    # (hbi_j1's shape, and msxdos2_512k_kanjirom's shape) conflict on I/O
    # ports 0xD8-0xDB even with no machine-declared io_device and no
    # ram_mapper sub-slot involved (see the RejectMachineAndOverlayIoDevice
    # Conflict guidance this weed pass generalized to all three sources).
    hbi_dir = tmp_path / "hbi_j1_rom"
    hbi_dir.mkdir()
    (hbi_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    other_dir = tmp_path / "other_kanji_rom"
    other_dir.mkdir()
    (other_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    overlay1 = ExpandedExtensionOverlay(
        subslots={},
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=hbi_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )
    overlay2 = ExpandedExtensionOverlay(
        subslots={},
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=other_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )
    with pytest.raises(MachineLoadError, match="Kanji-ROM"):
        build_machine_from_spec(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay1=overlay1, extension_overlay2=overlay2,
        )


# ---------------------------------------------------------------------------
# fmpac on one slot survives a non-fmpac flat overlay on the other slot
# (weed-follow-up bug fix: _wire_extension_overlay used to unconditionally
# overwrite fmpac_device/fmpac_sram_save_path from whichever slot's flat
# overlay was wired last, even when that overlay was scc_i_cart -- silently
# erasing the other slot's already-resolved FM-PAC, exactly the class of
# bug the halnote_device assignment a few lines below was already guarded
# against)
# ---------------------------------------------------------------------------

def test_fmpac_on_slot1_survives_scc_plus_on_slot2(tmp_path: Path) -> None:
    fmpac_dir = tmp_path / "fmpac_rom"
    fmpac_dir.mkdir()
    fmpac_overlay = _fmpac_overlay(fmpac_dir)
    config = load_machine_config(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay1=fmpac_overlay,
        extension_overlay2=ExtensionOverlay(device="scc_i_cart"),
    )
    machine = build_machine(config)
    assert machine.fmpac is not None
    assert sram_paths(config)["fmpac.sram"] == fmpac_overlay.sram_save_path
    assert machine.sram_media()["fmpac.sram"] is machine.fmpac


def test_fmpac_on_slot2_survives_scc_plus_on_slot1(tmp_path: Path) -> None:
    # The reverse placement -- exercises the slot-1 assignment's own guard,
    # not currently reachable as a *bug* (slot 1 is wired first, so nothing
    # to overwrite yet), but kept symmetric with the slot-2 case above.
    fmpac_dir = tmp_path / "fmpac_rom"
    fmpac_dir.mkdir()
    fmpac_overlay = _fmpac_overlay(fmpac_dir)
    config = load_machine_config(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay1=ExtensionOverlay(device="scc_i_cart"),
        extension_overlay2=fmpac_overlay,
    )
    machine = build_machine(config)
    assert machine.fmpac is not None
    assert sram_paths(config)["fmpac.sram"] == fmpac_overlay.sram_save_path


def test_fmpac_on_both_slots_rejected(tmp_path: Path) -> None:
    # Two fmpac overlays (one per slot) would both claim Machine's single
    # fmpac field -- real hardware has one FM-PAC cartridge. Not reachable
    # through the actual CLI (the identical-extension-id check rejects
    # --extension1 fmpac --extension2 fmpac first), but build_machine
    # itself must still refuse it as defense in depth.
    fmpac_dir1 = tmp_path / "fmpac_rom1"
    fmpac_dir1.mkdir()
    fmpac_dir2 = tmp_path / "fmpac_rom2"
    fmpac_dir2.mkdir()
    with pytest.raises(MachineLoadError, match="fmpac"):
        build_machine_from_spec(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay1=_fmpac_overlay(fmpac_dir1),
            extension_overlay2=_fmpac_overlay(fmpac_dir2),
        )


# ---------------------------------------------------------------------------
# Cross-slot SRAM independence: an FM-PAC on slot 1 and HBI-J1's halnote
# cartridge on slot 2, each with its own battery-backed SRAM, persisting
# through one save/load round-trip without cross-writing to each other's
# path (regression coverage for the fmpac_sram_save_path bug this phase
# found and fixed: it used to be resolved only from build_machine's
# slot-2-specific extension_overlay parameter, so an FM-PAC placed on slot 1
# never got a save path wired at all).
# ---------------------------------------------------------------------------

_RGB = RgbFrame(256, 192, bytes(256 * 192 * 3))


@pytest.fixture()
def saves_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


def test_fmpac_slot1_and_hbi_j1_slot2_sram_persist_independently(
    saves_dir: Path, tmp_path: Path
) -> None:
    fmpac_dir = tmp_path / "fmpac_rom"
    fmpac_dir.mkdir()
    hbi_dir = tmp_path / "hbi_j1_rom"
    hbi_dir.mkdir()
    fmpac_overlay = _fmpac_overlay(fmpac_dir)
    config = load_machine_config(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay1=fmpac_overlay,
        extension_overlay2=_hbi_j1_overlay(hbi_dir),
    )
    machine = build_machine(config)
    assert machine.fmpac is not None
    assert sram_paths(config)["fmpac.sram"] == fmpac_overlay.sram_save_path
    halnote = machine.halnote_cart
    assert halnote is not None

    machine.fmpac.write(0x5FFE, 0x4D)  # unlock magic (byte 1/2)
    machine.fmpac.write(0x5FFF, 0x69)  # unlock magic (byte 2/2)
    machine.fmpac.write(0x4000, 0xAB)  # SRAM byte, slot 1's FM-PAC
    halnote.write(0x4FFF, 0x85)  # SRAM enabled, bank 0 -> page 5
    halnote.write(0x0000, 0xCD)  # SRAM byte, slot 2's halnote

    persist_dirty_media(machine, sram_paths(config))

    save_state(machine, _RGB, "test")
    machine.fmpac.write(0x4000, 0x00)
    halnote.write(0x0000, 0x00)
    load_state(machine)

    assert machine.fmpac.read(0x4000) == 0xAB
    assert halnote.read(0x0000) == 0xCD
    # Each device's save file holds only its own data -- no cross-write.
    assert fmpac_overlay.sram_save_path.read_bytes()[0] == 0xAB
    assert hbi_dir.joinpath("msx-je.sram").read_bytes()[0] == 0xCD
