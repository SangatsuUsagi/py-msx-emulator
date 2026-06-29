"""YAML-based machine configuration loader.

Two-pass loading: device registry first, then machine spec resolution.
Raises MachineLoadError with specific file and field names on any validation failure.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from msx.cpu.z80 import Z80
from msx.debug.logger import DebugLogger
from msx.input import InputState
from msx.io import IOBus
from msx.machine import _make_mapper, _resolve_mapper_type
from msx.mapper import MajutsushiMapper
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.ram_mapper import RamMapper
from msx.rtc import RTC
from msx.scc import SCC
from msx.vdp.tracer import Tracer
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP

if False:  # TYPE_CHECKING — avoid circular at runtime
    pass


class MachineLoadError(Exception):
    """Raised when a device or machine YAML fails validation."""


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------

@dataclass
class _DeviceDef:
    id: str
    type: str
    implemented: bool
    raw: dict[str, Any]


@dataclass
class _RomEntry:
    file: str
    size_kb: int
    pages: list[int]
    sha1: str | None = None


@dataclass
class MachineSpec:
    """Fully-resolved machine wiring, ready for instantiation."""

    id: str
    name: str
    generation: str          # 'msx1' | 'msx2'
    rom_base_dir: Path

    # Slot 0
    main_rom_entry: _RomEntry
    logo_rom_entry: _RomEntry | None

    # Slot 3 MSX2
    sub_rom_entry: _RomEntry | None
    has_ram_mapper: bool

    # Slot 3 MSX1
    ram_size_kb: int

    # Device flags
    has_v9938: bool
    has_rtc: bool


# ---------------------------------------------------------------------------
# Pass 1: device registry
# ---------------------------------------------------------------------------

def load_device_registry(config_dir: Path) -> dict[str, _DeviceDef]:
    """Load all *.yaml files from config_dir/devices/ into a registry keyed by id.

    Args:
        config_dir: Path to the config/ directory (parent of devices/).

    Returns:
        Dict mapping device id to _DeviceDef.

    Raises:
        MachineLoadError: If any device file is missing required fields or
            has an id that does not match its filename stem.
    """
    devices_dir = config_dir / "devices"
    registry: dict[str, _DeviceDef] = {}
    if not devices_dir.exists():
        return registry
    for path in sorted(devices_dir.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise MachineLoadError(f"{path}: expected a YAML mapping at top level")
        dev_id: Any = raw.get("id")
        if not dev_id:
            raise MachineLoadError(f"{path}: missing required field 'id'")
        if dev_id != path.stem:
            raise MachineLoadError(
                f"{path}: 'id' field {dev_id!r} does not match filename stem {path.stem!r}"
            )
        dev_type: Any = raw.get("type")
        if not dev_type:
            raise MachineLoadError(f"{path}: missing required field 'type'")
        implemented: bool = bool(raw.get("implemented", True))
        registry[str(dev_id)] = _DeviceDef(
            id=str(dev_id), type=str(dev_type), implemented=implemented, raw=raw
        )
    return registry


# ---------------------------------------------------------------------------
# Slot parsers
# ---------------------------------------------------------------------------

def _parse_rom_entry(entry: dict[str, Any], context: str) -> _RomEntry:
    file: Any = entry.get("file")
    if not file:
        raise MachineLoadError(f"{context}: ROM entry missing required field 'file'")
    size_kb: int = int(entry.get("size_kb", 0))
    pages_raw: Any = entry.get("pages", [])
    pages: list[int] = [int(p) for p in pages_raw]
    sha1: str | None = entry.get("sha1") or None
    return _RomEntry(file=str(file), size_kb=size_kb, pages=pages, sha1=sha1)


def _parse_slot0(
    slot0: dict[str, Any], machine_id: str
) -> tuple[_RomEntry | None, _RomEntry | None]:
    """Extract main ROM (pages [0,1]) and optional logo ROM (page [2]) from slot 0."""
    main_rom: _RomEntry | None = None
    logo_rom: _RomEntry | None = None
    context = f"machine '{machine_id}' slot 0"
    for item in slot0.get("content", []):
        rom_data: Any = item.get("rom")
        if not isinstance(rom_data, dict):
            continue
        entry = _parse_rom_entry(rom_data, context)
        if 0 in entry.pages or 1 in entry.pages:
            main_rom = entry
        elif 2 in entry.pages:
            logo_rom = entry
    return main_rom, logo_rom


def _parse_slot3_msx1(slot3: dict[str, Any]) -> int:
    """Return flat RAM size in KB for an MSX1 slot 3 declaration."""
    return int(slot3.get("size_kb", 32))


def _parse_slot3_msx2(
    slot3: dict[str, Any], machine_id: str
) -> tuple[_RomEntry | None, bool]:
    """Return (sub_rom_entry, has_ram_mapper) for an MSX2 slot 3 declaration."""
    sub_rom: _RomEntry | None = None
    has_ram_mapper = False
    if slot3.get("expanded"):
        secondary: dict[Any, Any] = slot3.get("secondary", {})
        sub0: Any = secondary.get(0, {})
        if isinstance(sub0, dict):
            for item in sub0.get("content", []):
                rom_data: Any = item.get("rom")
                if isinstance(rom_data, dict):
                    sub_rom = _parse_rom_entry(
                        rom_data, f"machine '{machine_id}' slot 3 sub-slot 0"
                    )
                    break
        for sub_val in secondary.values():
            if isinstance(sub_val, dict) and sub_val.get("mapper") == "standard":
                has_ram_mapper = True
    elif slot3.get("mapper") == "standard":
        has_ram_mapper = True
    return sub_rom, has_ram_mapper


# ---------------------------------------------------------------------------
# Pass 2: machine spec
# ---------------------------------------------------------------------------

def load_machine_spec(
    machine_id: str,
    config_dir: Path,
    device_registry: dict[str, _DeviceDef],
    project_root: Path,
) -> MachineSpec:
    """Load and validate a machine YAML by id.

    Args:
        machine_id: Stem of the YAML file in config_dir/machines/ (e.g. 'cbios_msx2').
        config_dir: Path to the config/ directory.
        device_registry: Pre-loaded registry from load_device_registry().
        project_root: Project root used to resolve rom_base relative paths.

    Returns:
        A MachineSpec with all ROM entries and device flags resolved.

    Raises:
        MachineLoadError: On missing file, bad schema_version, unresolved ref,
            missing slot 0 main ROM, or unknown generation.
    """
    machines_dir = config_dir / "machines"
    machine_path = machines_dir / f"{machine_id}.yaml"
    if not machine_path.exists():
        raise MachineLoadError(f"machine not found: {machine_path}")

    with machine_path.open(encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise MachineLoadError(f"{machine_path}: expected a YAML mapping at top level")

    schema_version: Any = raw.get("schema_version")
    if schema_version != 1:
        raise MachineLoadError(
            f"{machine_path}: unsupported schema_version {schema_version!r} (expected 1)"
        )

    m_id: Any = raw.get("id")
    if m_id != machine_path.stem:
        raise MachineLoadError(
            f"{machine_path}: 'id' field {m_id!r} does not match filename stem "
            f"{machine_path.stem!r}"
        )

    generation: Any = raw.get("generation")
    if generation not in ("msx1", "msx2"):
        raise MachineLoadError(
            f"{machine_path}: unsupported generation {generation!r} "
            f"(expected 'msx1' or 'msx2')"
        )

    name: str = str(raw.get("name", machine_id))
    rom_base: str = str(raw.get("rom_base", "roms/cbios"))
    rom_base_dir = project_root / rom_base

    # --- Slot parsing ---
    slots: dict[str, Any] = raw.get("slots", {})
    primary: dict[Any, Any] = slots.get("primary", {})

    slot0: Any = primary.get(0, {})
    if not isinstance(slot0, dict):
        slot0 = {}
    main_rom_entry, logo_rom_entry = _parse_slot0(slot0, machine_id)
    if main_rom_entry is None:
        raise MachineLoadError(
            f"{machine_path}: slot 0 has no main ROM (content with pages [0] or [1])"
        )

    sub_rom_entry: _RomEntry | None = None
    has_ram_mapper = False
    ram_size_kb = 32

    slot3: Any = primary.get(3, {})
    if not isinstance(slot3, dict):
        slot3 = {}

    if generation == "msx2":
        sub_rom_entry, has_ram_mapper = _parse_slot3_msx2(slot3, machine_id)
    else:
        ram_size_kb = _parse_slot3_msx1(slot3)

    # --- Builtin device resolution ---
    has_v9938 = False
    has_rtc = False

    for entry in raw.get("builtin_devices", []):
        if not isinstance(entry, dict):
            continue
        ref: Any = entry.get("ref")
        if ref is None:
            continue
        ref_str = str(ref)
        if ref_str not in device_registry:
            raise MachineLoadError(
                f"{machine_path}: builtin_devices ref {ref_str!r} not found in device registry"
            )
        dev = device_registry[ref_str]
        if not dev.implemented:
            print(
                f"warning: device {ref_str!r} is not implemented, skipping",
                file=sys.stderr,
            )
            continue
        if ref_str == "vdp_v9938":
            has_v9938 = True
        elif ref_str == "rtc_rp5c01":
            has_rtc = True

    return MachineSpec(
        id=machine_id,
        name=name,
        generation=str(generation),
        rom_base_dir=rom_base_dir,
        main_rom_entry=main_rom_entry,
        logo_rom_entry=logo_rom_entry,
        sub_rom_entry=sub_rom_entry,
        has_ram_mapper=has_ram_mapper,
        ram_size_kb=ram_size_kb,
        has_v9938=has_v9938,
        has_rtc=has_rtc,
    )


# ---------------------------------------------------------------------------
# ROM loading helper
# ---------------------------------------------------------------------------

def _load_rom(rom_base_dir: Path, filename: str, *, required: bool) -> bytes | None:
    """Load a ROM file, raising or warning based on the required flag.

    Args:
        rom_base_dir: Base directory for ROM files.
        filename: Filename relative to rom_base_dir.
        required: If True and the file is missing, raise MachineLoadError.
            If False and the file is missing, print a warning and return None.

    Returns:
        ROM bytes, or None if the file is missing and required is False.

    Raises:
        MachineLoadError: If required is True and the file does not exist.
    """
    path = rom_base_dir / filename
    if not path.exists():
        if required:
            raise MachineLoadError(f"ROM file not found: {path}")
        print(f"warning: optional ROM file not found, skipping: {path}", file=sys.stderr)
        return None
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Machine builder
# ---------------------------------------------------------------------------

def build_machine(
    spec: MachineSpec,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logger: DebugLogger | None = None,
    tracer: Tracer | None = None,
    bios_override: bytes | None = None,
    logo_override: bytes | None = None,
    extrom_override: bytes | None = None,
) -> "Machine":  # type: ignore[name-defined]  # noqa: F821
    """Build a Machine from a resolved MachineSpec.

    Args:
        spec: Fully-resolved MachineSpec from load_machine_spec().
        cartridge: Optional cartridge ROM bytes for slot 1.
        mapper: Mapper type for slot 1 cartridge ('auto' or explicit name).
        cartridge2: Optional cartridge ROM bytes for slot 2.
        mapper2: Mapper type for slot 2 cartridge.
        logger: Optional DebugLogger for diagnostic output.
        tracer: Optional VDP register write tracer (V9938 only).
        bios_override: If given, use these bytes as the main BIOS ROM instead
            of loading spec.main_rom_entry.file from disk.
        logo_override: If given, use these bytes as the logo ROM instead of
            loading spec.logo_rom_entry.file from disk. Pass None to skip logo.
        extrom_override: If given (MSX2 only), use these bytes as the MSX2
            extension/sub ROM instead of loading spec.sub_rom_entry.file.

    Returns:
        A fully-wired Machine ready for emulation.

    Raises:
        MachineLoadError: If a required ROM file is missing and no override is given.
    """
    from msx.machine import Machine  # local import to avoid circular dependency

    # --- ROM loading ---
    if bios_override is not None:
        main_bytes: bytes = bios_override
    else:
        loaded = _load_rom(spec.rom_base_dir, spec.main_rom_entry.file, required=True)
        assert loaded is not None
        main_bytes = loaded

    if logo_override is not None:
        logo_bytes: bytes | None = logo_override
    elif spec.logo_rom_entry is not None:
        logo_bytes = _load_rom(spec.rom_base_dir, spec.logo_rom_entry.file, required=False)
    else:
        logo_bytes = None

    # --- Cartridge mapper resolution ---
    resolved = _resolve_mapper_type(mapper, cartridge)
    scc: SCC | None = SCC() if resolved == "KonamiSCC" else None

    resolved2 = _resolve_mapper_type(mapper2, cartridge2)
    if resolved2 == "KonamiSCC":
        print(
            "warning: KonamiSCC is not supported for slot 2, using Konami mapper",
            file=sys.stderr,
        )
        resolved2 = "Konami"

    mapper_instance = _make_mapper(resolved, cartridge, scc=scc)
    mapper2_instance = _make_mapper(resolved2, cartridge2)
    dac: MajutsushiMapper | None = (
        mapper_instance if isinstance(mapper_instance, MajutsushiMapper) else None
    )

    input_state = InputState()
    psg = PSG(_input=input_state)
    io = IOBus(_logger=logger)

    if spec.generation == "msx2":
        machine = _build_msx2(
            spec=spec,
            main_bytes=main_bytes,
            logo_bytes=logo_bytes,
            extrom_override=extrom_override,
            mapper_instance=mapper_instance,
            mapper2_instance=mapper2_instance,
            dac=dac,
            scc=scc,
            input_state=input_state,
            psg=psg,
            io=io,
            logger=logger,
            tracer=tracer,
            Machine=Machine,
        )
    else:
        machine = _build_msx1(
            spec=spec,
            main_bytes=main_bytes,
            logo_bytes=logo_bytes,
            mapper_instance=mapper_instance,
            mapper2_instance=mapper2_instance,
            dac=dac,
            scc=scc,
            input_state=input_state,
            psg=psg,
            io=io,
            logger=logger,
            Machine=Machine,
        )

    io._get_pc = lambda: machine.cpu.registers.PC
    if dac is not None:
        dac._get_cycle = lambda: machine.cycle_count
    return machine


def _build_msx1(
    *,
    spec: MachineSpec,
    main_bytes: bytes,
    logo_bytes: bytes | None,
    mapper_instance: Any,
    mapper2_instance: Any,
    dac: MajutsushiMapper | None,
    scc: SCC | None,
    input_state: InputState,
    psg: PSG,
    io: IOBus,
    logger: DebugLogger | None,
    Machine: Any,
) -> Any:
    memory = Memory(
        rom=main_bytes,
        ram=bytearray(spec.ram_size_kb * 1024),
        _mapper=mapper_instance,
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logo_bytes,
    )
    vdp = VDP(_logger=logger)
    ppi = PPI(memory=memory, _input=input_state)
    io.register_read(0x98, 0x99, vdp.read_port)
    io.register_write(0x98, 0x99, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    return Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
    )


def _build_msx2(
    *,
    spec: MachineSpec,
    main_bytes: bytes,
    logo_bytes: bytes | None,
    extrom_override: bytes | None,
    mapper_instance: Any,
    mapper2_instance: Any,
    dac: MajutsushiMapper | None,
    scc: SCC | None,
    input_state: InputState,
    psg: PSG,
    io: IOBus,
    logger: DebugLogger | None,
    tracer: Tracer | None,
    Machine: Any,
) -> Any:
    if extrom_override is not None:
        sub_bytes: bytes | None = extrom_override
    elif spec.sub_rom_entry is not None:
        sub_bytes = _load_rom(spec.rom_base_dir, spec.sub_rom_entry.file, required=True)
    else:
        sub_bytes = None

    ram_mapper: RamMapper | None = RamMapper() if spec.has_ram_mapper else None
    memory = Memory(
        rom=main_bytes,
        ram=bytearray(32768),
        _mapper=mapper_instance,
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logo_bytes,
        sub0_rom=sub_bytes,
        sub_slot_enabled=True,
        ram_mapper=ram_mapper,
    )
    vdp: V9938 | VDP = V9938() if spec.has_v9938 else VDP(_logger=logger)
    rtc: RTC | None = RTC() if spec.has_rtc else None
    ppi = PPI(memory=memory, _input=input_state)

    vdp_end_port = 0x9C if isinstance(vdp, V9938) else 0x99
    io.register_read(0x98, vdp_end_port, vdp.read_port)
    io.register_write(0x98, vdp_end_port, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    if rtc is not None:
        io.register_read(0xB4, 0xB5, rtc.read_port)
        io.register_write(0xB4, 0xB5, rtc.write_port)
    if ram_mapper is not None:
        io.register_read(0xFC, 0xFF, ram_mapper.read_port)
        io.register_write(0xFC, 0xFF, ram_mapper.write_port)

    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    machine = Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
    )
    if tracer is not None and isinstance(vdp, V9938):
        vdp.tracer = tracer
        vdp._get_pc = lambda: machine.cpu.instruction_pc
        vdp._get_cycle = lambda: machine.cycle_count
        vdp._get_frame = lambda: vdp._frame_count
    return machine
