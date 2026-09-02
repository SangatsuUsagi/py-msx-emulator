"""YAML-based machine configuration loader.

Two-pass loading: device registry first, then machine spec resolution.
Raises MachineLoadError with specific file and field names on any validation failure.

PORT-LIBRARY-NOTE: this file's `yaml.safe_load()` -> `dict[str, Any]` ->
  hand-rolled isinstance/.get() validation into typed dataclasses (MachineSpec
  et al.) used to be the largest source of `Any` in this codebase (same
  pattern, at smaller scale, still applies to msx/app_config.py and
  msx/romdb.py). RESOLVED for this file by
  openspec/changes/typed-machine-loader-yaml: every raw YAML mapping this
  file validates is now typed via `TypedDict` (`RomEntryYaml`,
  `DeviceEntryYaml`, `MachineEntryYaml`, etc.) with `TypeGuard`-returning
  `_is_<thing>_shape` narrowing functions, so a field read off an
  already-validated mapping is checked against a known shape, not `Any`.
  This is a Python-side approximation, not a schema-validation library --
  the `TypeGuard` functions still hand-write the same runtime checks this
  file always performed (see design.md Decision 3); only the point where
  static typing starts moved earlier. Remaining `Any` in this file is
  deliberate: `_DeviceDef.raw`/`overrides` (arbitrary per-device passthrough
  content), and `secondary`/`primary`/`slots` (raw YAML keys before
  `_int_keys()` coercion narrows their values per-item).
Rust crate candidates: serde + serde_yaml (or saphyr) deserializing directly
  into the equivalent MachineSpec/AppConfig structs, replacing this hand-
  written validation layer with derive-macro-generated checks -- would need
  custom Deserialize impls to reproduce the exact error messages and
  _KNOWN_*_KEYS unknown-key warnings this file emits. The TypedDict shapes
  this file now declares are a reasonable 1:1 map to the equivalent serde
  structs, should a Rust port be undertaken.
C++ library candidates: no single dominant equivalent -- yaml-cpp + manual
  validation (closest to today's Python shape), or a schema-validated
  approach via nlohmann/json-style patterns if the config format were
  migrated to JSON at port time.
"""
from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, TypedDict, TypeGuard, cast

import yaml

if TYPE_CHECKING:
    from msx.fdc.interface import FloppyDisk
    from msx.machine import Machine

import msx.romdb as romdb
from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.fmpac import SRAM_SIZE as FMPAC_SRAM_SIZE
from msx.fmpac import FmPac
from msx.input import InputState
from msx.io import IOBus
from msx.mapper import (
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    FixedPageMapper,
    FlatMapper,
    GameMaster2Mapper,
    KoeiSRAM32Mapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    Mapper,
    RTypeMapper,
    SCCICart,
)
from msx.memory import Memory
from msx.opll import Opll
from msx.ppi import PPI
from msx.psg import PSG
from msx.ram_mapper import RamMapper
from msx.rtc import RTC
from msx.rtc import SRAM_SIZE as RTC_SRAM_SIZE
from msx.scc import SCC
from msx.vdp.tracer import Tracer
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP, VdpDevice

# ---------------------------------------------------------------------------
# Mapper helpers (shared with build_machine)
# ---------------------------------------------------------------------------

_SUPPORTED_MAPPERS = frozenset({
    "Mirrored", "Normal", "ASCII8", "ASCII16", "Konami", "KonamiSCC", "Majutsushi",
    "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
    "R-Type", "Page2", "0x4000", "0x8000", "KoeiSRAM32", "GameMaster2",
})

# Supported FDC controller chips and connection styles, selected by machine YAML.
# New entries here (plus a builder branch in _build_fdc) add hardware without
# touching Memory.
_SUPPORTED_FDC_CONTROLLERS = frozenset({"wd2793", "tc8566af"})
_SUPPORTED_FDC_STYLES = frozenset({"sony", "tc8566af"})
# (controller, connection_style) pairs _build_fdc actually knows how to
# construct -- each controller family has exactly one style today, but the
# two are independent axes in the YAML schema, so validate the pair
# explicitly rather than silently defaulting a mismatched combo (e.g.
# controller: wd2793 + connection_style: tc8566af) to whichever branch
# _build_fdc happens to fall into.
_SUPPORTED_FDC_PAIRS = frozenset({("wd2793", "sony"), ("tc8566af", "tc8566af")})

# The MSX2 secondary slot register selects each page's sub-slot via a 2-bit
# field (openspec/specs/msx2-subslot/spec.md's "Secondary slot register
# field" Requirement: bits 7:6/5:4/3:2/1:0 select the sub-slot for pages
# 3/2/1/0 respectively; the "Slot 3 sub-slot dispatch" Requirement extracts
# it as `(sub_slot_reg >> (page * 2)) & 0x03`, matching msx/memory.py's own
# `& 0x03` masks). A 2-bit field can only ever hold 0-3 -- this is not a
# bound the loader is free to pick independently of that register layout.
_MAX_SUB_SLOT_INDEX = 3

_SRAM_SIZES: dict[str, int] = {
    "ASCII8SRAM2": 2048,
    "ASCII8SRAM8": 8192,
    "ASCII16SRAM2": 2048,
    "ASCII16SRAM8": 8192,
    "KoeiSRAM32": 32768,
    "GameMaster2": 8192,
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
        raise MachineLoadError("KonamiSCC mapper requires an SCC instance")
    return scc


def _sram_or_empty(sram: bytearray | None) -> bytearray:
    """Adapt an Optional preloaded save to the SRAM mappers' non-Optional
    `sram` field -- an empty buffer is the "no preload" sentinel each
    mapper's __post_init__ already replaces with a correctly-sized one."""
    return sram if sram is not None else bytearray()


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
    "ASCII8SRAM2":  lambda cart, rom, sram, scc: Ascii8Sram2Mapper(rom, sram=_sram_or_empty(sram)),
    "ASCII8SRAM8":  lambda cart, rom, sram, scc: Ascii8Sram8Mapper(rom, sram=_sram_or_empty(sram)),
    "ASCII16":      lambda cart, rom, sram, scc: Ascii16Mapper(rom),
    "ASCII16SRAM2": lambda cart, rom, sram, scc: Ascii16Sram2Mapper(
        rom, sram=_sram_or_empty(sram)
    ),
    "ASCII16SRAM8": lambda cart, rom, sram, scc: Ascii16Sram8Mapper(
        rom, sram=_sram_or_empty(sram)
    ),
    "Konami":       lambda cart, rom, sram, scc: KonamiMapper(rom),
    "Majutsushi":   lambda cart, rom, sram, scc: MajutsushiMapper(rom),
    "R-Type":       lambda cart, rom, sram, scc: RTypeMapper(rom),
    "KonamiSCC":    lambda cart, rom, sram, scc: KonamiSCCMapper(rom, scc=_require_scc(scc)),
    # "Page2" (MSX slot page 2, 0x8000-0xBFFF) and "0x8000" are distinct
    # ROM-DB mapper names for the same fixed-window layout -- not a duplicate.
    "Page2":        lambda cart, rom, sram, scc: FixedPageMapper(rom, base=0x8000),
    "0x4000":       lambda cart, rom, sram, scc: FixedPageMapper(rom, base=0x4000),
    "0x8000":       lambda cart, rom, sram, scc: FixedPageMapper(rom, base=0x8000),
    "KoeiSRAM32":   lambda cart, rom, sram, scc: KoeiSRAM32Mapper(rom, sram=_sram_or_empty(sram)),
    "GameMaster2":  lambda cart, rom, sram, scc: GameMaster2Mapper(rom, sram=_sram_or_empty(sram)),
}


def _make_mapper(
    mapper_type: str,
    cartridge: bytes | None,
    scc: SCC | None = None,
    sram: bytearray | None = None,
) -> Mapper:
    builder = _MAPPER_BUILDERS.get(mapper_type)
    if builder is None:
        raise MachineLoadError(f"unknown mapper type: {mapper_type!r}")
    rom_bytes = cartridge if cartridge is not None else b""
    return builder(cartridge, rom_bytes, sram, scc)


# Standard MSX I/O port map (first, last), device_id -> ports. Used as the
# fallback when a device's YAML omits an explicit port range. The V9938 VDP
# extends the high port to 0x9B; that case is handled at its call site.
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
class _Slot3Msx2:
    """Resolved MSX2 slot 3 layout (named to avoid a positional tuple)."""
    sub_rom: _RomEntry | None = None
    sub_rom_subslot: int = 0
    has_ram_mapper: bool = False
    flat_ram_subslot: int | None = None
    flat_ram_size_kb: int = 64
    fdc: _FdcDef | None = None
    fdc_subslot: int = 0


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

    # The --machine value / config/machines/<machine_id>.yaml stem this spec
    # was loaded for (set by load_machine_spec). Used for per-machine (as
    # opposed to per-cartridge) persistence paths, e.g. each RTC-equipped
    # machine's own CMOS RAM file. Defaults to "unknown" for direct/test
    # construction, which never goes through load_machine_spec and doesn't
    # need a real id (each test isolates its own saves/ via tmp_path).
    machine_id: str = "unknown"

    # I/O port ranges from device YAML: device_id -> (first_port, last_port)
    device_io_ports: dict[str, tuple[int, int]] = field(default_factory=dict)

    # Timing (derived from video_standard in YAML)
    cycles_per_frame: int = 59_659   # NTSC default
    lines_per_frame: int = 262       # NTSC default

    # Extra T-states per Z80 M1 (opcode fetch), from the YAML `cpu:` block.
    # 0 = pure datasheet Z80; MSX machines set 1 (the MSX M1 wait state).
    m1_wait_states: int = 0

    # MSX2 flat (non-mapper) RAM sub-slot, e.g. HB-F1XD's 64 KB in sub-slot 3.
    # None when RAM is mapper-backed (the common C-BIOS case).
    flat_ram_subslot: int | None = None
    flat_ram_size_kb: int = 64

    # Which sub-slot the SUB ROM resolves to -- default 0 preserves every
    # existing machine's resolved layout (all predate this field and
    # declare SUB ROM in sub-slot 0).
    sub_rom_subslot: int = 0

    # Floppy interface in slot 3, or None when the machine has none. Which
    # sub-slot it resolves to -- default 0, same backward-compat rationale
    # as sub_rom_subslot -- independent of sub_rom_subslot (a machine MAY
    # place SUB ROM and the FDC in different sub-slots, e.g. FS-A1F's real
    # hardware layout).
    fdc: _FdcDef | None = None
    fdc_subslot: int = 0


@dataclass
class _FmPacOverlay:
    """Resolved FM-PAC overlay (config/machines/fmpac.yaml): ROM + SRAM save
    path for the --fmpac flag. Not a machine spec — applied on top of one."""

    rom_base_dir: Path
    rom_entry: _RomEntry
    slot: int
    sram_save_path: Path


# ---------------------------------------------------------------------------
# Raw YAML shapes (TypedDict + TypeGuard)
#
# One TypedDict per distinct raw-YAML shape this file validates, named
# `<Thing>Yaml` to stay visually distinct from the *resolved* dataclasses
# above (e.g. RomEntryYaml vs _RomEntry). Every shape is a single
# `total=False` TypedDict, no field statically required: this file's
# validation style is per-field `.get()` + immediate raise, inline in the
# same function that uses the value, not an upstream gate that downstream
# code could trust -- see design.md Decision 2 for why a required/optional
# split (the originally-planned approach) doesn't fit that style.
#
# Each shape's `_is_<thing>_shape(data: object) -> TypeGuard[<Thing>Yaml]`
# function performs the same isinstance/.get() checks the corresponding
# parser already performed before this change -- TypeGuard only tells mypy
# "trust the narrower type once this returns True", it does not generate or
# replace runtime validation. See openspec/changes/typed-machine-loader-yaml/
# design.md for the full rationale.
# ---------------------------------------------------------------------------

class RomEntryYaml(TypedDict, total=False):
    file: str
    size_kb: int
    pages: list[int]
    sha1: str


def _is_rom_entry_shape(data: object) -> TypeGuard[RomEntryYaml]:
    """True if `data` is a dict (rom: block shape). `file`'s presence is
    still checked by `_parse_rom_entry` itself, not guaranteed here -- see
    openspec/changes/typed-machine-loader-yaml/design.md Decision 2."""
    return isinstance(data, dict)


class DeviceEntryYaml(TypedDict, total=False):
    id: str
    type: str
    implemented: bool


def _is_device_entry_shape(data: object) -> TypeGuard[DeviceEntryYaml]:
    """True if `data` is a dict (device YAML top-level shape). `id`/`type`
    presence are still checked by `load_device_registry` itself, not
    guaranteed here -- see design.md Decision 2."""
    return isinstance(data, dict)


class ContentItemYaml(TypedDict, total=False):
    rom: RomEntryYaml


def _is_content_item_shape(data: object) -> TypeGuard[ContentItemYaml]:
    """True if `data` is a dict (slot content-list item shape)."""
    return isinstance(data, dict)


class Slot0Yaml(TypedDict, total=False):
    content: list[ContentItemYaml]


def _is_slot0_shape(data: object) -> TypeGuard[Slot0Yaml]:
    """True if `data` is a dict (slot 0 block shape)."""
    return isinstance(data, dict)


class Slot3Msx1Yaml(TypedDict, total=False):
    size_kb: int


class FdcYaml(TypedDict, total=False):
    rom: RomEntryYaml
    controller: str
    connection_style: str
    drives: int


def _is_fdc_shape(data: object) -> TypeGuard[FdcYaml]:
    """True if `data` is a dict (fdc: block shape). `rom`'s presence is
    still checked by `_parse_fdc` itself, not guaranteed here."""
    return isinstance(data, dict)


class SubSlotYaml(TypedDict, total=False):
    content: list[ContentItemYaml]
    mapper: str
    type: str
    size_kb: int
    fdc: FdcYaml


def _is_sub_slot_shape(data: object) -> TypeGuard[SubSlotYaml]:
    """True if `data` is a dict (MSX2 slot 3 sub-slot block shape)."""
    return isinstance(data, dict)


class Slot3Msx2Yaml(TypedDict, total=False):
    expanded: bool
    mapper: str
    # Raw YAML keys (int-or-str) pre-_int_keys() coercion -- narrowed to
    # dict[int, SubSlotYaml] per-item after coercion (design.md Decision 4).
    secondary: dict[Any, Any]


class BuiltinDeviceEntryYaml(TypedDict, total=False):
    ref: str
    # Arbitrary per-device override content (e.g. {"keyboard_type": "jp"}) --
    # not a good TypedDict candidate, same reasoning as _DeviceDef.raw
    # (design.md Decision 5).
    overrides: dict[str, Any]


def _is_builtin_device_entry_shape(data: object) -> TypeGuard[BuiltinDeviceEntryYaml]:
    """True if `data` is a dict (builtin_devices list-item shape)."""
    return isinstance(data, dict)


class CpuBlockYaml(TypedDict, total=False):
    m1_wait_states: int


class MachineEntryYaml(TypedDict, total=False):
    schema_version: int
    id: str
    generation: str
    name: str
    rom_base: str
    video_standard: str
    cpu: CpuBlockYaml
    # Raw YAML keys (int-or-str) pre-_int_keys() coercion under
    # slots["primary"] -- narrowed per-item to Slot0Yaml/Slot3Msx1Yaml/
    # Slot3Msx2Yaml after coercion (design.md Decision 4).
    slots: dict[str, Any]
    builtin_devices: list[BuiltinDeviceEntryYaml]


def _is_machine_entry_shape(data: object) -> TypeGuard[MachineEntryYaml]:
    """True if `data` is a dict (machine YAML top-level shape). Individual
    required fields (schema_version/id/generation) are still checked by
    `load_machine_spec` itself, not guaranteed here (design.md Decision 2)."""
    return isinstance(data, dict)


class SramYaml(TypedDict, total=False):
    save_file: str


def _is_sram_shape(data: object) -> TypeGuard[SramYaml]:
    """True if `data` is a dict (sram: block shape)."""
    return isinstance(data, dict)


class FmPacOverlayYaml(TypedDict, total=False):
    schema_version: int
    slot: int
    rom_base: str
    rom: RomEntryYaml
    sram: SramYaml


def _is_fmpac_overlay_shape(data: object) -> TypeGuard[FmPacOverlayYaml]:
    """True if `data` is a dict (fmpac.yaml top-level shape). Individual
    required fields are still checked by `load_fmpac_overlay` itself, not
    guaranteed here (design.md Decision 2)."""
    return isinstance(data, dict)


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
            raw = yaml.safe_load(fh)
        if not _is_device_entry_shape(raw):
            raise MachineLoadError(f"{path}: expected a YAML mapping at top level")
        dev_id = raw.get("id")
        if not dev_id:
            raise MachineLoadError(f"{path}: missing required field 'id'")
        if dev_id != path.stem:
            raise MachineLoadError(
                f"{path}: 'id' field {dev_id!r} does not match filename stem {path.stem!r}"
            )
        dev_type = raw.get("type")
        if not dev_type:
            raise MachineLoadError(f"{path}: missing required field 'type'")
        implemented = bool(raw.get("implemented", True))
        # _DeviceDef.raw stays dict[str, Any] (arbitrary passthrough fields
        # beyond DeviceEntryYaml's known keys, e.g. keyboard_type/io_ports --
        # design.md Decision 5); TypedDict is a plain dict at runtime, so this
        # cast is a widening, not a type mismatch.
        registry[str(dev_id)] = _DeviceDef(implemented=implemented, raw=cast(dict[str, Any], raw))
    return registry


# ---------------------------------------------------------------------------
# Slot parsers
# ---------------------------------------------------------------------------

def _parse_rom_entry(entry: RomEntryYaml, context: str) -> _RomEntry:
    """Parse a `rom: {file, size_kb, pages, sha1}` block into a _RomEntry.

    This is the one shared slot-resident-device ROM schema in the loader: it
    parses every `rom:` block in the codebase, with the same shape and the
    same validation, regardless of where that ROM lives — slot 0 main/logo
    ROM and the MSX2 slot 3 sub ROM (_parse_slot0, _parse_slot3_msx2), the
    FDC's DISK ROM (_parse_fdc), and the FM-PAC overlay's ROM
    (load_fmpac_overlay). A new ROM-bearing device added to the loader should
    reuse this rather than hand-rolling another parser.
    """
    file = entry.get("file")
    if not file:
        raise MachineLoadError(f"{context}: ROM entry missing required field 'file'")
    size_kb = entry.get("size_kb", 0)
    pages_raw = entry.get("pages", [])
    pages: list[int] = [int(p) for p in pages_raw]
    sha1 = entry.get("sha1") or None
    return _RomEntry(file=str(file), size_kb=size_kb, pages=pages, sha1=sha1)


def _coerce_int_key(key: Any) -> int | None:
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def _int_keys(slot_map: dict[Any, Any]) -> dict[int, Any]:
    """Coerce slot-map keys to int so string/JSON keys (e.g. "0") resolve via .get(0)."""
    out: dict[int, Any] = {}
    for key, value in slot_map.items():
        int_key = _coerce_int_key(key)
        if int_key is not None:
            out[int_key] = value
    return out


def _parse_slot0(
    slot0: Slot0Yaml, machine_id: str
) -> tuple[_RomEntry | None, _RomEntry | None]:
    """Extract main ROM (pages [0,1]) and optional logo ROM (page [2]) from slot 0."""
    main_rom: _RomEntry | None = None
    logo_rom: _RomEntry | None = None
    context = f"machine '{machine_id}' slot 0"
    for item in slot0.get("content", []):
        if not _is_content_item_shape(item):
            continue
        rom_data = item.get("rom")
        if not _is_rom_entry_shape(rom_data):
            continue
        entry = _parse_rom_entry(rom_data, context)
        if 0 in entry.pages or 1 in entry.pages:
            main_rom = entry
        elif 2 in entry.pages:
            logo_rom = entry
    return main_rom, logo_rom


def _parse_slot3_msx1(slot3: Slot3Msx1Yaml) -> int:
    """Return flat RAM size in KB for an MSX1 slot 3 declaration."""
    return int(slot3.get("size_kb", 32))


def _parse_fdc(sub_val: SubSlotYaml, machine_id: str, sub_idx: int) -> _FdcDef | None:
    """Resolve an optional `fdc:` block in the given MSX2 slot 3 sub-slot.

    Raises:
        MachineLoadError: On a missing DISK ROM entry, or an unsupported
            controller type or connection style.
    """
    fdc_raw = sub_val.get("fdc")
    if not _is_fdc_shape(fdc_raw):
        return None
    context = f"machine '{machine_id}' slot 3 sub-slot {sub_idx} fdc"
    rom_data = fdc_raw.get("rom")
    if not _is_rom_entry_shape(rom_data):
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
    if (controller, style) not in _SUPPORTED_FDC_PAIRS:
        raise MachineLoadError(
            f"{context}: controller {controller!r} does not support "
            f"connection_style {style!r} "
            f"(supported pairs: {sorted(_SUPPORTED_FDC_PAIRS)})"
        )
    drives = int(fdc_raw.get("drives", 1))
    if drives <= 0:
        raise MachineLoadError(f"{context}: drives must be positive, got {drives}")
    return _FdcDef(
        disk_rom_entry=disk_rom_entry,
        controller=controller,
        connection_style=style,
        drives=drives,
    )


def _check_subslot_index(context: str, label: str, value: int) -> None:
    if not (0 <= value <= _MAX_SUB_SLOT_INDEX):
        raise MachineLoadError(
            f"{context}: {label} sub-slot {value} out of range "
            f"(must be 0-{_MAX_SUB_SLOT_INDEX})"
        )


def _parse_slot3_msx2(slot3: Slot3Msx2Yaml, machine_id: str) -> _Slot3Msx2:
    """Resolve an MSX2 slot 3 declaration into a _Slot3Msx2.

    A sub-slot declaring `type: ram` without `mapper: standard` is a flat
    (non-mapper) RAM (e.g. HB-F1XD's 64 KB in sub-slot 3); a `mapper: standard`
    sub-slot sets has_ram_mapper as before. The SUB ROM and an `fdc:` block are
    each resolved from whichever sub-slot declares them -- scanned in
    ascending sub-slot order, first match wins -- independently of each other,
    so a machine MAY place them in the same sub-slot (every machine predating
    this generalisation does, and still resolves to sub-slot 0 for both,
    unchanged) or in different sub-slots (e.g. FS-A1F's real hardware layout).

    Raises:
        MachineLoadError: If a declared sub-slot role's index falls outside
            0-_MAX_SUB_SLOT_INDEX, or if a mapper: standard sub-slot and a
            flat (non-mapper) RAM sub-slot are both declared -- these are
            mutually exclusive MSX2 slot 3 RAM strategies (see
            allium/slots.allium's SlotThreeStrategyIsExclusive invariant and
            Memory.__post_init__'s corresponding construction-time check).
    """
    result = _Slot3Msx2()
    if slot3.get("expanded"):
        secondary: dict[int, Any] = _int_keys(slot3.get("secondary", {}))
        for sub_idx in sorted(secondary):
            sub_val = secondary[sub_idx]
            if not _is_sub_slot_shape(sub_val):
                continue
            if result.sub_rom is None:
                for item in sub_val.get("content", []):
                    if not _is_content_item_shape(item):
                        continue
                    rom_data = item.get("rom")
                    if _is_rom_entry_shape(rom_data):
                        result.sub_rom = _parse_rom_entry(
                            rom_data, f"machine '{machine_id}' slot 3 sub-slot {sub_idx}"
                        )
                        result.sub_rom_subslot = sub_idx
                        break
            if result.fdc is None:
                fdc = _parse_fdc(sub_val, machine_id, sub_idx)
                if fdc is not None:
                    result.fdc = fdc
                    result.fdc_subslot = sub_idx
            if sub_val.get("mapper") == "standard":
                result.has_ram_mapper = True
            elif sub_val.get("type") == "ram":
                result.flat_ram_subslot = sub_idx
                result.flat_ram_size_kb = int(sub_val.get("size_kb", 64))
    elif slot3.get("mapper") == "standard":
        result.has_ram_mapper = True

    context = f"machine '{machine_id}' slot 3"
    if result.sub_rom is not None:
        _check_subslot_index(context, "SUB ROM", result.sub_rom_subslot)
    if result.fdc is not None:
        _check_subslot_index(context, "fdc", result.fdc_subslot)
    if result.flat_ram_subslot is not None:
        _check_subslot_index(context, "flat RAM", result.flat_ram_subslot)
    if result.has_ram_mapper and result.flat_ram_subslot is not None:
        raise MachineLoadError(
            f"{context}: RAM mapper and flat RAM sub-slot {result.flat_ram_subslot} "
            "declared simultaneously -- these are mutually exclusive MSX2 slot 3 "
            "RAM strategies (Memory cannot host both at once)"
        )
    if result.flat_ram_subslot is not None and result.sub_rom is not None \
            and result.flat_ram_subslot == result.sub_rom_subslot:
        raise MachineLoadError(
            f"{context}: flat RAM and SUB ROM both declared in sub-slot "
            f"{result.flat_ram_subslot} -- Memory's write path has no SUB-ROM "
            "guard, so a write to that page would silently land in the flat RAM"
        )
    if result.flat_ram_subslot is not None and result.fdc is not None \
            and result.flat_ram_subslot == result.fdc_subslot:
        raise MachineLoadError(
            f"{context}: flat RAM and fdc both declared in sub-slot "
            f"{result.flat_ram_subslot} -- Memory's write path has no FDC "
            "guard, so a write to that page would silently land in the flat RAM"
        )
    return result


# ---------------------------------------------------------------------------
# Pass 2: machine spec
# ---------------------------------------------------------------------------

class _BuiltinDevices(NamedTuple):
    has_v9938: bool
    has_rtc: bool
    keyboard_type: str
    io_ports: dict[str, tuple[int, int]]


def _parse_builtin_devices(
    raw: MachineEntryYaml,
    device_registry: dict[str, _DeviceDef],
    machine_path: Path,
) -> _BuiltinDevices:
    """Resolve the `builtin_devices` list against the device registry."""
    has_v9938 = False
    has_rtc = False
    keyboard_type = "int"
    device_io_ports: dict[str, tuple[int, int]] = {}

    for entry in raw.get("builtin_devices", []):
        if not _is_builtin_device_entry_shape(entry):
            continue
        ref = entry.get("ref")
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

    return _BuiltinDevices(has_v9938, has_rtc, keyboard_type, device_io_ports)


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
        raw = yaml.safe_load(fh)
    if not _is_machine_entry_shape(raw):
        raise MachineLoadError(f"{machine_path}: expected a YAML mapping at top level")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise MachineLoadError(
            f"{machine_path}: unsupported schema_version {schema_version!r} (expected 1)"
        )

    m_id = raw.get("id")
    if m_id != machine_path.stem:
        raise MachineLoadError(
            f"{machine_path}: 'id' field {m_id!r} does not match filename stem "
            f"{machine_path.stem!r}"
        )

    generation = raw.get("generation")
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

    # CPU timing: extra T-states per M1 (opcode fetch). Absent → 0 (pure Z80).
    cpu_block: CpuBlockYaml = raw.get("cpu", {}) or {}
    m1_wait_states = int(cpu_block.get("m1_wait_states", 0))

    # --- Slot parsing ---
    slots = raw.get("slots", {})
    primary: dict[int, Any] = _int_keys(slots.get("primary", {}))

    slot0_raw = primary.get(0, {})
    slot0: Slot0Yaml = slot0_raw if _is_slot0_shape(slot0_raw) else {}
    main_rom_entry, logo_rom_entry = _parse_slot0(slot0, machine_id)
    if main_rom_entry is None:
        raise MachineLoadError(
            f"{machine_path}: slot 0 has no main ROM (content with pages [0] or [1])"
        )

    sub_rom_entry: _RomEntry | None = None
    sub_rom_subslot = 0
    has_ram_mapper = False
    ram_size_kb = 32
    flat_ram_subslot: int | None = None
    flat_ram_size_kb = 64
    fdc: _FdcDef | None = None
    fdc_subslot = 0

    slot3: dict[str, Any] = primary.get(3, {})
    if not isinstance(slot3, dict):
        slot3 = {}

    if generation == "msx2":
        # Same reasoning as the msx1 branch's cast below: slot3 is already
        # confirmed dict-shaped, shared with _parse_slot3_msx1's dict[str, Any].
        s3 = _parse_slot3_msx2(cast(Slot3Msx2Yaml, slot3), machine_id)
        sub_rom_entry = s3.sub_rom
        sub_rom_subslot = s3.sub_rom_subslot
        has_ram_mapper = s3.has_ram_mapper
        flat_ram_subslot = s3.flat_ram_subslot
        flat_ram_size_kb = s3.flat_ram_size_kb
        fdc = s3.fdc
        fdc_subslot = s3.fdc_subslot
    else:
        # slot3 is already confirmed dict-shaped above; TypedDict is a plain
        # dict at runtime, so this is a widening/narrowing view of the same
        # object, not an unchecked cast (design.md Decision 2).
        ram_size_kb = _parse_slot3_msx1(cast(Slot3Msx1Yaml, slot3))

    # --- Builtin device resolution ---
    has_v9938, has_rtc, keyboard_type, device_io_ports = _parse_builtin_devices(
        raw, device_registry, machine_path
    )

    return MachineSpec(
        name=name,
        machine_id=machine_id,
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
        m1_wait_states=m1_wait_states,
        flat_ram_subslot=flat_ram_subslot,
        flat_ram_size_kb=flat_ram_size_kb,
        sub_rom_subslot=sub_rom_subslot,
        fdc=fdc,
        fdc_subslot=fdc_subslot,
    )


# ---------------------------------------------------------------------------
# FM-PAC overlay (config/machines/fmpac.yaml)
# ---------------------------------------------------------------------------

def load_fmpac_overlay(config_dir: Path, project_root: Path) -> _FmPacOverlay:
    """Load and validate the FM-PAC overlay fragment.

    Args:
        config_dir: Path to the config/ directory.
        project_root: Project root used to resolve rom_base and the SRAM save
            path relative paths.

    Returns:
        A resolved _FmPacOverlay, ready for build_machine(fmpac_overlay=...).

    Raises:
        MachineLoadError: On missing file, bad schema_version, or a missing
            'rom' entry.
    """
    path = config_dir / "machines" / "fmpac.yaml"
    if not path.exists():
        raise MachineLoadError(f"FM-PAC overlay not found: {path}")

    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not _is_fmpac_overlay_shape(raw):
        raise MachineLoadError(f"{path}: expected a YAML mapping at top level")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise MachineLoadError(
            f"{path}: unsupported schema_version {schema_version!r} (expected 1)"
        )

    slot = int(raw.get("slot", 2))
    if slot != 2:
        raise MachineLoadError(
            f"{path}: unsupported FM-PAC slot {slot!r} (only slot 2 is supported)"
        )
    rom_base: str = str(raw.get("rom_base", "roms/fmpac"))
    rom_base_dir = project_root / rom_base

    rom_data = raw.get("rom")
    if not _is_rom_entry_shape(rom_data):
        raise MachineLoadError(f"{path}: missing required 'rom' entry")
    rom_entry = _parse_rom_entry(rom_data, f"FM-PAC overlay '{path}'")

    sram_data = raw.get("sram")
    save_file = str(sram_data.get("save_file", "saves/sram/fmpac.sram")) \
        if _is_sram_shape(sram_data) else "saves/sram/fmpac.sram"

    return _FmPacOverlay(
        rom_base_dir=rom_base_dir,
        rom_entry=rom_entry,
        slot=slot,
        sram_save_path=Path(save_file),
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


def _register_common_io(
    io: IOBus,
    spec: MachineSpec,
    vdp: VdpDevice,
    psg: PSG,
    ppi: PPI,
    vdp_default_ports: tuple[int, int],
) -> None:
    """Register the VDP/PSG/PPI port ranges every machine generation shares."""
    vdp_dev_id = "vdp_v9938" if isinstance(vdp, V9938) else "vdp_tms9918a"
    vdp_s, vdp_e = _io_range(spec, vdp_dev_id, vdp_default_ports)
    psg_s, psg_e = _io_range(spec, "psg_ay8910", _DEFAULT_IO_PORTS["psg_ay8910"])
    ppi_s, ppi_e = _io_range(spec, "ppi8255", _DEFAULT_IO_PORTS["ppi8255"])
    io.register_read(vdp_s, vdp_e, vdp.read_port)
    io.register_write(vdp_s, vdp_e, vdp.write_port)
    io.register_read(psg_s, psg_e, psg.read_port)
    io.register_write(psg_s, psg_e, psg.write_port)
    io.register_read(ppi_s, ppi_e, ppi.read_port)
    io.register_write(ppi_s, ppi_e, ppi.write_port)


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


def _load_sram_or_warn(path: Path, expected_size: int, label: str = "") -> bytearray | None:
    """Load a save file's SRAM bytes, or warn and start fresh if the size mismatches."""
    if not path.exists():
        return None
    raw = path.read_bytes()
    if len(raw) == expected_size:
        return bytearray(raw)
    print(
        f"warning: {label}SRAM file {path} has wrong size "
        f"({len(raw)} != {expected_size}), starting fresh",
        file=sys.stderr,
    )
    return None


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
    fdd1: Path | None = None,
    fdd2: Path | None = None,
    fmpac_overlay: _FmPacOverlay | None = None,
    joy_map: Mapping[int, tuple[int, int]] | None = None,
    scc_plus: bool = False,
) -> "Machine":
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
        joy_map: Optional Joy1 keyboard key map override for InputState
            (see AppConfig.keyboard_joy_map). Defaults to the built-in JOY_MAP.
        scc_plus: When True, slot 1 is unconditionally an SCC-I cartridge
            (SCCICart) instead of the normal cartridge/mapper resolution --
            `cartridge`/`mapper` are ignored. The caller (CLI layer) is
            responsible for ensuring `cartridge is None` in this mode.

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
    sram_save_path: Path | None = None
    scc: SCC | None
    mapper_instance: Mapper
    if scc_plus:
        # SCC-I cartridge unconditionally occupies slot 1; normal cartridge/
        # mapper resolution (and the SRAM path, which SCC-I has none of) is
        # skipped entirely. The caller ensures `cartridge is None` here.
        # is_052539=True: the SCC-I cartridge carries a genuine Konami-052539
        # chip, not a 051649 (see SCC.is_052539's docstring).
        scc = SCC(is_052539=True)
        mapper_instance = SCCICart(scc=scc)
    else:
        resolved, cart_sha1 = _resolve_mapper_type(mapper, cartridge)
        scc = SCC() if resolved == "KonamiSCC" else None

        # SRAM: load existing save file if mapper supports it
        sram_data: bytearray | None = None
        if resolved in _SRAM_SIZES and cartridge is not None:
            # Reuse the sha1 computed in _resolve_mapper_type (cartridge is not
            # None here, so cart_sha1 is set).
            assert cart_sha1 is not None
            sram_save_path = Path("saves") / "sram" / f"{cart_sha1}.sram"
            sram_data = _load_sram_or_warn(sram_save_path, _SRAM_SIZES[resolved])

        mapper_instance = _make_mapper(resolved, cartridge, scc=scc, sram=sram_data)

    resolved2, _ = _resolve_mapper_type(mapper2, cartridge2)
    if resolved2 == "KonamiSCC":
        print(
            "warning: KonamiSCC is not supported for slot 2, using Konami mapper",
            file=sys.stderr,
        )
        resolved2 = "Konami"
    mapper2_instance = _make_mapper(resolved2, cartridge2)
    dac: MajutsushiMapper | None = (
        mapper_instance if isinstance(mapper_instance, MajutsushiMapper) else None
    )

    # FM-PAC overlay: occupies primary slot 2 (load_fmpac_overlay validates this),
    # replacing whatever slot-2 cartridge mapper was resolved above.
    fmpac_device: FmPac | None = None
    if fmpac_overlay is not None:
        fmpac_rom = _load_rom(
            fmpac_overlay.rom_base_dir, fmpac_overlay.rom_entry.file, required=True
        )
        assert fmpac_rom is not None
        fmpac_sram = _load_sram_or_warn(
            fmpac_overlay.sram_save_path, FMPAC_SRAM_SIZE, label="FM-PAC "
        )
        fmpac_device = FmPac(
            rom=fmpac_rom,
            opll=Opll(),
            sram=fmpac_sram if fmpac_sram is not None else bytearray(FMPAC_SRAM_SIZE),
        )
        mapper2_instance = fmpac_device

    input_state = InputState(keyboard_type=spec.keyboard_type)
    if joy_map is not None:
        input_state.joy_map = joy_map
    psg = PSG(_input=input_state)
    io = IOBus(_logger=logger)
    if fmpac_device is not None:
        io.register_read(0x7C, 0x7D, fmpac_device.read_port)
        io.register_write(0x7C, 0x7D, fmpac_device.write_port)

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
            fdd_images=[fdd1, fdd2],
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
    # PSG software PCM: timestamp register writes so generate_samples can place
    # them at their sub-frame sample positions (mirrors the DAC wiring).
    machine.psg._machine = machine
    machine.sram_save_path = sram_save_path
    machine.fmpac = fmpac_device
    machine.fmpac_sram_save_path = (
        fmpac_overlay.sram_save_path if fmpac_overlay is not None else None
    )
    return machine


def _build_msx1(
    *,
    spec: MachineSpec,
    main_bytes: bytes,
    logo_bytes: bytes | None,
    mapper_instance: Mapper,
    mapper2_instance: Mapper,
    dac: MajutsushiMapper | None,
    scc: SCC | None,
    input_state: InputState,
    psg: PSG,
    io: IOBus,
    logger: DebugLogger | None,
    machine_cls: "type[Machine]",
) -> "Machine":
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
    _register_common_io(io, spec, vdp, psg, ppi, _DEFAULT_IO_PORTS["vdp_tms9918a"])
    cpu = Z80(read_byte=memory.read, write_byte=memory.write,
              m1_wait_states=spec.m1_wait_states, _logger=logger)
    return machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        cycles_per_frame=spec.cycles_per_frame,
        lines_per_frame=spec.lines_per_frame,
    )


def _build_fdc(
    spec: MachineSpec,
    fdd_images: list[Path | None],
    disk_rom_override: bytes | None,
) -> "FloppyDisk":
    """Construct the FloppyDisk device from spec.fdc, mounting each image in
    fdd_images into the drive with the matching index (fdd_images[0] -> drive A)."""
    from msx.fdc.disk_drive import DiskDrive
    from msx.fdc.disk_image import DskDiskImage
    from msx.fdc.interface import SonyPhilipsInterface, TC8566AFInterface
    from msx.fdc.tc8566af import TC8566AF
    from msx.fdc.wd2793 import WD2793

    assert spec.fdc is not None
    if disk_rom_override is not None:
        disk_rom: bytes | None = disk_rom_override
    else:
        disk_rom = _load_rom(spec.rom_base_dir, spec.fdc.disk_rom_entry.file, required=True)
    drives = [DiskDrive() for _ in range(spec.fdc.drives)]
    # controller/connection_style were validated by the loader (_SUPPORTED_FDC_*).
    device: SonyPhilipsInterface | TC8566AFInterface
    if spec.fdc.controller == "tc8566af":
        device = TC8566AFInterface(TC8566AF(drives=drives), drives, disk_rom=disk_rom)
    else:
        device = SonyPhilipsInterface(WD2793(), drives, disk_rom=disk_rom)
    for idx, image_path in enumerate(fdd_images):
        if image_path is None:
            continue
        if idx >= len(drives):
            print(
                f"warning: --fdd{idx + 1} given but machine has only "
                f"{len(drives)} drive(s); ignoring",
                file=sys.stderr,
            )
            continue
        device.mount(DskDiskImage(image_path), drive=idx)
    return device


def _build_msx2(
    *,
    spec: MachineSpec,
    main_bytes: bytes,
    logo_bytes: bytes | None,
    extrom_override: bytes | None,
    mapper_instance: Mapper,
    mapper2_instance: Mapper,
    dac: MajutsushiMapper | None,
    scc: SCC | None,
    input_state: InputState,
    psg: PSG,
    io: IOBus,
    logger: DebugLogger | None,
    tracer: Tracer | None,
    machine_cls: "type[Machine]",
    disk_rom_override: bytes | None = None,
    fdd_images: list[Path | None] | None = None,
) -> "Machine":
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
        _build_fdc(spec, fdd_images or [], disk_rom_override)
        if spec.fdc is not None else None
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
        sub_rom_subslot=spec.sub_rom_subslot,
        fdc=fdc_device,
        fdc_subslot=spec.fdc_subslot,
        rom_name=spec.main_rom_entry.file,
        sub0_rom_name=spec.sub_rom_entry.file if spec.sub_rom_entry is not None else "",
    )
    vdp: V9938 | VDP = V9938() if spec.has_v9938 else VDP(_logger=logger)
    rtc: RTC | None = None
    rtc_sram_save_path: Path | None = None
    if spec.has_rtc:
        rtc_sram_save_path = Path(f"saves/sram/rtc_{spec.machine_id}.sram")
        rtc_sram = _load_sram_or_warn(rtc_sram_save_path, RTC_SRAM_SIZE, label="RTC ")
        rtc = RTC(sram=rtc_sram if rtc_sram is not None else bytearray(RTC_SRAM_SIZE))
    ppi = PPI(memory=memory, _input=input_state)

    # V9938 adds the palette (0x9A) and indirect-register (0x9B) ports; the
    # TMS9918A stops at 0x99.
    vdp_default_ports = (0x98, 0x9B) if spec.has_v9938 else _DEFAULT_IO_PORTS["vdp_tms9918a"]
    _register_common_io(io, spec, vdp, psg, ppi, vdp_default_ports)
    if rtc is not None:
        rtc_s, rtc_e = _io_range(spec, "rtc_rp5c01", _DEFAULT_IO_PORTS["rtc_rp5c01"])
        io.register_read(rtc_s, rtc_e, rtc.read_port)
        io.register_write(rtc_s, rtc_e, rtc.write_port)
    if ram_mapper is not None:
        ram_s, ram_e = _io_range(spec, "memory_mapper_standard",
                                 _DEFAULT_IO_PORTS["memory_mapper_standard"])
        io.register_read(ram_s, ram_e, ram_mapper.read_port)
        io.register_write(ram_s, ram_e, ram_mapper.write_port)

    cpu = Z80(read_byte=memory.read, write_byte=memory.write,
              m1_wait_states=spec.m1_wait_states, _logger=logger)
    machine = machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        cycles_per_frame=spec.cycles_per_frame,
        lines_per_frame=spec.lines_per_frame,
        fdc=fdc_device,
        rtc=rtc,
        rtc_sram_save_path=rtc_sram_save_path,
    )
    if tracer is not None and isinstance(vdp, V9938):
        vdp.tracer = tracer
        vdp._get_pc = lambda: machine.cpu.instruction_pc
        vdp._get_cycle = lambda: machine.cycle_count
        # The tracer reads the VDP frame count directly (V9938.write_port passes
        # self._frame_count), so no _get_frame getter is needed.
    return machine
