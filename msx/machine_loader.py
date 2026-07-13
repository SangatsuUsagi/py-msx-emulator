"""YAML-based machine configuration loader.

Two-pass loading: device registry first, then machine spec resolution.
Raises MachineLoadError with specific file and field names on any validation failure.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

import msx.romdb as romdb
from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.input import InputState
from msx.io import IOBus
from msx.mapper import (
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    FlatMapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    Mapper,
    RTypeMapper,
)
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.ram_mapper import RamMapper
from msx.rtc import RTC
from msx.scc import SCC
from msx.vdp.tracer import Tracer
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP

# ---------------------------------------------------------------------------
# Mapper helpers (shared with build_machine)
# ---------------------------------------------------------------------------

_SUPPORTED_MAPPERS = frozenset({
    "Mirrored", "Normal", "ASCII8", "ASCII16", "Konami", "KonamiSCC", "Majutsushi",
    "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
    "R-Type",
})

# Supported FDC controller chips and connection styles, selected by machine YAML.
# New entries here (plus a builder branch in _build_fdc) add hardware without
# touching Memory.
_SUPPORTED_FDC_CONTROLLERS = frozenset({"wd2793"})
_SUPPORTED_FDC_STYLES = frozenset({"sony"})

_SRAM_SIZES: dict[str, int] = {
    "ASCII8SRAM2": 2048,
    "ASCII8SRAM8": 8192,
    "ASCII16SRAM2": 2048,
    "ASCII16SRAM8": 8192,
}


def _resolve_mapper_type(mapper: str, cartridge: bytes | None) -> tuple[str, str | None]:
    """Resolve the mapper type and return it with the cartridge sha1 (computed
    once here so callers can reuse it for the SRAM save path)."""
    sha1 = (
        hashlib.sha1(cartridge, usedforsecurity=False).hexdigest()
        if cartridge is not None
        else None
    )
    if mapper != "auto":
        return mapper, sha1
    if cartridge is None:
        return "Mirrored", sha1
    found = romdb.lookup(cartridge)
    if found is None:
        print("warning: cartridge not found in ROM database, using Mirrored mapper",
              file=sys.stderr)
        return "Mirrored", sha1
    if found not in _SUPPORTED_MAPPERS:
        print(f"warning: unsupported mapper type {found!r} from ROM database, "
              "using Mirrored mapper", file=sys.stderr)
        return "Mirrored", sha1
    return found, sha1


def _require_scc(scc: SCC | None) -> SCC:
    if scc is None:
        raise ValueError("KonamiSCC mapper requires an SCC instance")
    return scc


# mapper_type -> builder. Each builder receives (cartridge, rom_bytes, sram,
# scc): `cartridge` is the raw ROM (None when absent), `rom_bytes` the same
# with None normalised to b"". FlatMapper keeps the None-able cartridge (it
# treats "no ROM" specially); the bank-switching mappers take rom_bytes.
_MAPPER_BUILDERS: dict[
    str, Callable[[bytes | None, bytes, bytearray | None, SCC | None], Mapper]
] = {
    "Mirrored":     lambda cart, rom, sram, scc: FlatMapper(cart),
    "Normal":       lambda cart, rom, sram, scc: FlatMapper(cart),
    "ASCII8":       lambda cart, rom, sram, scc: Ascii8Mapper(rom),
    "ASCII8SRAM2":  lambda cart, rom, sram, scc: Ascii8Sram2Mapper(rom, sram=sram),
    "ASCII8SRAM8":  lambda cart, rom, sram, scc: Ascii8Sram8Mapper(rom, sram=sram),
    "ASCII16":      lambda cart, rom, sram, scc: Ascii16Mapper(rom),
    "ASCII16SRAM2": lambda cart, rom, sram, scc: Ascii16Sram2Mapper(rom, sram=sram),
    "ASCII16SRAM8": lambda cart, rom, sram, scc: Ascii16Sram8Mapper(rom, sram=sram),
    "Konami":       lambda cart, rom, sram, scc: KonamiMapper(rom),
    "Majutsushi":   lambda cart, rom, sram, scc: MajutsushiMapper(rom),
    "R-Type":       lambda cart, rom, sram, scc: RTypeMapper(rom),
    "KonamiSCC":    lambda cart, rom, sram, scc: KonamiSCCMapper(rom, scc=_require_scc(scc)),
}


def _make_mapper(
    mapper_type: str,
    cartridge: bytes | None,
    scc: SCC | None = None,
    sram: bytearray | None = None,
) -> Mapper:
    builder = _MAPPER_BUILDERS.get(mapper_type)
    if builder is None:
        raise ValueError(f"unknown mapper type: {mapper_type!r}")
    rom_bytes = cartridge if cartridge is not None else b""
    return builder(cartridge, rom_bytes, sram, scc)


# Standard MSX I/O port map (first, last), device_id -> ports. Used as the
# fallback when a device's YAML omits an explicit port range. The V9938 VDP
# extends the high port to 0x9C; that case is handled at its call site.
_DEFAULT_IO_PORTS: dict[str, tuple[int, int]] = {
    "vdp_tms9918a": (0x98, 0x99),
    "psg_ay8910": (0xA0, 0xA2),
    "ppi8255": (0xA8, 0xAB),
    "rtc_rp5c01": (0xB4, 0xB5),
    "memory_mapper_standard": (0xFC, 0xFF),
}


# cycles_per_frame, lines_per_frame keyed by video standard
_TIMING: dict[str, tuple[int, int]] = {
    "ntsc": (59_659, 262),
    "pal":  (71_364, 313),
}


class MachineLoadError(Exception):
    """Raised when a device or machine YAML fails validation."""


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------

@dataclass
class _DeviceDef:
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
class _FdcDef:
    """Resolved floppy interface: DISK ROM entry, controller, style, drive count."""
    disk_rom_entry: _RomEntry
    controller: str
    connection_style: str
    drives: int


@dataclass
class MachineSpec:
    """Fully-resolved machine wiring, ready for instantiation."""

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

    # Keyboard layout resolved from the ppi8255 device ("int" or "jp")
    keyboard_type: str = "int"

    # I/O port ranges from device YAML: device_id -> (first_port, last_port)
    device_io_ports: dict[str, tuple[int, int]] = field(default_factory=dict)

    # Timing (derived from video_standard in YAML)
    cycles_per_frame: int = 59_659   # NTSC default
    lines_per_frame: int = 262       # NTSC default

    # MSX2 flat (non-mapper) RAM sub-slot, e.g. HB-F1XD's 64 KB in sub-slot 3.
    # None when RAM is mapper-backed (the common C-BIOS case).
    flat_ram_subslot: int | None = None
    flat_ram_size_kb: int = 64

    # Floppy interface in slot 3 sub-slot 0, or None when the machine has none.
    fdc: _FdcDef | None = None


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
            type=str(dev_type), implemented=implemented, raw=raw
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


def _int_keys(slot_map: dict[Any, Any]) -> dict[int, Any]:
    """Coerce slot-map keys to int so string/JSON keys (e.g. "0") resolve via .get(0)."""
    out: dict[int, Any] = {}
    for key, value in slot_map.items():
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            continue
    return out


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


def _parse_fdc(sub0: dict[str, Any], machine_id: str) -> _FdcDef | None:
    """Resolve an optional `fdc:` block in slot 3 sub-slot 0.

    Raises:
        MachineLoadError: On a missing DISK ROM entry, or an unsupported
            controller type or connection style.
    """
    fdc_raw: Any = sub0.get("fdc")
    if not isinstance(fdc_raw, dict):
        return None
    context = f"machine '{machine_id}' slot 3 sub-slot 0 fdc"
    rom_data: Any = fdc_raw.get("rom")
    if not isinstance(rom_data, dict):
        raise MachineLoadError(f"{context}: missing required 'rom' entry")
    disk_rom_entry = _parse_rom_entry(rom_data, context)
    controller = str(fdc_raw.get("controller", "wd2793")).lower()
    if controller not in _SUPPORTED_FDC_CONTROLLERS:
        raise MachineLoadError(
            f"{context}: unsupported controller {controller!r} "
            f"(supported: {sorted(_SUPPORTED_FDC_CONTROLLERS)})"
        )
    style = str(fdc_raw.get("connection_style", "sony")).lower()
    if style not in _SUPPORTED_FDC_STYLES:
        raise MachineLoadError(
            f"{context}: unsupported connection_style {style!r} "
            f"(supported: {sorted(_SUPPORTED_FDC_STYLES)})"
        )
    drives = int(fdc_raw.get("drives", 1))
    return _FdcDef(
        disk_rom_entry=disk_rom_entry,
        controller=controller,
        connection_style=style,
        drives=max(1, drives),
    )


def _parse_slot3_msx2(
    slot3: dict[str, Any], machine_id: str
) -> tuple[_RomEntry | None, bool, int | None, int, _FdcDef | None]:
    """Resolve an MSX2 slot 3 declaration.

    Returns (sub_rom_entry, has_ram_mapper, flat_ram_subslot, flat_ram_size_kb,
    fdc). A sub-slot declaring `type: ram` without `mapper: standard` is a flat
    (non-mapper) RAM (e.g. HB-F1XD's 64 KB in sub-slot 3); a `mapper: standard`
    sub-slot sets has_ram_mapper as before. An `fdc:` block in sub-slot 0
    resolves the floppy interface.
    """
    sub_rom: _RomEntry | None = None
    has_ram_mapper = False
    flat_ram_subslot: int | None = None
    flat_ram_size_kb = 64
    fdc: _FdcDef | None = None
    if slot3.get("expanded"):
        secondary: dict[int, Any] = _int_keys(slot3.get("secondary", {}))
        sub0: Any = secondary.get(0, {})
        if isinstance(sub0, dict):
            for item in sub0.get("content", []):
                rom_data: Any = item.get("rom")
                if isinstance(rom_data, dict):
                    sub_rom = _parse_rom_entry(
                        rom_data, f"machine '{machine_id}' slot 3 sub-slot 0"
                    )
                    break
            fdc = _parse_fdc(sub0, machine_id)
        for sub_idx, sub_val in secondary.items():
            if not isinstance(sub_val, dict):
                continue
            if sub_val.get("mapper") == "standard":
                has_ram_mapper = True
            elif sub_val.get("type") == "ram":
                flat_ram_subslot = sub_idx
                flat_ram_size_kb = int(sub_val.get("size_kb", 64))
    elif slot3.get("mapper") == "standard":
        has_ram_mapper = True
    return sub_rom, has_ram_mapper, flat_ram_subslot, flat_ram_size_kb, fdc


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

    video_standard: str = str(raw.get("video_standard", "ntsc")).lower()
    if video_standard not in _TIMING:
        raise MachineLoadError(
            f"{machine_path}: unsupported video_standard {video_standard!r} "
            f"(expected 'ntsc' or 'pal')"
        )
    cycles_per_frame, lines_per_frame = _TIMING[video_standard]

    # --- Slot parsing ---
    slots: dict[str, Any] = raw.get("slots", {})
    primary: dict[int, Any] = _int_keys(slots.get("primary", {}))

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
    flat_ram_subslot: int | None = None
    flat_ram_size_kb = 64
    fdc: _FdcDef | None = None

    slot3: Any = primary.get(3, {})
    if not isinstance(slot3, dict):
        slot3 = {}

    if generation == "msx2":
        (sub_rom_entry, has_ram_mapper, flat_ram_subslot,
         flat_ram_size_kb, fdc) = _parse_slot3_msx2(slot3, machine_id)
    else:
        ram_size_kb = _parse_slot3_msx1(slot3)

    # --- Builtin device resolution ---
    has_v9938 = False
    has_rtc = False
    keyboard_type = "int"
    device_io_ports: dict[str, tuple[int, int]] = {}

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
        elif ref_str == "ppi8255":
            # Keyboard layout: device-YAML default, optionally overridden per machine.
            kt = dev.raw.get("keyboard_type", "int")
            overrides = entry.get("overrides")
            if isinstance(overrides, dict) and "keyboard_type" in overrides:
                kt = overrides["keyboard_type"]
            kt = str(kt).lower()
            if kt not in ("int", "jp"):
                raise MachineLoadError(
                    f"{machine_path}: ppi8255 keyboard_type must be 'int' or 'jp', got {kt!r}"
                )
            keyboard_type = kt
        ports_raw: Any = dev.raw.get("io_ports")
        if isinstance(ports_raw, list) and len(ports_raw) >= 1:
            device_io_ports[ref_str] = (int(ports_raw[0]), int(ports_raw[-1]))

    return MachineSpec(
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
        keyboard_type=keyboard_type,
        device_io_ports=device_io_ports,
        cycles_per_frame=cycles_per_frame,
        lines_per_frame=lines_per_frame,
        flat_ram_subslot=flat_ram_subslot,
        flat_ram_size_kb=flat_ram_size_kb,
        fdc=fdc,
    )


# ---------------------------------------------------------------------------
# I/O port range helper
# ---------------------------------------------------------------------------

def _io_range(
    spec: MachineSpec,
    device_id: str,
    fallback: tuple[int, int],
) -> tuple[int, int]:
    """Return (first_port, last_port) for device_id from spec, or fallback."""
    return spec.device_io_ports.get(device_id, fallback)


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
    disk_rom_override: bytes | None = None,
    disc1: Path | None = None,
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
    resolved, cart_sha1 = _resolve_mapper_type(mapper, cartridge)
    scc: SCC | None = SCC() if resolved == "KonamiSCC" else None

    resolved2, _ = _resolve_mapper_type(mapper2, cartridge2)
    if resolved2 == "KonamiSCC":
        print(
            "warning: KonamiSCC is not supported for slot 2, using Konami mapper",
            file=sys.stderr,
        )
        resolved2 = "Konami"

    # SRAM: load existing save file if mapper supports it
    sram_save_path: Path | None = None
    sram_data: bytearray | None = None
    if resolved in _SRAM_SIZES and cartridge is not None:
        # Reuse the sha1 computed in _resolve_mapper_type (cartridge is not None
        # here, so cart_sha1 is set).
        assert cart_sha1 is not None
        sram_save_path = Path("saves") / "sram" / f"{cart_sha1}.sram"
        expected_size = _SRAM_SIZES[resolved]
        if sram_save_path.exists():
            raw = sram_save_path.read_bytes()
            if len(raw) == expected_size:
                sram_data = bytearray(raw)
            else:
                print(
                    f"warning: SRAM file {sram_save_path} has wrong size "
                    f"({len(raw)} != {expected_size}), starting fresh",
                    file=sys.stderr,
                )

    mapper_instance = _make_mapper(resolved, cartridge, scc=scc, sram=sram_data)
    mapper2_instance = _make_mapper(resolved2, cartridge2)
    dac: MajutsushiMapper | None = (
        mapper_instance if isinstance(mapper_instance, MajutsushiMapper) else None
    )

    input_state = InputState(keyboard_type=spec.keyboard_type)
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
            machine_cls=Machine,
            disk_rom_override=disk_rom_override,
            disc1=disc1,
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
            machine_cls=Machine,
        )

    io._get_pc = lambda: machine.cpu.registers.PC
    if dac is not None:
        dac._get_cycle = lambda: machine.cycle_count
    machine.sram_save_path = sram_save_path
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
    machine_cls: Any,
) -> Any:
    memory = Memory(
        rom=main_bytes,
        ram=bytearray(spec.ram_size_kb * 1024),
        _mapper=mapper_instance,
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logo_bytes,
        rom_name=spec.main_rom_entry.file,
    )
    vdp = VDP(_logger=logger)
    ppi = PPI(memory=memory, _input=input_state)
    vdp_s, vdp_e = _io_range(spec, "vdp_tms9918a", _DEFAULT_IO_PORTS["vdp_tms9918a"])
    psg_s, psg_e = _io_range(spec, "psg_ay8910", _DEFAULT_IO_PORTS["psg_ay8910"])
    ppi_s, ppi_e = _io_range(spec, "ppi8255", _DEFAULT_IO_PORTS["ppi8255"])
    io.register_read(vdp_s, vdp_e, vdp.read_port)
    io.register_write(vdp_s, vdp_e, vdp.write_port)
    io.register_read(psg_s, psg_e, psg.read_port)
    io.register_write(psg_s, psg_e, psg.write_port)
    io.register_read(ppi_s, ppi_e, ppi.read_port)
    io.register_write(ppi_s, ppi_e, ppi.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    return machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        cycles_per_frame=spec.cycles_per_frame,
        lines_per_frame=spec.lines_per_frame,
    )


def _build_fdc(
    spec: MachineSpec, disc1: Path | None, disk_rom_override: bytes | None
) -> Any:
    """Construct the FloppyDisk device from spec.fdc, mounting disc1 into drive A."""
    from msx.fdc.disk_drive import DiskDrive
    from msx.fdc.disk_image import DskDiskImage
    from msx.fdc.interface import SonyPhilipsInterface
    from msx.fdc.wd2793 import WD2793

    assert spec.fdc is not None
    if disk_rom_override is not None:
        disk_rom: bytes | None = disk_rom_override
    else:
        disk_rom = _load_rom(spec.rom_base_dir, spec.fdc.disk_rom_entry.file, required=True)
    drives = [DiskDrive() for _ in range(spec.fdc.drives)]
    controller = WD2793()
    # connection_style was validated by the loader; only 'sony' exists today.
    device = SonyPhilipsInterface(controller, drives, disk_rom=disk_rom)
    if disc1 is not None:
        device.mount(DskDiskImage(disc1), drive=0)
    return device


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
    machine_cls: Any,
    disk_rom_override: bytes | None = None,
    disc1: Path | None = None,
) -> Any:
    if extrom_override is not None:
        sub_bytes: bytes | None = extrom_override
    elif spec.sub_rom_entry is not None:
        sub_bytes = _load_rom(spec.rom_base_dir, spec.sub_rom_entry.file, required=True)
    else:
        sub_bytes = None

    # Flat (non-mapper) RAM machines (e.g. HB-F1XD) allocate their full RAM and
    # use the data-driven slot-3 sub-slot dispatch; mapper machines keep the
    # 32 KB + RamMapper wiring unchanged.
    if spec.flat_ram_subslot is not None and not spec.has_ram_mapper:
        ram_mapper: RamMapper | None = None
        ram_bytes = bytearray(spec.flat_ram_size_kb * 1024)
        flat_ram_subslot: int | None = spec.flat_ram_subslot
    else:
        ram_mapper = RamMapper() if spec.has_ram_mapper else None
        ram_bytes = bytearray(32768)
        flat_ram_subslot = None

    fdc_device = (
        _build_fdc(spec, disc1, disk_rom_override) if spec.fdc is not None else None
    )
    memory = Memory(
        rom=main_bytes,
        ram=ram_bytes,
        _mapper=mapper_instance,
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logo_bytes,
        sub0_rom=sub_bytes,
        sub_slot_enabled=True,
        ram_mapper=ram_mapper,
        flat_ram_subslot=flat_ram_subslot,
        fdc=fdc_device,
        rom_name=spec.main_rom_entry.file,
        sub0_rom_name=spec.sub_rom_entry.file if spec.sub_rom_entry is not None else "",
    )
    vdp: V9938 | VDP = V9938() if spec.has_v9938 else VDP(_logger=logger)
    rtc: RTC | None = RTC() if spec.has_rtc else None
    ppi = PPI(memory=memory, _input=input_state)

    vdp_dev_id = "vdp_v9938" if spec.has_v9938 else "vdp_tms9918a"
    # V9938 extends the VDP high port to 0x9C (palette/indirect regs); TMS9918A stops at 0x99.
    vdp_default_ports = (0x98, 0x9C) if spec.has_v9938 else _DEFAULT_IO_PORTS["vdp_tms9918a"]
    vdp_s, vdp_e = _io_range(spec, vdp_dev_id, vdp_default_ports)
    psg_s, psg_e = _io_range(spec, "psg_ay8910", _DEFAULT_IO_PORTS["psg_ay8910"])
    ppi_s, ppi_e = _io_range(spec, "ppi8255", _DEFAULT_IO_PORTS["ppi8255"])
    io.register_read(vdp_s, vdp_e, vdp.read_port)
    io.register_write(vdp_s, vdp_e, vdp.write_port)
    io.register_read(psg_s, psg_e, psg.read_port)
    io.register_write(psg_s, psg_e, psg.write_port)
    io.register_read(ppi_s, ppi_e, ppi.read_port)
    io.register_write(ppi_s, ppi_e, ppi.write_port)
    if rtc is not None:
        rtc_s, rtc_e = _io_range(spec, "rtc_rp5c01", _DEFAULT_IO_PORTS["rtc_rp5c01"])
        io.register_read(rtc_s, rtc_e, rtc.read_port)
        io.register_write(rtc_s, rtc_e, rtc.write_port)
    if ram_mapper is not None:
        ram_s, ram_e = _io_range(spec, "memory_mapper_standard",
                                 _DEFAULT_IO_PORTS["memory_mapper_standard"])
        io.register_read(ram_s, ram_e, ram_mapper.read_port)
        io.register_write(ram_s, ram_e, ram_mapper.write_port)

    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    machine = machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        cycles_per_frame=spec.cycles_per_frame,
        lines_per_frame=spec.lines_per_frame,
        fdc=fdc_device,
    )
    if tracer is not None and isinstance(vdp, V9938):
        vdp.tracer = tracer
        vdp._get_pc = lambda: machine.cpu.instruction_pc
        vdp._get_cycle = lambda: machine.cycle_count
        # The tracer reads the VDP frame count directly (V9938.write_port passes
        # self._frame_count), so no _get_frame getter is needed.
    return machine
