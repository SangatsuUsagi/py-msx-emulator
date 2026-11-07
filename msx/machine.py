from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from msx.audio_mixer import AudioMixer
from msx.boundary import (
    AUDIO_SAMPLE_RATE,
    MAX_BREAKPOINTS,
    MAX_WATCHPOINTS,
    MOUSE_LEFT,
    MOUSE_RIGHT,
    OUTPUT_HEIGHT,
    STATUS_BAD_ARGUMENT,
    STATUS_BAD_IMAGE,
    STATUS_NO_DRIVE,
    STATUS_NO_MEDIUM,
    STATUS_NO_PRINTER_PORT,
    STATUS_OK,
    STATUS_STATE_REJECTED,
    STOP_BREAKPOINT,
    STOP_NONE,
    STOP_REQUESTED,
    STOP_WATCHPOINT,
    TRACE_DEBUG,
    TRACE_EVENT_HANG_HALT_DI,
    TRACE_EVENT_HANG_PC_LOOP,
    TRACE_MAPPER,
    TRACE_VDP,
    VDP_KIND_TMS9918A,
    VDP_KIND_V9938,
    WATCH_READ,
    WATCH_WRITE,
    CpuRegisters,
    FrameOutput,
    KeyboardLayout,
    MediaId,
    RgbFrame,
    SlotInfo,
    SlotLayout,
    StopInfo,
    TraceEvent,
    VdpInfo,
    VideoStandard,
    drive_media_id,
)
from msx.cassette import CassettePort
from msx.clock import CoreClock
from msx.cpu.z80 import Z80
from msx.input import InputFrame, InputLines
from msx.io import IOBus
from msx.kanji import KanjiRom
from msx.mapper import MajutsushiMapper
from msx.memory import Memory
from msx.mouse import TRIGGER_A_BIT, TRIGGER_B_BIT, MouseDevice
from msx.ppi import PPI
from msx.printer import PrinterPort
from msx.psg import PSG, JoystickPort
from msx.scc import SCC
from msx.snapshot_schema import SnapshotError
from msx.sram import Medium, SramImage
from msx.trace import TraceLog
from msx.vdp._geometry import cycles_per_frame, lines_per_frame
from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
from msx.vdp.vdp import VDP, VdpDevice

if TYPE_CHECKING:
    from msx.fdc.disk_image import DskDiskImage
    from msx.fdc.interface import FloppyDisk
    from msx.fmpac import FmPac
    from msx.rtc import RTC

HANG_PC_REPEAT_THRESHOLD: int = 1000


# PORT-NOTE: Machine owns every device. A reference that one device holds to
# another (the trace log, the input lines, the SCC, the Majutsushi mapper as
# `dac`, the FM-PAC in a slot and in `fmpac`) does not own it and lives no longer
# than the Machine, as CoreClock's readers do (msx/clock.py). In Rust, these are
# indices or a borrow of the Machine; in C++ and Zig, plain pointers.
@dataclass
class Machine:
    """The whole emulated machine, and the implementation of the core API
    (msx/core_api.py): it owns the devices build_machine wires and runs them
    frame by frame (step_frame)."""

    cpu: Z80
    # VDP and V9938 are unrelated classes sharing this structural contract by
    # convention, not inheritance -- see VdpDevice's own docstring.
    vdp: VdpDevice
    memory: Memory
    io: IOBus
    psg: PSG
    scc: SCC | None = field(default=None)
    dac: MajutsushiMapper | None = field(default=None)
    fdc: "FloppyDisk | None" = field(default=None)
    fmpac: "FmPac | None" = field(default=None, repr=False)
    kanji: KanjiRom | None = field(default=None, repr=False)
    ppi: PPI | None = field(default=None, repr=False)
    printer: PrinterPort | None = field(default=None, repr=False)
    # The cassette port's tape deck, or None when the machine has no port.
    cassette: CassettePort | None = field(default=None, repr=False)
    rtc: "RTC | None" = field(default=None, repr=False)
    input: InputLines = field(default_factory=InputLines)
    # Per-frame audio mix, produced by run_frame() and read with take_audio().
    audio: AudioMixer = field(default_factory=AudioMixer, repr=False)
    video_standard: VideoStandard = VideoStandard.NTSC
    # The keyboard matrix layout, from the machine definition (frontends pick
    # their host-key table from it).
    keyboard_layout: KeyboardLayout = KeyboardLayout.INTERNATIONAL
    # The emulated time (msx/clock.py): the free-running T-state count
    # (never reset), the frame count and the instruction address. The run
    # loop adds to its cycle count; build_machine gives the record to the
    # components that read or advance it.
    clock: CoreClock = field(default_factory=CoreClock, repr=False)
    # The trace buffer the CPU, the I/O bus, the VDP, the slot 1/2 mappers
    # and the frame loop record into (set_trace_kinds, drain_trace); wired by
    # msx/machine_loader.py.
    _trace: TraceLog = field(default_factory=TraceLog, repr=False)
    # SRAM media by id, filled once by build_machine (see sram_media()).
    _sram_table: dict[MediaId, SramImage] = field(default_factory=dict, repr=False)
    # SHA-256 (hex) of the MachineConfig document this machine was built from;
    # a snapshot records it and loads only into a machine with the same one.
    # Empty for a Machine constructed by hand.
    machine_config_sha256: str = field(default="", repr=False)
    # Set by a break during a run: the loop arm returns at the break and
    # run_frame() keeps the frame position. _resume_skip_pc is the PC whose
    # breakpoint test the first instruction of the next run skips.
    _stop_pending: bool = field(default=False, init=False, repr=False)
    _resume_skip_pc: int = field(default=-1, init=False, repr=False)  # -1: none
    _breakpoints: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_read: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_write: frozenset[int] = field(default_factory=frozenset, repr=False)
    _last_pc: int = field(default=0, init=False, repr=False)
    _pc_repeat: int = field(default=0, init=False, repr=False)
    # Crash-signature auto-break conditions (debugger bh/bs).
    _break_halt_di: bool = field(default=False, init=False, repr=False)
    _sp_low: int = field(default=-1, init=False, repr=False)  # -1: no SP range
    _sp_high: int = field(default=-1, init=False, repr=False)
    _halt_di_seen: bool = field(default=False, init=False, repr=False)
    _sp_out_seen: bool = field(default=False, init=False, repr=False)
    # Targeted execution control (debugger g/so); one-shot, cleared on hit.
    # -1: not set, here and in _frame_breakpoint.
    _temp_breakpoint: int = field(default=-1, init=False, repr=False)
    _stepout_sp: int = field(default=-1, init=False, repr=False)
    # One-shot run-to-frame breakpoint (debugger 'gf'); checked once per
    # run_frame() call, not per instruction — see set_frame_breakpoint.
    _frame_breakpoint: int = field(default=-1, init=False, repr=False)
    # The most recent stop (last_stop()), cleared at the start of each
    # step_frame() and step() call.
    _stop: StopInfo = field(default_factory=StopInfo, init=False, repr=False)
    # Set by request_break() (Ctrl-C); run_frame() honors it before the next
    # frame segment.
    _break_request: bool = field(default=False, init=False, repr=False)
    # The UTF-8 message of the last call that failed with a status other than
    # STATUS_OK, b"" after a later call that clears it (last_error_text()).
    _error_text: bytes = field(default=b"", init=False, repr=False)
    # Position of a frame that stopped at a break (machine-core "A break stops
    # step_frame and the next call continues the frame"): the next scanline
    # to run, the frame-relative T-state total, the frame's start cycle (audio
    # mix), whether the frame was rendered and the rendered index buffer.
    _frame_active: bool = field(default=False, init=False, repr=False)
    _frame_line: int = field(default=0, init=False, repr=False)
    _frame_total: int = field(default=0, init=False, repr=False)
    _frame_start_cycle: int = field(default=0, init=False, repr=False)
    _frame_rendered: bool = field(default=False, init=False, repr=False)
    _frame_result: bytearray = field(default_factory=bytearray, init=False, repr=False)
    # The scanline a loop arm stopped on (the next one to run).
    _stop_line: int = field(default=0, init=False, repr=False)
    # Set while step() runs one instruction through run_frame(): the debug
    # arm then skips the break tests and stops after one instruction, whose
    # T-states it leaves in _step_cycles (-1 until it has run).
    _step_one: bool = field(default=False, init=False, repr=False)
    _step_cycles: int = field(default=-1, init=False, repr=False)
    # Set when step_frame() stopped or step() ran, so the next run skips the
    # breakpoint test for its first instruction (machine-core "A resume does
    # not re-break at once").
    _resume_after_stop: bool = field(default=False, init=False, repr=False)

    @property
    def cycle_count(self) -> int:
        """T-states since power-on (the clock record's cycle count)."""
        return self.clock.cycle

    @property
    def cycles_per_frame(self) -> int:
        """Z80 T-states per frame, from the video standard."""
        return cycles_per_frame(self.video_standard)

    @property
    def lines_per_frame(self) -> int:
        """Scanlines per frame, from the video standard."""
        return lines_per_frame(self.video_standard)

    def __post_init__(self) -> None:
        # PORT-NOTE: this wires the CPU's memory/IO bus by reassigning bound
        #   methods onto Callable fields at runtime -- the hottest path in
        #   the emulator (see also set_watchpoints below, which re-swaps the
        #   same fields).
        # Rust equivalent: a `trait MemoryBus` (or an `enum { Normal,
        #   Watchpoint }`) whose concrete implementation is selected once
        #   behind a flag at construction, not reassigned at runtime.
        # C++ equivalent: same -- a virtual `MemoryBus` interface or a
        #   feature-flagged concrete type chosen once, not a runtime method
        #   swap (C++ has no equivalent of rebinding an instance method).
        # Kept as-is here because: read/written on every CPU memory/IO access;
        #   resolving the bus once (here and in set_watchpoints) keeps the
        #   per-access dispatch a single bound-method call with no added
        #   branch, which a per-access abstraction layer would cost in Python.
        self.cpu.read_byte = self.memory.read
        self.cpu.write_byte = self.memory.write
        self.cpu.read_port = self.io.read_port
        self.cpu.write_port = self.io.write_port

    def reset(self) -> int:
        """Full power-on reset: CPU, PSG, SCC (if present), FM-PAC (if
        present), VDP, FDC (if present), Kanji ROM (if present), PPI (if
        present), the printer port (if present), the primary/secondary slot
        registers, the RAM mapper (if present, in either its slot-3 or its
        expanded-slot-2-sub-slot placement), and every `Mapper` --
        `memory.mapper1`, `memory.mapper2`, and every non-`None` entry of an
        expanded slot's per-sub-slot array
        (this also covers a present Halnote cartridge, which only ever lives
        in one of those sub-slot entries). Memory/VRAM contents are retained.

        Every `Mapper` is reset unconditionally regardless of whether a given
        real cartridge's bank-select hardware is actually wired to the slot
        reset signal -- rationale in openspec/changes/
        wire-cartridge-mapper-and-ppi-reset. Always STATUS_OK."""
        self.cpu.reset()
        self.psg.reset()
        self.audio.reset()
        if self.scc is not None:
            self.scc.reset()
        if self.fmpac is not None:
            self.fmpac.reset()
        self.vdp.reset()
        if self.fdc is not None:
            self.fdc.reset()
        if self.kanji is not None:
            self.kanji.reset()
        if self.ppi is not None:
            self.ppi.reset()
        if self.printer is not None:
            self.printer.reset()
        # Power-on slot state: all pages select slot 0 (matches construction).
        self.memory.set_slot_register(0x00)
        self.memory.set_sub_slot_reg(0x00)
        self.memory.set_slot1_sub_slot_reg(0x00)
        self.memory.set_slot2_sub_slot_reg(0x00)
        if self.memory.ram_mapper is not None:
            self.memory.ram_mapper.reset()
        self.memory.mapper1.reset()
        self.memory.mapper2.reset()
        # A HalnoteMapper (when present) is always one of these sub-slot
        # entries -- _wire_expanded_overlay is the only place that ever
        # constructs one (EXTENSION_DEVICES, the flat overlay's device
        # allow-list in msx/machine_config.py, lacks "halnote") -- so this
        # loop resets it exactly once. Slot 1's own sub-slots (openspec/changes/
        # slot1-slot2-symmetrization) get the identical treatment.
        for sub_mapper in (*self.memory.mapper1_subslots, *self.memory.mapper2_subslots):
            if sub_mapper is not None:
                sub_mapper.reset()
        self._drop_frame()
        return STATUS_OK

    def _drop_frame(self) -> None:
        """Forget a stopped frame: the next run_frame() starts a new one
        (machine-core "reset and a state load drop a stopped frame")."""
        self._frame_active = False
        self._frame_result = bytearray()
        self._stop_pending = False

    def sram_media(self) -> Mapping[MediaId, SramImage]:
        """Every SRAM medium by id: `slot1.sram`/`slot2.sram` (cartridge
        mapper), `slot<n>.<sub>.sram` (expanded-slot device), `fmpac.sram`,
        `rtc.cmos`. Fixed when build_machine finished wiring (no device is
        replaced afterwards), so this is a read-only view of that table."""
        return self._sram_table

    def _media(self) -> dict[int, Medium]:
        media: dict[int, Medium] = {}
        for media_id, image in self.sram_media().items():
            media[int(media_id)] = image
        if self.fdc is not None:
            for index, drive in enumerate(self.fdc.drives):
                if drive.image is not None:
                    media[int(drive_media_id(index))] = drive.image
        return media

    def dirty_media(self) -> list[int]:
        """Ids of the media written since they were last persisted."""
        dirty: list[int] = []
        for media_id, medium in self._media().items():
            if medium.dirty:
                dirty.append(media_id)
        return dirty

    def read_media(self, media_id: int) -> bytes:
        """A medium's current bytes, or b"" for an id the machine does not
        have (last_error_text() gives the message). A medium is never empty,
        so b"" is unambiguous."""
        medium = self._media().get(media_id)
        if medium is None:
            self._fail(STATUS_NO_MEDIUM, f"no medium {_media_label(media_id)} on this machine")
            return b""
        self._error_text = b""
        return medium.data()

    def mark_persisted(self, media_id: int) -> int:
        """Clear a medium's dirty flag after the host has written it, and
        return STATUS_OK. Returns STATUS_NO_MEDIUM, changing nothing, for an
        id the machine does not have."""
        medium = self._media().get(media_id)
        if medium is None:
            return self._fail(
                STATUS_NO_MEDIUM, f"no medium {_media_label(media_id)} on this machine"
            )
        medium.mark_persisted()
        self._error_text = b""
        return STATUS_OK

    def mount_disk(self, drive: int, data: bytes, write_protected: bool) -> int:
        """Swap the image `data` into `drive` (0 = A),
        rejecting sector writes when `write_protected`, and return STATUS_OK.
        The host writes a dirty outgoing image back before the swap. Returns
        STATUS_NO_DRIVE for a machine without that drive and STATUS_BAD_IMAGE
        for an image whose size is not a positive multiple of 512, changing
        nothing (last_error_text() gives the message). Returns
        STATUS_BAD_ARGUMENT, changing nothing, for a `write_protected` other
        than 0 and 1."""
        from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage

        if write_protected != 0 and write_protected != 1:
            return self._fail(
                STATUS_BAD_ARGUMENT, f"write_protected {write_protected} is not 0 or 1"
            )
        if self.fdc is None or not self._has_drive(drive):
            return self._fail(STATUS_NO_DRIVE, f"no floppy drive {drive} on this machine")
        size = len(data)
        if size == 0 or size % SECTOR_SIZE != 0:
            return self._fail(
                STATUS_BAD_IMAGE,
                f"size {size} is not a positive multiple of {SECTOR_SIZE}",
            )
        self.fdc.swap(drive, DskDiskImage(data, write_protected=write_protected == 1))
        self._error_text = b""
        return STATUS_OK

    def eject_disk(self, drive: int) -> int:
        """Empty `drive` (0 = A) and return STATUS_OK. The host writes a
        dirty outgoing image back first. Returns STATUS_NO_DRIVE, changing
        nothing, for a machine without that drive."""
        if self.fdc is None or not self._has_drive(drive):
            return self._fail(STATUS_NO_DRIVE, f"no floppy drive {drive} on this machine")
        self.fdc.swap(drive, None)
        self._error_text = b""
        return STATUS_OK

    def last_error_text(self) -> bytes:
        """The UTF-8 message of the last call that failed with a status other
        than STATUS_OK (or an empty result from read_media, read_memory,
        read_vram or slot_info). A member that checks its arguments clears
        it on success; the members that cannot fail leave it as it is."""
        return self._error_text

    def _fail(self, status: int, message: str) -> int:
        self._error_text = message.encode("utf-8")
        return status

    def disk_mounted(self, drive: int) -> bool:
        """Whether `drive` (0 = A) exists and holds an image."""
        return self._mounted_image(drive) is not None

    def _mounted_image(self, drive: int) -> DskDiskImage | None:
        if self.fdc is None or not self._has_drive(drive):
            return None
        return self.fdc.drives[drive].image

    def _has_drive(self, drive: int) -> bool:
        """Whether the machine has floppy drive `drive` (0 = A)."""
        return self.fdc is not None and 0 <= drive < len(self.fdc.drives)

    def attach_mouse(self, port: int) -> int:
        """Attach a new MSX mouse to joystick port `port` (0 = Joy1, 1 =
        Joy2) and return STATUS_OK. Its motion and buttons come from each
        InputFrame. Returns STATUS_BAD_ARGUMENT, attaching nothing, for
        another port.

        Delegates to `PSG.attach_mouse` rather than setting `self.psg._mouse`
        directly, so the PSG stays the sole writer of its mouse slot.
        """
        if port != JoystickPort.JOY1 and port != JoystickPort.JOY2:
            return self._fail(STATUS_BAD_ARGUMENT, f"no joystick port {port}")
        self.psg.attach_mouse(MouseDevice(), JoystickPort(port))
        self._error_text = b""
        return STATUS_OK

    def attach_printer(self) -> int:
        """Connect a printer to the printer port and return STATUS_OK, or
        STATUS_NO_PRINTER_PORT on a machine with no port. Repeating it changes
        nothing."""
        if self.printer is None:
            return self._fail(STATUS_NO_PRINTER_PORT, "this machine has no printer port")
        self.printer.attach()
        self._error_text = b""
        return STATUS_OK

    def drain_printer(self) -> bytes:
        """The strobed bytes in order; empties the buffer. b"" when there is
        no port or no byte waits."""
        if self.printer is None:
            return b""
        return self.printer.drain()

    def _enter_break(self, reason: int) -> None:
        """Record a stop (`reason` is a STOP_* value) at the current PC and
        make the running loop arm return at it."""
        self._stop = StopInfo(reason, self.cpu.registers.PC)
        self._stop_pending = True

    def request_break(self) -> int:
        """Ask the core to stop before the next frame segment (machine-core
        "The host requests a break"). Safe to call from a signal handler: it
        only sets a flag. Always STATUS_OK."""
        self._break_request = True
        return STATUS_OK

    def last_stop(self) -> StopInfo:
        """The most recent stop of step_frame() or step(), or a STOP_NONE
        record when that call did not stop (machine-core "The last stop is
        reported as a flat record")."""
        return self._stop

    def set_breakpoints(self, addrs: list[int]) -> int:
        """Set breakpoint addresses, replacing the existing set. Returns
        STATUS_OK, or STATUS_BAD_ARGUMENT, keeping the set, for more than
        MAX_BREAKPOINTS addresses or one outside 0x0000-0xFFFF."""
        if len(addrs) > MAX_BREAKPOINTS:
            return self._fail(
                STATUS_BAD_ARGUMENT,
                f"{len(addrs)} breakpoints, at most {MAX_BREAKPOINTS}")
        for addr in addrs:
            if addr < 0 or addr > 0xFFFF:
                return self._fail(
                    STATUS_BAD_ARGUMENT, f"breakpoint address 0x{addr:X} outside 0x0000-0xFFFF")
        self._breakpoints = frozenset(addrs)
        self._error_text = b""
        return STATUS_OK

    def set_break_halt_di(self, enabled: bool) -> int:
        """Enable (1) or disable (0) breaking when the CPU executes HALT with
        interrupts off, and return STATUS_OK. Returns STATUS_BAD_ARGUMENT,
        keeping the setting, for any other value."""
        if enabled != 0 and enabled != 1:
            return self._fail(
                STATUS_BAD_ARGUMENT, f"set_break_halt_di value {enabled} is not 0 or 1"
            )
        self._break_halt_di = enabled == 1
        self._halt_di_seen = False
        self._error_text = b""
        return STATUS_OK

    def set_sprites_hidden(self, hidden: bool) -> int:
        """Hide or show sprites: while hidden, the renderer skips its sprite
        pass (on the TMS9918A also the S#0 sprite flags of that frame). A
        debug setting: no reset clears it and the saved state omits it.
        Takes 0 or 1 and returns STATUS_OK. Returns STATUS_BAD_ARGUMENT,
        keeping the setting, for any other value."""
        if hidden != 0 and hidden != 1:
            return self._fail(
                STATUS_BAD_ARGUMENT, f"set_sprites_hidden value {hidden} is not 0 or 1"
            )
        self.vdp.debug_disable_sprites = hidden == 1
        self._error_text = b""
        return STATUS_OK

    def set_sp_range(self, low: int, high: int) -> int:
        """Set the valid-RAM range for SP; break when SP leaves it.
        (-1, -1) disables. Returns STATUS_OK, or STATUS_BAD_ARGUMENT, keeping
        the range, for an end outside 0x0000-0xFFFF (one -1 included) or a
        low end above the high end."""
        if low != -1 or high != -1:
            for value in (low, high):
                if value < 0 or value > 0xFFFF:
                    return self._fail(
                        STATUS_BAD_ARGUMENT, f"SP range end 0x{value:X} outside 0x0000-0xFFFF")
            if low > high:
                return self._fail(
                    STATUS_BAD_ARGUMENT,
                    f"SP range low 0x{low:04X} above high 0x{high:04X}")
        self._sp_low = low
        self._sp_high = high
        self._sp_out_seen = False
        self._error_text = b""
        return STATUS_OK

    def set_temp_breakpoint(self, addr: int) -> int:
        """Set a one-shot run-to breakpoint (debugger 'g'); cleared when hit.
        -1 clears it. Returns STATUS_OK, or STATUS_BAD_ARGUMENT for another
        address outside 0x0000-0xFFFF."""
        if addr != -1 and (addr < 0 or addr > 0xFFFF):
            return self._fail(
                STATUS_BAD_ARGUMENT, f"run-to address 0x{addr:X} outside 0x0000-0xFFFF")
        self._temp_breakpoint = addr
        self._error_text = b""
        return STATUS_OK

    def set_step_out(self, sp: int) -> int:
        """Break (once) when SP rises above sp, i.e. the current routine
        returns. Returns STATUS_OK, or STATUS_BAD_ARGUMENT for an SP outside
        0x0000-0xFFFF."""
        if sp < 0 or sp > 0xFFFF:
            return self._fail(
                STATUS_BAD_ARGUMENT, f"step-out SP 0x{sp:X} outside 0x0000-0xFFFF")
        self._stepout_sp = sp
        self._error_text = b""
        return STATUS_OK

    def set_frame_breakpoint(self, frame: int) -> int:
        """Set a one-shot run-to-frame breakpoint (debugger 'gf'); cleared
        when hit. -1 clears it. Returns STATUS_OK, or STATUS_BAD_ARGUMENT
        for a frame below -1 or above 2^63 - 1 (the C int64_t)."""
        if frame < -1 or frame > 0x7FFF_FFFF_FFFF_FFFF:
            return self._fail(STATUS_BAD_ARGUMENT, f"frame {frame} is outside -1 to 2^63 - 1")
        self._frame_breakpoint = frame
        self._error_text = b""
        return STATUS_OK

    def _break_conditions_active(self) -> bool:
        """True when any execution-break condition needs the per-instruction loop."""
        return (
            self._step_one
            or bool(self._breakpoints)
            or self._break_halt_di
            or self._sp_low != -1
            or self._temp_breakpoint != -1
            or self._stepout_sp != -1
            # Watchpoints need the per-instruction loop so a hit can return at the
            # instruction boundary instead of running to frame end.
            # NOTE: mere RPC attachment does NOT force the debug loop — a bare
            # attached "continue" runs at fast-loop speed; only real break
            # conditions (breakpoints/watchpoints) route here. An async
            # debugger.pause is handled at frame granularity by the frontend.
            or bool(self._watch_read)
            or bool(self._watch_write)
        )

    def _post_step_break(self) -> bool:
        """Evaluate crash-signature break conditions after one CPU step.

        Returns True at most once per rising edge of each condition (so resuming
        from a still-true condition does not immediately re-break).
        """
        cpu = self.cpu
        if self._break_halt_di:
            if cpu.halted and not cpu.iff1:
                if not self._halt_di_seen:
                    self._halt_di_seen = True
                    return True
            else:
                self._halt_di_seen = False
        if self._sp_low != -1:
            sp = cpu.registers.SP
            if sp < self._sp_low or sp > self._sp_high:
                if not self._sp_out_seen:
                    self._sp_out_seen = True
                    return True
            else:
                self._sp_out_seen = False
        if self._stepout_sp != -1 and cpu.registers.SP > self._stepout_sp:
            self._stepout_sp = -1
            return True
        return False

    def _post_step_checks_armed(self) -> bool:
        """True when any condition _post_step_break() tests is armed.

        Lifted out of the per-instruction debug loop: with only breakpoints or
        watchpoints set (the common case) none of these are armed, so the call
        to _post_step_break() can be skipped entirely. A break returns from
        the loop, so the value holds for the whole call."""
        return (
            self._break_halt_di
            or self._sp_low != -1
            or self._stepout_sp != -1
        )

    def set_watchpoints(self, entries: list[tuple[int, int]]) -> int:
        """Set watchpoints: [(addr, kind), ...], where kind is WATCH_READ,
        WATCH_WRITE or both ORed. Returns STATUS_OK, or STATUS_BAD_ARGUMENT,
        keeping the current watchpoints, for more than MAX_WATCHPOINTS
        entries, an address outside 0x0000-0xFFFF, or a kind of 0 or with
        another bit."""
        if len(entries) > MAX_WATCHPOINTS:
            return self._fail(
                STATUS_BAD_ARGUMENT,
                f"{len(entries)} watchpoints, at most {MAX_WATCHPOINTS}")
        for addr, kind in entries:
            if addr < 0 or addr > 0xFFFF:
                return self._fail(
                    STATUS_BAD_ARGUMENT, f"watchpoint address 0x{addr:X} outside 0x0000-0xFFFF")
            if kind == 0 or kind & ~(WATCH_READ | WATCH_WRITE):
                return self._fail(STATUS_BAD_ARGUMENT, f"invalid watch kind {kind}")
        # PORT-NOTE: enabling watchpoints re-swaps cpu.read_byte/write_byte
        #   between the plain memory bus and the watch variant at runtime --
        #   same shape as __post_init__'s initial wiring above.
        # Rust equivalent: an `enum { Normal, Watchpoint }` bus (or trait
        #   object) chosen once per configuration change, not a reassigned
        #   function pointer.
        # C++ equivalent: same -- select a concrete `MemoryBus` implementation
        #   once per configuration change, not a runtime method swap.
        # Kept as-is here because: same hot-path reasoning as __post_init__'s
        #   bus wiring -- resolving once here keeps every memory access a
        #   single bound-method call with no added branch.
        r: set[int] = set()
        w: set[int] = set()
        for addr, kind in entries:
            if kind & WATCH_READ:
                r.add(addr)
            if kind & WATCH_WRITE:
                w.add(addr)
        self._watch_read = frozenset(r)
        self._watch_write = frozenset(w)
        if self._watch_read or self._watch_write:
            self.cpu.read_byte = self._read_with_watch
            self.cpu.write_byte = self._write_with_watch
        else:
            self.cpu.read_byte = self.memory.read
            self.cpu.write_byte = self.memory.write
        self._error_text = b""
        return STATUS_OK

    # ------------------------------------------------------------------
    # Inspection queries (core API, machine-core "Core inspection queries
    # return integers and bytes"): read-only, no emulated state changes.
    # ------------------------------------------------------------------

    def cpu_registers(self) -> CpuRegisters:
        """The Z80 register file."""
        cpu = self.cpu
        r = cpu.registers
        return CpuRegisters(
            AF=r.AF, BC=r.BC, DE=r.DE, HL=r.HL,
            AF_=r.AF_, BC_=r.BC_, DE_=r.DE_, HL_=r.HL_,
            IX=r.IX, IY=r.IY, SP=r.SP, PC=r.PC, I=r.I, R=r.R,
            IFF1=1 if cpu.iff1 else 0, IFF2=1 if cpu.iff2 else 0, IM=cpu.im,
        )

    def read_memory(self, addr: int, length: int) -> bytes:
        """`length` bytes from `addr` as the CPU reads them in the current slot
        selection, wrapping at 0xFFFF. Bypasses watchpoints. A memory-mapped
        device register reads as it does for the CPU, side effects included.
        Returns b"" for a length outside 0-0x10000 (last_error_text() gives
        the message)."""
        if not 0 <= length <= 0x10000:
            self._fail(STATUS_BAD_ARGUMENT, f"read_memory length {length} is outside 0-0x10000")
            return b""
        self._error_text = b""
        read = self.memory.read
        out = bytearray(length)
        for i in range(length):
            out[i] = read((addr + i) & 0xFFFF)
        return bytes(out)

    def write_memory(self, addr: int, data: bytes) -> int:
        """Write `data` from `addr` as the CPU would, wrapping at 0xFFFF.
        Bypasses watchpoints. Always STATUS_OK."""
        write = self.memory.write
        for i in range(len(data)):
            write((addr + i) & 0xFFFF, data[i])
        return STATUS_OK

    def vram_size(self) -> int:
        """VRAM size in bytes (0x4000 or 0x20000)."""
        return len(self.vdp.vram)

    def read_vram(self, addr: int, length: int) -> bytes:
        """`length` VRAM bytes from `addr`, wrapping at the VRAM size by the
        address's low bits (a negative address wraps to the end). Returns b""
        for a length outside 0 to vram_size() (last_error_text() gives the
        message)."""
        vram = self.vdp.vram
        size = len(vram)
        if not 0 <= length <= size:
            self._fail(STATUS_BAD_ARGUMENT, f"read_vram length {length} is outside 0-{size:#x}")
            return b""
        self._error_text = b""
        # The VRAM size is a power of two (0x4000 or 0x20000), so the mask
        # wraps the same way in every language.
        mask = size - 1
        out = bytearray(length)
        for i in range(length):
            out[i] = vram[(addr + i) & mask]
        return bytes(out)

    def vdp_info(self) -> VdpInfo:
        """The VDP kind, registers, palette, status and command progress."""
        vdp = self.vdp
        if isinstance(vdp, V9938):
            x, y, nx, ny, remaining = vdp.command_progress()
            return VdpInfo(
                kind=VDP_KIND_V9938,
                regs=bytes(vdp.regs),
                cmd_regs=bytes(vdp.cmd_regs),
                status=vdp.peek_status_registers(),
                palette=tuple(vdp.palette),
                cmd_x=x, cmd_y=y, cmd_nx=nx, cmd_ny=ny, cmd_remaining=remaining,
            )
        return VdpInfo(
            kind=VDP_KIND_TMS9918A,
            regs=bytes(vdp.regs),
            cmd_regs=b"",
            status=bytes((vdp.status & 0xFF,)),
            palette=(),
        )

    def frame_number(self) -> int:
        """Frames the VDP has completed (the frame-breakpoint counter)."""
        return self.vdp.frame_count

    def drive_count(self) -> int:
        """Floppy drives on this machine; 0 without a floppy interface."""
        if self.fdc is None:
            return 0
        return len(self.fdc.drives)

    def main_ram_range(self) -> tuple[int, int]:
        """The main-RAM address window the SP-range break defaults to."""
        return self.memory.main_ram_range()

    def slot_layout(self) -> SlotLayout:
        """The primary slot register, the expanded primary slots (bit n for
        slot n) and each slot's secondary slot register (0 when not
        expanded). Slot 0 is never expanded."""
        mem = self.memory
        expanded = 0
        secondary = bytearray(4)
        if mem.slot1_sub_slot_enabled:
            expanded |= 0x02
            secondary[1] = mem.slot1_sub_slot_reg & 0xFF
        if mem.slot2_sub_slot_enabled:
            expanded |= 0x04
            secondary[2] = mem.slot2_sub_slot_reg & 0xFF
        if mem.sub_slot_enabled:
            expanded |= 0x08
            secondary[3] = mem.sub_slot_reg & 0xFF
        return SlotLayout(mem.slot_register & 0xFF, expanded, bytes(secondary))

    def slot_info(self, primary: int, secondary: int, page: int) -> SlotInfo:
        """The content, mapper, size in KB and bank of a primary slot, a
        sub-slot (-1: none) and a page (-1: none), as integers. Returns a
        content, mapper and size of -1 for a value outside 0-3
        (last_error_text() gives the message)."""
        if not 0 <= primary <= 3:
            return self._bad_slot(f"no primary slot {primary}")
        if secondary != -1 and not 0 <= secondary <= 3:
            return self._bad_slot(f"no sub-slot {secondary}")
        if page != -1 and not 0 <= page <= 3:
            return self._bad_slot(f"no page {page}")
        self._error_text = b""
        return self.memory.describe_slot(primary, secondary, page)

    def _bad_slot(self, message: str) -> SlotInfo:
        self._fail(STATUS_BAD_ARGUMENT, message)
        return SlotInfo(content=-1, mapper=-1, size_kb=-1)

    def breakpoints(self) -> tuple[int, ...]:
        """The breakpoint addresses, ascending."""
        return tuple(sorted(self._breakpoints))

    def watchpoints(self) -> tuple[tuple[int, int], ...]:
        """The watchpoints as (addr, kind) pairs, ascending by address, in the
        shape set_watchpoints takes (kind WATCH_READ, WATCH_WRITE or both)."""
        result: list[tuple[int, int]] = []
        for addr in sorted(self._watch_read | self._watch_write):
            kind = 0
            if addr in self._watch_read:
                kind |= WATCH_READ
            if addr in self._watch_write:
                kind |= WATCH_WRITE
            result.append((addr, kind))
        return tuple(result)

    def break_halt_di(self) -> bool:
        """Whether the HALT-with-interrupts-off break is enabled."""
        return self._break_halt_di

    def sprites_hidden(self) -> bool:
        """Whether sprites are hidden (set_sprites_hidden)."""
        return self.vdp.debug_disable_sprites

    def set_trace_kinds(self, kinds: int) -> int:
        """Turn on exactly the trace kinds `kinds` (TRACE_VDP, TRACE_MAPPER,
        TRACE_DEBUG) and return STATUS_OK. Returns STATUS_BAD_ARGUMENT,
        changing nothing, for another bit. A debug setting: no reset clears
        it and the saved state omits it. TRACE_DEBUG hands the CPU the
        trace buffer, which it checks on each instruction, and starts an
        empty instruction history when it turns on."""
        if kinds & ~(TRACE_VDP | TRACE_MAPPER | TRACE_DEBUG):
            return self._fail(STATUS_BAD_ARGUMENT, f"invalid trace kinds {kinds}")
        trace = self._trace
        if kinds & TRACE_DEBUG:
            if not trace.kinds & TRACE_DEBUG:
                trace.clear_history()
            self.cpu._trace = trace
        else:
            self.cpu._trace = None
        trace.kinds = kinds
        self._error_text = b""
        return STATUS_OK

    def trace_kinds(self) -> int:
        """The trace kinds that are on."""
        return self._trace.kinds

    def trace_sources(self) -> int:
        """The trace kinds this machine can produce: TRACE_VDP and
        TRACE_DEBUG, plus TRACE_MAPPER with a bank-switching ROM mapper in
        slot 1 or 2."""
        return self._trace.sources

    def drain_trace(self) -> tuple[TraceEvent, ...]:
        """The recorded trace events in order, ending with one
        TRACE_EVENT_LOST record when the buffer was full; empties it."""
        return self._trace.drain()

    def render_preview(self) -> RgbFrame:
        """The current frame as RGB24, without changing emulated state (the
        debugger's and the RPC's screenshot and state-save capture)."""
        from msx.screenshot import render_current_rgb24

        rgb, width, height = render_current_rgb24(self.vdp)
        return RgbFrame(width, height, rgb)

    def save_state(self) -> bytes:
        """The machine state in the versioned state format (msx/state.py)."""
        from msx import state

        return state.save_state(self)

    def load_state(self, data: bytes) -> int:
        """Restore save_state() bytes and return STATUS_OK. Returns
        STATUS_STATE_REJECTED and restores nothing when msx/state.py's
        load_state rejects the data (last_error_text() gives its message)."""
        from msx import state

        try:
            state.load_state(self, data)
        except SnapshotError as exc:
            return self._fail(STATUS_STATE_REJECTED, str(exc))
        self._drop_frame()
        self._error_text = b""
        return STATUS_OK

    def _read_with_watch(self, addr: int) -> int:
        val = self.memory.read(addr)
        if addr in self._watch_read:
            pc = self.clock.instruction_pc
            self._stop = StopInfo(STOP_WATCHPOINT, pc, addr, val, WATCH_READ)
            self._stop_pending = True
        return val

    def _write_with_watch(self, addr: int, value: int) -> None:
        if addr in self._watch_write:
            pc = self.clock.instruction_pc
            self._stop = StopInfo(STOP_WATCHPOINT, pc, addr, value, WATCH_WRITE)
            self._stop_pending = True
        self.memory.write(addr, value)

    def step(self) -> int:
        """Run one instruction of the current frame and return its T-states
        (machine-core "A single step runs inside the current frame").

        The instruction runs through run_frame()'s debug arm, so it advances
        cycle_count, the frame position and the VDP like any other; a line
        end, the VBlank split or the frame end the previous call left
        pending is handled first. No breakpoint, post-step condition or
        break request is tested ("A single step ignores the break
        settings"); a watchpoint hit is recorded in last_stop(). A frame
        this call completes is counted and mixed, but its audio is
        dropped: step() returns only the T-states.
        """
        self._stop = StopInfo()
        self._step_one = True
        self._step_cycles = -1
        try:
            # One pass either runs the instruction or completes the frame
            # the previous call left at its end; the next pass then starts
            # a new frame and runs it.
            while self._step_cycles < 0:
                self.run_frame()
        finally:
            self._step_one = False
        self.take_audio()  # a step returns no audio
        self._resume_after_stop = True
        return self._step_cycles

    def run_frame(self, skip_render: bool = False) -> bytearray:
        """Run one frame, or the rest of a stopped one, up to its end or the
        next break, and return the rendered index framebuffer.

        Returns a `width * OUTPUT_HEIGHT` buffer (the frame's width, see
        v9938_renderer.render_frame) when the frame completes in this call,
        and an empty bytearray when the call stops at a break or the render
        was skipped (`skip_render` in the call that reached the render). A
        stopped frame continues on the next call (machine-core "A break stops
        step_frame and the next call continues the frame"): the render, the
        TMS9918A VBlank, the frame count, the audio mix (read it with
        take_audio()) and the frame breakpoint each happen once per frame, in
        the call that reaches them.

        Both VDP variants render at the start of vertical blanking, not at the
        end of the scanline loop, and both raise their VBlank flag/interrupt at
        that same point: rendering after the whole loop would show the VRAM the
        game's VBlank ISR left behind — sprite attributes and name tables one
        frame early, while the display registers correctly come from the
        frame-start snapshot. Splitting the loop there renders the frame the
        ISR has not touched yet, and lets the renderer's sprite-collision / 5S
        scan finish before the ISR reads S#0. The V9938 raises VBlank in
        begin_scanline(vblank_start_line) at the end of the first segment; the
        TMS9918A, which has no per-scanline hook, in VDP.begin_vblank() called
        right after the render.
        """
        lpf = self.lines_per_frame
        vdp9938 = self.vdp if isinstance(self.vdp, V9938) else None
        # begin_scanline(line) runs *after* that line's cycles, so the
        # pre-render segment is the half-open range [0, vblank_split).
        vblank_split = self.vdp.vblank_start_line + 1
        stepping = self._step_one
        if not stepping:
            if self._stop_pending or self._resume_after_stop:
                # First instruction after a stop or a step: no breakpoint test.
                self._resume_skip_pc = self.cpu.registers.PC
            self._resume_after_stop = False
        self._stop_pending = False
        if not self._frame_active:
            self._frame_active = True
            self._frame_line = 0
            self._frame_total = 0
            self._frame_start_cycle = self.cycle_count
            self._frame_rendered = False
            self._frame_result = bytearray()

        line = self._frame_line
        if line < vblank_split:
            if self._run_segment(vdp9938, line, vblank_split, stepping):
                return bytearray()
            line = vblank_split
        if not self._frame_rendered:
            if vdp9938 is not None:
                self._frame_result = render_frame_v9938(vdp9938, skip_render=skip_render)
            else:
                assert isinstance(self.vdp, VDP)
                self._frame_result = render_frame(self.vdp, skip_render=skip_render)
                self.vdp.begin_vblank()
            self._frame_rendered = True
        if line < lpf:
            if self._run_segment(vdp9938, line, lpf, stepping):
                return bytearray()
        self._resume_skip_pc = -1
        self._frame_active = False
        self.vdp.increment_frame()
        self._mix_frame_audio(self._frame_start_cycle)
        result = self._frame_result
        self._frame_result = bytearray()
        # Frame breakpoint (debugger 'gf'): once per frame, after the frame
        # is complete, so the stop reports a complete frame.
        if self._frame_breakpoint != -1 and self.vdp.frame_count == self._frame_breakpoint:
            self._frame_breakpoint = -1
            self._enter_break(STOP_BREAKPOINT)
        return result

    def _run_segment(
        self, vdp9938: V9938 | None, line: int, end_line: int, stepping: bool
    ) -> bool:
        """Run scanlines [line, end_line) of the frame, unless a break request
        comes first. Returns True when the frame stopped, after recording where
        it continues."""
        if stepping or not self._take_break_request(line):
            self._frame_total = self._run_lines(vdp9938, line, end_line, self._frame_total)
            if not self._stop_pending:
                return False
        self._resume_skip_pc = -1
        self._frame_line = self._stop_line
        return True

    def samples_per_frame(self) -> int:
        """Audio samples per frame: the output rate over the pacing rate (735 at
        60 fps NTSC, 882 at 50 fps PAL; both divide exactly)."""
        return AUDIO_SAMPLE_RATE // self.video_standard.target_fps

    def apply_input(self, frame_input: InputFrame) -> int:
        """Replace the input lines and mouse buttons with `frame_input`'s,
        effective for the next instruction (machine-core "apply_input
        replaces the input lines at once"). Feeds no mouse motion, so it can
        be called any number of times between frames (RPC injection)."""
        self.input.apply(frame_input)
        mouse = self.psg.mouse
        if mouse is not None:
            buttons = frame_input.mouse_buttons
            mouse.set_button(TRIGGER_A_BIT, bool(buttons & MOUSE_LEFT))
            mouse.set_button(TRIGGER_B_BIT, bool(buttons & MOUSE_RIGHT))
        return STATUS_OK

    def step_frame(self, frame_input: InputFrame, render_video: bool) -> FrameOutput:
        """Run one frame with `frame_input` held for the whole frame (the
        core's per-frame API, allium/core-frontend.allium FrameStepping).

        Applies the input (apply_input), feeds the frame's mouse motion to the
        attached mouse once, runs the frame, and returns its RGB24 video (when
        `render_video`), its pre-filter audio and the frame number.
        `render_video` never changes emulated state.

        A call that stops at a break returns its stop reason, no video, no
        audio and `frame_complete` 0; the next call continues the frame,
        applies its input lines and buttons and feeds no mouse motion.
        """
        self._stop = StopInfo()
        new_frame = not self._frame_active
        self.apply_input(frame_input)
        mouse = self.psg.mouse
        if mouse is not None and new_frame:
            # The core clamps the motion to 16 bits (machine-core "The core
            # masks or clamps an input value outside its range").
            dx = max(-0x8000, min(0x7FFF, frame_input.mouse_dx))
            dy = max(-0x8000, min(0x7FFF, frame_input.mouse_dy))
            mouse.add_motion(dx, dy)
        index_buf = self.run_frame(skip_render=not render_video)
        width = 0
        height = 0
        pixels = b""
        if index_buf:
            width = len(index_buf) // OUTPUT_HEIGHT
            height = OUTPUT_HEIGHT
            pixels = self.vdp.to_rgb24(index_buf)
        stop_reason = self._stop.reason if self._stop_pending else STOP_NONE
        return FrameOutput(
            width,
            height,
            pixels,
            self.take_audio(),
            self.vdp.frame_count,
            stop_reason,
            0 if self._frame_active else 1,
        )

    def take_audio(self) -> bytes:
        """Return the mixed PCM of the most recent run_frame() and clear it."""
        return self.audio.take()

    def _mix_frame_audio(self, frame_start_cycle: int) -> None:
        """Synthesize this frame's audio from every present sound source."""
        self.audio.mix_frame(
            self.samples_per_frame(),
            frame_start_cycle,
            self.cycle_count,
            self.psg,
            self.scc,
            self.dac,
            self.ppi.click if self.ppi is not None else None,
            self.fmpac.opll if self.fmpac is not None else None,
        )

    def _take_break_request(self, line: int) -> bool:
        """Honor request_break() before running the segment that starts at
        scanline `line` (machine-core "The host requests a break"). Returns
        True when the frame stops there.

        Checked at segment boundaries, not in the scanline loops: Ctrl-C needs
        no finer stop (openspec/changes/archive/2026-10-29-add-core-debug-control
        design Decision 3)."""
        if not self._break_request:
            return False
        self._break_request = False
        self._stop_line = line
        self._enter_break(STOP_REQUESTED)
        return True

    def _run_lines(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Run scanlines [first_line, end_line) with the loop arm this mode
        needs (break conditions / hot / TRACE_DEBUG); return the running T-state
        total, measured from the start of the frame.

        PORT-NOTE: the arms take `self` and `vdp9938` -- which aliases
          `self.vdp` -- at the same time, and alternate between mutating the
          VDP and mutating the Machine.
        Rust equivalent: drop the `vdp9938` parameter and dispatch on a
          `VdpKind` tag inside each arm instead, or split Machine into an
          owning bus struct plus debugger state so the two borrows are
          disjoint -- Rust's borrow checker rejects this double mutable
          borrow as written.
        C++ equivalent: no borrow-checker issue, but the same aliasing is
          worth avoiding for clarity -- prefer a `VdpKind` tag dispatch or
          the same struct split, since two mutable references/pointers to
          overlapping state is a well-known source of aliasing bugs there too.
        Kept as-is here because: semantic necessity, not performance -- this
          is a translation hazard specific to Rust's ownership model, not a
          Python optimization to preserve.
        """
        if self._break_conditions_active():
            return self._run_lines_debug(vdp9938, first_line, end_line, total)
        if not self._trace.kinds & TRACE_DEBUG:
            if self._tape_mounted():
                return self._run_lines_exact(vdp9938, first_line, end_line, total)
            return self._run_lines_fast(vdp9938, first_line, end_line, total)
        return self._run_lines_traced(vdp9938, first_line, end_line, total)

    def _tape_mounted(self) -> bool:
        """Whether the clock must count per instruction: a tape is mounted,
        so a read of the tape input sees the T-state of its instruction
        (io-cassette). Chosen by "mounted", not "motor on": a mount happens
        only between frames, so the arm cannot change inside a call however
        the host breaks the frame (add-cassette-playback design D4)."""
        return self.cassette is not None and self.cassette.mounted

    def _run_lines_debug(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop with active break conditions: checks breakpoints and
        post-step break conditions each instruction."""
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        clock = self.clock
        # Two loop-invariants lifted out of the inner loop: the post-step
        # condition test (see _post_step_checks_armed) and, as in the fast arm,
        # cycle_count aggregation into a per-line local flushed once a scanline.
        post_checks = self._post_step_checks_armed()
        step_one = self._step_one
        # While a tape is mounted the clock counts per instruction, as in
        # _run_lines_exact, so a break does not change what a read sees.
        exact = self._tape_mounted()
        # Either chip; hot-loop local for the per-instruction .irq read. When
        # vdp9938 is not None it is this same object, passed only to gate the
        # V9938 per-scanline hooks.
        vdp = self.vdp
        line_cycles = 0
        for line in range(first_line, end_line):
            line_end = (line + 1) * cpf // lpf
            while total < line_end:
                pc = cpu.registers.PC
                if pc == self._resume_skip_pc and not step_one:
                    # First instruction after a stop or a step: run it
                    # without the breakpoint test.
                    self._resume_skip_pc = -1
                elif not step_one and (pc in self._breakpoints or pc == self._temp_breakpoint):
                    if pc == self._temp_breakpoint:
                        self._temp_breakpoint = -1
                    self._enter_break(STOP_BREAKPOINT)
                    clock.cycle += line_cycles
                    self._stop_line = line
                    return total
                cpu.int_pending = vdp.irq
                n = cpu_step()
                total += n
                if exact:
                    clock.cycle += n
                else:
                    line_cycles += n
                if vdp9938 is not None:
                    # Full tick() (command-timer decrement + _line_cycle) only
                    # while a command is actually running -- see _run_lines_fast's
                    # own comment on this same optimization for the rationale.
                    if vdp9938._cmd_active:
                        vdp9938.tick(n)
                    else:
                        vdp9938._line_cycle += n
                if step_one:
                    # step(): one instruction, then return like a stop.
                    self._step_cycles = n
                    self._stop_pending = True
                elif post_checks and self._post_step_break():
                    self._enter_break(STOP_BREAKPOINT)
                if self._stop_pending:
                    # A watchpoint (or post-step condition) stopped this
                    # instruction; return at the boundary.
                    clock.cycle += line_cycles
                    self._stop_line = line
                    return total
            clock.cycle += line_cycles
            line_cycles = 0
            if vdp9938 is not None:
                vdp9938.begin_scanline(line)
        return total

    def _run_lines_fast(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Hot-path scanline loop (no break conditions, TRACE_DEBUG off); see
        `_run_lines`."""
        # This loop reads vdp9938._cmd_active and writes _line_cycle directly, per
        # instruction: a method call here costs measurable frame rate.
        # Hot path (no break conditions, TRACE_DEBUG off). Two frame-invariants are lifted
        # out of the inner loop: (1) the is_v9938 branch — split into a
        # V9938 loop and a plain loop so the per-instruction `if vdp9938 /
        # if vdp_tick` tests vanish; (2) cycle_count aggregation — summed
        # into a per-line local and flushed once per scanline instead of
        # once per instruction. Line granularity is the finest flush
        # allowed: devices read cycle_count *within* a frame, so a frame-end
        # flush would starve them; a one-scanline lag matches the existing
        # scanline-stepped timing. The duplicated loop body is the
        # readability cost of removing that per-instruction overhead.
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        clock = self.clock
        if vdp9938 is not None:
            for line in range(first_line, end_line):
                line_end = (line + 1) * cpf // lpf
                line_cycles = 0
                while total < line_end:
                    cpu.int_pending = vdp9938.irq
                    # HALT-spin (EI; HALT waiting for VBlank/line IRQ) and
                    # JR $ idle loops are the most common execution pattern
                    # in MSX software (HALT-spin alone measured ~74% of all
                    # step() calls on a representative title), so batch
                    # their per-instruction stepping when it's provably
                    # safe: would_accept_interrupt is the exact condition
                    # step() itself checks (KEEP IN SYNC with z80.py's
                    # Z80.step(), the `if self.int_pending and self.iff1 and
                    # not self.ei_pending:` line) before its halted branch,
                    # which never mutates any of nmi_pending/int_pending/
                    # iff1/ei_pending/halted -- so none of them can flip
                    # partway through a batch. The remaining clause is "no
                    # VDP command is running": with a command active,
                    # tick() can raise the command-engine interrupt at a
                    # T-state offset this loop can't predict without calling
                    # tick() anyway, so batching is skipped and the normal
                    # per-instruction path (below) still ticks it. See
                    # openspec cpu-z80 "Batched HALT execution is externally
                    # transparent" for the full equivalence proof.
                    # cpu.idle_hint also covers a JR $ (18 FE) idle loop:
                    # executing it does no port I/O or memory write and
                    # touches none of those flags either, so the same
                    # argument holds; step_idle_batch() re-checks the code
                    # at PC and falls back to one step() if it changed
                    # (openspec cpu-z80 "Batched JR-to-self execution is
                    # externally transparent").
                    # PORT-NOTE: this condition is written out here, in the
                    #   TMS9918A branch below and (negated) in Z80.step().
                    # Rust equivalent: one #[inline] fn can_batch_idle(&self)
                    #   on Z80 (idle_hint && !nmi_pending && !(int_pending &&
                    #   iff1 && !ei_pending)), plus `&& !cmd_active` here.
                    # C++ equivalent: an inline member function, same body.
                    # Kept as-is here because: a method call per
                    #   instruction costs CPython more than the inline test.
                    would_accept_interrupt = (
                        cpu.int_pending and cpu.iff1 and not cpu.ei_pending
                    )
                    if (
                        cpu.idle_hint
                        and not cpu.nmi_pending
                        and not would_accept_interrupt
                        and not vdp9938._cmd_active
                    ):
                        n = cpu.step_idle_batch(line_end - total)
                    else:
                        n = cpu_step()
                    total += n
                    line_cycles += n
                    # Full tick() (command-timer decrement + _line_cycle) only
                    # while a command is actually running: _cmd_active is False
                    # for most of a game's runtime, and tick()'s per-instruction
                    # call overhead (profiled at roughly Z80.step()'s own
                    # self-time) is otherwise paid for two branches that would
                    # both be no-ops anyway. _line_cycle (the S#2 horizontal-
                    # retrace input) still needs updating every instruction
                    # regardless, via the direct increment below -- see
                    # openspec vdp-command-engine for the full rationale.
                    if vdp9938._cmd_active:
                        vdp9938.tick(n)
                    else:
                        vdp9938._line_cycle += n
                clock.cycle += line_cycles
                vdp9938.begin_scanline(line)
        else:
            vdp = self.vdp  # hot-loop local for the per-instruction .irq read
            for line in range(first_line, end_line):
                line_end = (line + 1) * cpf // lpf
                line_cycles = 0
                while total < line_end:
                    cpu.int_pending = vdp.irq
                    # Same HALT / JR $ batching as the V9938 branch
                    # above (no _cmd_active clause: the TMS9918A has no
                    # command engine, so int_pending can't change mid-line
                    # by any other means while the CPU is idle). KEEP IN
                    # SYNC with z80.py's Z80.step() interrupt-accept check
                    # and this same condition in the V9938 branch above.
                    would_accept_interrupt = (
                        cpu.int_pending and cpu.iff1 and not cpu.ei_pending
                    )
                    if (
                        cpu.idle_hint
                        and not cpu.nmi_pending
                        and not would_accept_interrupt
                    ):
                        n = cpu.step_idle_batch(line_end - total)
                    else:
                        n = cpu_step()
                    total += n
                    line_cycles += n
                clock.cycle += line_cycles
        return total

    def _run_lines_exact(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop while a tape is mounted (no break conditions,
        TRACE_DEBUG off): as _run_lines_fast, but the clock counts each
        instruction before the next one starts, so a device reads the T-state
        of the instruction that accesses it. The HALT and JR $ batching of
        _run_lines_fast stays: a batched idle instruction does no port I/O."""
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        clock = self.clock
        vdp = self.vdp
        for line in range(first_line, end_line):
            line_end = (line + 1) * cpf // lpf
            while total < line_end:
                cpu.int_pending = vdp.irq
                # The batching condition of _run_lines_fast (KEEP IN SYNC).
                would_accept_interrupt = (
                    cpu.int_pending and cpu.iff1 and not cpu.ei_pending
                )
                if (
                    cpu.idle_hint
                    and not cpu.nmi_pending
                    and not would_accept_interrupt
                    and not (vdp9938 is not None and vdp9938._cmd_active)
                ):
                    n = cpu.step_idle_batch(line_end - total)
                else:
                    n = cpu_step()
                total += n
                clock.cycle += n
                if vdp9938 is not None:
                    if vdp9938._cmd_active:
                        vdp9938.tick(n)
                    else:
                        vdp9938._line_cycle += n
            if vdp9938 is not None:
                vdp9938.begin_scanline(line)
        return total

    def _run_lines_traced(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop while TRACE_DEBUG is on: records PC-loop and HALT+DI
        hang events."""
        trace = self._trace
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        clock = self.clock
        # Either chip; hot-loop local for the per-instruction .irq read. When
        # vdp9938 is not None it is this same object, passed only to gate the
        # V9938 per-scanline hooks.
        vdp = self.vdp
        for line in range(first_line, end_line):
            line_end = (line + 1) * cpf // lpf
            while total < line_end:
                pc = cpu.registers.PC
                cpu.int_pending = vdp.irq
                n = cpu_step()
                total += n
                clock.cycle += n
                if vdp9938 is not None:
                    # Same optimization as _run_lines_fast's own comment.
                    if vdp9938._cmd_active:
                        vdp9938.tick(n)
                    else:
                        vdp9938._line_cycle += n
                if not (cpu.halted and cpu.iff1):
                    if pc == self._last_pc:
                        # The count stops at the threshold, so it never wraps
                        # and the hang is recorded once per run of repeats.
                        if self._pc_repeat < HANG_PC_REPEAT_THRESHOLD:
                            self._pc_repeat += 1
                            if self._pc_repeat == HANG_PC_REPEAT_THRESHOLD:
                                trace.record_hang(TRACE_EVENT_HANG_PC_LOOP, pc)
                    else:
                        self._pc_repeat = 0
                    self._last_pc = pc
            if vdp9938 is not None:
                vdp9938.begin_scanline(line)

        # Once per frame, not once per segment: only the segment that ends the
        # frame reports a HALT-with-interrupts-off hang.
        is_final_segment = end_line >= lpf
        if is_final_segment and cpu.halted and not cpu.iff1:
            trace.record_hang(TRACE_EVENT_HANG_HALT_DI, cpu.registers.PC)
        return total


def _media_label(media_id: int) -> str:
    """A media id for a message: its MediaId name, or its number for an
    integer outside MediaId (the ids are 0 to DRIVE_D, without gaps)."""
    if 0 <= media_id <= MediaId.DRIVE_D:
        return MediaId(media_id).name
    return str(int(media_id))
