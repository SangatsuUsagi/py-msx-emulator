"""Machine builder: wires a Machine from a MachineConfig.

The core half of machine loading. host/machine_config.py parses the YAML,
reads every ROM, SRAM and disk file and resolves "auto" mappers into a
MachineConfig (msx/boundary.py); build_machine() only constructs and wires
devices from those bytes. It reads no file and looks up no database.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from msx.fdc.interface import FloppyDisk
    from msx.machine import Machine

from msx.boundary import RTC_DEFAULT_START, MachineConfig
from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.fmpac import SRAM_SIZE as FMPAC_SRAM_SIZE
from msx.fmpac import FmPac
from msx.input import InputState
from msx.io import IOBus
from msx.kanji import KanjiRom
from msx.machine_spec import (
    MachineLoadError,
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    _ExtensionOverlay,
)
from msx.mapper import (
    HALNOTE_SRAM_SIZE,
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
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
from msx.rtc import RTC
from msx.rtc import SRAM_SIZE as RTC_SRAM_SIZE
from msx.scc import SCC
from msx.vdp.tracer import Tracer
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP, VdpDevice

# ---------------------------------------------------------------------------
# Mapper helpers
# ---------------------------------------------------------------------------

_SRAM_SIZES: dict[str, int] = {
    "ASCII8SRAM2": 2048,
    "ASCII8SRAM8": 8192,
    "ASCII16SRAM2": 2048,
    "ASCII16SRAM8": 8192,
    "KoeiSRAM32": 32768,
    "GameMaster2": 8192,
}


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


def _rom(config: MachineConfig, role: str) -> bytes:
    """A required ROM image from the config (see MachineConfig.roms)."""
    rom = config.roms.get(role)
    if rom is None:
        raise MachineLoadError(f"MachineConfig has no {role!r} ROM")
    return rom


def _empty_subslots() -> list[Mapper | None]:
    """A fresh (never shared) length-4 all-None sub-slot array -- the
    default every build_machine()/_build_msx1()/_build_msx2() call site
    falls back to when no expanded overlay is active on that slot."""
    return [None, None, None, None]


@dataclass
class _ExtensionWiring:
    """Every device/flag `_wire_extension_overlay` can produce from a
    machine-declared `io_device` and/or a `--extension1`/`--extension2`
    overlay, bundled for `build_machine` to unpack into `Memory` constructor
    arguments and `Machine` attributes. `mapper_instance`/`mapper2_instance`
    are each independently either unchanged (no overlay on that slot) or
    replaced by that slot's flat overlay's device."""

    mapper_instance: Mapper
    mapper2_instance: Mapper
    scc: SCC | None
    slot1_sub_slot_enabled: bool
    mapper1_subslots: list[Mapper | None]
    slot2_sub_slot_enabled: bool
    mapper2_subslots: list[Mapper | None]
    fmpac_device: FmPac | None
    halnote_device: HalnoteMapper | None
    kanji_device: KanjiRom | None


def _wire_machine_io_device(config: MachineConfig) -> KanjiRom | None:
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
    halnote_device: HalnoteMapper | None
    kanji_device: KanjiRom | None


@dataclass
class _ExpandedSubslotDeviceWiring:
    """One expanded-overlay sub-slot's constructed device, from
    `_build_expanded_subslot_device`. `halnote_device` is populated only when
    `mapper_instance` is that sub-slot's `HalnoteMapper`
    (`None` for every other device kind), so the caller can still track
    `Machine`'s single `halnote_cart` field without this dataclass needing a
    separate "which kind was this" flag -- `halnote_device is not None` already
    says so unambiguously."""

    mapper_instance: Mapper
    halnote_device: HalnoteMapper | None


def _build_expanded_subslot_device(
    config: MachineConfig, slot: int, subslot: _ExpandedSubslotDevice, index: int
) -> _ExpandedSubslotDeviceWiring:
    """Construct one expanded-overlay sub-slot's device.

    Raises:
        MachineLoadError: If `subslot.device` passed load-time validation
            (`_KNOWN_EXPANDED_SUBSLOT_DEVICES`) but has no construction case
            here (an internal consistency error between the two).
    """
    spec = config.spec
    rom_role = f"slot{slot}.{index}"
    if subslot.device == "halnote":
        halnote_sram = config.media.get(f"{rom_role}.sram")
        halnote_device = HalnoteMapper(
            rom=_rom(config, rom_role),
            sram=bytearray(halnote_sram) if halnote_sram is not None
            else bytearray(HALNOTE_SRAM_SIZE),
        )
        return _ExpandedSubslotDeviceWiring(halnote_device, halnote_device)
    if subslot.device == "flat_rom":
        mapper = FixedPageMapper(rom=_rom(config, rom_role), base=0x4000)
        return _ExpandedSubslotDeviceWiring(mapper, None)
    if subslot.device == "ascii8":
        return _ExpandedSubslotDeviceWiring(Ascii8Mapper(rom=_rom(config, rom_role)), None)
    if subslot.device == "ascii16":
        return _ExpandedSubslotDeviceWiring(Ascii16Mapper(rom=_rom(config, rom_role)), None)
    if subslot.device == "ram_mapper":
        assert subslot.size_kb is not None  # guaranteed by load-time validation
        return _ExpandedSubslotDeviceWiring(RamMapper(size_kb=subslot.size_kb), None)
    raise MachineLoadError(
        f"{spec.machine_id}: internal error -- expanded overlay sub-slot "
        f"{index} device {subslot.device!r} passed load-time validation "
        f"(_KNOWN_EXPANDED_SUBSLOT_DEVICES) but has no build_machine "
        "construction case"
    )


def _wire_expanded_overlay(
    config: MachineConfig,
    slot: int,
    extension_overlay: _ExpandedExtensionOverlay,
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
    (0xFC-0xFF), and two independent HalnoteMapper instances would otherwise
    both claim `Machine`'s single `halnote_cart` field.

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
            `_KNOWN_EXPANDED_SUBSLOT_DEVICES` and this function).
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
            "real hardware never has two HBI-J1 MSX-JE cartridges attached at once, "
            "and Machine has a single halnote_cart field"
        )
    subslots: list[Mapper | None] = _empty_subslots()
    halnote_device: HalnoteMapper | None = None
    for index, subslot in extension_overlay.subslots.items():
        built = _build_expanded_subslot_device(config, slot, subslot, index)
        subslots[index] = built.mapper_instance
        if built.halnote_device is not None:
            halnote_device = built.halnote_device
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
    return _ExpandedOverlayWiring(
        subslots=subslots,
        halnote_device=halnote_device,
        kanji_device=kanji_device,
    )


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
    config: MachineConfig,
    slot: int,
    extension_overlay: _ExtensionOverlay,
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
            validation (`_KNOWN_EXTENSION_DEVICES`) but has no construction
            case here (an internal consistency error between the two), if
            `scc_i_cart` is selected while a KonamiSCC cartridge mapper or
            another slot's `scc_i_cart` already resolved a chip (see
            above), or if `fmpac` is selected while the other cartridge
            slot's `fmpac` extension already resolved one.
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
        fmpac_sram = config.media.get("fmpac.sram")
        fmpac_device = FmPac(
            rom=_rom(config, f"slot{slot}.ext"),
            opll=Opll(),
            sram=bytearray(fmpac_sram) if fmpac_sram is not None
            else bytearray(FMPAC_SRAM_SIZE),
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
        # same `scc` variable by build_machine before either overlay is
        # wired (see build_machine's "Either slot's KonamiSCC mapper shares
        # the machine's single SCC chip" handling), so this single check
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
        "(_KNOWN_EXTENSION_DEVICES) but has no build_machine construction case"
    )


def _wire_extension_overlay(
    config: MachineConfig,
    mapper_instance: Mapper,
    mapper2_instance: Mapper,
    scc: SCC | None,
) -> _ExtensionWiring:
    """Resolve every device a machine-declared `io_device` and/or either
    slot's `--extensionN` overlay contribute, and how they change that
    slot's dispatch. `mapper_instance`/`mapper2_instance`/`scc` are the
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
    extension_overlay1 = config.extension_overlay1
    extension_overlay2 = config.extension_overlay2
    kanji_device = _wire_machine_io_device(config)
    slot1_sub_slot_enabled = False
    mapper1_subslots: list[Mapper | None] = _empty_subslots()
    slot2_sub_slot_enabled = False
    mapper2_subslots: list[Mapper | None] = _empty_subslots()
    halnote_device: HalnoteMapper | None = None
    fmpac_device: FmPac | None = None
    slot1_ram_mapper_claimed = False
    slot1_halnote_claimed = False

    # Slot 1's overlay, if any: unconditionally occupies primary slot 1,
    # replacing whatever slot-1 cartridge mapper was resolved above. Keep
    # this block and slot 2's below in sync -- they are structurally
    # identical (each resolves its own slot's overlay, and this block
    # additionally records slot1_ram_mapper_claimed/slot1_halnote_claimed
    # for slot 2's block to pass in as other_slot_ram_mapper_claimed/
    # other_slot_halnote_claimed), differing only in which slot's
    # variables they populate. A future third cross-slot conflict type
    # needs adding to both blocks.
    if isinstance(extension_overlay1, _ExpandedExtensionOverlay):
        expanded1 = _wire_expanded_overlay(config, 1, extension_overlay1, kanji_device)
        slot1_sub_slot_enabled = True
        mapper1_subslots = expanded1.subslots
        if expanded1.halnote_device is not None:
            halnote_device = expanded1.halnote_device
            slot1_halnote_claimed = True
        kanji_device = expanded1.kanji_device
        slot1_ram_mapper_claimed = any(_is_ram_mapper(sub) for sub in mapper1_subslots)
    elif extension_overlay1 is not None:
        flat1 = _wire_flat_overlay(config, 1, extension_overlay1, scc)
        mapper_instance = flat1.mapper_instance
        scc = flat1.scc
        if flat1.fmpac_device is not None:
            fmpac_device = flat1.fmpac_device

    # Slot 2's overlay, if any: unconditionally occupies primary slot 2,
    # replacing whatever slot-2 cartridge mapper was resolved above.
    if isinstance(extension_overlay2, _ExpandedExtensionOverlay):
        expanded2 = _wire_expanded_overlay(
            config, 2, extension_overlay2, kanji_device,
            other_slot_ram_mapper_claimed=slot1_ram_mapper_claimed,
            other_slot_halnote_claimed=slot1_halnote_claimed,
        )
        slot2_sub_slot_enabled = True
        mapper2_subslots = expanded2.subslots
        if expanded2.halnote_device is not None:
            halnote_device = expanded2.halnote_device
        kanji_device = expanded2.kanji_device
    elif extension_overlay2 is not None:
        flat2 = _wire_flat_overlay(config, 2, extension_overlay2, scc, existing_fmpac=fmpac_device)
        mapper2_instance = flat2.mapper_instance
        scc = flat2.scc
        if flat2.fmpac_device is not None:
            fmpac_device = flat2.fmpac_device

    return _ExtensionWiring(
        mapper_instance=mapper_instance,
        mapper2_instance=mapper2_instance,
        scc=scc,
        slot1_sub_slot_enabled=slot1_sub_slot_enabled,
        mapper1_subslots=mapper1_subslots,
        slot2_sub_slot_enabled=slot2_sub_slot_enabled,
        mapper2_subslots=mapper2_subslots,
        fmpac_device=fmpac_device,
        halnote_device=halnote_device,
        kanji_device=kanji_device,
    )


@dataclass
class _BiosRoms:
    """Slot 0's main/logo ROM bytes, resolved once (independent of either
    cartridge slot) for `build_machine` to unpack before cartridge-slot
    resolution and dispatching into `_build_msx1`/`_build_msx2`."""

    main_bytes: bytes
    logo_bytes: bytes | None


def _bios_roms(config: MachineConfig) -> _BiosRoms:
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
    sram_data = bytearray(sram) if sram is not None and mapper in _SRAM_SIZES else None
    mapper_instance: Mapper = _make_mapper(mapper, cartridge, scc=scc, sram=sram_data)
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


def _resolve_shared_dac(
    spec: MachineSpec, mapper_instance: Mapper, mapper2_instance: Mapper
) -> MajutsushiMapper | None:
    """Cross-slot Majutsushi-DAC-sharing check between the two cartridge
    slots' final mapper instances. Takes the post-extension-overlay-wiring
    instances specifically (not `_resolve_cartridge_slot`'s own results),
    since an extension overlay replacing a slot's mapper (e.g. --extension1
    fmpac over a Majutsushi cartridge) removes that slot's own DAC along
    with it. `Machine` has a single `dac` field and real hardware never has
    two Majutsushi-style DAC cartridges attached at once."""
    dac1 = mapper_instance if _is_majutsushi_mapper(mapper_instance) else None
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
    io: IOBus,
    dac: MajutsushiMapper | None,
    wiring: _ExtensionWiring,
) -> None:
    """Wire closures and device/save-path attributes onto an already-built
    Machine: the CPU PC accessor IOBus needs, the cycle-count accessor a
    Majutsushi DAC and the PPI key click need, and the FM-PAC/Halnote/Kanji
    attributes `build_machine`'s extension-overlay resolution produced."""
    io._get_pc = lambda: machine.cpu.registers.PC
    # PORT-NOTE: these cycle closures capture `machine`, which owns the
    #   devices -- a cycle in Rust/C++. A port should pass the cycle into the
    #   write call (or share a narrow cycle counter) instead.
    if dac is not None:
        dac._get_cycle = lambda: machine.cycle_count
    if machine.ppi is not None:
        machine.ppi._get_cycle = lambda: machine.cycle_count
    # PSG software PCM: timestamp register writes so generate_samples can place
    # them at their sub-frame sample positions (mirrors the DAC wiring).
    machine.psg._machine = machine
    machine.fmpac = wiring.fmpac_device
    machine.halnote_cart = wiring.halnote_device
    machine.kanji = wiring.kanji_device


def build_machine(
    config: MachineConfig,
    logger: DebugLogger | None = None,
    tracer: Tracer | None = None,
) -> "Machine":
    """Build a Machine from a MachineConfig (host/machine_config.py's
    load_machine_config produces one). Reads no file, resolves no path and
    looks up no database.

    Args:
        config: The resolved spec, ROM images, cartridges and their resolved
            mappers, extension overlays, initial SRAM images, disks to mount
            and RTC start time.
        logger: Optional DebugLogger for diagnostic output.
        tracer: Optional VDP register write tracer (V9938 only).

    Returns:
        A fully-wired Machine ready for emulation.

    Raises:
        MachineLoadError: If a required ROM is missing from `config.roms`, if
            both slots' mappers/extensions resolve to KonamiSCC-family SCC
            chips, if both slots' mappers resolve to Majutsushi, if both
            slots' extensions resolve to fmpac, or if both slots' expanded
            overlays declare a 'ram_mapper' or 'halnote' sub-slot.
    """
    from msx.machine import Machine  # local import to avoid circular dependency

    spec = config.spec
    bios = _bios_roms(config)
    main_bytes = bios.main_bytes
    logo_bytes = bios.logo_bytes

    slot1 = _resolve_cartridge_slot(
        config.mapper1, config.cartridge1, config.media.get("slot1.sram")
    )
    slot2 = _resolve_cartridge_slot(
        config.mapper2, config.cartridge2, config.media.get("slot2.sram")
    )
    mapper_instance = slot1.mapper_instance
    mapper2_instance = slot2.mapper_instance
    scc = _resolve_shared_scc(spec, slot1.scc, slot2.scc)

    # Machine-declared io_device and either slot's --extensionN overlay:
    # resolved together (an overlay's io_device conflicts with a
    # machine-declared one) and, when present, unconditionally occupy their
    # target slot, replacing whatever cartridge mapper was resolved above --
    # see _wire_extension_overlay.
    wiring = _wire_extension_overlay(config, mapper_instance, mapper2_instance, scc)
    mapper_instance = wiring.mapper_instance
    mapper2_instance = wiring.mapper2_instance
    scc = wiring.scc
    slot1_sub_slot_enabled = wiring.slot1_sub_slot_enabled
    mapper1_subslots = wiring.mapper1_subslots
    slot2_sub_slot_enabled = wiring.slot2_sub_slot_enabled
    mapper2_subslots = wiring.mapper2_subslots
    # Resolved after extension-overlay wiring (see _resolve_shared_dac):
    # an extension overlay replacing a slot's mapper (e.g. --extension1
    # fmpac over a Majutsushi cartridge) removes that slot's own DAC along
    # with it, and either slot's mapper can resolve to Majutsushi.
    dac = _resolve_shared_dac(spec, mapper_instance, mapper2_instance)

    input_state = InputState(keyboard_type=spec.keyboard_type)
    psg = PSG(_input=input_state)
    io = IOBus(_logger=logger)
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
    for sub_mapper in (*mapper1_subslots, *mapper2_subslots):
        if _is_ram_mapper(sub_mapper):
            mm_s, mm_e = _io_range(spec, "memory_mapper_standard",
                                    _DEFAULT_IO_PORTS["memory_mapper_standard"])
            io.register_read(mm_s, mm_e, sub_mapper.read_port)
            io.register_write(mm_s, mm_e, sub_mapper.write_port)

    if spec.generation == "msx2":
        machine = _build_msx2(
            config=config,
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
            tracer=tracer,
            machine_cls=Machine,
            slot1_sub_slot_enabled=slot1_sub_slot_enabled,
            mapper1_subslots=mapper1_subslots,
            slot2_sub_slot_enabled=slot2_sub_slot_enabled,
            mapper2_subslots=mapper2_subslots,
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
            slot1_sub_slot_enabled=slot1_sub_slot_enabled,
            mapper1_subslots=mapper1_subslots,
            slot2_sub_slot_enabled=slot2_sub_slot_enabled,
            mapper2_subslots=mapper2_subslots,
        )

    _wire_post_construction_attrs(machine, io, dac, wiring)
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
    slot1_sub_slot_enabled: bool = False,
    mapper1_subslots: list[Mapper | None] | None = None,
    slot2_sub_slot_enabled: bool = False,
    mapper2_subslots: list[Mapper | None] | None = None,
) -> "Machine":
    slot0_ram, slot0_ram_page = _slot0_ram_buffer(spec)
    memory = Memory(
        rom=main_bytes,
        ram=bytearray(spec.ram_size_kb * 1024),
        _mapper=mapper_instance,
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logo_bytes,
        rom_name=spec.main_rom_entry.file,
        slot0_ram=slot0_ram,
        slot0_ram_page=slot0_ram_page,
        slot1_sub_slot_enabled=slot1_sub_slot_enabled,
        _mapper1_subslots=mapper1_subslots if mapper1_subslots is not None
        else _empty_subslots(),
        slot2_sub_slot_enabled=slot2_sub_slot_enabled,
        _mapper2_subslots=mapper2_subslots if mapper2_subslots is not None
        else _empty_subslots(),
    )
    vdp = VDP(_logger=logger)
    ppi = PPI(
        memory=memory, _input=input_state, control_read=spec.ppi_control_read
    )
    _register_common_io(io, spec, vdp, psg, ppi, _DEFAULT_IO_PORTS["vdp_tms9918a"])
    cpu = Z80(read_byte=memory.read, write_byte=memory.write,
              m1_wait_states=spec.m1_wait_states, _logger=logger)
    return machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        video_standard=spec.video_standard,
        ppi=ppi,
    )


def _build_fdc(config: MachineConfig) -> "FloppyDisk":
    """Construct the FloppyDisk device from spec.fdc, mounting each disk in
    `config.disks` into the drive with the matching id ("drive_a" -> drive A)."""
    from msx.fdc.disk_drive import DiskDrive
    from msx.fdc.disk_image import DskDiskImage
    from msx.fdc.interface import SonyPhilipsInterface, TC8566AFInterface
    from msx.fdc.tc8566af import TC8566AF
    from msx.fdc.wd2793 import WD2793
    from msx.machine import drive_media_id

    spec = config.spec
    assert spec.fdc is not None
    disk_rom = _rom(config, "disk")
    drives = [DiskDrive() for _ in range(spec.fdc.drives)]
    # controller/connection_style were validated by the loader (_SUPPORTED_FDC_*).
    device: SonyPhilipsInterface | TC8566AFInterface
    if spec.fdc.controller == "tc8566af":
        device = TC8566AFInterface(TC8566AF(drives=drives), drives, disk_rom=disk_rom)
    else:
        device = SonyPhilipsInterface(WD2793(), drives, disk_rom=disk_rom)
    for idx in range(max(len(drives), 2)):
        disk = config.disks.get(drive_media_id(idx))
        if disk is None:
            continue
        if idx >= len(drives):
            print(
                f"warning: --fdd{idx + 1} given but machine has only "
                f"{len(drives)} drive(s); ignoring",
                file=sys.stderr,
            )
            continue
        image = DskDiskImage(disk.data, write_protected=disk.write_protected, path=disk.path)
        device.mount(image, drive=idx)
    return device


def _build_msx2(
    *,
    config: MachineConfig,
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
    tracer: Tracer | None,
    machine_cls: "type[Machine]",
    slot1_sub_slot_enabled: bool = False,
    mapper1_subslots: list[Mapper | None] | None = None,
    slot2_sub_slot_enabled: bool = False,
    mapper2_subslots: list[Mapper | None] | None = None,
) -> "Machine":
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
        slot0_ram=slot0_ram,
        slot0_ram_page=slot0_ram_page,
        slot1_sub_slot_enabled=slot1_sub_slot_enabled,
        _mapper1_subslots=mapper1_subslots if mapper1_subslots is not None
        else _empty_subslots(),
        slot2_sub_slot_enabled=slot2_sub_slot_enabled,
        _mapper2_subslots=mapper2_subslots if mapper2_subslots is not None
        else _empty_subslots(),
    )
    vdp: V9938 | VDP = V9938(_logger=logger) if spec.has_v9938 else VDP(_logger=logger)
    rtc: RTC | None = None
    if spec.has_rtc:
        rtc_sram = config.media.get("rtc.cmos")
        start = config.rtc_initial_time
        rtc = RTC(
            sram=bytearray(rtc_sram) if rtc_sram is not None else bytearray(RTC_SRAM_SIZE),
            start=start if start is not None else RTC_DEFAULT_START,
        )
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
              m1_wait_states=spec.m1_wait_states, _logger=logger)
    machine = machine_cls(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc, dac=dac,
        input=input_state, _logger=logger,
        video_standard=spec.video_standard,
        fdc=fdc_device,
        rtc=rtc,
        ppi=ppi,
    )
    if rtc is not None:
        rtc._get_cycle = lambda: machine.cycle_count
        rtc._start_cycle = machine.cycle_count
    if tracer is not None and isinstance(vdp, V9938):
        vdp.tracer = tracer
        vdp._get_pc = lambda: machine.cpu.instruction_pc
        vdp._get_cycle = lambda: machine.cycle_count
        # The tracer reads the VDP frame count directly (V9938.write_port passes
        # self._frame_count), so no _get_frame getter is needed.
    return machine
