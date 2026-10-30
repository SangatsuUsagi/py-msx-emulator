"""Value types that cross the core boundary (allium/core-frontend.allium).

Plain data only: the host and the frontend build these and hand them to the
core, or receive them from it. No module here reads files or the host clock.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class VideoStandard(enum.IntEnum):
    """Video standard of a machine, an integer at the core boundary. The
    machine YAML and the canonical MachineConfig document spell it with
    `spelling` and `from_spelling`. The core's frame timing is derived from
    it (msx/vdp/_geometry.py lines_per_frame, cycles_per_frame)."""

    NTSC = 0
    PAL = 1

    @property
    def spelling(self) -> str:
        """The text form of the machine YAML and the canonical document."""
        return _VIDEO_STANDARD_SPELLINGS[self]

    @classmethod
    def from_spelling(cls, text: str) -> VideoStandard:
        """The member spelled `text`. Raises ValueError for another text."""
        for member in cls:
            if _VIDEO_STANDARD_SPELLINGS[member] == text:
                return member
        raise ValueError(f"unknown video standard {text!r}")

    # Each property names every member (a port's exhaustive `match`), so a
    # new standard fails loudly instead of silently taking NTSC values.

    @property
    def target_fps(self) -> int:
        """Real-time pacing target in frames per second, an integer: a
        round, host-vsync-friendly rate, not the exact field rate (NTSC
        59.92 Hz / PAL 50.16 Hz) -- see openspec/specs/app-frame-timer/spec.md's
        Purpose."""
        if self is VideoStandard.NTSC:
            return 60
        if self is VideoStandard.PAL:
            return 50
        raise AssertionError(f"unhandled video standard {self!r}")


_VIDEO_STANDARD_SPELLINGS: dict[VideoStandard, str] = {
    VideoStandard.NTSC: "ntsc",
    VideoStandard.PAL: "pal",
}


class KeyboardLayout(enum.IntEnum):
    """A machine's keyboard matrix layout, an integer at the core boundary.
    The machine YAML (ppi8255 `keyboard_type`) and the canonical
    MachineConfig document spell it with `spelling` and `from_spelling`."""

    INTERNATIONAL = 0
    JAPANESE = 1

    @property
    def spelling(self) -> str:
        """The text form of the machine YAML and the canonical document."""
        return _KEYBOARD_LAYOUT_SPELLINGS[self]

    @classmethod
    def from_spelling(cls, text: str) -> KeyboardLayout:
        """The member spelled `text`. Raises ValueError for another text."""
        for member in cls:
            if _KEYBOARD_LAYOUT_SPELLINGS[member] == text:
                return member
        raise ValueError(f"unknown keyboard layout {text!r}")


_KEYBOARD_LAYOUT_SPELLINGS: dict[KeyboardLayout, str] = {
    KeyboardLayout.INTERNATIONAL: "int",
    KeyboardLayout.JAPANESE: "jp",
}


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

class MediaId(enum.IntEnum):
    """How the host and the core name each battery-backed image and each
    floppy drive (MachineConfig.media/disks, Machine.read_media, ...): a
    closed set of fixed integers, so an id crosses the core boundary as an
    int. Format one with `.name`, never str() or an f-string, whose output
    differs between Python versions."""

    SLOT1_SRAM = 0
    SLOT2_SRAM = 1
    SLOT1_SUB0_SRAM = 2
    SLOT1_SUB1_SRAM = 3
    SLOT1_SUB2_SRAM = 4
    SLOT1_SUB3_SRAM = 5
    SLOT2_SUB0_SRAM = 6
    SLOT2_SUB1_SRAM = 7
    SLOT2_SUB2_SRAM = 8
    SLOT2_SUB3_SRAM = 9
    FMPAC_SRAM = 10
    RTC_CMOS = 11
    DRIVE_A = 12
    DRIVE_B = 13
    DRIVE_C = 14
    DRIVE_D = 15


_SLOT_SRAM_IDS = (MediaId.SLOT1_SRAM, MediaId.SLOT2_SRAM)
_SUBSLOT_SRAM_IDS = (
    (MediaId.SLOT1_SUB0_SRAM, MediaId.SLOT1_SUB1_SRAM,
     MediaId.SLOT1_SUB2_SRAM, MediaId.SLOT1_SUB3_SRAM),
    (MediaId.SLOT2_SUB0_SRAM, MediaId.SLOT2_SUB1_SRAM,
     MediaId.SLOT2_SUB2_SRAM, MediaId.SLOT2_SUB3_SRAM),
)
_DRIVE_IDS = (MediaId.DRIVE_A, MediaId.DRIVE_B, MediaId.DRIVE_C, MediaId.DRIVE_D)


def slot_sram_id(slot: int) -> MediaId:
    """Media id of cartridge slot `slot`'s mapper SRAM (1 -> SLOT1_SRAM).
    Raises ValueError for a slot other than 1 or 2."""
    if not 1 <= slot <= 2:
        raise ValueError(f"no SRAM media id for slot {slot}")
    return _SLOT_SRAM_IDS[slot - 1]


def subslot_sram_id(slot: int, sub: int) -> MediaId:
    """Media id of an expanded-slot device's SRAM (2, 1 -> SLOT2_SUB1_SRAM).
    Raises ValueError outside slots 1-2 / sub-slots 0-3."""
    if not 1 <= slot <= 2 or not 0 <= sub <= 3:
        raise ValueError(f"no SRAM media id for slot {slot}-{sub}")
    return _SUBSLOT_SRAM_IDS[slot - 1][sub]


def drive_media_id(index: int) -> MediaId:
    """Media id of floppy drive `index` (0 -> DRIVE_A ... 3 -> DRIVE_D, the
    four drives a TC8566AF selects). Raises ValueError for any other index."""
    if not 0 <= index <= 3:
        raise ValueError(f"no media id for floppy drive {index}")
    return _DRIVE_IDS[index]


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

# The result of every control operation (load_state, mount_disk, the break
# setters, reset, ...). A failed call changes nothing, and last_error_text()
# gives its message.
STATUS_OK = 0
STATUS_STATE_REJECTED = 1   # load_state: not a state this machine can load
STATUS_NO_DRIVE = 2         # mount_disk: the machine has no such drive
STATUS_BAD_IMAGE = 3        # mount_disk: size not a positive multiple of 512
STATUS_BAD_CONFIG = 4       # create_machine: a configuration the build rejects
STATUS_NO_MEDIUM = 5        # mark_persisted (read_media: b""): no such medium
STATUS_BAD_ARGUMENT = 6     # a break setter: a value outside its range or too
                            # many entries; set_watchpoints: a watch kind;
                            # attach_mouse: a port; set_trace_kinds: a trace kind;
                            # a flag setter, mount_disk: a flag not 0 or 1;
                            # read_memory, read_vram: a length (b"")

# The most breakpoints set_breakpoints and watchpoints set_watchpoints take.
MAX_BREAKPOINTS = 4
MAX_WATCHPOINTS = 4

# The access kind of a watchpoint hit (StopInfo.watch_access).
WATCH_READ = 1
WATCH_WRITE = 2

# The mouse buttons of InputFrame.mouse_buttons, bit flags.
MOUSE_LEFT = 1
MOUSE_RIGHT = 2

# The trace kinds (set_trace_kinds, trace_kinds, trace_sources), bit flags.
TRACE_VDP = 0x01
TRACE_MAPPER = 0x02
TRACE_DEBUG = 0x04  # the --debug log: I/O, VDP mode registers, undefined opcodes, hangs

# TraceEvent.kind, and what the fields a-d hold for each kind.
TRACE_EVENT_VDP_PORT99 = 1  # a = the byte written to port 0x99
TRACE_EVENT_VDP_PORT9B = 2  # a = the byte written to port 0x9B, b = R#17 before the write
TRACE_EVENT_MAP_BANK = 3    # a = window, b = old bank, c = new bank, d = written address
TRACE_EVENT_LOST = 4        # a = events lost to a full buffer; the other fields 0
TRACE_EVENT_IO_READ = 5     # a = port, b = value read, c = the CPU's PC register
TRACE_EVENT_IO_WRITE = 6    # a = port, b = value written, c = the CPU's PC register
TRACE_EVENT_VDP_MODE_REG = 7  # a = register (0 or 1), b = stored value
TRACE_EVENT_UNDEFINED_OP = 8  # a = PC, b = opcode byte
TRACE_EVENT_CPU_HISTORY = 9   # a = PC, b = opcode: one entry of the last 64 instructions
TRACE_EVENT_HANG_PC_LOOP = 10  # a = PC repeated HANG_PC_REPEAT_THRESHOLD times
TRACE_EVENT_HANG_HALT_DI = 11  # a = PC of a HALT with interrupts off

# Instructions the TRACE_DEBUG history keeps (TRACE_EVENT_CPU_HISTORY).
TRACE_HISTORY_SIZE = 64

# Events the trace buffer holds between two drain_trace() calls.
TRACE_CAPACITY = 65536


@dataclass(frozen=True)
class TraceEvent:
    """One recorded trace event (drain_trace): the cycle count, the VDP
    frame number and the CPU's instruction PC at the event, and the fields
    a-d whose meaning the kind gives (TRACE_EVENT_*)."""

    kind: int
    cycle: int
    frame: int
    pc: int
    a: int = 0
    b: int = 0
    c: int = 0
    d: int = 0


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
    """What Machine.step_frame returns for one frame, a flat record of
    integers and bytes.

    `video_pixels` and `audio` are guaranteed only until the next step_frame
    call (allium/core-frontend.allium FrameOutputValidUntilNextStep); a
    frontend that keeps them longer copies them. Here they are fresh
    immutable bytes, which satisfies that, but a native port may reuse its
    buffers.
    """

    video_width: int     # 256 or 512; 0 when the frame was not rendered
    video_height: int    # OUTPUT_H (212); 0 when the frame was not rendered
    video_pixels: bytes  # packed RGB24, width * height * 3; b"" when not rendered
    audio: bytes         # signed 16-bit LE mono PCM, before any output filter
    frame_number: int
    stop_reason: int = STOP_NONE  # nonzero when the call stopped at a break
    frame_complete: int = 1       # 0 when the frame continues on the next call


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
class SlotLayout:
    """The slot selection the core API's slot layout query returns
    (msx/core_api.py). `primary` is the port 0xA8 value. Bit n of `expanded`
    is set when primary slot n is expanded. `secondary` holds 4 bytes, the
    secondary slot register of each primary slot (0 when not expanded)."""

    primary: int
    expanded: int
    secondary: bytes


@dataclass(frozen=True)
class SlotInfo:
    """One slot as the core API's slot description query returns it
    (msx/core_api.py): the content and the bank as UTF-8 text (bank b"-"
    when the page has no bank), and the size in KB (0 when no size
    applies)."""

    content: bytes
    bank: bytes
    size_kb: int


@dataclass(frozen=True)
class RomImage:
    """A ROM image the MachineConfig document refers to by `sha1` (40
    lowercase hex digits of the SHA1 of `data`)."""

    sha1: str
    data: bytes


@dataclass(frozen=True)
class MediaImage:
    """The saved image of a battery-backed medium (an SRAM id or RTC_CMOS)."""

    media_id: MediaId
    data: bytes


@dataclass(frozen=True)
class DiskMount:
    """A disk image to mount in floppy drive `drive` (0 = A) at build time."""

    drive: int
    data: bytes
    write_protected: bool = False


@dataclass
class MachineConfig:
    """Everything build_machine needs, as values: no path is opened and no
    database is consulted while building.

    `document` is the canonical MachineConfig document (msx/machine_config.py,
    msx/machine_config.schema.json): the resolved machine, cartridges with
    their resolved mappers, and extension overlays, every ROM a reference.
    `roms` holds the referenced ROM images, each with its SHA1. The rest are
    run inputs, outside the document: `media` holds initial battery-backed
    images (the SRAM ids and RTC_CMOS of MediaId; an absent id starts
    blank), `disks` the images to mount by drive index, and
    `rtc_initial_time` the RTC's start time. All three are tuples of flat
    records, at most one record per id or drive.
    """

    document: bytes
    roms: tuple[RomImage, ...] = ()
    media: tuple[MediaImage, ...] = ()
    disks: tuple[DiskMount, ...] = ()
    rtc_initial_time: RtcTime | None = None
