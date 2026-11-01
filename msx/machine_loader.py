"""Machine builder: wires a Machine from a MachineConfig.

The core half of machine loading. host/machine_config.py parses the YAML,
reads every ROM, SRAM and disk file and resolves "auto" mappers into a
MachineConfig (msx/boundary.py): a canonical document plus the ROM images it
references, keyed by SHA1. build_machine() parses the document, checks every
referenced image, then only constructs and wires devices from those bytes.
It reads no file and looks up no database.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from msx.core_api import CoreApi
    from msx.fdc.interface import FloppyDisk
    from msx.machine import Machine

from msx.boundary import (
    DRIVE_MEDIA_IDS,
    STATUS_BAD_CONFIG,
    STATUS_OK,
    TRACE_DEBUG,
    TRACE_MAPPER,
    TRACE_VDP,
    DiskMount,
    MachineConfig,
    MediaId,
    RtcTime,
    slot_sram_id,
    subslot_sram_id,
)
from msx.cpu.z80 import Z80
from msx.fmpac import FMPAC_ROM_SIZE, FmPac
from msx.input import InputLines
from msx.io import IOBus
from msx.kanji import KanjiRom
from msx.machine_config import RomRef, parse_machine_config
from msx.machine_spec import (
    AnyExtensionOverlay,
    ExpandedExtensionOverlay,
    ExpandedSubslotDevice,
    ExtensionOverlay,
    MachineLoadError,
    MachineSpec,
)
from msx.mapper import (
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    BankTracingMapper,
    FixedPageMapper,
    FlatMapper,
    GameMaster2Mapper,
    HalnoteMapper,
    KoeiSRAM32Mapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    Mapper,
    MapperKind,
    RTypeMapper,
    SCCICart,
)
from msx.memory import Memory
from msx.opll import Opll
from msx.ppi import PPI
from msx.psg import PSG
from msx.ram_mapper import RamMapper
from msx.rtc import RTC, check_start
from msx.scc import SCC
from msx.sram import SramImage
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP, VdpDevice

# ---------------------------------------------------------------------------
# Mapper helpers
# ---------------------------------------------------------------------------

def _require_scc(scc: SCC | None) -> SCC:
    if scc is None:
        raise MachineLoadError("KonamiSCC mapper requires an SCC instance")
    return scc


# PORT-NOTE: the build dispatches on strings from the parsed document
#   (mapper type here, `generation` and the FDC `controller` below). It
#   runs once per build, not per instruction. A native core maps each
#   string to an enum once, when it parses the document. The other closed
#   vocabularies: the ROM roles ("main", "logo", "sub", "disk", "slot<N>.ext",
#   "slot<N>.<sub-slot>", "slot<N>.io_device"; also composed strings, one image
#   table key each) and the `device_io_ports` keys (_IO_DEVICES in
#   msx/machine_config.py).
# mapper_type -> builder. Each builder receives (cartridge, rom_bytes, sram,
# scc): `cartridge` is the raw ROM (None when absent), `rom_bytes` the same
# with None normalised to b"". FlatMapper keeps the None-able cartridge (it
# treats "no ROM" specially); the bank-switching mappers take rom_bytes.
_MAPPER_BUILDERS: dict[
    str, Callable[[bytes | None, bytes, bytes | None, SCC | None], Mapper]
] = {
    "Mirrored":     lambda cart, rom, sram, scc: FlatMapper(cart),
    "Normal":       lambda cart, rom, sram, scc: FlatMapper(cart),
    "ASCII8":       lambda cart, rom, sram, scc: Ascii8Mapper(rom),
    "ASCII8SRAM2":  lambda cart, rom, sram, scc: Ascii8Sram2Mapper(rom, sram=sram),
    "ASCII8SRAM8":  lambda cart, rom, sram, scc: Ascii8Sram8Mapper(rom, sram=sram),
    "ASCII16":      lambda cart, rom, sram, scc: Ascii16Mapper(rom),
    "ASCII16SRAM2": lambda cart, rom, sram, scc: Ascii16Sram2Mapper(
        rom, sram=sram
    ),
    "ASCII16SRAM8": lambda cart, rom, sram, scc: Ascii16Sram8Mapper(
        rom, sram=sram
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
    "KoeiSRAM32":   lambda cart, rom, sram, scc: KoeiSRAM32Mapper(rom, sram=sram),
    "GameMaster2":  lambda cart, rom, sram, scc: GameMaster2Mapper(rom, sram=sram),
}


def _make_mapper(
    mapper_type: str,
    cartridge: bytes | None,
    scc: SCC | None = None,
    sram: bytes | None = None,
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


def _slot0_ram_buffer(spec: MachineSpec) -> tuple[bytearray | None, int]:
    """Slot-0 flat RAM buffer and page for Memory; (None, 3) when undeclared."""
    if spec.slot0_ram_entry is None:
        return None, 3
    return bytearray(spec.slot0_ram_entry.size_kb * 1024), spec.slot0_ram_entry.page


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
# Machine builder
# ---------------------------------------------------------------------------


@dataclass
class _ResolvedConfig:
    """A MachineConfig with its document parsed and its ROM images checked:
    the values the wiring below reads. `roms` maps each ROM role ("main",
    "logo", "sub", "disk", "io_device", "slot<n>.ext", "slot<n>.io_device",
    "slot<n>.<sub>") to its image; `mapper1`/`mapper2` are resolved names;
    `config_sha256` is the configuration identity (ParsedMachineConfig)."""

    spec: MachineSpec
    roms: dict[str, bytes]
    cartridge1: bytes | None
    mapper1: str
    cartridge2: bytes | None
    mapper2: str
    extension_overlay1: AnyExtensionOverlay | None
    extension_overlay2: AnyExtensionOverlay | None
    media: dict[MediaId, bytes]
    disks: dict[int, DiskMount]       # by drive index
    rtc_initial_time: RtcTime
    config_sha256: str


def _checked_image(images: dict[str, bytes], role: str, ref: RomRef) -> bytes:
    """The image a ROM reference names, after checking its size and SHA1."""
    data = images.get(ref.sha1)
    if data is None:
        raise MachineLoadError(f"MachineConfig has no ROM image for {role} (sha1 {ref.sha1})")
    sha1 = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    if len(data) != ref.size or sha1 != ref.sha1:
        raise MachineLoadError(
            f"ROM image for {role} does not match its reference: expected {ref.size} bytes, "
            f"sha1 {ref.sha1}; got {len(data)} bytes, sha1 {sha1}"
        )
    return data


def _media_by_id(config: MachineConfig) -> dict[MediaId, bytes]:
    """The medium images by id, rejecting a second record for one id, a
    drive id (disks travel in MachineConfig.disks) and an id that is no
    MediaId. An id given as a plain int is read as the MediaId of that
    value."""
    media: dict[MediaId, bytes] = {}
    for image in config.media:
        mid = int(image.media_id)
        if mid in DRIVE_MEDIA_IDS:
            raise MachineLoadError(
                f"MachineConfig has a medium image for {MediaId(mid).name}, a drive id"
            )
        if mid < 0 or mid > MediaId.RTC_CMOS:
            raise MachineLoadError(f"MachineConfig has a medium image for unknown id {mid}")
        media_id = MediaId(mid)
        if media_id in media:
            raise MachineLoadError(f"MachineConfig has two medium images for {media_id.name}")
        media[media_id] = image.data
    return media


def _disks_by_drive(config: MachineConfig, drive_count: int) -> dict[int, DiskMount]:
    """The disks by drive index, rejecting a second disk for one drive and a
    disk for a drive the machine does not have."""
    disks: dict[int, DiskMount] = {}
    for disk in config.disks:
        if not 0 <= disk.drive < drive_count:
            raise MachineLoadError(
                f"MachineConfig has a disk for drive {disk.drive} but the machine has "
                f"{drive_count} drive(s)"
            )
        if disk.drive in disks:
            raise MachineLoadError(f"MachineConfig has two disks for drive {disk.drive}")
        disks[disk.drive] = disk
    return disks


def _check_rtc_initial_time(start: RtcTime) -> None:
    """Reject an RTC start time that a state load would reject, so a built
    machine can always load its own saved state."""
    fields = [start.year, start.month, start.day, start.hour, start.minute, start.second]
    try:
        check_start(fields, "MachineConfig.rtc_initial_time")
    except ValueError as exc:
        raise MachineLoadError(str(exc)) from None


def _resolve(config: MachineConfig) -> _ResolvedConfig:
    """Parse the document and pair every ROM reference with its checked
    image -- everything that can reject the config, before anything is built."""
    parsed = parse_machine_config(config.document)
    images: dict[str, bytes] = {}
    for rom in config.roms:
        images[rom.sha1] = rom.data
    spec = parsed.spec
    # Only _build_msx2 wires an FDC, so an MSX1 has no drive to mount into.
    drive_count = 0
    if spec.generation == "msx2" and spec.fdc is not None:
        drive_count = spec.fdc.drives
    media = _media_by_id(config)
    disks = _disks_by_drive(config, drive_count)
    _check_rtc_initial_time(config.rtc_initial_time)
    roms: dict[str, bytes] = {}
    cartridges: dict[str, bytes] = {}
    for role, ref in parsed.roms.items():
        image = _checked_image(images, role, ref)
        if role.startswith("cartridge"):
            cartridges[role] = image
        else:
            roms[role] = image
    return _ResolvedConfig(
        spec=parsed.spec,
        roms=roms,
        cartridge1=cartridges.get("cartridge1"),
        mapper1=parsed.mapper1,
        cartridge2=cartridges.get("cartridge2"),
        mapper2=parsed.mapper2,
        extension_overlay1=parsed.extension_overlay1,
        extension_overlay2=parsed.extension_overlay2,
        media=media,
        disks=disks,
        rtc_initial_time=config.rtc_initial_time,
        config_sha256=parsed.config_sha256,
    )


def _rom(config: _ResolvedConfig, role: str) -> bytes:
    """A required ROM image by role (see _ResolvedConfig.roms)."""
    rom = config.roms.get(role)
    if rom is None:
        raise MachineLoadError(f"MachineConfig has no {role!r} ROM")
    return rom


def _empty_subslots() -> list[Mapper | None]:
    """A fresh (never shared) length-4 all-None sub-slot array: the value of a
    slot with no expanded overlay."""
    return [None, None, None, None]


@dataclass
class _ExtensionWiring:
    """Every device/flag `_wire_extension_overlay` can produce from a
    machine-declared `io_device` and/or a `--extension1`/`--extension2`
    overlay, bundled for `build_machine` to unpack into `Memory` constructor
    arguments and `Machine` attributes. `mapper1_instance`/`mapper2_instance`
    are each independently either unchanged (no overlay on that slot) or
    replaced by that slot's flat overlay's device."""

    mapper1_instance: Mapper
    mapper2_instance: Mapper
    scc: SCC | None
    slot1_sub_slot_enabled: bool
    mapper1_subslots: list[Mapper | None]
    slot2_sub_slot_enabled: bool
    mapper2_subslots: list[Mapper | None]
    fmpac_device: FmPac | None
    kanji_device: KanjiRom | None


def _wire_machine_io_device(config: _ResolvedConfig) -> KanjiRom | None:
    """Resolve a machine-declared global I/O device (currently only a
    built-in Kanji font ROM), independent of any --extension overlay."""
    spec = config.spec
    if spec.io_device is not None and spec.io_device.device == "kanji_rom":
        return KanjiRom(rom=_rom(config, "io_device"))
    return None


@dataclass
class _ExpandedOverlayWiring:
    """Every device/flag an *expanded* --extension overlay (multiple
    sub-slots on whichever cartridge slot it targets) produces, for
    `_wire_extension_overlay` to fold into `_ExtensionWiring`."""

    subslots: list[Mapper | None]
    kanji_device: KanjiRom | None


def _build_expanded_subslot_device(
    config: _ResolvedConfig, slot: int, subslot: ExpandedSubslotDevice, index: int
) -> Mapper:
    """Construct one expanded-overlay sub-slot's device.

    Raises:
        MachineLoadError: If `subslot.device` passed load-time validation
            (`SUBSLOT_DEVICES`) but has no construction case
            here (an internal consistency error between the two).
    """
    spec = config.spec
    rom_role = f"slot{slot}.{index}"
    if subslot.device == "halnote":
        halnote_sram = config.media.get(subslot_sram_id(slot, index))
        return HalnoteMapper(rom=_rom(config, rom_role), sram=halnote_sram)
    if subslot.device == "flat_rom":
        return FixedPageMapper(rom=_rom(config, rom_role), base=0x4000)
    if subslot.device == "ascii8":
        return Ascii8Mapper(rom=_rom(config, rom_role))
    if subslot.device == "ascii16":
        return Ascii16Mapper(rom=_rom(config, rom_role))
    if subslot.device == "ram_mapper":
        assert subslot.size_kb is not None  # guaranteed by load-time validation
        return RamMapper(size_kb=subslot.size_kb)
    raise MachineLoadError(
        f"{spec.machine_id}: internal error -- expanded overlay sub-slot "
        f"{index} device {subslot.device!r} passed load-time validation "
        f"(SUBSLOT_DEVICES) but has no build_machine "
        "construction case"
    )


def _wire_expanded_overlay(
    config: _ResolvedConfig,
    slot: int,
    extension_overlay: ExpandedExtensionOverlay,
    kanji_device: KanjiRom | None,
    other_slot_ram_mapper_claimed: bool = False,
    other_slot_halnote_claimed: bool = False,
) -> _ExpandedOverlayWiring:
    """Resolve an expanded --extension overlay's per-sub-slot devices and its
    own optional `io_device`. `kanji_device` is whatever
    `_wire_machine_io_device` already resolved, so a conflicting
    overlay-declared Kanji ROM can be detected. `other_slot_ram_mapper_claimed`/
    `other_slot_halnote_claimed` are whether the *other* cartridge slot's own
    expanded overlay (wired first by `_wire_extension_overlay`) already
    claimed a 'ram_mapper'/'halnote' sub-slot -- two independent RamMapper
    instances would otherwise both register on the same I/O ports
    (0xFC-0xFF), and a machine never has two HBI-J1 MSX-JE cartridges.

    Raises:
        MachineLoadError: If applied to an MSX1 machine, if the overlay
            declares a 'ram_mapper' sub-slot on a machine that already has a
            slot-3 memory mapper or whose other cartridge slot already
            claimed one, if the overlay declares a 'halnote' sub-slot while
            the other cartridge slot's overlay already claimed one, if this
            overlay's `io_device` and another source (a machine-declared
            `io_device`, or the other cartridge slot's overlay) both
            resolve to a Kanji-ROM device (ports 0xD8-0xDB cannot serve
            two), or if a sub-slot names a device kind that passed
            load-time validation but has no construction case here (an
            internal consistency error between
            `SUBSLOT_DEVICES` and this function).
    """
    spec = config.spec
    if spec.generation == "msx1":
        raise MachineLoadError(
            f"{spec.machine_id}: an expanded extension overlay has no MSX1 "
            "counterpart (a memory-mapper sub-slot device has no MSX1-standard "
            "hardware, and a real HBI-J1 is an MSX2-era peripheral) -- MSX1 "
            "machines are not supported for this extension shape"
        )
    wants_ram_mapper = any(
        sub.device == "ram_mapper" for sub in extension_overlay.subslots.values()
    )
    if wants_ram_mapper and (spec.has_ram_mapper or other_slot_ram_mapper_claimed):
        reason = (
            "this machine already has one (has_ram_mapper=True)" if spec.has_ram_mapper
            else "the other cartridge slot's extension overlay already claimed one"
        )
        raise MachineLoadError(
            f"{spec.machine_id}: a 'ram_mapper' extension sub-slot requires a machine "
            f"with no existing slot-3 memory mapper and no ram_mapper sub-slot already "
            f"claimed elsewhere, but {reason} -- two independent memory mappers sharing "
            "the same I/O ports (0xFC-0xFF) is not supported"
        )
    wants_halnote = any(
        sub.device == "halnote" for sub in extension_overlay.subslots.values()
    )
    if wants_halnote and other_slot_halnote_claimed:
        raise MachineLoadError(
            f"{spec.machine_id}: a 'halnote' extension sub-slot conflicts with the "
            "other cartridge slot's extension overlay, which already claimed one -- "
            "real hardware never has two HBI-J1 MSX-JE cartridges attached at once"
        )
    subslots: list[Mapper | None] = _empty_subslots()
    for index, subslot in extension_overlay.subslots.items():
        subslots[index] = _build_expanded_subslot_device(config, slot, subslot, index)
    if (
        extension_overlay.io_device is not None
        and extension_overlay.io_device.device == "kanji_rom"
    ):
        if kanji_device is not None:
            raise MachineLoadError(
                f"{spec.machine_id}: this extension overlay's io_device and another "
                "source (a machine-declared io_device, or the other cartridge slot's "
                "extension overlay) both resolve to a Kanji-ROM device on I/O ports "
                "0xD8-0xDB -- these cannot coexist"
            )
        kanji_device = KanjiRom(rom=_rom(config, f"slot{slot}.io_device"))
    return _ExpandedOverlayWiring(subslots=subslots, kanji_device=kanji_device)


@dataclass
class _FlatOverlayWiring:
    """Every device/flag a *flat* --extension overlay (fmpac / scc_i_cart,
    replacing a cartridge slot's mapper wholesale) produces. `mapper_instance`
    is generic -- the caller decides whether it replaces slot 1's or slot
    2's mapper, since a flat overlay's device construction is identical
    regardless of which slot it targets."""

    mapper_instance: Mapper
    scc: SCC | None
    fmpac_device: FmPac | None


def _wire_flat_overlay(
    config: _ResolvedConfig,
    slot: int,
    extension_overlay: ExtensionOverlay,
    scc: SCC | None,
    existing_fmpac: FmPac | None = None,
) -> _FlatOverlayWiring:
    """Resolve a flat --extension overlay's device. The caller places the
    returned `mapper_instance` into whichever slot's overlay this is --
    (and, for scc_i_cart, `scc` -- rejected instead when the incoming `scc`
    is already set, i.e. a cartridge mapper or another extension in either
    slot already resolved its own KonamiSCC chip: real hardware never has
    two SCC chips attached at once, and `Machine` has a single `scc` field).
    `existing_fmpac` is whichever FM-PAC the *other* cartridge slot's own
    flat overlay (wired first by `_wire_extension_overlay`) already
    resolved, if any -- rejected instead when this overlay is also `fmpac`,
    since `Machine` likewise has a single `fmpac` field.

    Raises:
        MachineLoadError: If `extension_overlay.device` passed load-time
            validation (`EXTENSION_DEVICES`) but has no construction
            case here (an internal consistency error between the two), if
            `scc_i_cart` is selected while a KonamiSCC cartridge mapper or
            another slot's `scc_i_cart` already resolved a chip (see
            above), or if `fmpac` is selected while the other cartridge
            slot's `fmpac` extension already resolved one, or if the
            FM-PAC ROM is not FMPAC_ROM_SIZE bytes.
    """
    spec = config.spec
    fmpac_device: FmPac | None = None
    if extension_overlay.device == "fmpac":
        if existing_fmpac is not None:
            raise MachineLoadError(
                f"{spec.machine_id}: cannot use the fmpac extension on both "
                "cartridge slots at once: real hardware has one FM-PAC "
                "cartridge, and Machine has a single fmpac field"
            )
        fmpac_rom = _rom(config, f"slot{slot}.ext")
        if len(fmpac_rom) != FMPAC_ROM_SIZE:
            raise MachineLoadError(
                f"{spec.machine_id}: the FM-PAC ROM is {len(fmpac_rom)} bytes, "
                f"not {FMPAC_ROM_SIZE}"
            )
        fmpac_sram = config.media.get(MediaId.FMPAC_SRAM)
        fmpac_device = FmPac(
            rom=fmpac_rom,
            opll=Opll(),
            sram=fmpac_sram,
        )
        return _FlatOverlayWiring(mapper_instance=fmpac_device, scc=scc, fmpac_device=fmpac_device)
    if extension_overlay.device == "scc_i_cart":
        # is_052539=True: the SCC-I cartridge carries a genuine
        # Konami-052539 chip, not a 051649 (see SCC.is_052539's
        # docstring). This chip becomes machine.scc -- rejected below when
        # one already exists, since real hardware never has two SCC chips
        # attached at once and this Machine has a single `scc` field. The
        # incoming `scc` param reflects whatever either cartridge slot's
        # mapper resolution and/or the other slot's extension overlay
        # already produced -- all four sources are threaded through the
        # same `scc` variable before either overlay is wired (see
        # _resolve_shared_scc), so this single check
        # covers every combination.
        if scc is not None:
            raise MachineLoadError(
                f"{spec.machine_id}: cannot combine a KonamiSCC cartridge mapper "
                "(or another slot's scc_plus extension) with --extension scc_plus: "
                "real hardware never has two SCC chips attached at once, and "
                "Machine has a single scc field"
            )
        scc = SCC(is_052539=True)
        return _FlatOverlayWiring(mapper_instance=SCCICart(scc=scc), scc=scc, fmpac_device=None)
    raise MachineLoadError(
        f"{spec.machine_id}: internal error -- extension overlay device "
        f"{extension_overlay.device!r} passed load-time validation "
        "(EXTENSION_DEVICES) but has no build_machine construction case"
    )


@dataclass
class _OverlayClaims:
    """What one slot's overlay has taken, for the other slot's overlay to check:
    slot 1 is wired first, so a conflict is caught when slot 2 is wired."""

    scc: SCC | None
    kanji_device: KanjiRom | None
    fmpac_device: FmPac | None = None
    ram_mapper_claimed: bool = False
    halnote_claimed: bool = False


@dataclass
class _SlotOverlay:
    """How an overlay leaves one slot."""

    mapper_instance: Mapper
    sub_slot_enabled: bool
    subslots: list[Mapper | None]


def _wire_slot_overlay(
    config: _ResolvedConfig,
    slot: int,
    overlay: AnyExtensionOverlay | None,
    mapper_instance: Mapper,
    claims: _OverlayClaims,
) -> _SlotOverlay:
    """Wire one slot's overlay, if any. It unconditionally occupies the primary
    slot, replacing the cartridge mapper resolved for it."""
    if isinstance(overlay, ExpandedExtensionOverlay):
        expanded = _wire_expanded_overlay(
            config, slot, overlay, claims.kanji_device,
            other_slot_ram_mapper_claimed=claims.ram_mapper_claimed,
            other_slot_halnote_claimed=claims.halnote_claimed,
        )
        claims.kanji_device = expanded.kanji_device
        claims.ram_mapper_claimed = any(_is_ram_mapper(sub) for sub in expanded.subslots)
        claims.halnote_claimed = any(_is_halnote_mapper(sub) for sub in expanded.subslots)
        return _SlotOverlay(mapper_instance, True, expanded.subslots)
    if overlay is not None:
        flat = _wire_flat_overlay(
            config, slot, overlay, claims.scc, existing_fmpac=claims.fmpac_device
        )
        claims.scc = flat.scc
        if flat.fmpac_device is not None:
            claims.fmpac_device = flat.fmpac_device
        return _SlotOverlay(flat.mapper_instance, False, _empty_subslots())
    return _SlotOverlay(mapper_instance, False, _empty_subslots())


def _wire_extension_overlay(
    config: _ResolvedConfig,
    mapper1_instance: Mapper,
    mapper2_instance: Mapper,
    scc: SCC | None,
) -> _ExtensionWiring:
    """Resolve every device a machine-declared `io_device` and/or either
    slot's `--extensionN` overlay contribute, and how they change that
    slot's dispatch. `mapper1_instance`/`mapper2_instance`/`scc` are the
    values `build_machine` already resolved from `cartridge`/`mapper`/
    `cartridge2`/`mapper2` -- a flat overlay's device (fmpac/scc_i_cart)
    unconditionally replaces its slot's mapper instance, and `scc_i_cart`
    also replaces `scc`; an expanded overlay leaves its slot's mapper
    instance alone and populates the returned `mapper1_subslots`/
    `mapper2_subslots` array instead. See `_wire_machine_io_device`,
    `_wire_expanded_overlay`, and `_wire_flat_overlay` for the independent
    pieces this assembles. `scc` is threaded through slot 1's overlay
    before slot 2's, so a conflict between any combination of the four
    possible KonamiSCC sources (either slot's cartridge mapper, either
    slot's `scc_plus` extension) is caught by `_wire_flat_overlay`'s own
    check, run at most twice. `fmpac_device` is threaded the same way (as
    `existing_fmpac`), so both slots resolving to the `fmpac` extension is
    likewise caught by `_wire_flat_overlay`'s own check. Both threaded
    values are only *conditionally* copied out of each `_wire_flat_overlay`
    call (only when that call actually produced one), so a slot whose flat
    overlay is the other device (`scc_i_cart` when threading `fmpac`, or
    vice versa) never clobbers what the other slot already resolved.
    Likewise, slot 1's expanded overlay (if any) is wired before slot 2's,
    so a 'ram_mapper'/'halnote' sub-slot conflict between the two slots'
    expanded overlays is caught by `_wire_expanded_overlay`'s own check,
    run at most twice.

    Raises:
        MachineLoadError: see `_wire_expanded_overlay`/`_wire_flat_overlay`.
    """
    claims = _OverlayClaims(scc=scc, kanji_device=_wire_machine_io_device(config))
    slot1 = _wire_slot_overlay(config, 1, config.extension_overlay1, mapper1_instance, claims)
    slot2 = _wire_slot_overlay(config, 2, config.extension_overlay2, mapper2_instance, claims)
    return _ExtensionWiring(
        mapper1_instance=slot1.mapper_instance,
        mapper2_instance=slot2.mapper_instance,
        scc=claims.scc,
        slot1_sub_slot_enabled=slot1.sub_slot_enabled,
        mapper1_subslots=slot1.subslots,
        slot2_sub_slot_enabled=slot2.sub_slot_enabled,
        mapper2_subslots=slot2.subslots,
        fmpac_device=claims.fmpac_device,
        kanji_device=claims.kanji_device,
    )


@dataclass
class _BiosRoms:
    """Slot 0's main/logo ROM bytes, resolved once (independent of either
    cartridge slot) for `build_machine` to unpack before cartridge-slot
    resolution and dispatching into `_build_msx1`/`_build_msx2`."""

    main_bytes: bytes
    logo_bytes: bytes | None


def _bios_roms(config: _ResolvedConfig) -> _BiosRoms:
    """Slot 0's main/logo ROMs -- unrelated to which cartridge (if any)
    occupies slot 1 or slot 2. The logo ROM is optional."""
    return _BiosRoms(_rom(config, "main"), config.roms.get("logo"))


@dataclass
class _CartridgeSlotSetup:
    """One cartridge slot's resolved mapper (including any loaded SRAM),
    independent of which primary slot (1 or 2) it occupies -- for
    `build_machine` to call once per slot before extension-overlay wiring
    and the cross-slot KonamiSCC-sharing check (see `_resolve_shared_scc`)."""

    mapper_instance: Mapper
    scc: SCC | None


def _resolve_cartridge_slot(
    mapper: str, cartridge: bytes | None, sram: bytes | None
) -> _CartridgeSlotSetup:
    """Build one cartridge slot's mapper from its resolved mapper name and
    the slot's initial SRAM image (None starts blank). Each slot's
    `KonamiSCC` resolution (if any) builds its own fresh `SCC` instance here
    -- `build_machine` reconciles the two via `_resolve_shared_scc`, since
    `Machine` has a single `scc` field."""
    scc: SCC | None = SCC() if mapper == "KonamiSCC" else None
    mapper_instance: Mapper = _make_mapper(mapper, cartridge, scc=scc, sram=sram)
    return _CartridgeSlotSetup(mapper_instance, scc)


def _resolve_shared_scc(
    spec: MachineSpec, slot1_scc: SCC | None, slot2_scc: SCC | None
) -> SCC | None:
    """Cross-slot KonamiSCC-sharing check between the two cartridge slots'
    own mapper resolutions (`_resolve_cartridge_slot`'s independent `scc`
    results) -- `Machine` has a single `scc` field and real hardware never
    has two SCC chips attached at once. `--extensionN scc_plus`'s own
    conflict against whichever chip this returns is checked separately, in
    `_wire_flat_overlay` (see `_wire_extension_overlay`'s docstring for how
    all four possible sources compose)."""
    if slot1_scc is not None and slot2_scc is not None:
        raise MachineLoadError(
            f"{spec.machine_id}: cannot use the KonamiSCC mapper in both slot 1 "
            "and slot 2: real hardware never has two SCC chips attached at "
            "once, and Machine has a single scc field"
        )
    return slot1_scc if slot1_scc is not None else slot2_scc


def _is_majutsushi_mapper(mapper: Mapper) -> TypeGuard[MajutsushiMapper]:
    """True if `mapper` is the one DAC-equipped mapper kind this codebase
    models (Majutsushi). Narrows the concrete type from a closed `kind`
    comparison instead of `isinstance`, matching this file's `_is_*_shape`
    TypeGuard convention (see `_is_rom_entry_shape` and friends above) for
    the identical "check the discriminant, then need the narrowed type"
    shape."""
    return mapper.kind == MapperKind.MAJUTSUSHI


def _is_ram_mapper(mapper: Mapper | None) -> TypeGuard[RamMapper]:
    """True if `mapper` is a `RamMapper` -- see `_is_majutsushi_mapper`."""
    return mapper is not None and mapper.kind == MapperKind.RAM_MAPPER


def _is_halnote_mapper(mapper: Mapper | None) -> bool:
    """True if `mapper` is a `HalnoteMapper` -- see `_is_majutsushi_mapper`."""
    return mapper is not None and mapper.kind == MapperKind.HALNOTE


def _resolve_shared_dac(
    spec: MachineSpec, mapper1_instance: Mapper, mapper2_instance: Mapper
) -> MajutsushiMapper | None:
    """Cross-slot Majutsushi-DAC-sharing check between the two cartridge
    slots' final mapper instances. Takes the post-extension-overlay-wiring
    instances specifically (not `_resolve_cartridge_slot`'s own results),
    since an extension overlay replacing a slot's mapper (e.g. --extension1
    fmpac over a Majutsushi cartridge) removes that slot's own DAC along
    with it. `Machine` has a single `dac` field and real hardware never has
    two Majutsushi-style DAC cartridges attached at once."""
    dac1 = mapper1_instance if _is_majutsushi_mapper(mapper1_instance) else None
    dac2 = mapper2_instance if _is_majutsushi_mapper(mapper2_instance) else None
    if dac1 is not None and dac2 is not None:
        raise MachineLoadError(
            f"{spec.machine_id}: cannot use the Majutsushi mapper in both slot 1 "
            "and slot 2: real hardware never has two DAC-equipped cartridges "
            "attached at once, and Machine has a single dac field"
        )
    return dac1 if dac1 is not None else dac2


def _wire_post_construction_attrs(
    machine: "Machine",
    dac: MajutsushiMapper | None,
    wiring: _ExtensionWiring,
) -> None:
    """Wire the clock record and the device attributes onto an already-built
    Machine: every component that reads or advances the emulated time gets
    the machine's clock record (msx/clock.py), and the machine gets the
    FM-PAC/Kanji attributes `build_machine`'s extension-overlay resolution
    produced. Every value of the record is 0 here, so a
    component's own record held nothing to keep."""
    clock = machine.clock
    machine.cpu._clock = clock
    machine.vdp._clock = clock
    machine.psg._clock = clock
    if machine.ppi is not None:
        machine.ppi._clock = clock
    if dac is not None:
        dac._clock = clock
    if machine.rtc is not None:
        machine.rtc._clock = clock
    _wire_trace(machine)
    machine.fmpac = wiring.fmpac_device
    machine.kanji = wiring.kanji_device


def _wire_trace(machine: "Machine") -> None:
    """Wire the machine's trace buffer (msx/trace.py) into the VDP, the I/O
    bus and the slot 1/2 bank-switching ROM mappers, give it the clock
    record it stamps each event with, and record which trace kinds this
    machine can produce."""
    trace = machine._trace
    trace._clock = machine.clock
    machine.vdp._trace = trace
    machine.io._trace = trace
    trace.sources = TRACE_VDP | TRACE_DEBUG
    mem = machine.memory
    for mp in (mem.mapper1, mem.mapper2):
        if isinstance(mp, BankTracingMapper):
            mp._trace = trace
            trace.sources |= TRACE_MAPPER


@dataclass(frozen=True)
class BuildResult:
    """What create_machine returns: `status` is STATUS_OK with the machine
    and an empty `error_text`, or STATUS_BAD_CONFIG with no machine and the
    UTF-8 message."""

    status: int
    machine: "Machine | None"
    error_text: bytes


def create_machine(machine_config: MachineConfig) -> BuildResult:
    """Build a Machine from a MachineConfig (host/machine_config.py's
    load_machine_config produces one), reporting a rejected configuration
    as STATUS_BAD_CONFIG instead of raising (machine-core "The core builds
    a machine and reports a failure as a status"). Reads no file, resolves
    no path and looks up no database. This is the build entry point of the
    core; host code builds through it."""
    try:
        machine = _build(machine_config)
    except (MachineLoadError, ValueError) as exc:
        # ValueError: a device constructor (disk image, RAM mapper, Kanji
        # ROM, Halnote, Memory) rejected a size or a combination.
        return BuildResult(STATUS_BAD_CONFIG, None, str(exc).encode("utf-8"))
    return BuildResult(STATUS_OK, machine, b"")


def release_machine(machine: "CoreApi") -> None:
    """The host's last use of a machine, the form of msx_core_destroy. The
    Python core has nothing to free: the garbage collector takes the
    machine."""


def build_machine(machine_config: MachineConfig) -> "Machine":
    """create_machine for tests and Python embedding: returns the machine,
    or raises MachineLoadError with the build's message. Host code calls
    create_machine instead (tests/test_core_frontend_boundary.py)."""
    result = create_machine(machine_config)
    if result.machine is None:
        raise MachineLoadError(result.error_text.decode("utf-8"))
    return result.machine


def _build(machine_config: MachineConfig) -> "Machine":
    """The build itself, raising on a rejected configuration.

    Args:
        machine_config: The MachineConfig document, the ROM images it
            references by SHA1, initial SRAM images, disks to mount and RTC
            start time.

    Returns:
        A fully-wired Machine ready for emulation.

    Raises:
        MachineLoadError: If the document is malformed, if a referenced ROM
            image is missing or does not match its size and SHA1, if
            both slots' mappers/extensions resolve to KonamiSCC-family SCC
            chips, if both slots' mappers resolve to Majutsushi, if both
            slots' extensions resolve to fmpac, or if both slots' expanded
            overlays declare a 'ram_mapper' or 'halnote' sub-slot.
    """
    config = _resolve(machine_config)
    spec = config.spec
    bios = _bios_roms(config)

    slot1 = _resolve_cartridge_slot(
        config.mapper1, config.cartridge1, config.media.get(slot_sram_id(1))
    )
    slot2 = _resolve_cartridge_slot(
        config.mapper2, config.cartridge2, config.media.get(slot_sram_id(2))
    )
    mapper1_instance = slot1.mapper_instance
    mapper2_instance = slot2.mapper_instance
    scc = _resolve_shared_scc(spec, slot1.scc, slot2.scc)

    # Machine-declared io_device and either slot's --extensionN overlay:
    # resolved together (an overlay's io_device conflicts with a
    # machine-declared one) and, when present, unconditionally occupy their
    # target slot, replacing whatever cartridge mapper was resolved above --
    # see _wire_extension_overlay.
    wiring = _wire_extension_overlay(config, mapper1_instance, mapper2_instance, scc)
    # Resolved after extension-overlay wiring (see _resolve_shared_dac):
    # an extension overlay replacing a slot's mapper (e.g. --extension1
    # fmpac over a Majutsushi cartridge) removes that slot's own DAC along
    # with it, and either slot's mapper can resolve to Majutsushi.
    dac = _resolve_shared_dac(spec, wiring.mapper1_instance, wiring.mapper2_instance)

    input_state = InputLines()
    psg = PSG(_input=input_state)
    io = IOBus()
    if wiring.fmpac_device is not None:
        io.register_read(0x7C, 0x7D, wiring.fmpac_device.read_port)
        io.register_write(0x7C, 0x7D, wiring.fmpac_device.write_port)
    if wiring.kanji_device is not None:
        io.register_read(0xD8, 0xDB, wiring.kanji_device.read_port)
        io.register_write(0xD8, 0xDB, wiring.kanji_device.write_port)
    # An expanded slot's ram_mapper sub-slot (--extensionN memory_512k/
    # msxdos2_512k*) registers on the same standard memory-mapper ports
    # slot 3's own RamMapper would use -- safe because _wire_extension_overlay
    # already rejected a RamMapper on both cartridge slots at once, and on a
    # slot alongside spec.has_ram_mapper being True.
    for sub_mapper in (*wiring.mapper1_subslots, *wiring.mapper2_subslots):
        if _is_ram_mapper(sub_mapper):
            mm_s, mm_e = _io_range(spec, "memory_mapper_standard",
                                    _DEFAULT_IO_PORTS["memory_mapper_standard"])
            io.register_read(mm_s, mm_e, sub_mapper.read_port)
            io.register_write(mm_s, mm_e, sub_mapper.write_port)

    if spec.generation == "msx2":
        machine = _build_msx2(config, bios, wiring, dac, input_state, psg, io)
    else:
        machine = _build_msx1(spec, bios, wiring, dac, input_state, psg, io)

    _wire_post_construction_attrs(machine, dac, wiring)
    machine._sram_table = _sram_table(machine)
    machine.machine_config_sha256 = config.config_sha256
    return machine


def _sram_table(machine: Machine) -> dict[MediaId, SramImage]:
    """Every SRAM medium of the wired machine by id. The FM-PAC is listed
    only under its own id even when it is a slot's mapper."""
    table: dict[MediaId, SramImage] = {}
    if machine.fmpac is not None:
        table[MediaId.FMPAC_SRAM] = machine.fmpac.sram_image
    if machine.rtc is not None:
        table[MediaId.RTC_CMOS] = machine.rtc.cmos
    memory = machine.memory
    slots = (
        (1, memory.mapper1, memory.mapper1_subslots),
        (2, memory.mapper2, memory.mapper2_subslots),
    )
    for slot, mapper, subslots in slots:
        image = mapper.sram_medium()
        if image is not None and mapper is not machine.fmpac:
            table[slot_sram_id(slot)] = image
        for sub, sub_mapper in enumerate(subslots):
            if sub_mapper is None or sub_mapper is machine.fmpac:
                continue
            sub_image = sub_mapper.sram_medium()
            if sub_image is not None:
                table[subslot_sram_id(slot, sub)] = sub_image
    return table


def _build_msx1(
    spec: MachineSpec,
    bios: _BiosRoms,
    wiring: _ExtensionWiring,
    dac: MajutsushiMapper | None,
    input_state: InputLines,
    psg: PSG,
    io: IOBus,
) -> "Machine":
    from msx.machine import Machine  # local import to avoid circular dependency

    slot0_ram, slot0_ram_page = _slot0_ram_buffer(spec)
    memory = Memory(
        rom=bios.main_bytes,
        ram=bytearray(spec.ram_size_kb * 1024),
        mapper1=wiring.mapper1_instance,
        mapper2=wiring.mapper2_instance,
        slot_register=0x00,
        extrom=bios.logo_bytes,
        slot0_ram=slot0_ram,
        slot0_ram_page=slot0_ram_page,
        slot1_sub_slot_enabled=wiring.slot1_sub_slot_enabled,
        mapper1_subslots=wiring.mapper1_subslots,
        slot2_sub_slot_enabled=wiring.slot2_sub_slot_enabled,
        mapper2_subslots=wiring.mapper2_subslots,
    )
    vdp = VDP()
    ppi = PPI(
        memory=memory, _input=input_state, control_read=spec.ppi_control_read
    )
    _register_common_io(io, spec, vdp, psg, ppi, _DEFAULT_IO_PORTS["vdp_tms9918a"])
    cpu = Z80(read_byte=memory.read, write_byte=memory.write,
              m1_wait_states=spec.m1_wait_states)
    return Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=wiring.scc, dac=dac,
        input=input_state,
        video_standard=spec.video_standard,
        keyboard_layout=spec.keyboard_type,
        ppi=ppi,
    )


def _build_fdc(config: _ResolvedConfig) -> "FloppyDisk":
    """Construct the FloppyDisk device from spec.fdc, mounting each disk in
    `config.disks` into the drive with its index (0 -> drive A)."""
    from msx.fdc.disk_drive import DiskDrive
    from msx.fdc.disk_image import DskDiskImage
    from msx.fdc.interface import SonyPhilipsInterface, TC8566AFInterface
    from msx.fdc.tc8566af import TC8566AF
    from msx.fdc.wd2793 import WD2793

    spec = config.spec
    assert spec.fdc is not None
    disk_rom = _rom(config, "disk")
    drives = [DiskDrive() for _ in range(spec.fdc.drives)]
    # controller/connection_style were validated by the document parser (FDC_*).
    device: SonyPhilipsInterface | TC8566AFInterface
    if spec.fdc.controller == "tc8566af":
        device = TC8566AFInterface(TC8566AF(drives=drives), drives, disk_rom=disk_rom)
    else:
        device = SonyPhilipsInterface(WD2793(), drives, disk_rom=disk_rom)
    # _resolve already rejected a disk for a drive the machine lacks.
    for idx in range(len(drives)):
        disk = config.disks.get(idx)
        if disk is None:
            continue
        image = DskDiskImage(disk.data, write_protected=disk.write_protected)
        device.mount(image, drive=idx)
    return device


def _build_msx2(
    config: _ResolvedConfig,
    bios: _BiosRoms,
    wiring: _ExtensionWiring,
    dac: MajutsushiMapper | None,
    input_state: InputLines,
    psg: PSG,
    io: IOBus,
) -> "Machine":
    from msx.machine import Machine  # local import to avoid circular dependency

    spec = config.spec
    sub_bytes = _rom(config, "sub") if spec.sub_rom_entry is not None else None

    # Flat (non-mapper) RAM machines (e.g. HB-F1XD) allocate their full RAM and
    # use the data-driven slot-3 sub-slot dispatch; mapper machines keep the
    # 32 KB + RamMapper wiring unchanged.
    if spec.flat_ram_subslot is not None and not spec.has_ram_mapper:
        ram_mapper: RamMapper | None = None
        ram_bytes = bytearray(spec.flat_ram_size_kb * 1024)
        flat_ram_subslot: int | None = spec.flat_ram_subslot
    else:
        ram_mapper = RamMapper(size_kb=spec.ram_mapper_size_kb) if spec.has_ram_mapper else None
        ram_bytes = bytearray(32768)
        flat_ram_subslot = None

    fdc_device = _build_fdc(config) if spec.fdc is not None else None
    slot0_ram, slot0_ram_page = _slot0_ram_buffer(spec)
    memory = Memory(
        rom=bios.main_bytes,
        ram=ram_bytes,
        mapper1=wiring.mapper1_instance,
        mapper2=wiring.mapper2_instance,
        slot_register=0x00,
        extrom=bios.logo_bytes,
        sub0_rom=sub_bytes,
        sub_slot_enabled=True,
        ram_mapper=ram_mapper,
        flat_ram_subslot=flat_ram_subslot,
        sub_rom_subslot=spec.sub_rom_subslot,
        fdc=fdc_device,
        fdc_subslot=spec.fdc_subslot,
        slot0_ram=slot0_ram,
        slot0_ram_page=slot0_ram_page,
        slot1_sub_slot_enabled=wiring.slot1_sub_slot_enabled,
        mapper1_subslots=wiring.mapper1_subslots,
        slot2_sub_slot_enabled=wiring.slot2_sub_slot_enabled,
        mapper2_subslots=wiring.mapper2_subslots,
    )
    vdp: V9938 | VDP = V9938() if spec.has_v9938 else VDP()
    rtc: RTC | None = None
    if spec.has_rtc:
        rtc_sram = config.media.get(MediaId.RTC_CMOS)
        rtc = RTC(sram=rtc_sram, start=config.rtc_initial_time)
    ppi = PPI(
        memory=memory, _input=input_state, control_read=spec.ppi_control_read
    )

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
              m1_wait_states=spec.m1_wait_states)
    return Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=wiring.scc, dac=dac,
        input=input_state,
        video_standard=spec.video_standard,
        keyboard_layout=spec.keyboard_type,
        fdc=fdc_device,
        rtc=rtc,
        ppi=ppi,
    )
