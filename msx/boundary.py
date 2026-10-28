"""Value types that cross the core boundary (allium/core-frontend.allium).

Plain data only: the host and the frontend build these and hand them to the
core, or receive them from it. No module here reads files or the host clock.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


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
    DRIVE_C = "drive_c"
    DRIVE_D = "drive_d"


def slot_sram_id(slot: int) -> MediaId:
    """Media id of cartridge slot `slot`'s mapper SRAM (1 -> SLOT1_SRAM).
    Raises ValueError for a slot other than 1 or 2."""
    return MediaId(f"slot{slot}.sram")


def subslot_sram_id(slot: int, sub: int) -> MediaId:
    """Media id of an expanded-slot device's SRAM (2, 1 -> SLOT2_SUB1_SRAM).
    Raises ValueError outside slots 1-2 / sub-slots 0-3."""
    return MediaId(f"slot{slot}.{sub}.sram")


def drive_media_id(index: int) -> MediaId:
    """Media id of floppy drive `index` (0 -> DRIVE_A ... 3 -> DRIVE_D, the
    four drives a TC8566AF selects). Raises ValueError for any other index."""
    if not 0 <= index <= 3:
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


# Why step_frame or step stopped (StopInfo.reason, FrameOutput.stop_reason).
# STOP_BREAKPOINT covers the PC breakpoints, the run-to target, step out,
# HALT with interrupts off, SP out of range and the frame breakpoint.
STOP_NONE = 0
STOP_BREAKPOINT = 1
STOP_WATCHPOINT = 2
STOP_REQUESTED = 3

# The access kind of a watchpoint hit (StopInfo.watch_access).
WATCH_READ = 1
WATCH_WRITE = 2


@dataclass(frozen=True)
class StopInfo:
    """The most recent stop, as the core API's last-stop query returns it
    (msx/core_api.py). `pc` is the instruction's address for a watchpoint
    hit and the current PC otherwise; the watch fields are 0 unless
    `reason` is STOP_WATCHPOINT."""

    reason: int = STOP_NONE
    pc: int = 0
    watch_addr: int = 0
    watch_value: int = 0
    watch_access: int = 0


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
    stop_reason: int = STOP_NONE  # nonzero when the call stopped at a break
    frame_complete: bool = True   # false when the frame continues on the next call


@dataclass(frozen=True)
class CpuRegisters:
    """The Z80 register file as the core API's register query returns it
    (msx/core_api.py). IFF1 and IFF2 are 0 or 1, IM is 0-2."""

    AF: int
    BC: int
    DE: int
    HL: int
    AF_: int
    BC_: int
    DE_: int
    HL_: int
    IX: int
    IY: int
    SP: int
    PC: int
    I: int  # noqa: E741 - Z80 interrupt vector register (canonical name)
    R: int
    IFF1: int
    IFF2: int
    IM: int


VDP_KIND_TMS9918A = 0
VDP_KIND_V9938 = 1


@dataclass(frozen=True)
class VdpInfo:
    """The VDP state the core API's VDP query returns (msx/core_api.py).

    `regs` holds the control registers (8 on the TMS9918A, R#0-R#27 on the
    V9938). On the TMS9918A `cmd_regs` and `palette` are empty, `status` is
    S#0 and the command fields are 0. On the V9938 `cmd_regs` is R#32-R#46,
    `status` is S#0-S#9 (V9938.peek_status_registers) and `palette` holds 16
    9-bit entries.
    """

    kind: int
    regs: bytes
    cmd_regs: bytes
    status: bytes
    palette: tuple[int, ...]
    cmd_x: int = 0
    cmd_y: int = 0
    cmd_nx: int = 0
    cmd_ny: int = 0
    cmd_remaining: int = 0


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

    `document` is the canonical MachineConfig document (msx/machine_config.py,
    msx/machine_config.schema.json): the resolved machine, cartridges with
    their resolved mappers, and extension overlays, every ROM a reference.
    `roms` holds the referenced ROM images keyed by SHA1 (lowercase hex).
    The rest are run inputs, outside the document: `media` holds initial
    battery-backed images by media id ("slot<n>.sram", "slot<n>.<sub>.sram",
    "fmpac.sram", "rtc.cmos"; an absent id starts blank), `disks` the images
    to mount by drive id ("drive_a", "drive_b"), and `rtc_initial_time` the
    RTC's start time.
    """

    document: bytes
    roms: dict[str, bytes] = field(default_factory=dict)
    media: dict[MediaId, bytes] = field(default_factory=dict)
    disks: dict[MediaId, DiskMedium] = field(default_factory=dict)
    rtc_initial_time: RtcTime | None = None
