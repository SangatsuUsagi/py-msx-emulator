"""Western Digital WD2793 floppy disk controller (functional model).

Register-level chip emulation. Transfers are resolved to/from a whole 512-byte
sector buffer and paced by DRQ per byte (the MSX DISK ROM polls STATUS/DRQ, not
T-states), so the model is deterministic rather than cycle-accurate. Type I
commands are the exception: their step, settle and verify times run on the
machine clock (msx/clock.py), and the state catches up at each register
access. Status-bit constants and command decode follow openMSX
``WD2793.cc`` and the WD279X data sheet
(references/docs/WESTERN_DIGITAL_WD279X.md).

The status byte's bit meanings depend on the command type:
  Type I  (positioning): INDEX/TRACK00/CRC/SEEK_ERROR/HEAD_LOADED/WPRT/NOT_READY
  Type II/III (transfer): DRQ/LOST_DATA/CRC/RECORD_NOT_FOUND/REC_TYPE/WPRT/NOT_READY
BUSY (bit 0) is common to both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from msx.clock import CPU_CLOCK_HZ, CoreClock
from msx.fdc.disk_drive import DiskDrive
from msx.snapshot_schema import (
    SAFE_INT_MAX,
    check_bool,
    check_bytes,
    check_choice,
    check_int,
    check_object,
)


class Mode(Enum):
    """Controller transfer state (an enum, not a string, for a clean port)."""
    IDLE = 0
    READ = 1
    WRITE = 2
    WRITE_TRACK = 3


class Type1Phase(Enum):
    """What a running Type I command waits for (NONE: no Type I command runs)."""
    NONE = 0
    STEP = 1    # the step time after a step pulse
    SETTLE = 2  # the head settle time before a verify
    VERIFY = 3  # an ID field with the TRACK number
    NO_INDEX = 4  # a verify with no disk: index pulses to start the search

# Status register bits (shared byte; meaning depends on command type).
BUSY: int = 0x01
S_DRQ: int = 0x02             # Type II/III: data request
INDEX: int = 0x02             # Type I: index pulse (timing; always 0 here)
TRACK00: int = 0x04           # Type I: head at track 0
CRC_ERROR: int = 0x08
SEEK_ERROR: int = 0x10        # Type I
RECORD_NOT_FOUND: int = 0x10  # Type II/III: addressed sector absent
HEAD_LOADED: int = 0x20       # Type I: the HLD output (HLT is tied high on MSX)
LOST_DATA: int = 0x04         # Type II/III
RECORD_TYPE: int = 0x20       # Type II/III
WRITE_PROTECTED: int = 0x40
NOT_READY: int = 0x80

# Status bits a Type II / Type III command clears when it starts.
_TYPE2_CLEAR: int = LOST_DATA | RECORD_NOT_FOUND | RECORD_TYPE | WRITE_PROTECTED
_TYPE3_CLEAR: int = LOST_DATA | RECORD_NOT_FOUND | RECORD_TYPE

# Type I command flags.
T_FLAG: int = 0x10            # STEP/STEP-IN/STEP-OUT: update TRACK too
H_FLAG: int = 0x08            # load the head at the start
V_FLAG: int = 0x04            # verify the track after the last step
STEP_RATE: int = 0x03         # r1r0: index into STEP_CYCLES
# FORCE INTERRUPT condition bit: hold INTRQ until a plain 0xD0.
IMMEDIATE_IRQ: int = 0x08

SECTOR_SIZE: int = 512
# Approximate 2DD MFM track capacity; WRITE TRACK consumes this many bytes then
# terminates. The DISK ROM polls DRQ (not a byte count), so any value in this
# range works — the exact count is not observable by software.
TRACK_BYTES: int = 6250


def _ms_to_cycles(ms: int) -> int:
    return CPU_CLOCK_HZ * ms // 1000


# Type I times for the 1 MHz FDC clock of MSX machines, in CPU T-states
# (WD279X data sheet; openMSX WD2793::step uses the same step times).
STEP_CYCLES: tuple[int, int, int, int] = (
    _ms_to_cycles(6), _ms_to_cycles(12), _ms_to_cycles(20), _ms_to_cycles(30),
)
SETTLE_CYCLES: int = _ms_to_cycles(30)
# No ID field with the TRACK number within 5 revolutions (300 rpm): seek error.
VERIFY_FAIL_CYCLES: int = _ms_to_cycles(1000)
# HLD drops after 15 index pulses with the controller idle (300 rpm).
HEAD_UNLOAD_CYCLES: int = _ms_to_cycles(3000)


def _is_type1(command: int) -> bool:
    """Type I (positioning) or FORCE INTERRUPT -- both read STATUS the same way."""
    return (command & 0x80) == 0 or (command & 0xF0) == 0xD0


def _is_restore_or_seek(command: int) -> bool:
    """RESTORE (0x0X) or SEEK (0x1X): the Type I commands that step toward
    a target track and always update TRACK."""
    return (command & 0xE0) == 0x00


class WD2793State(TypedDict):
    """Save-state schema for WD2793.snapshot()/apply_state().

    ``drive`` is not part of this state -- it's a live alias into whatever
    list the connection-style layer owns, reattached by the machine's own
    construction, same as a mapper's rom: bytes is never part of
    Mapper.snapshot().
    """

    command_reg: int
    track_reg: int
    sector_reg: int
    data_reg: int
    status_reg: int
    intrq: bool
    drq: bool
    mode: int
    buffer: bytearray
    index: int
    step_dir: int
    immediate_irq: bool
    type1_phase: int
    # Cycles from the save to the end of the current Type I phase (0 if due).
    type1_cycles_left: int
    hld: bool
    # Cycles from the controller going idle with the head loaded to the save.
    hld_idle_cycles: int


# PORT-NOTE: key set read from the TypedDict at import time (see
#   msx/vdp/v9938.py's _STATE_KEYS note: serde `deny_unknown_fields` in Rust,
#   a constexpr key array in C++).
_STATE_KEYS: frozenset[str] = frozenset(WD2793State.__annotations__)


@dataclass
class WD2793:
    """WD2793 controller bound to a single selected drive.

    The connection-style interface sets ``drive`` on drive-select and updates
    ``drive.side`` on side-select before issuing transfer commands.
    """

    # PORT-NOTE: this is a live alias into whatever list the connection-style
    #   layer (e.g. FloppyDisk.drives) owns, reassigned on every drive-select
    #   write and read again inside several command handlers below. Fine under
    #   Python's GC/reference semantics.
    # Rust equivalent: this struct can't both own nothing and hold a
    #   persistent &mut into an element a sibling struct owns -- resolve by
    #   threading a selected-drive index/parameter through instead of storing
    #   a reference here.
    # C++ equivalent: a raw/shared pointer alias works, but is worth avoiding
    #   for the same lifetime-safety reason -- prefer a selected-drive index
    #   passed explicitly, same as the Rust shape.
    # Kept as-is here because: port target/shape not decided yet -- no
    #   C++/Rust port exists yet (see CLAUDE.md's "pure Python" tech-stack
    #   note), and the fix touches this class's entire public API plus every
    #   caller.
    drive: DiskDrive | None = None
    command_reg: int = 0
    track_reg: int = 0
    sector_reg: int = 1
    data_reg: int = 0
    status_reg: int = 0
    _intrq: bool = False
    _drq: bool = False
    _mode: Mode = Mode.IDLE
    _buffer: bytearray = field(default_factory=bytearray)
    _index: int = 0
    _step_dir: int = 1
    _immediate_irq: bool = False
    _clock: CoreClock = field(default_factory=CoreClock, repr=False)
    _phase: Type1Phase = Type1Phase.NONE
    _event_cycle: int = 0
    _hld: bool = False
    # The idle time for the head unload is _hld_base_age + (clock.cycle -
    # _hld_base_cycle). Two fields, so a restored state with more idle
    # cycles than the clock's count needs no base below zero (an unsigned
    # clock in a native core).
    _hld_base_cycle: int = 0
    _hld_base_age: int = 0

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Power-on/reset state, then RESTORE, as openMSX WD2793::reset does.
        drive is kept: reset() does not disconnect the selected drive."""
        self.track_reg = 0
        self.sector_reg = 1
        self.data_reg = 0
        self.status_reg = 0
        self._intrq = False
        self._drq = False
        self._immediate_irq = False
        self._mode = Mode.IDLE
        self._buffer = bytearray()
        self._index = 0
        self._step_dir = 1
        self._phase = Type1Phase.NONE
        self._hld = False
        self.set_command(0x03)

    # -- Register writes ---------------------------------------------------

    def set_command(self, value: int) -> None:
        self._stop_running_command()
        value &= 0xFF
        self.command_reg = value
        self._intrq = False  # a new command clears INTRQ
        high = value & 0xF0
        if high == 0xD0:
            self._force_interrupt(value)
        elif (value & 0x80) == 0:
            self._type1(value)
        elif high in (0x80, 0x90):
            self._read_sector()
        elif high in (0xA0, 0xB0):
            self._write_sector()
        elif high == 0xC0:
            self._read_address()
        elif high == 0xF0:
            self._write_track()
        else:  # 0xE0 READ TRACK: not needed for the MSX boot path
            self._hld = True
            self.status_reg = 0
            self._end_command()

    def set_track(self, value: int) -> None:
        self.catch_up()
        self.track_reg = value & 0xFF

    def set_sector(self, value: int) -> None:
        self.catch_up()
        self.sector_reg = value & 0xFF

    def set_data(self, value: int) -> None:
        self.catch_up()
        value &= 0xFF
        self.data_reg = value
        if self._mode == Mode.WRITE:
            self._buffer.append(value)
            self._index += 1
            if self._index >= SECTOR_SIZE:
                if self.drive is not None:
                    self.drive.write_sector(
                        self.drive.track, self.drive.side, self.sector_reg, bytes(self._buffer)
                    )
                self._end_command()
        elif self._mode == Mode.WRITE_TRACK:
            self._index += 1
            if self._index >= TRACK_BYTES:
                if self.drive is not None:
                    self.drive.format_track(self.drive.track, self.drive.side)
                self._end_command()

    # -- Register reads ----------------------------------------------------

    def get_status(self) -> int:
        """Rebuild the live bits into STATUS and return it, as openMSX
        WD2793::getStatusReg does. Clears INTRQ, but not the
        immediate-interrupt latch."""
        self.catch_up()
        status = self.status_reg
        if _is_type1(self.command_reg):
            status &= ~(INDEX | TRACK00 | HEAD_LOADED | WRITE_PROTECTED)
            if self._at_track00():
                status |= TRACK00
            if self._write_protect_signal():
                status |= WRITE_PROTECTED
            if self._hld:
                status |= HEAD_LOADED
        elif self._drq:
            status |= S_DRQ
        else:
            status &= ~S_DRQ
        if self._ready():
            status &= ~NOT_READY
        else:
            status |= NOT_READY
        self.status_reg = status & 0xFF
        self._intrq = False
        return self.status_reg

    def get_track(self) -> int:
        self.catch_up()
        return self.track_reg & 0xFF

    def get_sector(self) -> int:
        self.catch_up()
        return self.sector_reg & 0xFF

    def get_data(self) -> int:
        self.catch_up()
        if self._mode == Mode.READ and self._index < len(self._buffer):
            value = self._buffer[self._index]
            self._index += 1
            self.data_reg = value
            if self._index >= len(self._buffer):
                self._end_command()  # last byte consumed
            return value
        return self.data_reg & 0xFF

    def get_irq(self) -> bool:
        self.catch_up()
        return self._intrq or self._immediate_irq

    def get_drq(self) -> bool:
        self.catch_up()
        return self._drq

    # -- Drive signals -------------------------------------------------------

    def _ready(self) -> bool:
        return self.drive is not None and self.drive.has_disk

    def _index_pulses(self) -> bool:
        # A disk in the drive turns and gives index pulses. The motor is not
        # modelled, so a disk counts as turning: the same test as _ready.
        return self._ready()

    def _at_track00(self) -> bool:
        # openMSX's DummyDrive (no drive selected) never reports track 0.
        return self.drive is not None and self.drive.track == 0

    def _write_protect_signal(self) -> bool:
        # openMSX reports write-protected for a missing drive and for an
        # empty one (DummyDrive / DummyDisk), not only for a protected disk.
        return self.drive is None or not self.drive.has_disk or self.drive.write_protected

    def _sector_found(self, sector: int) -> bool:
        """The head's track has an ID field for `sector`, and TRACK matches
        the track in the head's sector IDs (the head position itself)."""
        if self.drive is None or self.track_reg != self.drive.track:
            return False
        return self.drive.has_sector(self.drive.track, self.drive.side, sector)

    def _sector_on_head_track(self) -> bytes | None:
        """The addressed sector's data, or None when _sector_found fails."""
        if self.drive is None or not self._sector_found(self.sector_reg):
            return None
        return self.drive.read_sector(self.drive.track, self.drive.side, self.sector_reg)

    # -- Command implementations ------------------------------------------

    def attach_clock(self, clock: CoreClock) -> None:
        """Time the Type I steps on the machine's clock record
        (msx/clock.py)."""
        self._clock = clock

    def catch_up(self) -> None:
        """Run every Type I event that is due by now, then drop HLD when the
        controller has been idle for 15 index pulses. Every register access
        runs it; the interface also runs it before a drive or side select,
        so due steps reach the drive selected when they were due."""
        if self._phase is Type1Phase.NONE and not self._hld:
            return  # no Type I event pending and no head to unload
        now = self._clock.cycle
        if self._phase is Type1Phase.NO_INDEX and self._index_pulses():
            self._phase = Type1Phase.NONE
            self._verify_search(now)  # a disk is in: the search starts now
        while (
            self._phase is not Type1Phase.NONE
            and self._phase is not Type1Phase.NO_INDEX
            and self._event_cycle <= now
        ):
            if self._phase is Type1Phase.VERIFY and not self._index_pulses():
                self._phase = Type1Phase.NO_INDEX  # the disk went out
                break
            self._type1_event()
        if self._hld:
            self._update_head_unload(now)

    def _update_head_unload(self, now: int) -> None:
        """Drop HLD after 15 index pulses of idle time. With no index
        pulses the idle count restarts at `now` instead."""
        idle = self._phase is Type1Phase.NONE and (self.status_reg & BUSY) == 0
        if not idle:
            return
        if not self._index_pulses():
            self._start_idle(now)  # no index pulses to count yet
        elif self._idle_cycles(now) >= HEAD_UNLOAD_CYCLES:
            self._hld = False

    def _start_idle(self, now: int) -> None:
        """Start the idle time for the head unload at `now`."""
        self._hld_base_cycle = now
        self._hld_base_age = 0

    def _idle_cycles(self, now: int) -> int:
        return self._hld_base_age + (now - self._hld_base_cycle)

    def _type1(self, value: int) -> None:
        self._mode = Mode.IDLE
        self._drq = False
        self.status_reg &= ~(SEEK_ERROR | CRC_ERROR)
        self.status_reg |= BUSY
        if value & H_FLAG:
            self._hld = True
        elif (value & V_FLAG) == 0:
            self._hld = False
        now = self._clock.cycle
        high = value & 0xF0
        if high == 0x00:            # RESTORE
            self.track_reg = 0xFF
            self.data_reg = 0
            self._seek_step(now)
        elif high == 0x10:          # SEEK
            self._seek_step(now)
        else:
            if high in (0x40, 0x50):    # STEP-IN
                self._step_dir = 1
            elif high in (0x60, 0x70):  # STEP-OUT
                self._step_dir = -1
            self._step_pulse(now)       # STEP (0x20/0x30) reuses the direction

    def _type1_event(self) -> None:
        """End the current Type I phase at its own cycle, not at the access
        that noticed it, so a late access does not stretch the command."""
        now = self._event_cycle
        phase = self._phase
        self._phase = Type1Phase.NONE
        if phase == Type1Phase.STEP:
            if _is_restore_or_seek(self.command_reg):   # next step
                self._seek_step(now)
            else:
                self._steps_done(now)
        elif phase == Type1Phase.SETTLE:
            if self._index_pulses():
                self._verify_search(now)
            else:
                self._phase = Type1Phase.NO_INDEX   # no disk: no ID field
        else:  # VERIFY: 5 revolutions with no ID field of this track
            self.status_reg |= SEEK_ERROR
            self._end_type1(now)

    def _seek_step(self, now: int) -> None:
        """One SEEK decision: done when TRACK equals DATA, else a step
        toward DATA. Each step moves TRACK one closer, so a seek ends
        within 255 steps."""
        if self.track_reg == self.data_reg:
            self._steps_done(now)
            return
        self._step_dir = 1 if self.data_reg > self.track_reg else -1
        self._step_pulse(now)

    def _step_pulse(self, now: int) -> None:
        """One head step in the current direction, then the step time.
        TRACK follows it for RESTORE/SEEK and when the T flag is set. A step
        out that finds the head at track 0 ends the steps with TRACK = 0."""
        command = self.command_reg
        inward = self._step_dir > 0
        if (command & T_FLAG) or _is_restore_or_seek(command):
            if inward:
                self.track_reg = (self.track_reg + 1) & 0xFF
            else:
                self.track_reg = (self.track_reg - 1) & 0xFF
        if not inward and self._at_track00():
            self.track_reg = 0
            self._steps_done(now)
            return
        if self.drive is not None:
            self.drive.step(inward)
        self._phase = Type1Phase.STEP
        self._event_cycle = now + STEP_CYCLES[command & STEP_RATE]

    def _steps_done(self, now: int) -> None:
        """After the last step: end, or with V load the head and settle
        before the verify."""
        if self.command_reg & V_FLAG:
            self._hld = True
            self._phase = Type1Phase.SETTLE
            self._event_cycle = now + SETTLE_CYCLES
        else:
            self._end_type1(now)

    def _verify_search(self, now: int) -> None:
        """Look for an ID field of TRACK from cycle `now`: a match ends the
        command, else SEEK_ERROR after 5 revolutions."""
        if self._track_id_found():
            self._end_type1(now)
        else:
            self._phase = Type1Phase.VERIFY
            self._event_cycle = now + VERIFY_FAIL_CYCLES

    def _track_id_found(self) -> bool:
        """The head's track has ID fields and their track equals TRACK."""
        return self._sector_found(1)

    def _end_type1(self, now: int) -> None:
        self._end_command()
        self._start_idle(now)

    def _start_transfer(self, clear: int) -> bool:
        """Clear the given status bits and set BUSY; end the command and
        return False when no disk is ready (openMSX startType2Cmd /
        startType3Cmd)."""
        self.status_reg &= ~clear
        self.status_reg |= BUSY
        self._hld = True
        if not self._ready():
            self._end_command()
            return False
        return True

    def _read_sector(self) -> None:
        if not self._start_transfer(_TYPE2_CLEAR):
            return
        data = self._sector_on_head_track()
        if data is None:
            self.status_reg |= RECORD_NOT_FOUND
            self._end_command()
            return
        self._buffer = bytearray(data)
        self._index = 0
        self._mode = Mode.READ
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _write_sector(self) -> None:
        if not self._start_transfer(_TYPE2_CLEAR):
            return
        assert self.drive is not None
        if self.drive.write_protected:
            self.status_reg |= WRITE_PROTECTED
            self._end_command()
            return
        if not self._sector_found(self.sector_reg):
            self.status_reg |= RECORD_NOT_FOUND
            self._end_command()
            return
        self._buffer = bytearray()
        self._index = 0
        self._mode = Mode.WRITE
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _write_track(self) -> None:
        if not self._start_transfer(_TYPE3_CLEAR):
            return
        assert self.drive is not None
        if self.drive.write_protected:
            self.status_reg |= WRITE_PROTECTED
            self._end_command()
            return
        self._index = 0
        self._mode = Mode.WRITE_TRACK
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _read_address(self) -> None:
        if not self._start_transfer(_TYPE3_CLEAR):
            return
        assert self.drive is not None
        track = self.drive.track
        if not self.drive.has_sector(track, self.drive.side, 1):
            self.status_reg |= RECORD_NOT_FOUND  # no sector IDs on this track
            self._end_command()
            return
        # 6-byte ID field: track, side, sector, N (2 = 512 bytes), CRC hi, CRC lo.
        # The track is the head's own. Functional-model simplification: this
        # model has no rotation, so it always reports sector 1 (rather than
        # "whichever sector passes the head next") and CRC 0/0.
        self._buffer = bytearray([track & 0xFF, self.drive.side & 1, 1, 2, 0, 0])
        self._index = 0
        self._mode = Mode.READ
        self._drq = True
        self.status_reg = BUSY | S_DRQ
        self.sector_reg = track & 0xFF  # WD quirk: SECTOR gets the ID's track

    def _force_interrupt(self, value: int) -> None:
        # The index-pulse condition (bit 2) needs rotation timing and is
        # not modelled; the not-ready/ready transitions (bits 0-1) are not
        # implemented by openMSX either.
        self._mode = Mode.IDLE
        self._drq = False
        self.status_reg &= ~BUSY
        if (value & 0x0F) == 0:
            self._immediate_irq = False
        if value & IMMEDIATE_IRQ:
            self._immediate_irq = True

    def abort(self) -> None:
        """Abort any in-progress command, leaving the controller idle.

        Used on a disk swap so no read/write buffer keeps referencing the
        previous medium. It first runs the Type I events already due (which
        can move the head and TRACK), then stops a running Type I command
        where it is, with no INTRQ of its own. Unlike reset() this keeps the
        TRACK/SECTOR registers.
        """
        self._stop_running_command()
        self._mode = Mode.IDLE
        self._drq = False
        self._buffer = bytearray()
        self._index = 0
        self.status_reg &= ~(BUSY | S_DRQ)

    def _stop_running_command(self) -> None:
        """Run the due Type I events, then stop any running command where it
        is. Stopping a busy command starts the idle time for the head unload."""
        self.catch_up()
        if self._phase != Type1Phase.NONE or (self.status_reg & BUSY):
            self._start_idle(self._clock.cycle)
        self._phase = Type1Phase.NONE

    def _end_command(self) -> None:
        self._mode = Mode.IDLE
        self._drq = False
        self.status_reg &= ~(BUSY | S_DRQ)
        self._intrq = True
        self._start_idle(self._clock.cycle)

    # ------------------------------------------------------------ save-state

    def snapshot(self) -> WD2793State:
        """Capture register/phase/buffer state; it changes nothing. Run
        catch_up() first, so the Type I events already due are in the
        capture. `drive` is not included -- see WD2793State."""
        cycles_left = 0
        if self._phase != Type1Phase.NONE and self._phase != Type1Phase.NO_INDEX:
            cycles_left = max(0, self._event_cycle - self._clock.cycle)
        return {
            "command_reg": self.command_reg,
            "track_reg": self.track_reg,
            "sector_reg": self.sector_reg,
            "data_reg": self.data_reg,
            "status_reg": self.status_reg,
            "intrq": self._intrq,
            "drq": self._drq,
            "mode": self._mode.value,
            "buffer": bytearray(self._buffer),
            "index": self._index,
            "step_dir": self._step_dir,
            "immediate_irq": self._immediate_irq,
            "type1_phase": self._phase.value,
            "type1_cycles_left": cycles_left,
            "hld": self._hld,
            "hld_idle_cycles": min(SAFE_INT_MAX, self._idle_cycles(self._clock.cycle)),
        }

    def parse_state(self, obj: object, label: str) -> WD2793State:
        """Validate a snapshot() object without mutating the controller.

        Raises:
            SchemaError: A missing/unknown key or a value of the wrong type
                or range.
        """
        fields = check_object(obj, label, _STATE_KEYS)
        return {
            "command_reg": check_int(fields["command_reg"], f"{label}.command_reg", 0, 0xFF),
            "track_reg": check_int(fields["track_reg"], f"{label}.track_reg", 0, 0xFF),
            "sector_reg": check_int(fields["sector_reg"], f"{label}.sector_reg", 0, 0xFF),
            "data_reg": check_int(fields["data_reg"], f"{label}.data_reg", 0, 0xFF),
            "status_reg": check_int(fields["status_reg"], f"{label}.status_reg", 0, 0xFF),
            "intrq": check_bool(fields["intrq"], f"{label}.intrq"),
            "drq": check_bool(fields["drq"], f"{label}.drq"),
            "mode": check_int(fields["mode"], f"{label}.mode", 0, 3),
            "buffer": check_bytes(fields["buffer"], f"{label}.buffer", max_length=SECTOR_SIZE),
            "index": check_int(fields["index"], f"{label}.index", 0, TRACK_BYTES),
            "step_dir": check_choice(fields["step_dir"], f"{label}.step_dir", (-1, 1)),
            "immediate_irq": check_bool(fields["immediate_irq"], f"{label}.immediate_irq"),
            "type1_phase": check_int(
                fields["type1_phase"], f"{label}.type1_phase", 0, len(Type1Phase) - 1
            ),
            "type1_cycles_left": check_int(
                fields["type1_cycles_left"], f"{label}.type1_cycles_left", 0, SAFE_INT_MAX
            ),
            "hld": check_bool(fields["hld"], f"{label}.hld"),
            "hld_idle_cycles": check_int(
                fields["hld_idle_cycles"], f"{label}.hld_idle_cycles", 0, SAFE_INT_MAX
            ),
        }

    def apply_state(self, state: WD2793State) -> None:
        """Assign a state returned by parse_state()."""
        self.command_reg = state["command_reg"]
        self.track_reg = state["track_reg"]
        self.sector_reg = state["sector_reg"]
        self.data_reg = state["data_reg"]
        self.status_reg = state["status_reg"]
        self._intrq = state["intrq"]
        self._drq = state["drq"]
        self._mode = Mode(state["mode"])
        self._buffer = bytearray(state["buffer"])
        self._index = state["index"]
        self._step_dir = state["step_dir"]
        self._immediate_irq = state["immediate_irq"]
        self._phase = Type1Phase(state["type1_phase"])
        self._event_cycle = self._clock.cycle + state["type1_cycles_left"]
        self._hld = state["hld"]
        self._hld_base_cycle = self._clock.cycle
        self._hld_base_age = state["hld_idle_cycles"]
