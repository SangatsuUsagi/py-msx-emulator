"""Western Digital WD2793 floppy disk controller (functional model).

Register-level chip emulation. Transfers are resolved to/from a whole 512-byte
sector buffer and paced by DRQ per byte (the MSX DISK ROM polls STATUS/DRQ, not
T-states), so the model is deterministic rather than cycle-accurate. Status-bit
constants and command decode follow openMSX ``WD2793.cc`` and the WD279X
data sheet (references/docs/WESTERN_DIGITAL_WD279X.md).

The status byte's bit meanings depend on the command type:
  Type I  (positioning): INDEX/TRACK00/CRC/SEEK_ERROR/HEAD_LOADED/WPRT/NOT_READY
  Type II/III (transfer): DRQ/LOST_DATA/CRC/RECORD_NOT_FOUND/REC_TYPE/WPRT/NOT_READY
BUSY (bit 0) is common to both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from msx.fdc.disk_drive import DiskDrive
from msx.snapshot_schema import check_bool, check_bytes, check_choice, check_int, check_object


class Mode(Enum):
    """Controller transfer state (an enum, not a string, for a clean port)."""
    IDLE = 0
    READ = 1
    WRITE = 2
    WRITE_TRACK = 3

# Status register bits (shared byte; meaning depends on command type).
BUSY: int = 0x01
S_DRQ: int = 0x02             # Type II/III: data request
INDEX: int = 0x02             # Type I: index pulse (timing; always 0 here)
TRACK00: int = 0x04           # Type I: head at track 0
CRC_ERROR: int = 0x08
SEEK_ERROR: int = 0x10        # Type I
RECORD_NOT_FOUND: int = 0x10  # Type II/III: addressed sector absent
HEAD_LOADED: int = 0x20       # Type I (timing; always 0 here)
LOST_DATA: int = 0x04         # Type II/III
RECORD_TYPE: int = 0x20       # Type II/III
WRITE_PROTECTED: int = 0x40
NOT_READY: int = 0x80

# Status bits a Type II / Type III command clears when it starts.
_TYPE2_CLEAR: int = LOST_DATA | RECORD_NOT_FOUND | RECORD_TYPE | WRITE_PROTECTED
_TYPE3_CLEAR: int = LOST_DATA | RECORD_NOT_FOUND | RECORD_TYPE

# Type I command flags.
T_FLAG: int = 0x10            # STEP/STEP-IN/STEP-OUT: update TRACK too
# FORCE INTERRUPT condition bit: hold INTRQ until a plain 0xD0.
IMMEDIATE_IRQ: int = 0x08

SECTOR_SIZE: int = 512
# Approximate 2DD MFM track capacity; WRITE TRACK consumes this many bytes then
# terminates. The DISK ROM polls DRQ (not a byte count), so any value in this
# range works — the exact count is not observable by software.
TRACK_BYTES: int = 6250


def _is_type1(command: int) -> bool:
    """Type I (positioning) or FORCE INTERRUPT -- both read STATUS the same way."""
    return (command & 0x80) == 0 or (command & 0xF0) == 0xD0


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
        self.set_command(0x03)

    # -- Register writes ---------------------------------------------------

    def set_command(self, value: int) -> None:
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
            self.status_reg = 0
            self._end_command()

    def set_track(self, value: int) -> None:
        self.track_reg = value & 0xFF

    def set_sector(self, value: int) -> None:
        self.sector_reg = value & 0xFF

    def set_data(self, value: int) -> None:
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
        status = self.status_reg
        if _is_type1(self.command_reg):
            status &= ~(INDEX | TRACK00 | HEAD_LOADED | WRITE_PROTECTED)
            if self._at_track00():
                status |= TRACK00
            if self._write_protect_signal():
                status |= WRITE_PROTECTED
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
        return self.track_reg & 0xFF

    def get_sector(self) -> int:
        return self.sector_reg & 0xFF

    def get_data(self) -> int:
        if self._mode == Mode.READ and self._index < len(self._buffer):
            value = self._buffer[self._index]
            self._index += 1
            self.data_reg = value
            if self._index >= len(self._buffer):
                self._end_command()  # last byte consumed
            return value
        return self.data_reg & 0xFF

    def get_irq(self) -> bool:
        return self._intrq or self._immediate_irq

    def get_drq(self) -> bool:
        return self._drq

    # -- Drive signals -------------------------------------------------------

    def _ready(self) -> bool:
        return self.drive is not None and self.drive.has_disk

    def _at_track00(self) -> bool:
        # openMSX's DummyDrive (no drive selected) never reports track 0.
        return self.drive is not None and self.drive.track == 0

    def _write_protect_signal(self) -> bool:
        # openMSX reports write-protected for a missing drive and for an
        # empty one (DummyDrive / DummyDisk), not only for a protected disk.
        return self.drive is None or not self.drive.has_disk or self.drive.write_protected

    def _sector_on_head_track(self) -> bytes | None:
        """The addressed sector, found only when TRACK matches the track in
        the head's sector IDs (the head position itself)."""
        if self.drive is None or self.track_reg != self.drive.track:
            return None
        return self.drive.read_sector(self.drive.track, self.drive.side, self.sector_reg)

    # -- Command implementations ------------------------------------------

    def _step(self, command: int) -> bool:
        """One head step in the current direction. TRACK follows it for
        RESTORE/SEEK and when the T flag is set. Returns True when a step out
        found the head at track 0, which ends the command with TRACK = 0."""
        inward = self._step_dir > 0
        if (command & T_FLAG) or (command & 0xE0) == 0x00:
            if inward:
                self.track_reg = (self.track_reg + 1) & 0xFF
            else:
                self.track_reg = (self.track_reg - 1) & 0xFF
        if not inward and self._at_track00():
            self.track_reg = 0
            return True
        if self.drive is not None:
            self.drive.step(inward)
        return False

    def _seek(self, command: int) -> None:
        """Step toward DATA until TRACK equals it. Each step moves TRACK one
        closer, so this ends within 255 steps."""
        while self.track_reg != self.data_reg:
            self._step_dir = 1 if self.data_reg > self.track_reg else -1
            if self._step(command):
                return

    def _type1(self, value: int) -> None:
        self.status_reg &= ~(SEEK_ERROR | CRC_ERROR)
        high = value & 0xF0
        if high == 0x00:            # RESTORE
            self.track_reg = 0xFF
            self.data_reg = 0
            self._seek(value)
        elif high == 0x10:          # SEEK
            self._seek(value)
        else:
            if high in (0x40, 0x50):    # STEP-IN
                self._step_dir = 1
            elif high in (0x60, 0x70):  # STEP-OUT
                self._step_dir = -1
            self._step(value)           # STEP (0x20/0x30) reuses the direction
        self._end_command()

    def _start_transfer(self, clear: int) -> bool:
        """Clear the given status bits and set BUSY; end the command and
        return False when no disk is ready (openMSX startType2Cmd /
        startType3Cmd)."""
        self.status_reg &= ~clear
        self.status_reg |= BUSY
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
        if self._sector_on_head_track() is None:
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
        if self.drive.read_sector(track, self.drive.side, 1) is None:
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
        """Abort any in-progress transfer, leaving the controller idle.

        Used on a disk swap so no read/write buffer keeps referencing the
        previous medium. Unlike reset() this preserves the TRACK/SECTOR registers
        (the head does not move when a disk is exchanged).
        """
        self._mode = Mode.IDLE
        self._drq = False
        self._buffer = bytearray()
        self._index = 0
        self.status_reg &= ~(BUSY | S_DRQ)

    def _end_command(self) -> None:
        self._mode = Mode.IDLE
        self._drq = False
        self.status_reg &= ~(BUSY | S_DRQ)
        self._intrq = True

    # ------------------------------------------------------------ save-state

    def snapshot(self) -> WD2793State:
        """Capture register/phase/buffer state. `drive` is not included --
        see WD2793State."""
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
