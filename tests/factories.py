"""Convenience factory wrappers for tests.

make_machine / make_machine_msx2 delegate entirely to machine_loader.build_machine()
so construction logic lives in one place with no duplicate implementation.
"""
from __future__ import annotations

from pathlib import Path

from msx.debug.logger import DebugLogger
from msx.machine import Machine
from msx.machine_loader import MachineSpec, _RomEntry, build_machine
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


def make_machine(
    rom: bytes,
    cartridge: bytes | None = None,
    logger: DebugLogger | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logrom: bytes | None = None,
    tracer: Tracer | None = None,
) -> Machine:
    return build_machine(
        _msx1_spec(),
        cartridge=cartridge,
        mapper=mapper,
        cartridge2=cartridge2,
        mapper2=mapper2,
        logger=logger,
        tracer=tracer,
        bios_override=rom,
        logo_override=logrom,
    )


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
    return build_machine(
        _msx2_spec(),
        cartridge=cartridge,
        mapper=mapper,
        cartridge2=cartridge2,
        mapper2=mapper2,
        logger=logger,
        tracer=tracer,
        bios_override=rom,
        logo_override=logrom,
        extrom_override=extrom,
    )
