"""Value types that cross the core boundary (allium/core-frontend.allium).

Plain data only: the host and the frontend build these and hand them to the
core, or receive them from it. No module here reads files or the host clock.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.machine_spec import AnyExtensionOverlay, MachineSpec


@dataclass(frozen=True)
class RtcTime:
    """A calendar date and time, supplied by the host once to start the RTC.

    The weekday is not stored: the RTC derives it from the date.
    """

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int


# Start time when the host supplies none (a Saturday).
RTC_DEFAULT_START = RtcTime(2000, 1, 1, 0, 0, 0)

class MediaId(str, enum.Enum):
    """How the host and the core name each battery-backed image and each
    floppy drive (MachineConfig.media/disks, Machine.read_media, ...): a
    closed set whose values are the ids' strings. Format one with `.value`,
    never str() or an f-string, whose output differs between Python
    versions."""

    SLOT1_SRAM = "slot1.sram"
    SLOT2_SRAM = "slot2.sram"
    SLOT1_SUB0_SRAM = "slot1.0.sram"
    SLOT1_SUB1_SRAM = "slot1.1.sram"
    SLOT1_SUB2_SRAM = "slot1.2.sram"
    SLOT1_SUB3_SRAM = "slot1.3.sram"
    SLOT2_SUB0_SRAM = "slot2.0.sram"
    SLOT2_SUB1_SRAM = "slot2.1.sram"
    SLOT2_SUB2_SRAM = "slot2.2.sram"
    SLOT2_SUB3_SRAM = "slot2.3.sram"
    FMPAC_SRAM = "fmpac.sram"
    RTC_CMOS = "rtc.cmos"
    DRIVE_A = "drive_a"
    DRIVE_B = "drive_b"


def slot_sram_id(slot: int) -> MediaId:
    """Media id of cartridge slot `slot`'s mapper SRAM (1 -> SLOT1_SRAM).
    Raises ValueError for a slot other than 1 or 2."""
    return MediaId(f"slot{slot}.sram")


def subslot_sram_id(slot: int, sub: int) -> MediaId:
    """Media id of an expanded-slot device's SRAM (2, 1 -> SLOT2_SUB1_SRAM).
    Raises ValueError outside slots 1-2 / sub-slots 0-3."""
    return MediaId(f"slot{slot}.{sub}.sram")


def drive_media_id(index: int) -> MediaId:
    """Media id of floppy drive `index` (0 -> DRIVE_A). Raises ValueError
    for a drive other than 0 or 1."""
    if not 0 <= index <= 1:
        raise ValueError(f"no media id for floppy drive {index}")
    return MediaId("drive_" + chr(ord("a") + index))


@dataclass(frozen=True)
class RgbFrame:
    """One rendered frame as packed RGB24, row-major (width * height * 3 bytes).

    `width` is 256 or 512; `height` is the constant output height (212).
    """

    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class FrameOutput:
    """What Machine.step_frame returns for one frame.

    `video` and `audio` are guaranteed only until the next step_frame call
    (allium/core-frontend.allium FrameOutputValidUntilNextStep); a frontend
    that keeps them longer copies them. Here they are fresh immutable bytes,
    which satisfies that, but a native port may reuse its buffers.
    """

    video: RgbFrame | None   # absent when the frame was not rendered
    audio: bytes             # signed 16-bit LE mono PCM, before any output filter
    frame_number: int


@dataclass(frozen=True)
class DiskMedium:
    """A disk image the host read, to mount at build time."""

    data: bytes
    write_protected: bool = False
    # The host's name for the image's file; only recorded (see DskDiskImage).
    path: Path | None = None


@dataclass
class MachineConfig:
    """Everything build_machine needs, as values: no path is opened and no
    database is consulted while building.

    `roms` holds ROM images by role: "main", "logo" (optional), "sub"
    (MSX2 SUB ROM), "disk" (FDC DISK ROM), "io_device" (a machine-declared
    I/O device's ROM), and per cartridge slot n: "slot<n>.ext" (a flat
    extension overlay's device ROM), "slot<n>.io_device" (an expanded
    overlay's I/O device ROM), "slot<n>.<sub>" (an expanded overlay's
    sub-slot device ROM). `media` holds initial battery-backed images by
    media id ("slot<n>.sram", "slot<n>.<sub>.sram", "fmpac.sram",
    "rtc.cmos"); an absent id starts blank. `disks` holds the images to mount
    by drive id ("drive_a", "drive_b"). `mapper1`/`mapper2` are resolved
    mapper names, never "auto".
    """

    spec: MachineSpec
    roms: dict[str, bytes] = field(default_factory=dict)
    cartridge1: bytes | None = None
    mapper1: str = "Mirrored"
    cartridge2: bytes | None = None
    mapper2: str = "Mirrored"
    extension_overlay1: AnyExtensionOverlay | None = None
    extension_overlay2: AnyExtensionOverlay | None = None
    media: dict[MediaId, bytes] = field(default_factory=dict)
    disks: dict[MediaId, DiskMedium] = field(default_factory=dict)
    rtc_initial_time: RtcTime | None = None
