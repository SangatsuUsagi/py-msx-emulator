"""Convenience factory wrappers for tests.

make_config / make_config_msx2 delegate to host.machine_config.load_machine_config(),
and make_machine / make_machine_msx2 build those configs with msx.machine_loader's
build_machine(), so construction logic lives in one place.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from host.machine_config import load_machine_config
from msx.boundary import MachineConfig
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.machine_spec import (
    ExpandedExtensionOverlay,
    ExtensionOverlay,
    MachineSpec,
    _RomEntry,
)
from msx.vdp.v9938 import V9938


def _reversed_keys(node: object) -> object:
    if isinstance(node, dict):
        result: dict[str, object] = {}
        for key in reversed(list(node)):
            result[key] = _reversed_keys(node[key])
        return result
    if isinstance(node, list):
        return [_reversed_keys(item) for item in node]
    return node


def reformat_document(document: bytes) -> bytes:
    """The same MachineConfig document, not in canonical form: indented,
    every object's keys in reverse order."""
    return json.dumps(_reversed_keys(json.loads(document)), indent=2).encode("utf-8")


def _msx1_spec() -> MachineSpec:
    return MachineSpec(
        name="test_msx1",
        generation="msx1",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=None,
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=False,
        has_rtc=False,
    )


def _msx2_spec() -> MachineSpec:
    return MachineSpec(
        name="test_msx2",
        machine_id="test_msx2",
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=True,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=True,
    )


def make_config(
    rom: bytes,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logrom: bytes | None = None,
    extension_overlay: "ExtensionOverlay | ExpandedExtensionOverlay | None" = None,
    extension_overlay1: "ExtensionOverlay | ExpandedExtensionOverlay | None" = None,
) -> MachineConfig:
    return load_machine_config(
        _msx1_spec(),
        cartridge1=cartridge,
        mapper1=mapper,
        cartridge2=cartridge2,
        mapper2=mapper2,
        bios_override=rom,
        logo_override=logrom,
        extension_overlay2=extension_overlay,
        extension_overlay1=extension_overlay1,
    )


def make_config_msx2(
    rom: bytes,
    extrom: bytes,
    *,
    logrom: bytes | None = None,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
) -> MachineConfig:
    return load_machine_config(
        _msx2_spec(),
        cartridge1=cartridge,
        mapper1=mapper,
        cartridge2=cartridge2,
        mapper2=mapper2,
        bios_override=rom,
        logo_override=logrom,
        extrom_override=extrom,
    )


def make_machine(
    rom: bytes,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logrom: bytes | None = None,
    extension_overlay: "ExtensionOverlay | ExpandedExtensionOverlay | None" = None,
    extension_overlay1: "ExtensionOverlay | ExpandedExtensionOverlay | None" = None,
) -> Machine:
    config = make_config(
        rom, cartridge, mapper, cartridge2, mapper2, logrom,
        extension_overlay, extension_overlay1,
    )
    return build_machine(config)


def make_machine_msx2(
    rom: bytes,
    extrom: bytes,
    *,
    logrom: bytes | None = None,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
) -> Machine:
    config = make_config_msx2(
        rom, extrom, logrom=logrom, cartridge=cartridge, mapper=mapper,
        cartridge2=cartridge2, mapper2=mapper2,
    )
    return build_machine(config)


def load_state_file(machine: Machine, path: Path | None = None) -> None:
    """Load a state file (saves/states/latest.state when `path` is None)
    through msx/state.py's loader, which raises the typed errors
    (StateLoadError, SnapshotMismatchError) the machine-state scenarios name.
    host.state_files.load_state reports them as a plain ValueError."""
    from msx import state

    if path is None:
        path = (Path("saves") / "states" / "latest.state").resolve()
    state.load_state(machine, path.read_bytes())


def watch_scanlines(vdp: V9938, record: Callable[[int], None]) -> None:
    """Call `record(line)` after each `vdp.begin_scanline(line)`. The machine
    loop looks the method up on the instance, so the wrapper sees every
    call, in order."""
    begin = vdp.begin_scanline

    def begin_and_record(line: int) -> None:
        begin(line)
        record(line)

    vdp.begin_scanline = begin_and_record  # type: ignore[method-assign]
