"""Convenience factory wrappers for tests.

make_config / make_config_msx2 delegate to host.machine_config.load_machine_config(),
and make_machine / make_machine_msx2 build those configs with msx.machine_loader's
build_machine(), so construction logic lives in one place.
"""
from __future__ import annotations

from pathlib import Path

from host.machine_config import load_machine_config
from msx.boundary import MachineConfig
from msx.diagnostics.logger import DebugLogger
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.machine_spec import (
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExtensionOverlay,
    _RomEntry,
)
from msx.vdp.tracer import Tracer


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
    extension_overlay: "_ExtensionOverlay | _ExpandedExtensionOverlay | None" = None,
    extension_overlay1: "_ExtensionOverlay | _ExpandedExtensionOverlay | None" = None,
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
    logger: DebugLogger | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logrom: bytes | None = None,
    tracer: Tracer | None = None,
    extension_overlay: "_ExtensionOverlay | _ExpandedExtensionOverlay | None" = None,
    extension_overlay1: "_ExtensionOverlay | _ExpandedExtensionOverlay | None" = None,
) -> Machine:
    config = make_config(
        rom, cartridge, mapper, cartridge2, mapper2, logrom,
        extension_overlay, extension_overlay1,
    )
    return build_machine(config, logger=logger, tracer=tracer)


def make_machine_msx2(
    rom: bytes,
    extrom: bytes,
    *,
    logrom: bytes | None = None,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logger: DebugLogger | None = None,
    tracer: Tracer | None = None,
) -> Machine:
    config = make_config_msx2(
        rom, extrom, logrom=logrom, cartridge=cartridge, mapper=mapper,
        cartridge2=cartridge2, mapper2=mapper2,
    )
    return build_machine(config, logger=logger, tracer=tracer)
