"""Western Digital WD2793 floppy disk controller (functional model).

Register-level chip emulation. Transfers are resolved to/from a whole 512-byte
sector buffer and paced by DRQ per byte (the MSX DISK ROM polls STATUS/DRQ, not
T-states), so the model is deterministic rather than cycle-accurate. Status-bit
constants and command decode follow openMSX ``WD2793.cc``.

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
TRACK00: int = 0x04           # Type I: head at track 0
RECORD_NOT_FOUND: int = 0x10  # Type II/III: addressed sector absent
WRITE_PROTECTED: int = 0x40
NOT_READY: int = 0x80

SECTOR_SIZE: int = 512
# Approximate 2DD MFM track capacity; WRITE TRACK consumes this many bytes then
# terminates. The DISK ROM polls DRQ (not a byte count), so any value in this
# range works — the exact count is not observable by software.
TRACK_BYTES: int = 6250


def _is_type1(command: int) -> bool:
    """Type I (positioning) or FORCE INTERRUPT -- both read STATUS the same way."""
    return (command & 0x80) == 0 or (command & 0xF0) == 0xD0


class WD2793State(TypedDict):
    """Save-state schema for WD2793.snapshot()/restore().

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


_STATE_KEYS: frozenset[str] = frozenset({
    "command_reg", "track_reg", "sector_reg", "data_reg", "status_reg", "intrq", "drq",
    "mode", "buffer", "index", "step_dir",
})


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
    #   caller. See logs/review-python-20260814-210824.md for the full analysis.
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

    def reset(self) -> None:
        # Mirrors the dataclass field defaults above (drive is intentionally
        # excluded: reset() does not disconnect the selected drive). Keep the
        # two in sync if a field is added.
        self.command_reg = 0
        self.track_reg = 0
        self.sector_reg = 1
        self.data_reg = 0
        self.status_reg = 0
        self._intrq = False
        self._drq = False
        self._mode = Mode.IDLE
        self._buffer = bytearray()
        self._index = 0
        self._step_dir = 1

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
                        self.track_reg, self.drive.side, self.sector_reg, bytes(self._buffer)
                    )
                self._end_command()
        elif self._mode == Mode.WRITE_TRACK:
            self._index += 1
            if self._index >= TRACK_BYTES:
                if self.drive is not None:
                    self.drive.format_track(self.track_reg, self.drive.side)
                self._end_command()

    # -- Register reads ----------------------------------------------------

    def get_status(self) -> int:
        self._intrq = False  # reading STATUS clears INTRQ
        status = self.status_reg
        if not _is_type1(self.command_reg):
            if self._drq:
                status |= S_DRQ
            else:
                status &= ~S_DRQ
        return status & 0xFF

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
        return self._intrq

    def get_drq(self) -> bool:
        return self._drq

    # -- Command implementations ------------------------------------------

    def _step_track(self, direction: int) -> None:
        """Move track_reg one step: +1 wraps at 256 ("in"), -1 floors at 0 ("out")."""
        if direction > 0:
            self.track_reg = (self.track_reg + 1) & 0xFF
        else:
            self.track_reg = max(0, self.track_reg - 1)

    def _type1(self, value: int) -> None:
        high = value & 0xF0
        if high == 0x00:            # RESTORE
            self.track_reg = 0
        elif high == 0x10:          # SEEK
            self.track_reg = self.data_reg & 0xFF
        elif high in (0x40, 0x50):  # STEP-IN
            self._step_dir = 1
            self._step_track(self._step_dir)
        elif high in (0x60, 0x70):  # STEP-OUT
            self._step_dir = -1
            self._step_track(self._step_dir)
        else:                        # STEP (0x20/0x30): repeat last direction
            self._step_track(self._step_dir)
        if self.drive is not None:
            self.drive.track = self.track_reg
        status = 0
        if self.track_reg == 0:
            status |= TRACK00
        if self.drive is None or not self.drive.has_disk:
            status |= NOT_READY
        elif self.drive.write_protected:
            status |= WRITE_PROTECTED
        self.status_reg = status
        self._mode = Mode.IDLE
        self._drq = False
        self._intrq = True

    def _read_sector(self) -> None:
        self.status_reg = BUSY
        if self.drive is None or not self.drive.has_disk:
            self.status_reg = NOT_READY
            self._end_command()
            return
        data = self.drive.read_sector(self.track_reg, self.drive.side, self.sector_reg)
        if data is None:
            self.status_reg = RECORD_NOT_FOUND
            self._end_command()
            return
        self._buffer = bytearray(data)
        self._index = 0
        self._mode = Mode.READ
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _write_sector(self) -> None:
        self.status_reg = BUSY
        if self.drive is None or not self.drive.has_disk:
            self.status_reg = NOT_READY
            self._end_command()
            return
        if self.drive.write_protected:
            self.status_reg = WRITE_PROTECTED
            self._end_command()
            return
        if self.drive.read_sector(self.track_reg, self.drive.side, self.sector_reg) is None:
            self.status_reg = RECORD_NOT_FOUND
            self._end_command()
            return
        self._buffer = bytearray()
        self._index = 0
        self._mode = Mode.WRITE
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _write_track(self) -> None:
        self.status_reg = BUSY
        if self.drive is None or not self.drive.has_disk:
            self.status_reg = NOT_READY
            self._end_command()
            return
        if self.drive.write_protected:
            self.status_reg = WRITE_PROTECTED
            self._end_command()
            return
        self._index = 0
        self._mode = Mode.WRITE_TRACK
        self._drq = True
        self.status_reg = BUSY | S_DRQ

    def _read_address(self) -> None:
        self.status_reg = BUSY
        if self.drive is None or not self.drive.has_disk:
            self.status_reg = NOT_READY
            self._end_command()
            return
        # 6-byte ID field: track, side, sector, N (2 = 512 bytes), CRC hi, CRC lo.
        # Functional-model simplification: this model has no real per-sector ID
        # field to read, so it always reports sector 1 (rather than searching the
        # mounted image for "whichever sector is next") -- the DISK ROM does not
        # depend on the reported value here, only on sector_reg's post-command
        # "gets track" quirk below.
        self._buffer = bytearray(
            [self.track_reg & 0xFF, self.drive.side & 1, 1, 2, 0, 0]
        )
        self._index = 0
        self._mode = Mode.READ
        self._drq = True
        self.status_reg = BUSY | S_DRQ
        self.sector_reg = self.track_reg & 0xFF  # WD quirk: sector reg gets track

    def _force_interrupt(self, value: int) -> None:
        self._mode = Mode.IDLE
        self._drq = False
        self.status_reg &= ~(BUSY | S_DRQ)
        if value & 0x08:  # immediate-interrupt condition bit
            self._intrq = True

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
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore register/phase/buffer state produced by snapshot()."""
        self.apply_state(self.parse_state(state, "wd2793"))

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
