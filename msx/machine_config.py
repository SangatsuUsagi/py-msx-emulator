"""The MachineConfig document: the resolved machine as canonical JSON.

The host resolves the authored YAML (machine, devices, extensions), the
cartridges and their mappers into a MachineSpec, extension overlays and ROM
images by role; encode_machine_config() turns those into the document, and
parse_machine_config() turns a document back into them. Both are pure: no
file is read. The document holds no host path. Each ROM is a reference
{name, size, sha1, pages}: `name` is the bare file name (a display label for
the debugger), and the image itself travels beside the document, keyed by
SHA1. Run inputs (initial SRAM/CMOS images, disks, the RTC start time) are
not part of it. msx/machine_config.schema.json states the same layout
language-neutrally; see openspec/changes/archive/2026-10-25-portable-machine-config.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from msx.canonical_json import decode_strict, encode_canonical, mismatched_version
from msx.machine_spec import (
    AnyExtensionOverlay,
    ExpandedExtensionOverlay,
    ExtensionOverlay,
    KeyboardLayout,
    MachineLoadError,
    MachineSpec,
    _ExpandedSubslotDevice,
    _FdcDef,
    _RomEntry,
    _Slot0RamEntry,
)
from msx.ppi import PpiControlRead
from msx.snapshot_schema import (
    SAFE_INT_MAX,
    SchemaError,
    check_bool,
    check_int,
    check_int_list,
    check_lower_hex,
    check_object,
    check_optional_int,
    check_str,
)
from msx.vdp._geometry import VideoStandard

_T = TypeVar("_T")

# Version 1: the first MachineConfig document (openspec/changes/archive/
#   2026-10-25-portable-machine-config).
MACHINE_CONFIG_FORMAT_VERSION: int = 1

_SHA1_HEX_LEN = 40


@dataclass
class RomRef:
    """A ROM the document names: its bare file name (a display label, never
    a path), size in bytes, SHA1 (lowercase hex) and the slot pages it maps
    (empty for a device ROM)."""

    name: str
    size: int
    sha1: str
    pages: list[int]


@dataclass
class ParsedMachineConfig:
    """A document parsed back into the values build_machine wires from.
    `roms` maps each ROM role the wiring reads ("main", "sub", "slot2.ext",
    ..., and "cartridge1"/"cartridge2") to the reference whose image it
    needs. `config_sha256` is the configuration identity: the SHA-256 of the
    document's canonical form, whatever whitespace and key order it came in."""

    spec: MachineSpec
    mapper1: str
    mapper2: str
    extension_overlay1: AnyExtensionOverlay | None
    extension_overlay2: AnyExtensionOverlay | None
    roms: dict[str, RomRef]
    config_sha256: str


def rom_ref(name: str, data: bytes, pages: list[int]) -> RomRef:
    """The reference of ROM image `data`."""
    sha1 = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    return RomRef(name=name, size=len(data), sha1=sha1, pages=list(pages))


def document_sha256(document: bytes) -> str:
    """The identity of a configuration: SHA-256 of its canonical bytes."""
    return hashlib.sha256(document).hexdigest()


# --- encoding ---------------------------------------------------------------

def _ref_object(ref: RomRef) -> dict[str, object]:
    return {"name": ref.name, "size": ref.size, "sha1": ref.sha1, "pages": list(ref.pages)}


def _entry_ref(
    role: str, name: str, pages: list[int], roms: dict[str, bytes], images: dict[str, bytes]
) -> dict[str, object] | None:
    """The reference of the image the host loaded for `role` (also recorded
    in `images` by SHA1), or None when it loaded none (an optional ROM whose
    file is absent)."""
    data = roms.get(role)
    if data is None:
        return None
    ref = rom_ref(name, data, pages)
    images[ref.sha1] = data
    return _ref_object(ref)


def _device_object(
    device: _ExpandedSubslotDevice, role: str, roms: dict[str, bytes], images: dict[str, bytes]
) -> dict[str, object]:
    rom = None
    if device.rom_entry is not None:
        rom = _entry_ref(role, device.rom_entry.file, [], roms, images)
    return {"device": device.device, "rom": rom, "size_kb": device.size_kb}


def _overlay_object(
    slot: int, overlay: AnyExtensionOverlay | None, roms: dict[str, bytes],
    images: dict[str, bytes],
) -> dict[str, object] | None:
    if overlay is None:
        return None
    if isinstance(overlay, ExpandedExtensionOverlay):
        subslots: dict[str, object] = {}
        for index, sub in sorted(overlay.subslots.items()):
            subslots[str(index)] = _device_object(sub, f"slot{slot}.{index}", roms, images)
        io_device = None
        if overlay.io_device is not None:
            io_device = _device_object(overlay.io_device, f"slot{slot}.io_device", roms, images)
        return {"shape": "expanded", "subslots": subslots, "io_device": io_device}
    rom = None
    if overlay.rom_entry is not None:
        rom = _entry_ref(f"slot{slot}.ext", overlay.rom_entry.file, [], roms, images)
    return {"shape": "flat", "device": overlay.device, "rom": rom}


def _machine_object(
    spec: MachineSpec, roms: dict[str, bytes], images: dict[str, bytes]
) -> dict[str, object]:
    main = spec.main_rom_entry
    # A logo image is used whenever the host loaded one (an override needs no
    # YAML entry), as build_machine always has.
    logo_entry = spec.logo_rom_entry
    logo = _entry_ref(
        "logo", logo_entry.file if logo_entry is not None else "",
        logo_entry.pages if logo_entry is not None else [2], roms, images,
    )
    sub = None
    if spec.sub_rom_entry is not None:
        sub = _entry_ref("sub", spec.sub_rom_entry.file, spec.sub_rom_entry.pages, roms, images)
    slot0_ram = None
    if spec.slot0_ram_entry is not None:
        slot0_ram = {"size_kb": spec.slot0_ram_entry.size_kb, "page": spec.slot0_ram_entry.page}
    fdc = None
    if spec.fdc is not None:
        disk = spec.fdc.disk_rom_entry
        fdc = {
            "controller": spec.fdc.controller,
            "connection_style": spec.fdc.connection_style,
            "drives": spec.fdc.drives,
            "disk_rom": _entry_ref("disk", disk.file, disk.pages, roms, images),
        }
    io_device = None
    if spec.io_device is not None:
        io_device = _device_object(spec.io_device, "io_device", roms, images)
    io_ports: dict[str, object] = {}
    for device_id, (first, last) in sorted(spec.device_io_ports.items()):
        io_ports[device_id] = [first, last]
    main_ref = _entry_ref("main", main.file, main.pages, roms, images)
    if main_ref is None:
        raise MachineLoadError(f"{spec.machine_id}: no main ROM image to describe")
    return {
        "id": spec.machine_id,
        "name": spec.name,
        "generation": spec.generation,
        "video_standard": spec.video_standard.value,
        "m1_wait_states": spec.m1_wait_states,
        "keyboard_layout": spec.keyboard_type.value,
        "ppi_control_read": spec.ppi_control_read.value,
        "has_v9938": spec.has_v9938,
        "has_rtc": spec.has_rtc,
        "main_rom": main_ref,
        "logo_rom": logo,
        "slot0_ram": slot0_ram,
        "ram_size_kb": spec.ram_size_kb,
        "sub_rom": sub,
        "sub_rom_subslot": spec.sub_rom_subslot,
        "has_ram_mapper": spec.has_ram_mapper,
        "ram_mapper_size_kb": spec.ram_mapper_size_kb,
        "flat_ram_subslot": spec.flat_ram_subslot,
        "flat_ram_size_kb": spec.flat_ram_size_kb,
        "fdc": fdc,
        "fdc_subslot": spec.fdc_subslot,
        "io_device": io_device,
        "device_io_ports": io_ports,
    }


def _cartridge_object(
    cartridge: bytes | None, mapper: str, images: dict[str, bytes]
) -> dict[str, object]:
    rom = None
    if cartridge is not None:
        ref = rom_ref("", cartridge, [])
        images[ref.sha1] = cartridge
        rom = _ref_object(ref)
    return {"mapper": mapper, "rom": rom}


def encode_machine_config(
    spec: MachineSpec,
    roms: dict[str, bytes],
    *,
    cartridge1: bytes | None = None,
    mapper1: str = "Mirrored",
    cartridge2: bytes | None = None,
    mapper2: str = "Mirrored",
    extension_overlay1: AnyExtensionOverlay | None = None,
    extension_overlay2: AnyExtensionOverlay | None = None,
) -> tuple[bytes, dict[str, bytes]]:
    """The canonical document of a resolved machine, and the images it
    references keyed by SHA1 (MachineConfig.roms). `roms` holds the images
    the host loaded, by role; `mapper1`/`mapper2` are resolved names, never
    "auto"."""
    images: dict[str, bytes] = {}
    document = {
        "format_version": MACHINE_CONFIG_FORMAT_VERSION,
        "machine": _machine_object(spec, roms, images),
        "cartridges": {
            "slot1": _cartridge_object(cartridge1, mapper1, images),
            "slot2": _cartridge_object(cartridge2, mapper2, images),
        },
        "extensions": {
            "slot1": _overlay_object(1, extension_overlay1, roms, images),
            "slot2": _overlay_object(2, extension_overlay2, roms, images),
        },
    }
    return encode_canonical(document), images


# --- parsing ------------------------------------------------------------------

_TOP_KEYS = frozenset({"format_version", "machine", "cartridges", "extensions"})
_MACHINE_KEYS = frozenset({
    "id", "name", "generation", "video_standard", "m1_wait_states", "keyboard_layout",
    "ppi_control_read", "has_v9938", "has_rtc", "main_rom", "logo_rom", "slot0_ram",
    "ram_size_kb", "sub_rom", "sub_rom_subslot", "has_ram_mapper", "ram_mapper_size_kb",
    "flat_ram_subslot", "flat_ram_size_kb", "fdc", "fdc_subslot", "io_device",
    "device_io_ports",
})
_ROM_KEYS = frozenset({"name", "size", "sha1", "pages"})
_SLOT0_RAM_KEYS = frozenset({"size_kb", "page"})
_FDC_KEYS = frozenset({"controller", "connection_style", "drives", "disk_rom"})
_DEVICE_KEYS = frozenset({"device", "rom", "size_kb"})
_SLOT_PAIR_KEYS = frozenset({"slot1", "slot2"})
_CARTRIDGE_KEYS = frozenset({"mapper", "rom"})
_FLAT_KEYS = frozenset({"shape", "device", "rom"})
_EXPANDED_KEYS = frozenset({"shape", "subslots", "io_device"})
_MAX_KB = 1 << 20      # 1 GB: far above any MSX device, below SAFE_INT_MAX
_SUBSLOTS = 4
_PAGES = 4


def _choice(value: object, label: str, choices: frozenset[str]) -> str:
    text = check_str(value, label)
    if text not in choices:
        raise SchemaError(f"{label} must be one of {sorted(choices)}, got {text!r}")
    return text


def _enum(value: object, label: str, enum_type: Callable[[str], _T], names: list[str]) -> _T:
    return enum_type(_choice(value, label, frozenset(names)))


def _rom(value: object, label: str) -> RomRef:
    obj = check_object(value, label, _ROM_KEYS)
    sha1 = check_lower_hex(obj["sha1"], f"{label}.sha1", _SHA1_HEX_LEN)
    pages_obj = obj["pages"]
    if not isinstance(pages_obj, list) or len(pages_obj) > _PAGES:
        raise SchemaError(f"{label}.pages must be a list of at most {_PAGES} pages")
    pages = check_int_list(pages_obj, f"{label}.pages", len(pages_obj), 0, _PAGES - 1)
    return RomRef(
        name=check_str(obj["name"], f"{label}.name"),
        size=check_int(obj["size"], f"{label}.size", 0, SAFE_INT_MAX),
        sha1=sha1,
        pages=pages,
    )


def _optional_rom(value: object, label: str) -> RomRef | None:
    return None if value is None else _rom(value, label)


def _entry(ref: RomRef) -> _RomEntry:
    return _RomEntry(file=ref.name, size_kb=ref.size // 1024, pages=list(ref.pages), sha1=ref.sha1)


def _add_rom(roms: dict[str, RomRef], role: str, ref: RomRef | None) -> _RomEntry | None:
    if ref is None:
        return None
    roms[role] = ref
    return _entry(ref)


def _device(
    value: object, label: str, role: str, roms: dict[str, RomRef]
) -> _ExpandedSubslotDevice:
    obj = check_object(value, label, _DEVICE_KEYS)
    rom = _optional_rom(obj["rom"], f"{label}.rom")
    return _ExpandedSubslotDevice(
        device=check_str(obj["device"], f"{label}.device"),
        rom_entry=_add_rom(roms, role, rom),
        size_kb=check_optional_int(obj["size_kb"], f"{label}.size_kb", 0, _MAX_KB),
    )


def _optional_device(
    value: object, label: str, role: str, roms: dict[str, RomRef]
) -> _ExpandedSubslotDevice | None:
    return None if value is None else _device(value, label, role, roms)


def _parse_machine(value: object, roms: dict[str, RomRef]) -> MachineSpec:
    obj = check_object(value, "machine", _MACHINE_KEYS)
    label = "machine"
    main = _add_rom(roms, "main", _rom(obj["main_rom"], f"{label}.main_rom"))
    assert main is not None
    slot0_ram = None
    if obj["slot0_ram"] is not None:
        ram = check_object(obj["slot0_ram"], f"{label}.slot0_ram", _SLOT0_RAM_KEYS)
        slot0_ram = _Slot0RamEntry(
            size_kb=check_int(ram["size_kb"], f"{label}.slot0_ram.size_kb", 0, 64),
            page=check_int(ram["page"], f"{label}.slot0_ram.page", 0, _PAGES - 1),
        )
    fdc = None
    if obj["fdc"] is not None:
        fdc_obj = check_object(obj["fdc"], f"{label}.fdc", _FDC_KEYS)
        disk = _add_rom(roms, "disk", _optional_rom(fdc_obj["disk_rom"], f"{label}.fdc.disk_rom"))
        if disk is None:
            raise SchemaError(f"{label}.fdc.disk_rom is required when an FDC is declared")
        fdc = _FdcDef(
            disk_rom_entry=disk,
            controller=check_str(fdc_obj["controller"], f"{label}.fdc.controller"),
            connection_style=check_str(
                fdc_obj["connection_style"], f"{label}.fdc.connection_style"
            ),
            drives=check_int(fdc_obj["drives"], f"{label}.fdc.drives", 1, 4),
        )
    io_ports_obj = check_object(obj["device_io_ports"], f"{label}.device_io_ports")
    io_ports: dict[str, tuple[int, int]] = {}
    for device_id, pair in io_ports_obj.items():
        ports = check_int_list(pair, f"{label}.device_io_ports.{device_id}", 2, 0, 0xFF)
        io_ports[device_id] = (ports[0], ports[1])
    return MachineSpec(
        name=check_str(obj["name"], f"{label}.name"),
        generation=_choice(obj["generation"], f"{label}.generation", frozenset({"msx1", "msx2"})),
        main_rom_entry=main,
        logo_rom_entry=_add_rom(
            roms, "logo", _optional_rom(obj["logo_rom"], f"{label}.logo_rom")
        ),
        sub_rom_entry=_add_rom(roms, "sub", _optional_rom(obj["sub_rom"], f"{label}.sub_rom")),
        has_ram_mapper=check_bool(obj["has_ram_mapper"], f"{label}.has_ram_mapper"),
        ram_size_kb=check_int(obj["ram_size_kb"], f"{label}.ram_size_kb", 0, _MAX_KB),
        has_v9938=check_bool(obj["has_v9938"], f"{label}.has_v9938"),
        has_rtc=check_bool(obj["has_rtc"], f"{label}.has_rtc"),
        slot0_ram_entry=slot0_ram,
        keyboard_type=_enum(
            obj["keyboard_layout"], f"{label}.keyboard_layout", KeyboardLayout,
            [layout.value for layout in KeyboardLayout],
        ),
        ppi_control_read=_enum(
            obj["ppi_control_read"], f"{label}.ppi_control_read", PpiControlRead,
            [mode.value for mode in PpiControlRead],
        ),
        ram_mapper_size_kb=check_int(
            obj["ram_mapper_size_kb"], f"{label}.ram_mapper_size_kb", 0, _MAX_KB
        ),
        machine_id=check_str(obj["id"], f"{label}.id"),
        device_io_ports=io_ports,
        io_device=_optional_device(obj["io_device"], f"{label}.io_device", "io_device", roms),
        video_standard=_enum(
            obj["video_standard"], f"{label}.video_standard", VideoStandard,
            [standard.value for standard in VideoStandard],
        ),
        m1_wait_states=check_int(obj["m1_wait_states"], f"{label}.m1_wait_states", 0, 15),
        flat_ram_subslot=check_optional_int(
            obj["flat_ram_subslot"], f"{label}.flat_ram_subslot", 0, _SUBSLOTS - 1
        ),
        flat_ram_size_kb=check_int(
            obj["flat_ram_size_kb"], f"{label}.flat_ram_size_kb", 0, _MAX_KB
        ),
        sub_rom_subslot=check_int(
            obj["sub_rom_subslot"], f"{label}.sub_rom_subslot", 0, _SUBSLOTS - 1
        ),
        fdc=fdc,
        fdc_subslot=check_int(obj["fdc_subslot"], f"{label}.fdc_subslot", 0, _SUBSLOTS - 1),
    )


def _parse_cartridge(value: object, slot: int, roms: dict[str, RomRef]) -> str:
    label = f"cartridges.slot{slot}"
    obj = check_object(value, label, _CARTRIDGE_KEYS)
    mapper = check_str(obj["mapper"], f"{label}.mapper")
    if mapper == "auto":
        raise SchemaError(f"{label}.mapper must be resolved, got 'auto'")
    rom = _optional_rom(obj["rom"], f"{label}.rom")
    if rom is not None:
        roms[f"cartridge{slot}"] = rom
    return mapper


def _parse_overlay(
    value: object, slot: int, roms: dict[str, RomRef]
) -> AnyExtensionOverlay | None:
    if value is None:
        return None
    label = f"extensions.slot{slot}"
    shape_obj = check_object(value, label)
    shape = _choice(shape_obj.get("shape"), f"{label}.shape", frozenset({"flat", "expanded"}))
    if shape == "flat":
        obj = check_object(value, label, _FLAT_KEYS)
        rom = _optional_rom(obj["rom"], f"{label}.rom")
        return ExtensionOverlay(
            device=check_str(obj["device"], f"{label}.device"),
            rom_entry=_add_rom(roms, f"slot{slot}.ext", rom),
        )
    obj = check_object(value, label, _EXPANDED_KEYS)
    subslots_obj = check_object(obj["subslots"], f"{label}.subslots")
    subslots: dict[int, _ExpandedSubslotDevice] = {}
    for key, device in subslots_obj.items():
        if key not in ("0", "1", "2", "3"):
            raise SchemaError(f"{label}.subslots has a key {key!r} that is not a sub-slot 0-3")
        index = int(key)
        subslots[index] = _device(
            device, f"{label}.subslots.{key}", f"slot{slot}.{index}", roms
        )
    return ExpandedExtensionOverlay(
        subslots=subslots,
        io_device=_optional_device(
            obj["io_device"], f"{label}.io_device", f"slot{slot}.io_device", roms
        ),
    )


def _parse(decoded: object, config_sha256: str) -> ParsedMachineConfig:
    obj = check_object(decoded, "document", _TOP_KEYS)
    version = obj["format_version"]
    if version != MACHINE_CONFIG_FORMAT_VERSION:
        raise SchemaError(
            f"format_version {version!r} is not {MACHINE_CONFIG_FORMAT_VERSION}"
        )
    roms: dict[str, RomRef] = {}
    spec = _parse_machine(obj["machine"], roms)
    cartridges = check_object(obj["cartridges"], "cartridges", _SLOT_PAIR_KEYS)
    extensions = check_object(obj["extensions"], "extensions", _SLOT_PAIR_KEYS)
    return ParsedMachineConfig(
        spec=spec,
        mapper1=_parse_cartridge(cartridges["slot1"], 1, roms),
        mapper2=_parse_cartridge(cartridges["slot2"], 2, roms),
        extension_overlay1=_parse_overlay(extensions["slot1"], 1, roms),
        extension_overlay2=_parse_overlay(extensions["slot2"], 2, roms),
        roms=roms,
        config_sha256=config_sha256,
    )


def parse_machine_config(document: bytes) -> ParsedMachineConfig:
    """Parse a MachineConfig document strictly: every key required, no
    unknown key, every value's type and range checked. Any whitespace and
    key order is accepted; the identity is taken from the canonical form.

    Raises:
        MachineLoadError: For anything malformed, naming the field; for
            another format version, naming the version.
    """
    try:
        decoded = decode_strict(document)
        canonical = encode_canonical(decoded)
    except ValueError as exc:
        version = mismatched_version(document, MACHINE_CONFIG_FORMAT_VERSION)
        if version is not None:
            raise MachineLoadError(
                f"MachineConfig document: version {version}, "
                f"expected {MACHINE_CONFIG_FORMAT_VERSION}"
            ) from exc
        raise MachineLoadError(f"MachineConfig document is not valid JSON: {exc}") from exc
    try:
        return _parse(decoded, document_sha256(canonical))
    except SchemaError as exc:
        raise MachineLoadError(f"MachineConfig document: {exc}") from exc
