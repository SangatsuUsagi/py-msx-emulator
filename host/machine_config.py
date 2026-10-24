"""YAML-based machine configuration loader (host side).

Two-pass loading: device registry first, then machine spec resolution.
Raises MachineLoadError with specific file and field names on any validation failure.

PORT-LIBRARY-NOTE: this file's `yaml.safe_load()` -> `dict[str, Any]` ->
  hand-rolled isinstance/.get() validation into typed dataclasses (MachineSpec
  et al.) used to be the largest source of `Any` in this codebase (same
  pattern, at smaller scale, still applies to host/app_config.py and
  host/romdb.py). RESOLVED for this file by
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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict, TypeGuard, cast

if TYPE_CHECKING:
    from msx.machine import Machine

import yaml

import host.romdb as romdb
from host.media import read_disk_medium
from msx.boundary import (
    FMPAC_SRAM_ID,
    RTC_CMOS_ID,
    DiskMedium,
    MachineConfig,
    RtcTime,
    drive_media_id,
    slot_sram_id,
    subslot_sram_id,
)
from msx.diagnostics.logger import DebugLogger
from msx.fmpac import SRAM_SIZE as FMPAC_SRAM_SIZE
from msx.machine_loader import _SRAM_SIZES, build_machine
from msx.machine_spec import (
    MachineLoadError,
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    _ExtensionOverlay,
    _FdcDef,
    _RomEntry,
    _Slot0RamEntry,
)
from msx.mapper import HALNOTE_SRAM_SIZE
from msx.ppi import PpiControlRead
from msx.rtc import SRAM_SIZE as RTC_SRAM_SIZE
from msx.vdp._geometry import VideoStandard
from msx.vdp.tracer import Tracer

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
# field (openspec/specs/memory-slot-bus/spec.md's "Secondary slot register
# field" Requirement: bits 7:6/5:4/3:2/1:0 select the sub-slot for pages
# 3/2/1/0 respectively; the "Slot 3 sub-slot dispatch" Requirement extracts
# it as `(sub_slot_reg >> (page * 2)) & 0x03`, matching msx/memory.py's own
# `& 0x03` masks). A 2-bit field can only ever hold 0-3 -- this is not a
# bound the loader is free to pick independently of that register layout.
_MAX_SUB_SLOT_INDEX = 3


def _resolve_mapper_type(mapper: str, cartridge: bytes | None) -> str:
    """Resolve "auto" through the ROM database; any other name passes through."""
    if mapper != "auto":
        return mapper
    if cartridge is None:
        return "Mirrored"
    found = romdb.lookup(cartridge)
    if found is None:
        print("warning: cartridge not found in ROM database, using Mirrored mapper",
              file=sys.stderr)
        return "Mirrored"
    if found not in _SUPPORTED_MAPPERS:
        print(f"warning: unsupported mapper type {found!r} from ROM database, "
              "using Mirrored mapper", file=sys.stderr)
        return "Mirrored"
    return found


@dataclass
class _DeviceDef:
    implemented: bool
    raw: dict[str, Any]


@dataclass
class _Slot3Msx2:
    """Resolved MSX2 slot 3 layout (named to avoid a positional tuple)."""
    sub_rom: _RomEntry | None = None
    sub_rom_subslot: int = 0
    has_ram_mapper: bool = False
    ram_mapper_size_kb: int = 128
    flat_ram_subslot: int | None = None
    flat_ram_size_kb: int = 64
    fdc: _FdcDef | None = None
    fdc_subslot: int = 0



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


class RamEntryYaml(TypedDict, total=False):
    size_kb: int
    pages: list[int]


def _is_ram_entry_shape(data: object) -> TypeGuard[RamEntryYaml]:
    """True if `data` is a dict (ram: block shape, slot 0 content-list item only)."""
    return isinstance(data, dict)


class ContentItemYaml(TypedDict, total=False):
    rom: RomEntryYaml
    ram: RamEntryYaml


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
    size_kb: int
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


class SramYaml(TypedDict, total=False):
    save_file: str


def _is_sram_shape(data: object) -> TypeGuard[SramYaml]:
    """True if `data` is a dict (sram: block shape)."""
    return isinstance(data, dict)


class ExpandedDeviceYaml(TypedDict, total=False):
    """One `device`/`rom`/`sram` declaration -- the shape shared by an
    expanded extension overlay's per-sub-slot entry, its own `io_device`
    entry, and a machine YAML's own top-level `io_device` key (all three
    resolved through `_parse_expanded_device`, just against different
    known-device sets). Deliberately its own type, not folded into
    ExtensionOverlayYaml below: that type is the raw overlay *file*
    (schema_version/id/slot/rom_base/shape plus either this shape at its
    own top level, for a flat overlay, or `subslots`/`io_device` entries
    of this shape, for an expanded one) -- a sub-slot or io_device entry
    never legally carries schema_version/id/slot/shape itself."""
    device: str
    rom: RomEntryYaml
    sram: SramYaml
    size_kb: int


def _is_expanded_device_shape(data: object) -> TypeGuard[ExpandedDeviceYaml]:
    """True if `data` is a dict (a sub-slot or io_device entry's shape).
    `device`'s presence is still checked by `_parse_expanded_device`
    itself, not guaranteed here (design.md Decision 2)."""
    return isinstance(data, dict)


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
    # Optional slot-independent global I/O device built into this machine
    # (e.g. a Kanji font ROM) -- same shape as an extension overlay's own
    # io_device entry, ExpandedDeviceYaml (defined above), reused rather
    # than duplicated.
    io_device: ExpandedDeviceYaml


def _is_machine_entry_shape(data: object) -> TypeGuard[MachineEntryYaml]:
    """True if `data` is a dict (machine YAML top-level shape). Individual
    required fields (schema_version/id/generation) are still checked by
    `load_machine_spec` itself, not guaranteed here (design.md Decision 2)."""
    return isinstance(data, dict)


class ExtensionOverlayYaml(TypedDict, total=False):
    """The raw top-level shape of config/extensions/<id>.yaml, before
    `shape` has been read to dispatch to the flat or expanded parse path
    -- so it carries both shapes' fields as optional (`device`/`rom`/
    `sram` for the flat case; `subslots`/`io_device` for the expanded
    one), rather than being two separate discriminated types. Each
    `subslots`/`io_device` entry is the narrower ExpandedDeviceYaml, not
    this type recursively -- a sub-slot or io_device entry can never
    itself carry schema_version/id/slot/shape or nest another level of
    subslots/io_device."""
    schema_version: int
    id: str
    device: str
    slot: int
    rom_base: str
    rom: RomEntryYaml
    sram: SramYaml
    # size_kb is a size-configured sub-slot device's (e.g. ram_mapper)
    # ExpandedDeviceYaml field, not a flat-overlay top-level one -- present
    # here only so the top-level dict stays structurally assignable where
    # _parse_expanded_device's ExpandedDeviceYaml parameter is expected (see
    # this type's own docstring: it carries both shapes' fields as optional).
    size_kb: int
    # Expanded-shape-only fields (see cart-extension-overlay's "Extension
    # overlay shape: flat vs expanded" Requirement). `shape` defaults to
    # "flat" when omitted, so every existing flat overlay YAML needs no
    # change. `subslots`' raw keys are pre-_int_keys()-coercion (same
    # convention this module's docstring documents for `secondary`/
    # `primary`/`slots`).
    shape: str
    subslots: dict[Any, ExpandedDeviceYaml]
    io_device: ExpandedDeviceYaml


def _is_extension_overlay_shape(data: object) -> TypeGuard[ExtensionOverlayYaml]:
    """True if `data` is a dict (config/extensions/<id>.yaml top-level shape).
    Individual required fields are still checked by `load_extension_overlay`
    itself, not guaranteed here (design.md Decision 2)."""
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
    FDC's DISK ROM (_parse_fdc), and an extension overlay's ROM
    (load_extension_overlay). A new ROM-bearing device added to the loader
    should reuse this rather than hand-rolling another parser.
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
) -> tuple[_RomEntry | None, _RomEntry | None, _Slot0RamEntry | None]:
    """Extract main ROM (pages [0,1]), optional logo ROM (page [2]), and an
    optional flat RAM region (e.g. Sony HB-10's 16 KB page 3) from slot 0's
    content list. RAM coexists with the ROM entries -- see
    openspec/specs/memory-slot-bus/spec.md's "Slot 0 MAY host flat RAM at a
    configured page" Requirement."""
    main_rom: _RomEntry | None = None
    logo_rom: _RomEntry | None = None
    ram_entry: _Slot0RamEntry | None = None
    context = f"machine '{machine_id}' slot 0"
    for item in slot0.get("content", []):
        if not _is_content_item_shape(item):
            continue
        rom_data = item.get("rom")
        if _is_rom_entry_shape(rom_data):
            entry = _parse_rom_entry(rom_data, context)
            if 0 in entry.pages or 1 in entry.pages:
                main_rom = entry
            elif 2 in entry.pages:
                logo_rom = entry
            continue
        ram_data = item.get("ram")
        if _is_ram_entry_shape(ram_data):
            if ram_entry is not None:
                raise MachineLoadError(f"{context}: at most one 'ram' content item is supported")
            pages = [int(p) for p in ram_data.get("pages", [])]
            if len(pages) != 1:
                raise MachineLoadError(
                    f"{context}: 'ram' content item must declare exactly one page, "
                    f"got {pages!r}"
                )
            if not 0 <= pages[0] <= 3:
                raise MachineLoadError(
                    f"{context}: 'ram' content item page must be 0-3, got {pages[0]}"
                )
            size_kb = int(ram_data.get("size_kb", 0))
            if size_kb != 16:
                raise MachineLoadError(
                    f"{context}: 'ram' content item must be 16 KB (one page), got {size_kb}"
                )
            ram_entry = _Slot0RamEntry(size_kb=size_kb, page=pages[0])
    if ram_entry is not None and ram_entry.page == 2 and logo_rom is not None:
        raise MachineLoadError(f"{context}: page-2 'ram' conflicts with the page-2 ROM")
    return main_rom, logo_rom, ram_entry


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


def _check_ram_mapper_size_kb(context: str, size_kb: int) -> None:
    if size_kb <= 0 or size_kb % 16 != 0:
        raise MachineLoadError(
            f"{context}: RAM mapper size_kb must be a positive multiple of 16, got {size_kb}"
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
                result.ram_mapper_size_kb = int(sub_val.get("size_kb", 128))
                _check_ram_mapper_size_kb(
                    f"machine '{machine_id}' slot 3 sub-slot {sub_idx}",
                    result.ram_mapper_size_kb,
                )
            elif sub_val.get("type") == "ram":
                result.flat_ram_subslot = sub_idx
                result.flat_ram_size_kb = int(sub_val.get("size_kb", 64))
    elif slot3.get("mapper") == "standard":
        result.has_ram_mapper = True
        result.ram_mapper_size_kb = int(slot3.get("size_kb", 128))
        _check_ram_mapper_size_kb(f"machine '{machine_id}' slot 3", result.ram_mapper_size_kb)

    _validate_slot3(f"machine '{machine_id}' slot 3", result)
    return result


def _validate_slot3(context: str, result: _Slot3Msx2) -> None:
    """Enforce sub-slot index bounds and MSX2 slot 3's mutual-exclusivity
    rules on an already-parsed _Slot3Msx2: RAM mapper vs. flat RAM, and flat
    RAM vs. SUB ROM/fdc sharing the same sub-slot.

    Raises:
        MachineLoadError: on any violation -- see _parse_slot3_msx2's
            docstring for the rules this enforces.
    """
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


# ---------------------------------------------------------------------------
# Pass 2: machine spec
# ---------------------------------------------------------------------------

class _BuiltinDevices(NamedTuple):
    has_v9938: bool
    has_rtc: bool
    keyboard_type: str
    ppi_control_read: PpiControlRead
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
    ppi_control_read = PpiControlRead.CONTROL
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
            if "control_readback" in dev.raw or (
                isinstance(overrides, dict) and "control_readback" in overrides
            ):
                raise MachineLoadError(
                    f"{machine_path}: ppi8255 control_readback was replaced by "
                    "control_read (control, open_bus or bus_latch)"
                )
            cr = dev.raw.get("control_read", PpiControlRead.CONTROL.value)
            if isinstance(overrides, dict) and "control_read" in overrides:
                cr = overrides["control_read"]
            valid = [m.value for m in PpiControlRead]
            if cr not in valid:
                raise MachineLoadError(
                    f"{machine_path}: ppi8255 control_read must be one of {valid}, "
                    f"got {cr!r}"
                )
            ppi_control_read = PpiControlRead(cr)
        ports_raw: Any = dev.raw.get("io_ports")
        if isinstance(ports_raw, list) and len(ports_raw) >= 1:
            device_io_ports[ref_str] = (int(ports_raw[0]), int(ports_raw[-1]))

    return _BuiltinDevices(
        has_v9938, has_rtc, keyboard_type, ppi_control_read, device_io_ports
    )


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

    video_standard_name = str(raw.get("video_standard", "ntsc")).lower()
    try:
        video_standard = VideoStandard(video_standard_name)
    except ValueError:
        expected: list[str] = []
        for member in VideoStandard:
            expected.append(repr(member.value))
        raise MachineLoadError(
            f"{machine_path}: unsupported video_standard {video_standard_name!r} "
            f"(expected one of {', '.join(expected)})"
        ) from None

    # CPU timing: extra T-states per M1 (opcode fetch). Absent → 0 (pure Z80).
    cpu_block: CpuBlockYaml = raw.get("cpu", {}) or {}
    m1_wait_states = int(cpu_block.get("m1_wait_states", 0))

    # --- Slot parsing ---
    slots = raw.get("slots", {})
    primary: dict[int, Any] = _int_keys(slots.get("primary", {}))

    slot0_raw = primary.get(0, {})
    slot0: Slot0Yaml = slot0_raw if _is_slot0_shape(slot0_raw) else {}
    main_rom_entry, logo_rom_entry, slot0_ram_entry = _parse_slot0(slot0, machine_id)
    if main_rom_entry is None:
        raise MachineLoadError(
            f"{machine_path}: slot 0 has no main ROM (content with pages [0] or [1])"
        )

    sub_rom_entry: _RomEntry | None = None
    sub_rom_subslot = 0
    has_ram_mapper = False
    ram_mapper_size_kb = 128
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
        ram_mapper_size_kb = s3.ram_mapper_size_kb
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
    has_v9938, has_rtc, keyboard_type, ppi_control_read, device_io_ports = (
        _parse_builtin_devices(raw, device_registry, machine_path)
    )

    # --- Machine-level global I/O device (e.g. a built-in Kanji font ROM) ---
    io_device_raw = raw.get("io_device")
    io_device: _ExpandedSubslotDevice | None = None
    if io_device_raw is not None:
        if not _is_expanded_device_shape(io_device_raw):
            raise MachineLoadError(f"{machine_path}: io_device: expected a YAML mapping")
        io_device = _parse_expanded_device(
            io_device_raw, rom_base_dir, f"{machine_path}: io_device",
            _KNOWN_IO_DEVICES, _IO_DEVICES_REQUIRING_ROM,
        )

    return MachineSpec(
        name=name,
        machine_id=machine_id,
        generation=str(generation),
        rom_base_dir=rom_base_dir,
        main_rom_entry=main_rom_entry,
        logo_rom_entry=logo_rom_entry,
        slot0_ram_entry=slot0_ram_entry,
        sub_rom_entry=sub_rom_entry,
        has_ram_mapper=has_ram_mapper,
        ram_mapper_size_kb=ram_mapper_size_kb,
        ram_size_kb=ram_size_kb,
        has_v9938=has_v9938,
        has_rtc=has_rtc,
        keyboard_type=keyboard_type,
        ppi_control_read=ppi_control_read,
        device_io_ports=device_io_ports,
        video_standard=video_standard,
        m1_wait_states=m1_wait_states,
        flat_ram_subslot=flat_ram_subslot,
        flat_ram_size_kb=flat_ram_size_kb,
        sub_rom_subslot=sub_rom_subslot,
        fdc=fdc,
        fdc_subslot=fdc_subslot,
        io_device=io_device,
    )


# ---------------------------------------------------------------------------
# Extension overlay (config/extensions/<id>.yaml)
# ---------------------------------------------------------------------------

# Device kinds build_machine's extension_overlay dispatch (msx/machine_loader.py)
# knows how to construct. Kept here, next to the overlay loader, as the single
# place a new extension's device kind must be added.
_KNOWN_EXTENSION_DEVICES = frozenset({"fmpac", "scc_i_cart"})

# Device kinds whose overlay YAML MUST declare a 'rom' block (fmpac's device
# construction requires a ROM; scc_i_cart's RAM starts blank, no file loaded).
_EXTENSION_DEVICES_REQUIRING_ROM = frozenset({"fmpac"})

# Device kinds an expanded overlay's per-sub-slot declaration may name
# (build_machine's expanded-overlay dispatch knows how to construct each) --
# a separate namespace from _KNOWN_EXTENSION_DEVICES above (flat-overlay-only
# device names), which happens to share no members with it today.
_KNOWN_EXPANDED_SUBSLOT_DEVICES = frozenset(
    {"halnote", "flat_rom", "ram_mapper", "ascii8", "ascii16"}
)
_EXPANDED_SUBSLOT_DEVICES_REQUIRING_ROM = frozenset(
    {"halnote", "flat_rom", "ascii8", "ascii16"}
)
# ram_mapper carries no ROM -- it's configured by size instead (see
# _ExpandedSubslotDevice.size_kb).
_EXPANDED_SUBSLOT_DEVICES_REQUIRING_SIZE = frozenset({"ram_mapper"})

# Device kinds an expanded overlay's optional top-level io_device may name
# (a device with no cartridge-slot address decode of its own, e.g. the
# Kanji-ROM font device at fixed I/O ports).
_KNOWN_IO_DEVICES = frozenset({"kanji_rom"})
_IO_DEVICES_REQUIRING_ROM = frozenset({"kanji_rom"})


def _parse_expanded_device(
    data: ExpandedDeviceYaml,
    rom_base_dir: Path,
    context: str,
    known_devices: frozenset[str],
    devices_requiring_rom: frozenset[str],
    devices_requiring_size: frozenset[str] = frozenset(),
) -> _ExpandedSubslotDevice:
    """Parse one expanded-overlay device declaration (a sub-slot entry or
    the top-level io_device) -- resolved against its own known-device set
    rather than _KNOWN_EXTENSION_DEVICES."""
    device = data.get("device")
    if not device:
        raise MachineLoadError(f"{context}: missing required field 'device'")
    if device not in known_devices:
        raise MachineLoadError(
            f"{context}: unrecognized 'device' {device!r} "
            f"(expected one of {sorted(known_devices)})"
        )
    if device in devices_requiring_size:
        size_kb = data.get("size_kb")
        if not isinstance(size_kb, int) or size_kb <= 0:
            raise MachineLoadError(f"{context}: device {device!r} requires a 'size_kb' entry")
        if device == "ram_mapper":
            _check_ram_mapper_size_kb(context, size_kb)
        return _ExpandedSubslotDevice(device=str(device), size_kb=size_kb)
    rom_data = data.get("rom")
    if rom_data is None:
        if device in devices_requiring_rom:
            raise MachineLoadError(f"{context}: device {device!r} requires a 'rom' entry")
        return _ExpandedSubslotDevice(device=str(device))
    if not _is_rom_entry_shape(rom_data):
        raise MachineLoadError(f"{context}: missing required 'rom' entry")
    rom_entry = _parse_rom_entry(rom_data, context)

    sram_data = data.get("sram")
    sram_save_path = Path(str(sram_data.get("save_file"))) \
        if _is_sram_shape(sram_data) and sram_data.get("save_file") else None

    return _ExpandedSubslotDevice(
        device=str(device),
        rom_base_dir=rom_base_dir,
        rom_entry=rom_entry,
        sram_save_path=sram_save_path,
    )


def _parse_expanded_extension_overlay(
    raw: ExtensionOverlayYaml, path: Path, project_root: Path
) -> _ExpandedExtensionOverlay:
    rom_base: str = str(raw.get("rom_base", ""))
    rom_base_dir = project_root / rom_base

    subslots_raw = raw.get("subslots")
    if not isinstance(subslots_raw, dict) or not subslots_raw:
        raise MachineLoadError(f"{path}: expanded overlay missing required 'subslots' mapping")
    subslots: dict[int, _ExpandedSubslotDevice] = {}
    for index, sub_data in _int_keys(cast("dict[Any, Any]", subslots_raw)).items():
        _check_subslot_index(str(path), "extension", index)
        if not _is_expanded_device_shape(sub_data):
            raise MachineLoadError(f"{path}: subslot {index}: expected a YAML mapping")
        subslots[index] = _parse_expanded_device(
            sub_data, rom_base_dir, f"{path}: subslot {index}",
            _KNOWN_EXPANDED_SUBSLOT_DEVICES, _EXPANDED_SUBSLOT_DEVICES_REQUIRING_ROM,
            _EXPANDED_SUBSLOT_DEVICES_REQUIRING_SIZE,
        )

    ram_mapper_indices = [i for i, sub in subslots.items() if sub.device == "ram_mapper"]
    if len(ram_mapper_indices) > 1:
        raise MachineLoadError(
            f"{path}: at most one 'ram_mapper' sub-slot is allowed per overlay "
            f"(found it at sub-slots {sorted(ram_mapper_indices)}) -- two would both "
            "try to register the same standard memory-mapper I/O ports (0xFC-0xFF)"
        )

    io_device_raw = raw.get("io_device")
    io_device: _ExpandedSubslotDevice | None = None
    if io_device_raw is not None:
        if not _is_expanded_device_shape(io_device_raw):
            raise MachineLoadError(f"{path}: io_device: expected a YAML mapping")
        io_device = _parse_expanded_device(
            io_device_raw, rom_base_dir, f"{path}: io_device",
            _KNOWN_IO_DEVICES, _IO_DEVICES_REQUIRING_ROM,
        )

    return _ExpandedExtensionOverlay(subslots=subslots, io_device=io_device)


def load_extension_overlay(
    extension_id: str, config_dir: Path, project_root: Path
) -> _ExtensionOverlay | _ExpandedExtensionOverlay:
    """Load and validate an extension overlay fragment.

    Args:
        extension_id: The --extension value ("fmpac", "scc_plus", "hbi_j1",
            or "memory_512k"), also the YAML filename stem.
        config_dir: Path to the config/ directory.
        project_root: Project root used to resolve rom_base and the SRAM save
            path relative paths.

    Returns:
        A resolved _ExtensionOverlay (flat shape) or _ExpandedExtensionOverlay
        (expanded shape), ready for build_machine(extension_overlay=...).

    Raises:
        MachineLoadError: On missing file, bad schema_version, an
            unsupported shape, missing or unrecognized 'device' field(s), a
            'device' that requires a 'rom' block but has none, or (when a
            'rom' block is present) a malformed 'rom' entry. For an expanded
            overlay: also a missing/empty 'subslots' mapping or an
            out-of-range sub-slot index.
    """
    path = config_dir / "extensions" / f"{extension_id}.yaml"
    if not path.exists():
        raise MachineLoadError(f"extension overlay not found: {path}")

    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not _is_extension_overlay_shape(raw):
        raise MachineLoadError(f"{path}: expected a YAML mapping at top level")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise MachineLoadError(
            f"{path}: unsupported schema_version {schema_version!r} (expected 1)"
        )

    # `slot:` documents which primary slot this overlay YAML's author
    # targeted/tested -- both slots 1 and 2 are supported placements now
    # (openspec/changes/slot1-slot2-symmetrization); it does NOT itself
    # route the overlay to a slot -- build_machine's caller decides that via
    # which of extension_overlay1/extension_overlay2 it passes the resolved
    # overlay through as.
    slot = int(raw.get("slot", 2))
    if slot not in (1, 2):
        raise MachineLoadError(
            f"{path}: unsupported extension slot {slot!r} (must be 1 or 2)"
        )

    shape = raw.get("shape", "flat")
    if shape == "expanded":
        return _parse_expanded_extension_overlay(raw, path, project_root)
    if shape != "flat":
        raise MachineLoadError(
            f"{path}: unsupported extension shape {shape!r} (expected 'flat' or 'expanded')"
        )

    rom_base: str = str(raw.get("rom_base", ""))
    rom_base_dir = project_root / rom_base
    resolved = _parse_expanded_device(
        raw, rom_base_dir, f"extension overlay '{path}'",
        _KNOWN_EXTENSION_DEVICES, _EXTENSION_DEVICES_REQUIRING_ROM,
    )
    return _ExtensionOverlay(
        device=resolved.device,
        rom_base_dir=resolved.rom_base_dir,
        rom_entry=resolved.rom_entry,
        sram_save_path=resolved.sram_save_path,
    )


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


def _load_device_rom(device: _ExpandedSubslotDevice | _ExtensionOverlay, label: str) -> bytes:
    """Load a resolved device's required ROM file. `rom_base_dir`/
    `rom_entry` are asserted non-None: every caller here already resolved
    `device` from a role (machine io_device, an expanded sub-slot/
    io_device, or a flat extension overlay) that requires a ROM, and
    `_parse_expanded_device`/`load_extension_overlay` guarantee both fields
    are set together whenever a `rom:` block was present at load time."""
    assert device.rom_base_dir is not None, f"{label}: rom_base_dir not resolved"
    assert device.rom_entry is not None, f"{label}: rom_entry not resolved"
    rom = _load_rom(device.rom_base_dir, device.rom_entry.file, required=True)
    assert rom is not None
    return rom


# ---------------------------------------------------------------------------
# MachineConfig: every file the machine needs, read into bytes
# ---------------------------------------------------------------------------

def _cartridge_sram_path(cartridge: bytes) -> Path:
    sha1 = hashlib.sha1(cartridge, usedforsecurity=False).hexdigest()
    return Path("saves") / "sram" / f"{sha1}.sram"


def sram_paths(config: MachineConfig) -> dict[str, Path]:
    """The file each battery-backed medium of `config` is persisted in, by
    media id: an SRAM cartridge's `saves/sram/<sha1>.sram`, an extension's
    own `sram:` file, and an RTC machine's `saves/sram/rtc_<machine_id>.sram`."""
    paths: dict[str, Path] = {}
    carts = ((1, config.cartridge1, config.mapper1), (2, config.cartridge2, config.mapper2))
    for slot, cartridge, mapper in carts:
        if cartridge is not None and mapper in _SRAM_SIZES:
            paths[slot_sram_id(slot)] = _cartridge_sram_path(cartridge)
    overlays = ((1, config.extension_overlay1), (2, config.extension_overlay2))
    for slot, overlay in overlays:
        if isinstance(overlay, _ExpandedExtensionOverlay):
            for index, sub in overlay.subslots.items():
                if sub.device == "halnote" and sub.sram_save_path is not None:
                    paths[subslot_sram_id(slot, index)] = sub.sram_save_path
        elif (overlay is not None and overlay.device == "fmpac"
              and overlay.sram_save_path is not None):
            paths[FMPAC_SRAM_ID] = overlay.sram_save_path
    if config.spec.generation == "msx2" and config.spec.has_rtc:
        paths[RTC_CMOS_ID] = Path(f"saves/sram/rtc_{config.spec.machine_id}.sram")
    return paths


def _sram_size_and_label(config: MachineConfig, media_id: str) -> tuple[int, str]:
    """Expected image size and warning label of an id sram_paths produced."""
    if media_id == slot_sram_id(1):
        return _SRAM_SIZES[config.mapper1], ""
    if media_id == slot_sram_id(2):
        return _SRAM_SIZES[config.mapper2], ""
    if media_id == FMPAC_SRAM_ID:
        return FMPAC_SRAM_SIZE, "FM-PAC "
    if media_id == RTC_CMOS_ID:
        return RTC_SRAM_SIZE, "RTC "
    for slot in (1, 2):
        for sub in range(_MAX_SUB_SLOT_INDEX + 1):
            if media_id == subslot_sram_id(slot, sub):
                return HALNOTE_SRAM_SIZE, "HBI-J1 MSX-JE "
    raise ValueError(f"unknown SRAM media id {media_id!r}")


def _overlay_roms(
    slot: int, overlay: _ExtensionOverlay | _ExpandedExtensionOverlay | None
) -> dict[str, bytes]:
    roms: dict[str, bytes] = {}
    if isinstance(overlay, _ExpandedExtensionOverlay):
        for index, sub in overlay.subslots.items():
            if sub.rom_entry is not None:
                roms[f"slot{slot}.{index}"] = _load_device_rom(
                    sub, f"expanded overlay sub-slot {index}"
                )
        if overlay.io_device is not None and overlay.io_device.device == "kanji_rom":
            roms[f"slot{slot}.io_device"] = _load_device_rom(
                overlay.io_device, "expanded overlay io_device"
            )
    elif overlay is not None and overlay.rom_entry is not None:
        roms[f"slot{slot}.ext"] = _load_device_rom(overlay, f"{overlay.device} extension")
    return roms


def load_machine_config(
    spec: MachineSpec,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    bios_override: bytes | None = None,
    logo_override: bytes | None = None,
    extrom_override: bytes | None = None,
    disk_rom_override: bytes | None = None,
    fdd1: Path | None = None,
    fdd2: Path | None = None,
    extension_overlay: _ExtensionOverlay | _ExpandedExtensionOverlay | None = None,
    extension_overlay1: _ExtensionOverlay | _ExpandedExtensionOverlay | None = None,
    rtc_initial_time: RtcTime | None = None,
) -> MachineConfig:
    """Read every file `spec` and the arguments name into a MachineConfig for
    msx.machine_loader.build_machine.

    Args:
        spec: Fully-resolved MachineSpec from load_machine_spec().
        cartridge, cartridge2: Optional cartridge ROM bytes for slots 1 and 2.
        mapper, mapper2: Mapper names; "auto" is resolved here through the
            ROM database (Mirrored when unknown).
        bios_override, logo_override, extrom_override, disk_rom_override:
            Bytes used instead of the main, logo, SUB (MSX2) or DISK ROM file.
        fdd1, fdd2: Disk images for drives A and B (machines with an FDC).
        extension_overlay, extension_overlay1: Resolved --extension2 /
            --extension1 overlays (see load_extension_overlay).
        rtc_initial_time: The RTC's start time (the host's clock).

    Raises:
        MachineLoadError: If a required ROM file is missing and no override
            is given.
        OSError: If a disk image cannot be read.
    """
    roms: dict[str, bytes] = {}
    if bios_override is not None:
        roms["main"] = bios_override
    else:
        main = _load_rom(spec.rom_base_dir, spec.main_rom_entry.file, required=True)
        assert main is not None
        roms["main"] = main
    if logo_override is not None:
        roms["logo"] = logo_override
    elif spec.logo_rom_entry is not None:
        logo = _load_rom(spec.rom_base_dir, spec.logo_rom_entry.file, required=False)
        if logo is not None:
            roms["logo"] = logo
    mapper1_name = _resolve_mapper_type(mapper, cartridge)
    mapper2_name = _resolve_mapper_type(mapper2, cartridge2)
    if spec.io_device is not None and spec.io_device.device == "kanji_rom":
        roms["io_device"] = _load_device_rom(spec.io_device, "machine io_device")
    roms.update(_overlay_roms(1, extension_overlay1))
    roms.update(_overlay_roms(2, extension_overlay))

    disks: dict[str, DiskMedium] = {}
    if spec.generation == "msx2":
        if extrom_override is not None:
            roms["sub"] = extrom_override
        elif spec.sub_rom_entry is not None:
            sub = _load_rom(spec.rom_base_dir, spec.sub_rom_entry.file, required=True)
            assert sub is not None
            roms["sub"] = sub
        if spec.fdc is not None:
            if disk_rom_override is not None:
                roms["disk"] = disk_rom_override
            else:
                disk = _load_rom(spec.rom_base_dir, spec.fdc.disk_rom_entry.file, required=True)
                assert disk is not None
                roms["disk"] = disk
            for index, path in enumerate((fdd1, fdd2)):
                if path is not None and index >= spec.fdc.drives:
                    print(
                        f"warning: --fdd{index + 1} given but machine has only "
                        f"{spec.fdc.drives} drive(s); ignoring",
                        file=sys.stderr,
                    )
                elif path is not None:
                    disks[drive_media_id(index)] = read_disk_medium(path)

    config = MachineConfig(
        spec=spec, roms=roms,
        cartridge1=cartridge, mapper1=mapper1_name,
        cartridge2=cartridge2, mapper2=mapper2_name,
        extension_overlay1=extension_overlay1, extension_overlay2=extension_overlay,
        disks=disks, rtc_initial_time=rtc_initial_time,
    )
    for media_id, path in sram_paths(config).items():
        size, label = _sram_size_and_label(config, media_id)
        data = _load_sram_or_warn(path, size, label)
        if data is not None:
            config.media[media_id] = bytes(data)
    return config


def build_machine_from_spec(
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
    extension_overlay: _ExtensionOverlay | _ExpandedExtensionOverlay | None = None,
    extension_overlay1: _ExtensionOverlay | _ExpandedExtensionOverlay | None = None,
    rtc_initial_time: RtcTime | None = None,
) -> Machine:
    """load_machine_config() then build_machine(), for callers that need no
    access to the MachineConfig (see load_machine_config for the arguments)."""
    config = load_machine_config(
        spec, cartridge, mapper, cartridge2, mapper2,
        bios_override=bios_override, logo_override=logo_override,
        extrom_override=extrom_override, disk_rom_override=disk_rom_override,
        fdd1=fdd1, fdd2=fdd2,
        extension_overlay=extension_overlay, extension_overlay1=extension_overlay1,
        rtc_initial_time=rtc_initial_time,
    )
    return build_machine(config, logger=logger, tracer=tracer)
