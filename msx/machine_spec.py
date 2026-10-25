"""Resolved machine-wiring values the core builds a Machine from.

`MachineSpec` and the extension-overlay dataclasses are the resolved form of
a machine: host/machine_config.py fills them from config/machines/*.yaml and
config/extensions/*.yaml, and msx/machine_config.py parses them from a
MachineConfig document. Their `Path` fields (`rom_base_dir`,
`sram_save_path`) are host data, optional and keyword-only: the host sets
them when it resolves the YAML, the core never opens them, and they are None
in a spec parsed from a document (which carries no host paths).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from msx.ppi import PpiControlRead
from msx.vdp._geometry import VideoStandard


class KeyboardLayout(str, enum.Enum):
    """A machine's keyboard matrix layout; the values are the machine-YAML
    spellings (ppi8255 `keyboard_type`). Format one with `.value`."""

    INTERNATIONAL = "int"
    JAPANESE = "jp"


class MachineLoadError(Exception):
    """Raised when a machine definition or MachineConfig is invalid or
    self-conflicting (YAML validation on the host; wiring conflicts and missing
    ROM roles in build_machine)."""


@dataclass
class _RomEntry:
    file: str
    size_kb: int
    pages: list[int]
    sha1: str | None = None


@dataclass
class _Slot0RamEntry:
    """Resolved slot-0 flat RAM region (e.g. Sony HB-10's 16 KB page 3),
    coexisting with slot 0's main/logo ROM. Named to avoid a positional
    tuple, matching _RomEntry/_FdcDef's own precedent."""
    size_kb: int
    page: int


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
    # Host data (see the module docstring); None when parsed from a document.
    rom_base_dir: Path | None = field(default=None, kw_only=True)

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

    # Optional flat RAM region in slot 0, coexisting with main_rom_entry/
    # logo_rom_entry above (e.g. Sony HB-10's 16 KB page 3). None for every
    # machine that doesn't declare one (the default, unaffected by this
    # field's addition).
    slot0_ram_entry: _Slot0RamEntry | None = None

    # Keyboard layout resolved from the ppi8255 device
    keyboard_type: KeyboardLayout = KeyboardLayout.INTERNATIONAL

    # What a port 0xAB read returns, resolved from the ppi8255 device
    # (open_bus on the S1985 machines, bus_latch on the S3527 ones -- see
    # msx/ppi.py).
    ppi_control_read: PpiControlRead = PpiControlRead.CONTROL

    # RAM mapper's configured size (MSX2, when has_ram_mapper is True), resolved
    # from the mapper: standard sub-slot's size_kb -- default 128 preserves
    # every machine configured before this became configurable.
    ram_mapper_size_kb: int = 128

    # The --machine value / config/machines/<machine_id>.yaml stem this spec
    # was loaded for (set by load_machine_spec). Used for per-machine (as
    # opposed to per-cartridge) persistence paths, e.g. each RTC-equipped
    # machine's own CMOS RAM file. Defaults to "unknown" for direct/test
    # construction, which never goes through load_machine_spec and doesn't
    # need a real id (each test isolates its own saves/ via tmp_path).
    machine_id: str = "unknown"

    # I/O port ranges from device YAML: device_id -> (first_port, last_port)
    device_io_ports: dict[str, tuple[int, int]] = field(default_factory=dict)

    # Slot-independent global I/O device declared directly on this machine
    # (e.g. a built-in Kanji font ROM), as opposed to one supplied by an
    # --extension overlay. Same resolved shape as an overlay's own io_device
    # (_ExpandedSubslotDevice) -- see load_machine_spec's io_device parsing
    # and build_machine's KanjiRom construction, either of which SHALL NOT
    # both resolve a device at once (build_machine raises MachineLoadError).
    io_device: _ExpandedSubslotDevice | None = None

    # Video standard from the YAML (NTSC default); the frame timing derives
    # from it (VideoStandard.cycles_per_frame / lines_per_frame).
    video_standard: VideoStandard = VideoStandard.NTSC

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

    @property
    def cycles_per_frame(self) -> int:
        """Z80 T-states per frame, from the video standard."""
        return self.video_standard.cycles_per_frame

    @property
    def lines_per_frame(self) -> int:
        """Scanlines per frame, from the video standard."""
        return self.video_standard.lines_per_frame


@dataclass
class ExtensionOverlay:
    """Resolved extension overlay (config/extensions/<id>.yaml): the device
    kind plus optional ROM/SRAM paths for the --extension flag. Not a machine
    spec — applied on top of one, in cartridge slot 1 or 2.

    `rom_base_dir`/`rom_entry`/`sram_save_path` are None for a device with no
    ROM file and no persisted SRAM (e.g. "scc_i_cart", whose RAM starts
    blank).
    """

    device: str
    rom_base_dir: Path | None = field(default=None, kw_only=True)
    rom_entry: _RomEntry | None = None
    sram_save_path: Path | None = field(default=None, kw_only=True)


@dataclass
class _ExpandedSubslotDevice:
    """One sub-slot's (or the io_device's) resolved device declaration
    within an expanded extension overlay -- same shape as ExtensionOverlay
    above, since a sub-slot declaration uses the identical device/rom/sram
    schema a flat overlay's top level does (see cart-extension-overlay's
    "Extension overlay shape: flat vs expanded" Requirement), plus a
    `size_kb` alternative to `rom`/`sram` for a size-configured device kind
    (e.g. `ram_mapper`, which needs no ROM file)."""

    device: str
    rom_base_dir: Path | None = field(default=None, kw_only=True)
    rom_entry: _RomEntry | None = None
    sram_save_path: Path | None = field(default=None, kw_only=True)
    size_kb: int | None = None


@dataclass
class ExpandedExtensionOverlay:
    """Resolved expanded-shape extension overlay (config/extensions/<id>.yaml
    with `shape: expanded`, e.g. hbi_j1.yaml): its cartridge slot becomes an
    expanded slot with one device per declared sub-slot index, plus an
    optional slot-independent global I/O device (e.g. the Kanji-ROM font
    device, which has no cartridge-slot address decode of its own)."""

    subslots: dict[int, _ExpandedSubslotDevice]
    io_device: _ExpandedSubslotDevice | None = None


# Either overlay shape, as carried by MachineConfig.extension_overlay1/2.
AnyExtensionOverlay = ExtensionOverlay | ExpandedExtensionOverlay


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
