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

from msx.fdc.disk_drive import DiskDrive


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


@dataclass
class WD2793:
    """WD2793 controller bound to a single selected drive.

    The connection-style interface sets ``drive`` on drive-select and updates
    ``drive.side`` on side-select before issuing transfer commands.
    """

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
        is_type1 = (self.command_reg & 0x80) == 0 or (self.command_reg & 0xF0) == 0xD0
        if not is_type1:
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

    def _type1(self, value: int) -> None:
        high = value & 0xF0
        if high == 0x00:            # RESTORE
            self.track_reg = 0
        elif high == 0x10:          # SEEK
            self.track_reg = self.data_reg & 0xFF
        elif high in (0x40, 0x50):  # STEP-IN
            self._step_dir = 1
            self.track_reg = (self.track_reg + 1) & 0xFF
        elif high in (0x60, 0x70):  # STEP-OUT
            self._step_dir = -1
            self.track_reg = max(0, self.track_reg - 1)
        else:                       # STEP (0x20/0x30): repeat last direction
            self.track_reg = max(0, (self.track_reg + self._step_dir) & 0xFF)
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
