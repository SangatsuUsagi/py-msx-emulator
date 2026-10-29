from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from msx.audio_mixer import AudioMixer
from msx.boundary import (
    STATUS_BAD_ARGUMENT,
    STATUS_BAD_IMAGE,
    STATUS_NO_DRIVE,
    STATUS_NO_MEDIUM,
    STATUS_OK,
    STATUS_STATE_REJECTED,
    STOP_BREAKPOINT,
    STOP_NONE,
    STOP_REQUESTED,
    STOP_WATCHPOINT,
    VDP_KIND_TMS9918A,
    VDP_KIND_V9938,
    WATCH_READ,
    WATCH_WRITE,
    CpuRegisters,
    FrameOutput,
    MediaId,
    RgbFrame,
    StopInfo,
    VdpInfo,
    drive_media_id,
)
from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.input import InputFrame, InputLines
from msx.io import IOBus
from msx.kanji import KanjiRom
from msx.machine_spec import KeyboardLayout
from msx.mapper import HalnoteMapper, MajutsushiMapper
from msx.memory import Memory
from msx.mouse import TRIGGER_A_BIT, TRIGGER_B_BIT, MouseDevice
from msx.ppi import PPI
from msx.psg import PSG, SAMPLE_RATE, JoystickPort
from msx.scc import SCC
from msx.sram import Medium, SramImage
from msx.vdp._geometry import OUTPUT_H, VideoStandard
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


@dataclass
class Machine:
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
    halnote_cart: HalnoteMapper | None = field(default=None, repr=False)
    kanji: KanjiRom | None = field(default=None, repr=False)
    ppi: PPI | None = field(default=None, repr=False)
    rtc: "RTC | None" = field(default=None, repr=False)
    input: InputLines = field(default_factory=InputLines)
    # Per-frame audio mix, produced by run_frame() and read with take_audio().
    audio: AudioMixer = field(default_factory=AudioMixer, repr=False)
    video_standard: VideoStandard = VideoStandard.NTSC
    # The keyboard matrix layout, from the machine definition (frontends pick
    # their host-key table from it).
    keyboard_layout: KeyboardLayout = KeyboardLayout.INTERNATIONAL
    # Free-running T-state clock, never reset.
    #
    # PORT-NOTE: at 3.58 MHz a u32 wraps in ~20 minutes of emulated run time.
    # Rust/C++ equivalent: u64/uint64_t for this field; the per-frame totals
    #   threaded through _run_lines() are frame-relative and fit in u32.
    # Kept as-is here because: semantic necessity, not performance -- Python's
    #   arbitrary-precision int never wraps, so this width requirement is
    #   invisible in Python and must be sized explicitly at port time.
    cycle_count: int = 0
    _logger: DebugLogger | None = field(default=None, repr=False)
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
    _resume_skip_pc: int | None = field(default=None, init=False, repr=False)
    _breakpoints: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_read: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_write: frozenset[int] = field(default_factory=frozenset, repr=False)
    _last_pc: int = field(default=0, init=False, repr=False)
    _pc_repeat: int = field(default=0, init=False, repr=False)
    # Crash-signature auto-break conditions (debugger bh/bs).
    _break_halt_di: bool = field(default=False, init=False, repr=False)
    _sp_range: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _halt_di_seen: bool = field(default=False, init=False, repr=False)
    _sp_out_seen: bool = field(default=False, init=False, repr=False)
    # Targeted execution control (debugger g/so); one-shot, cleared on hit.
    _temp_breakpoint: int | None = field(default=None, init=False, repr=False)
    _stepout_sp: int | None = field(default=None, init=False, repr=False)
    # One-shot run-to-frame breakpoint (debugger 'gf'); checked once per
    # run_frame() call, not per instruction — see set_frame_breakpoint.
    _frame_breakpoint: int | None = field(default=None, init=False, repr=False)
    # The most recent stop (last_stop()), cleared at the start of each
    # step_frame() and step() call.
    _stop: StopInfo = field(default_factory=StopInfo, init=False, repr=False)
    # Set by request_break() (Ctrl-C); run_frame() honors it before the next
    # frame segment.
    _break_request: bool = field(default=False, init=False, repr=False)
    # The message of the last failed load_state or mount_disk (UTF-8), b""
    # after a successful one (last_error_text()).
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
    def cycles_per_frame(self) -> int:
        """Z80 T-states per frame, from the video standard."""
        return self.video_standard.cycles_per_frame

    @property
    def lines_per_frame(self) -> int:
        """Scanlines per frame, from the video standard."""
        return self.video_standard.lines_per_frame

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

    def reset(self) -> None:
        """Full power-on reset: CPU, PSG, SCC (if present), FM-PAC (if
        present), VDP, FDC (if present), Kanji ROM (if present), PPI (if
        present), the primary/secondary slot registers, the RAM mapper (if
        present, in either its slot-3 or its expanded-slot-2-sub-slot
        placement), and every `Mapper` -- `memory._mapper`, `memory._mapper2`,
        and every non-`None` entry of an expanded slot's per-sub-slot array
        (this also covers a present Halnote cartridge, which only ever lives
        in one of those sub-slot entries). Memory/VRAM contents are retained.

        Every `Mapper` is reset unconditionally regardless of whether a given
        real cartridge's bank-select hardware is actually wired to the slot
        reset signal -- rationale in openspec/changes/
        wire-cartridge-mapper-and-ppi-reset."""
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
        # Power-on slot state: all pages select slot 0 (matches construction).
        self.memory.set_slot_register(0x00)
        self.memory.set_sub_slot_reg(0x00)
        self.memory.set_slot1_sub_slot_reg(0x00)
        self.memory.set_slot2_sub_slot_reg(0x00)
        if self.memory.ram_mapper is not None:
            self.memory.ram_mapper.reset()
        self.memory._mapper.reset()
        self.memory._mapper2.reset()
        # halnote_cart (when present) is always also one of these sub-slot
        # entries -- _wire_expanded_overlay is the only place that ever
        # constructs a HalnoteMapper (EXTENSION_DEVICES, the flat overlay's
        # device allow-list in msx/machine_config.py, lacks "halnote") -- so
        # resetting it via this loop, not a separate halnote_cart branch,
        # covers it exactly once. Slot 1's own sub-slots (openspec/changes/
        # slot1-slot2-symmetrization) get the identical treatment.
        for sub_mapper in (*self.memory._mapper1_subslots, *self.memory._mapper2_subslots):
            if sub_mapper is not None:
                sub_mapper.reset()
        self._drop_frame()

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

    def _media(self) -> dict[MediaId, Medium]:
        media: dict[MediaId, Medium] = dict(self.sram_media())
        if self.fdc is not None:
            for index, drive in enumerate(self.fdc.drives):
                if drive.image is not None:
                    media[drive_media_id(index)] = drive.image
        return media

    def dirty_media(self) -> list[MediaId]:
        """Ids of the media written since they were last persisted."""
        dirty: list[MediaId] = []
        for media_id, medium in self._media().items():
            if medium.dirty:
                dirty.append(media_id)
        return dirty

    def read_media(self, media_id: MediaId) -> bytes | None:
        """A medium's current bytes, or None for an id the machine does not
        have (last_error_text() gives the message)."""
        medium = self._media().get(media_id)
        if medium is None:
            self._fail(STATUS_NO_MEDIUM, f"no medium {media_id.name} on this machine")
            return None
        self._error_text = b""
        return medium.data()

    def mark_persisted(self, media_id: MediaId) -> int:
        """Clear a medium's dirty flag after the host has written it, and
        return STATUS_OK. Returns STATUS_NO_MEDIUM, changing nothing, for an
        id the machine does not have."""
        medium = self._media().get(media_id)
        if medium is None:
            return self._fail(STATUS_NO_MEDIUM, f"no medium {media_id.name} on this machine")
        medium.mark_persisted()
        self._error_text = b""
        return STATUS_OK

    def mount_disk(self, drive: int, data: bytes | None, write_protected: bool) -> int:
        """Swap the image `data` (None ejects) into `drive` (0 = A),
        rejecting sector writes when `write_protected`, and return STATUS_OK.
        The host writes a dirty outgoing image back before the swap. Returns
        STATUS_NO_DRIVE for a machine without that drive and STATUS_BAD_IMAGE
        for an image whose size is not a positive multiple of 512, changing
        nothing (last_error_text() gives the message)."""
        from msx.fdc.disk_image import SECTOR_SIZE, DskDiskImage

        if self.fdc is None or not 0 <= drive < len(self.fdc.drives):
            return self._fail(STATUS_NO_DRIVE, f"no floppy drive {drive} on this machine")
        new_image = None
        if data is not None:
            size = len(data)
            if size == 0 or size % SECTOR_SIZE != 0:
                return self._fail(
                    STATUS_BAD_IMAGE,
                    f"size {size} is not a positive multiple of {SECTOR_SIZE}",
                )
            new_image = DskDiskImage(data, write_protected=write_protected)
        self.fdc.swap(drive, new_image)
        self._error_text = b""
        return STATUS_OK

    def last_error_text(self) -> bytes:
        """The UTF-8 message of the last failed load_state or mount_disk
        call, or b"" when the last such call succeeded."""
        return self._error_text

    def _fail(self, status: int, message: str) -> int:
        self._error_text = message.encode("utf-8")
        return status

    def disk_mounted(self, drive: int) -> bool:
        """Whether `drive` (0 = A) exists and holds an image."""
        return self._mounted_image(drive) is not None

    def _mounted_image(self, drive: int) -> DskDiskImage | None:
        if self.fdc is None or not 0 <= drive < len(self.fdc.drives):
            return None
        return self.fdc.drives[drive].image

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

    def _enter_break(self, reason: int) -> None:
        """Record a stop (`reason` is a STOP_* value) at the current PC and
        make the running loop arm return at it."""
        self._stop = StopInfo(reason, self.cpu.registers.PC)
        self._stop_pending = True

    def request_break(self) -> None:
        """Ask the core to stop before the next frame segment (machine-core
        "The host requests a break"). Safe to call from a signal handler: it
        only sets a flag."""
        self._break_request = True

    def last_stop(self) -> StopInfo:
        """The most recent stop of step_frame() or step(), or a STOP_NONE
        record when that call did not stop (machine-core "The last stop is
        reported as a flat record")."""
        return self._stop

    def set_breakpoints(self, addrs: list[int]) -> None:
        """Set breakpoint addresses (max 4). Replaces existing set."""
        self._breakpoints = frozenset(addrs[:4])

    def set_break_halt_di(self, enabled: bool) -> None:
        """Enable/disable breaking when the CPU executes HALT with interrupts off."""
        self._break_halt_di = enabled
        self._halt_di_seen = False

    def set_sp_range(self, rng: tuple[int, int] | None) -> None:
        """Set the valid-RAM range for SP; break when SP leaves it. None disables."""
        self._sp_range = rng
        self._sp_out_seen = False

    def set_temp_breakpoint(self, addr: int | None) -> None:
        """Set a one-shot run-to breakpoint (debugger 'g'); cleared when hit."""
        self._temp_breakpoint = addr

    def set_step_out(self, sp: int) -> None:
        """Break (once) when SP rises above sp, i.e. the current routine returns."""
        self._stepout_sp = sp

    def set_frame_breakpoint(self, frame: int | None) -> None:
        """Set a one-shot run-to-frame breakpoint (debugger 'gf'); cleared when hit."""
        self._frame_breakpoint = frame

    def _break_conditions_active(self) -> bool:
        """True when any execution-break condition needs the per-instruction loop."""
        return (
            self._step_one
            or bool(self._breakpoints)
            or self._break_halt_di
            or self._sp_range is not None
            or self._temp_breakpoint is not None
            or self._stepout_sp is not None
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
        if self._sp_range is not None:
            sp = cpu.registers.SP
            if sp < self._sp_range[0] or sp > self._sp_range[1]:
                if not self._sp_out_seen:
                    self._sp_out_seen = True
                    return True
            else:
                self._sp_out_seen = False
        if self._stepout_sp is not None and cpu.registers.SP > self._stepout_sp:
            self._stepout_sp = None
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
            or self._sp_range is not None
            or self._stepout_sp is not None
        )

    def set_watchpoints(self, entries: list[tuple[int, int]]) -> int:
        """Set watchpoints: [(addr, kind), ...], where kind is WATCH_READ,
        WATCH_WRITE or both ORed. Max 4. Returns STATUS_OK, or
        STATUS_BAD_ARGUMENT, keeping the current watchpoints, for a kind of 0
        or with another bit."""
        for _addr, kind in entries[:4]:
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
        for addr, kind in entries[:4]:
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
        device register reads as it does for the CPU, side effects included."""
        read = self.memory.read
        out = bytearray(length)
        for i in range(length):
            out[i] = read((addr + i) & 0xFFFF)
        return bytes(out)

    def write_memory(self, addr: int, data: bytes) -> None:
        """Write `data` from `addr` as the CPU would, wrapping at 0xFFFF.
        Bypasses watchpoints."""
        write = self.memory.write
        for i in range(len(data)):
            write((addr + i) & 0xFFFF, data[i])

    def vram_size(self) -> int:
        """VRAM size in bytes (0x4000 or 0x20000)."""
        return len(self.vdp.vram)

    def read_vram(self, addr: int, length: int) -> bytes:
        """`length` VRAM bytes from `addr`, wrapping at the VRAM size."""
        vram = self.vdp.vram
        size = len(vram)
        out = bytearray(length)
        for i in range(length):
            out[i] = vram[(addr + i) % size]
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
        except ValueError as exc:
            return self._fail(STATUS_STATE_REJECTED, str(exc))
        self._drop_frame()
        self._error_text = b""
        return STATUS_OK

    def _read_with_watch(self, addr: int) -> int:
        val = self.memory.read(addr)
        if addr in self._watch_read:
            pc = self.cpu.instruction_pc
            self._stop = StopInfo(STOP_WATCHPOINT, pc, addr, val, WATCH_READ)
            self._stop_pending = True
        return val

    def _write_with_watch(self, addr: int, value: int) -> None:
        if addr in self._watch_write:
            pc = self.cpu.instruction_pc
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
        self.audio.take()
        self._resume_after_stop = True
        return self._step_cycles

    def run_frame(self, skip_render: bool = False) -> bytearray:
        """Run one frame, or the rest of a stopped one, up to its end or the
        next break, and return the rendered index framebuffer.

        Returns a `width * OUTPUT_H` buffer (the frame's width, see
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
        total = self._frame_total
        if line < vblank_split:
            if not stepping and self._take_break_request(line):
                return self._keep_stopped_frame(total)
            total = self._run_lines(vdp9938, line, vblank_split, total)
            if self._stop_pending:
                return self._keep_stopped_frame(total)
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
            if not stepping and self._take_break_request(line):
                return self._keep_stopped_frame(total)
            total = self._run_lines(vdp9938, line, lpf, total)
            if self._stop_pending:
                return self._keep_stopped_frame(total)
        self._resume_skip_pc = None
        self._frame_active = False
        self.vdp.increment_frame()
        self._mix_frame_audio(self._frame_start_cycle)
        result = self._frame_result
        self._frame_result = bytearray()
        # Frame breakpoint (debugger 'gf'): once per frame, after the frame
        # is complete, so the stop reports a complete frame.
        if self._frame_breakpoint is not None and self.vdp.frame_count == self._frame_breakpoint:
            self._frame_breakpoint = None
            self._enter_break(STOP_BREAKPOINT)
        return result

    def _keep_stopped_frame(self, total: int) -> bytearray:
        """Record where a stopped frame continues; return no video."""
        self._resume_skip_pc = None
        self._frame_line = self._stop_line
        self._frame_total = total
        return bytearray()

    def samples_per_frame(self) -> int:
        """Audio samples per frame: the output rate over the pacing rate (735 at
        60 fps NTSC, 882 at 50 fps PAL; both divide exactly)."""
        return int(SAMPLE_RATE // self.video_standard.target_fps)

    def apply_input(self, frame_input: InputFrame) -> None:
        """Replace the input lines and mouse buttons with `frame_input`'s,
        effective for the next instruction (machine-core "apply_input
        replaces the input lines at once"). Feeds no mouse motion, so it can
        be called any number of times between frames (RPC injection)."""
        self.input.apply(frame_input)
        mouse = self.psg.mouse
        if mouse is not None:
            mouse.set_button(TRIGGER_A_BIT, frame_input.mouse_left)
            mouse.set_button(TRIGGER_B_BIT, frame_input.mouse_right)

    def step_frame(self, frame_input: InputFrame, render_video: bool) -> FrameOutput:
        """Run one frame with `frame_input` held for the whole frame (the
        core's per-frame API, allium/core-frontend.allium FrameStepping).

        Applies the input (apply_input), feeds the frame's mouse motion to the
        attached mouse once, runs the frame, and returns its RGB24 video (when
        `render_video`), its pre-filter audio and the frame number.
        `render_video` never changes emulated state.

        A call that stops at a break returns its stop reason, no video, no
        audio and `frame_complete` false; the next call continues the frame,
        applies its input lines and buttons and feeds no mouse motion.
        """
        self._stop = StopInfo()
        new_frame = not self._frame_active
        self.apply_input(frame_input)
        mouse = self.psg.mouse
        if mouse is not None and new_frame:
            mouse.add_motion(frame_input.mouse_dx, frame_input.mouse_dy)
        index_buf = self.run_frame(skip_render=not render_video)
        video: RgbFrame | None = None
        if index_buf:
            video = RgbFrame(
                len(index_buf) // OUTPUT_H, OUTPUT_H, self.vdp.to_rgb24(index_buf)
            )
        stop_reason = self._stop.reason if self._stop_pending else STOP_NONE
        return FrameOutput(
            video,
            self.take_audio(),
            self.vdp.frame_count,
            stop_reason,
            not self._frame_active,
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
        no finer stop (openspec/changes/add-core-debug-control design
        Decision 3)."""
        if not self._break_request:
            return False
        self._break_request = False
        self._stop_line = line
        self._enter_break(STOP_REQUESTED)
        return self._stop_pending

    def _run_lines(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Run scanlines [first_line, end_line) with the loop arm this mode
        needs (break conditions / hot / logger); return the running T-state
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
        if self._logger is None:
            return self._run_lines_fast(vdp9938, first_line, end_line, total)
        return self._run_lines_logged(vdp9938, first_line, end_line, total)

    def _run_lines_debug(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop with active break conditions: checks breakpoints and
        post-step break conditions each instruction."""
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        # Two loop-invariants lifted out of the inner loop: the post-step
        # condition test (see _post_step_checks_armed) and, as in the fast arm,
        # cycle_count aggregation into a per-line local flushed once a scanline.
        post_checks = self._post_step_checks_armed()
        step_one = self._step_one
        # Either chip; hot-loop local for the per-instruction .irq read. When
        # vdp9938 is not None it is this same object, passed only to gate the
        # V9938 per-scanline hooks.
        vdp = self.vdp
        line_cycles = 0
        for line in range(first_line, end_line):
            line_end = (line + 1) * cpf // lpf
            while total < line_end:
                pc = cpu.registers.PC
                if step_one:
                    pass  # step(): no breakpoint test
                elif pc == self._resume_skip_pc:
                    # First instruction after a stop or a step: run it
                    # without the breakpoint test.
                    self._resume_skip_pc = None
                elif pc in self._breakpoints or pc == self._temp_breakpoint:
                    if pc == self._temp_breakpoint:
                        self._temp_breakpoint = None
                    self._enter_break(STOP_BREAKPOINT)
                    self.cycle_count += line_cycles
                    self._stop_line = line
                    return total
                cpu.int_pending = vdp.irq
                n = cpu_step()
                total += n
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
                    self.cycle_count += line_cycles
                    self._stop_line = line
                    return total
            self.cycle_count += line_cycles
            line_cycles = 0
            if vdp9938 is not None:
                vdp9938.begin_scanline(line)
        return total

    def _run_lines_fast(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Hot-path scanline loop (no debugger, no logger); see `_run_lines`."""
        # Hot path (no debugger, no logger). Two frame-invariants are lifted
        # out of the inner loop: (1) the is_v9938 branch — split into a
        # V9938 loop and a plain loop so the per-instruction `if vdp9938 /
        # if vdp_tick` tests vanish; (2) cycle_count aggregation — summed
        # into a per-line local and flushed once per scanline instead of
        # once per instruction. Line granularity is the finest flush
        # allowed: io/dac read cycle_count *within* a frame, so a frame-end
        # flush would starve them; a one-scanline lag matches the existing
        # scanline-stepped timing. The duplicated loop body is the
        # readability cost of removing that per-instruction overhead.
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
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
                self.cycle_count += line_cycles
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
                self.cycle_count += line_cycles
        return total

    def _run_lines_logged(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop with diagnostic logging: detects PC-loop / HALT+DI hangs."""
        assert self._logger is not None
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
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
                self.cycle_count += n
                if vdp9938 is not None:
                    # Same optimization as _run_lines_fast's own comment.
                    if vdp9938._cmd_active:
                        vdp9938.tick(n)
                    else:
                        vdp9938._line_cycle += n
                if not (cpu.halted and cpu.iff1):
                    if pc == self._last_pc:
                        self._pc_repeat += 1
                        if self._pc_repeat >= HANG_PC_REPEAT_THRESHOLD:
                            self._logger.on_hang_pc_loop(pc)
                    else:
                        self._pc_repeat = 0
                    self._last_pc = pc
            if vdp9938 is not None:
                vdp9938.begin_scanline(line)

        # Once per frame, not once per segment: only the segment that ends the
        # frame reports a HALT-with-interrupts-off hang.
        is_final_segment = end_line >= lpf
        if is_final_segment and cpu.halted and not cpu.iff1:
            self._logger.on_hang_halt_di(cpu.registers.PC)
        return total


