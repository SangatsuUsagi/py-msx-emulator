"""Toshiba TC8566AF (uPD765-family) floppy disk controller (functional model).

Register-level chip emulation, Non-DMA mode only (the DRQ2/-DACK2/DMATC pins
are not modelled -- matches this project's DRQ-polled WD2793 model and
openMSX's own TC8566AF port, which never wires those pins either). Every
command goes through a Command Phase (command byte + a fixed number of
parameter bytes), an Execution Phase (single-sector data transfer -- no
multi-sector chaining, matching this project's WD2793 model), and a Result
Phase (a fixed number of status/echo bytes), all gated by the Main Status
Register's RQM/DIO bits. Unlike the WD2793 there is no directly addressable
TRACK/SECTOR register: cylinder/head/sector/N are Command-Phase parameters
and Result-Phase echo bytes carried entirely through the Data Register.

Implements SPECIFY, SENSE INTERRUPT STATUS, SENSE DEVICE STATUS, RECALIBRATE,
SEEK, READ DATA, WRITE DATA, and FORMAT -- enough for the MSX DISK ROM's
boot/sector I/O path and `CALL FORMAT`. READ DELETED DATA / WRITE DELETED
DATA / READ DIAGNOSTIC / READ ID / SCAN are not needed for that path and are
not implemented (matches the WD2793 model's own precedent of leaving out
READ TRACK as "not needed for the MSX boot path"). Like WD2793's own WRITE
TRACK, FORMAT discards the descriptor bytes DISK ROM streams for each
sector (C/H/R/N) rather than writing real gap/sync/ID/CRC bytes to a
simulated physical track -- it just blanks every sector of the drive's
current (track, side) via DiskDrive.format_track(), positioned by whatever
SEEK/RECALIBRATE already set, not by the streamed C values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from msx.fdc.disk_drive import DiskDrive
from msx.snapshot_schema import (
    check_bool,
    check_bytes,
    check_choice,
    check_int,
    check_int_list,
    check_object,
)

SECTOR_SIZE: int = 512

# Main Status Register bits.
MSR_RQM: int = 0x80  # request for master: Data Register ready
MSR_DIO: int = 0x40  # data direction: 1 = FDC -> processor
MSR_NDM: int = 0x20  # non-DMA mode (always set by this model)
MSR_CB: int = 0x10   # FDC busy (Command/Execution/Result Phase in progress)

# Command opcodes. _decode() matches each against its own mask: READ DATA,
# WRITE DATA and FORMAT allow some of the MT/MFM/SK flag bits (ignored by
# this functional model), the other commands match the whole byte (openMSX
# TC8566AF::idlePhaseWrite).
CMD_SPECIFY: int = 0x03
CMD_SENSE_DEVICE_STATUS: int = 0x04
CMD_WRITE_DATA: int = 0x05
CMD_READ_DATA: int = 0x06
CMD_RECALIBRATE: int = 0x07
CMD_SENSE_INTERRUPT_STATUS: int = 0x08
CMD_FORMAT: int = 0x0D
CMD_SEEK: int = 0x0F

# Number of Command-Phase parameter bytes (after the command byte) per command.
# FORMAT's 5: HD_DS, N (bytes/sector code, ignored -- this model's sectors are
# fixed at SECTOR_SIZE), SC (sectors/cylinder), GPL (gap length, ignored), D
# (fill byte).
_PARAM_COUNT: dict[int, int] = {
    CMD_SPECIFY: 2,
    CMD_SENSE_DEVICE_STATUS: 1,
    CMD_WRITE_DATA: 8,
    CMD_READ_DATA: 8,
    CMD_RECALIBRATE: 1,
    CMD_SENSE_INTERRUPT_STATUS: 0,
    CMD_FORMAT: 5,
    CMD_SEEK: 2,
}

# (command, mask) pairs for _decode().
_DECODE: tuple[tuple[int, int], ...] = (
    (CMD_READ_DATA, 0x1F),
    (CMD_WRITE_DATA, 0x3F),
    (CMD_FORMAT, 0xBF),
    (CMD_SEEK, 0xFF),
    (CMD_RECALIBRATE, 0xFF),
    (CMD_SENSE_INTERRUPT_STATUS, 0xFF),
    (CMD_SPECIFY, 0xFF),
    (CMD_SENSE_DEVICE_STATUS, 0xFF),
)

# RECALIBRATE gives up after this many step pulses without seeing track 0.
_RECALIBRATE_STEPS: int = 255

# ST0 bits.
ST0_ABNORMAL: int = 0x40
ST0_INVALID: int = 0x80
ST0_SEEK_END: int = 0x20
ST0_EQUIPMENT_CHECK: int = 0x10
ST0_NOT_READY: int = 0x08

# ST1 bits.
ST1_NO_DATA: int = 0x04
ST1_NOT_WRITABLE: int = 0x02

# ST3 bits.
ST3_WRITE_PROTECTED: int = 0x40
ST3_READY: int = 0x20
ST3_TRACK0: int = 0x10
ST3_TWO_SIDE: int = 0x08


def _decode(command: int) -> int | None:
    """The command a command byte selects, or None for an unrecognised one."""
    for opcode, mask in _DECODE:
        if (command & mask) == opcode:
            return opcode
    return None


def _locate_sector(drive: DiskDrive, hd_ds: int, c: int, h: int, r: int) -> bytes | None:
    """The sector with ID (C, H, R) on the head's track and the side the HD
    bit selects, or None. An ID's C is its track and its H its side, so
    only C equal to the head's track and H equal to HD can match (openMSX
    TC8566AF::locateSector)."""
    side = (hd_ds >> 2) & 0x01
    if c != drive.track or h != side:
        return None
    return drive.read_sector(drive.track, side, r)


class Phase(Enum):
    IDLE = 0       # Command Phase, ready for a new command byte
    COMMAND = 1    # Command Phase, mid multi-byte parameters
    EXECUTION = 2  # data transfer in progress
    RESULT = 3     # Result Phase, mid multi-byte result bytes


class TC8566AFState(TypedDict):
    """Save-state schema for TC8566AF.snapshot()/restore().

    ``drives``/``drive`` are not part of this state -- reattached by the
    machine's own construction, same as WD2793State excludes ``drive``.
    """

    control_reg0: int
    control_reg1: int
    selected_drive_index: int
    last_st0: int
    last_pcn: int
    pcn: list[int]  # present cylinder number per drive
    phase: int
    command: int
    cmd_buffer: bytearray
    cmd_needed: int
    result_buffer: bytearray
    result_index: int
    exec_buffer: bytearray
    exec_index: int
    exec_is_write: bool
    exec_needed: int
    exec_ctr: list[int]  # C, H, R, N
    format_fill: int


# PORT-NOTE: key set read from the TypedDict at import time (see
#   msx/vdp/v9938.py's _STATE_KEYS note: serde `deny_unknown_fields` in Rust,
#   a constexpr key array in C++).
_STATE_KEYS: frozenset[str] = frozenset(TC8566AFState.__annotations__)
# Largest Command-Phase parameter count / Result-Phase length / Execution-Phase
# buffer (FORMAT's 4 descriptor bytes for up to 255 sectors) this model produces.
_MAX_CMD_BYTES: int = 8
_MAX_RESULT_BYTES: int = 7
_MAX_EXEC_BYTES: int = 1024


@dataclass
class TC8566AF:
    """TC8566AF controller owning all connected drives directly.

    Unlike the WD2793 (one externally-swapped ``.drive`` reference), the real
    chip's Control Register 0 addresses one of up to four drives it owns
    directly, and READ DATA/WRITE DATA/SEEK/RECALIBRATE/SENSE DEVICE STATUS
    each carry their own target drive in their first parameter byte (DS1:DS0)
    -- see openspec/specs/fdc-tc8566af/spec.md.
    """

    drives: list[DiskDrive]
    control_reg0: int = 0
    control_reg1: int = 0
    # PORT-NOTE: unused by command dispatch -- exists only so
    #   FloppyDiskState.__init__ (msx/fdc/interface.py; composed into every
    #   connection style's shared FloppyDisk.__init__ as `self.state`) can do
    #   `self.controller.drive = self.drives[0]` without raising
    #   AttributeError. TC8566AF resolves "current drive" per command
    #   from that command's own DS1:DS0 parameter bits against `self.drives`,
    #   never from this field.
    drive: DiskDrive | None = None

    _phase: Phase = Phase.IDLE
    _command: int = 0
    _cmd_buffer: bytearray = field(default_factory=bytearray)
    _cmd_needed: int = 0
    _result_buffer: bytearray = field(default_factory=bytearray)
    _result_index: int = 0
    _exec_buffer: bytearray = field(default_factory=bytearray)
    _exec_index: int = 0
    _exec_is_write: bool = False
    # Target _exec_buffer length that completes the Execution Phase: fixed at
    # SECTOR_SIZE for WRITE DATA, 4 * sectors-per-cylinder (one C,H,R,N
    # descriptor group per sector) for FORMAT.
    _exec_needed: int = 0
    _exec_ctr: tuple[int, int, int, int] = (0, 0, 0, 0)  # C, H, R, N for the result echo
    _format_fill: int = 0  # FORMAT's D (fill byte) parameter
    _selected_drive_index: int = 0
    _last_st0: int = ST0_INVALID  # SENSE INTERRUPT STATUS with nothing pending
    _last_pcn: int = 0
    # Present cylinder number per drive: the cylinder the controller counts
    # the head at, separate from the head itself (DiskDrive.track).
    _pcn: list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    def reset(self) -> None:
        self.control_reg0 = 0
        self.control_reg1 = 0
        self._selected_drive_index = 0
        self._last_st0 = ST0_INVALID
        self._last_pcn = 0
        self._pcn = [0, 0, 0, 0]
        self.abort()

    def catch_up(self) -> None:
        """Nothing to run: no TC8566AF state advances with time (the
        FdcController contract; WD2793.catch_up runs its timed Type I
        events)."""

    def abort(self) -> None:
        """Cancel any in-progress command, back to Command-Phase idle.

        Does not touch the control registers (mirrors WD2793.abort() -- used
        on a runtime disk swap so no buffer keeps referencing the previous
        medium).
        """
        self._phase = Phase.IDLE
        self._command = 0
        self._cmd_buffer = bytearray()
        self._cmd_needed = 0
        self._result_buffer = bytearray()
        self._result_index = 0
        self._exec_buffer = bytearray()
        self._exec_index = 0
        self._exec_is_write = False

    # -- drive / motor -------------------------------------------------------

    def _drive(self, index: int) -> DiskDrive | None:
        if 0 <= index < len(self.drives):
            return self.drives[index]
        return None

    def motor_on(self, index: int) -> bool:
        if not (0 <= index <= 3):
            return False
        return bool(self.control_reg0 & (0x10 << index))

    # The FS-A1F's drives give Track 0 and Write Protected only with the
    # motor on (openMSX RealDrive with signalsNeedMotorOn, set by the MSXFDC
    # constructor when the machine names no connection style; see
    # RealDrive::isWriteProtected and RealDrive::isTrack00). A nonexistent drive
    # never reports track 0 and always reports write protected (openMSX
    # DummyDrive).

    def _track00(self, index: int) -> bool:
        drive = self._drive(index)
        return drive is not None and self.motor_on(index) and drive.track == 0

    def _write_protect_signal(self, index: int) -> bool:
        drive = self._drive(index)
        if drive is None:
            return True
        if not self.motor_on(index):
            return False
        return not drive.has_disk or drive.write_protected

    # -- register reads --------------------------------------------------------

    def read_main_status(self) -> int:
        value = MSR_NDM
        if self._phase is Phase.IDLE:
            value |= MSR_RQM
        elif self._phase is Phase.COMMAND:
            value |= MSR_RQM | MSR_CB
        elif self._phase is Phase.EXECUTION:
            value |= MSR_CB
            value |= MSR_RQM if self._exec_is_write else (MSR_RQM | MSR_DIO)
        elif self._phase is Phase.RESULT:
            value |= MSR_RQM | MSR_DIO | MSR_CB
        return value & 0xFF

    def read_data(self) -> int:
        if self._phase is Phase.EXECUTION and not self._exec_is_write:
            if self._exec_index < len(self._exec_buffer):
                value = self._exec_buffer[self._exec_index]
                self._exec_index += 1
                if self._exec_index >= len(self._exec_buffer):
                    self._start_result_phase()
                return value
            return 0xFF
        if self._phase is Phase.RESULT:
            if self._result_index < len(self._result_buffer):
                value = self._result_buffer[self._result_index]
                self._result_index += 1
                if self._result_index >= len(self._result_buffer):
                    self._phase = Phase.IDLE
                return value
            return 0xFF
        return 0xFF

    # -- register writes ---------------------------------------------------

    def write_control_reg0(self, value: int) -> None:
        self.control_reg0 = value & 0xFF
        if not (self.control_reg0 & 0x04):  # -FRST=0: hold internal FDC in reset
            self.abort()

    def write_control_reg1(self, value: int) -> None:
        self.control_reg1 = value & 0xFF

    def write_data(self, value: int) -> None:
        value &= 0xFF
        if self._phase is Phase.IDLE:
            self._start_command(value)
        elif self._phase is Phase.COMMAND:
            self._cmd_buffer.append(value)
            if len(self._cmd_buffer) >= self._cmd_needed:
                self._execute_command()
        elif self._phase is Phase.EXECUTION and self._exec_is_write:
            self._exec_buffer.append(value)
            if len(self._exec_buffer) >= self._exec_needed:
                if _decode(self._command) == CMD_FORMAT:
                    self._complete_format()
                else:
                    c, h, r, _n = self._exec_ctr
                    drive = self._drive(self._selected_drive_index)
                    if drive is not None:
                        drive.write_sector(c, h, r, bytes(self._exec_buffer))
                    self._start_result_phase()
        # RESULT Phase / read-side EXECUTION Phase: a host write here is
        # "Illegal" per the datasheet's Main Status Register function table;
        # this model simply ignores it rather than modelling the illegal-combo
        # detection logic.

    # -- command dispatch ----------------------------------------------------

    def _start_command(self, value: int) -> None:
        self._command = value
        opcode = _decode(value)
        if opcode is None:
            self._invalid_command()
            return
        needed = _PARAM_COUNT[opcode]
        self._cmd_buffer = bytearray()
        self._cmd_needed = needed
        if needed == 0:
            self._execute_command()
        else:
            self._phase = Phase.COMMAND

    def _invalid_command(self) -> None:
        self._result_buffer = bytearray([ST0_INVALID])
        self._result_index = 0
        self._phase = Phase.RESULT

    def _execute_command(self) -> None:
        opcode = _decode(self._command)
        params = self._cmd_buffer
        if opcode == CMD_SPECIFY:
            self._phase = Phase.IDLE
        elif opcode == CMD_SENSE_INTERRUPT_STATUS:
            self._result_buffer = bytearray([self._last_st0, self._last_pcn & 0xFF])
            self._result_index = 0
            self._phase = Phase.RESULT
            # SENSE INTERRUPT STATUS consumes the pending seek/recalibrate
            # result: a second, spurious call with no new seek in between
            # reports "nothing pending" (matches the power-on/reset default,
            # see ST0_INVALID) instead of replaying the same stale abnormal
            # status forever. The reset value is ST0 = 0x80 and PCN = 0, per
            # the datasheet; openMSX reports 0x00 here, an open question in
            # the fdc-tc8566af spec.
            self._last_st0 = ST0_INVALID
            self._last_pcn = 0
        elif opcode == CMD_SENSE_DEVICE_STATUS:
            self._cmd_sense_device_status(params)
        elif opcode == CMD_RECALIBRATE:
            self._cmd_recalibrate(params)
        elif opcode == CMD_SEEK:
            self._cmd_seek(params)
        elif opcode == CMD_READ_DATA:
            self._cmd_read_data(params)
        elif opcode == CMD_WRITE_DATA:
            self._cmd_write_data(params)
        elif opcode == CMD_FORMAT:
            self._cmd_format(params)
        else:
            self._invalid_command()

    def _cmd_sense_device_status(self, params: bytearray) -> None:
        hd_ds = params[0]
        index = hd_ds & 0x03
        drive = self._drive(index)
        st3 = hd_ds & 0x07  # DS1:DS0 and HD echoed
        if self._track00(index):
            st3 |= ST3_TRACK0
        if self._write_protect_signal(index):
            st3 |= ST3_WRITE_PROTECTED
        if drive is not None:
            # openMSX's empty-drive disk counts as double-sided.
            if drive.image is None or drive.image.sides == 2:
                st3 |= ST3_TWO_SIDE
            if drive.has_disk:
                st3 |= ST3_READY
        self._result_buffer = bytearray([st3 & 0xFF])
        self._result_index = 0
        self._phase = Phase.RESULT

    def _seek_st0(self, index: int) -> int:
        """ST0 base for SEEK/RECALIBRATE: the drive index, plus bit 7 and Not
        Ready for a nonexistent drive (openMSX TC8566AF::commandPhase1 and
        doSeek). A drive with no disk is not Not Ready: the head mechanism
        works without media."""
        st0 = index & 0x03
        if self._drive(index) is None:
            st0 |= ST0_INVALID | ST0_NOT_READY
        return st0

    def _end_seek(self, index: int, st0: int) -> None:
        """Record the result SENSE INTERRUPT STATUS reports. SEEK and
        RECALIBRATE have no Result Phase."""
        self._last_st0 = (st0 | ST0_SEEK_END) & 0xFF
        self._last_pcn = self._pcn[index]
        self._phase = Phase.IDLE

    def _cmd_seek(self, params: bytearray) -> None:
        """SEEK: step the PCN one cylinder at a time to NCN, stepping the
        head with it (openMSX TC8566AF::doSeek). The steps take no time in
        this model."""
        index = params[0] & 0x03
        ncn = params[1]
        self._selected_drive_index = index
        drive = self._drive(index)
        st0 = self._seek_st0(index)
        while self._pcn[index] != ncn:
            inward = ncn > self._pcn[index]
            self._pcn[index] += 1 if inward else -1
            if drive is not None:
                drive.step(inward)
        self._end_seek(index, st0)

    def _cmd_recalibrate(self, params: bytearray) -> None:
        """RECALIBRATE: step the head out until the drive reports track 0,
        at most _RECALIBRATE_STEPS pulses, then set the PCN to 0. Running out
        of pulses adds Equipment Check -- always so for a nonexistent drive,
        and for a drive whose motor is off (no track-0 signal), which is how
        a DISK ROM learns that a second drive is missing (openMSX
        TC8566AF::doSeek)."""
        index = params[0] & 0x03
        self._selected_drive_index = index
        drive = self._drive(index)
        st0 = self._seek_st0(index)
        remaining = _RECALIBRATE_STEPS
        while True:
            if self._track00(index) or remaining == 0:
                if remaining == 0:
                    st0 |= ST0_EQUIPMENT_CHECK
                break
            if drive is not None:
                drive.step(False)
            remaining -= 1
        self._pcn[index] = 0
        self._end_seek(index, st0)

    def _cmd_read_data(self, params: bytearray) -> None:
        hd_ds, c, h, r, n, _eot, _gpl, _dtl = params
        index = hd_ds & 0x03
        self._selected_drive_index = index
        drive = self._drive(index)
        if drive is None or not drive.has_disk:
            self._abnormal_result(index, ST0_NOT_READY, 0, c, h, r, n)
            return
        data = _locate_sector(drive, hd_ds, c, h, r)
        if data is None:
            self._abnormal_result(index, 0, ST1_NO_DATA, c, h, r, n)
            return
        self._exec_buffer = bytearray(data)
        self._exec_index = 0
        self._exec_is_write = False
        self._exec_ctr = (c, h, r, n)
        self._phase = Phase.EXECUTION

    def _cmd_write_data(self, params: bytearray) -> None:
        hd_ds, c, h, r, n, _eot, _gpl, _dtl = params
        index = hd_ds & 0x03
        self._selected_drive_index = index
        drive = self._drive(index)
        if drive is None or not drive.has_disk:
            self._abnormal_result(index, ST0_NOT_READY, 0, c, h, r, n)
            return
        if drive.write_protected:
            self._abnormal_result(index, 0, ST1_NOT_WRITABLE, c, h, r, n)
            return
        if _locate_sector(drive, hd_ds, c, h, r) is None:
            self._abnormal_result(index, 0, ST1_NO_DATA, c, h, r, n)
            return
        self._exec_buffer = bytearray()
        self._exec_index = 0
        self._exec_is_write = True
        self._exec_needed = SECTOR_SIZE
        self._exec_ctr = (c, h, r, n)
        self._phase = Phase.EXECUTION

    def _cmd_format(self, params: bytearray) -> None:
        """FORMAT: blank every sector of the drive's current (track, side).

        Positioned by whatever SEEK/RECALIBRATE already set (drive.track),
        not by the C values DISK ROM streams per sector -- see the module
        docstring. Readiness is checked upfront (drive/disk/write-protect),
        matching this file's own _cmd_write_data rather than openMSX's
        TC8566AF::executionPhaseWrite, which only discovers a write failure
        at the end via a caught exception around flushTrack()."""
        hd_ds, _n, sc, _gpl, filler = params
        index = hd_ds & 0x03
        self._selected_drive_index = index
        head = (hd_ds >> 2) & 0x01
        drive = self._drive(index)
        if drive is None or not drive.has_disk:
            self._abnormal_result(index, ST0_NOT_READY, 0, 0, head, 0, 0)
            return
        if drive.write_protected:
            self._abnormal_result(index, 0, ST1_NOT_WRITABLE, 0, head, 0, 0)
            return
        self._exec_buffer = bytearray()
        self._exec_index = 0
        self._exec_is_write = True
        self._exec_needed = 4 * max(1, sc)
        self._format_fill = filler
        self._exec_ctr = (0, head, 0, 0)
        self._phase = Phase.EXECUTION

    def _complete_format(self) -> None:
        _c, head, _r, _n = self._exec_ctr
        drive = self._drive(self._selected_drive_index)
        if drive is not None:
            drive.format_track(drive.track, head, fill=self._format_fill)
        st0 = self._selected_drive_index & 0x03
        self._result_buffer = bytearray([st0 & 0xFF, 0, 0, 0, head, 0, 0])
        self._result_index = 0
        self._phase = Phase.RESULT

    def _abnormal_result(
        self, index: int, st0_extra: int, st1: int, c: int, h: int, r: int, n: int
    ) -> None:
        st0 = (index & 0x03) | ST0_ABNORMAL | st0_extra
        self._result_buffer = bytearray([st0 & 0xFF, st1 & 0xFF, 0, c, h, r, n])
        self._result_index = 0
        self._phase = Phase.RESULT

    def _start_result_phase(self) -> None:
        c, h, r, n = self._exec_ctr
        st0 = self._selected_drive_index & 0x03
        self._result_buffer = bytearray([st0, 0, 0, c, h, (r + 1) & 0xFF, n])
        self._result_index = 0
        self._phase = Phase.RESULT

    # ------------------------------------------------------------ save-state

    def snapshot(self) -> TC8566AFState:
        """Capture register/phase/buffer state. ``drives``/``drive`` are not
        included -- see TC8566AFState."""
        return {
            "control_reg0": self.control_reg0,
            "control_reg1": self.control_reg1,
            "selected_drive_index": self._selected_drive_index,
            "last_st0": self._last_st0,
            "last_pcn": self._last_pcn,
            "pcn": list(self._pcn),
            "phase": self._phase.value,
            "command": self._command,
            "cmd_buffer": bytearray(self._cmd_buffer),
            "cmd_needed": self._cmd_needed,
            "result_buffer": bytearray(self._result_buffer),
            "result_index": self._result_index,
            "exec_buffer": bytearray(self._exec_buffer),
            "exec_index": self._exec_index,
            "exec_is_write": self._exec_is_write,
            "exec_needed": self._exec_needed,
            "exec_ctr": list(self._exec_ctr),
            "format_fill": self._format_fill,
        }

    def parse_state(self, obj: object, label: str) -> TC8566AFState:
        """Validate a snapshot() object without mutating the controller.

        Raises:
            SchemaError: A missing/unknown key or a value of the wrong type
                or range, or a read index past its buffer.
        """
        fields = check_object(obj, label, _STATE_KEYS)
        result_buffer = check_bytes(
            fields["result_buffer"], f"{label}.result_buffer", max_length=_MAX_RESULT_BYTES
        )
        exec_buffer = check_bytes(
            fields["exec_buffer"], f"{label}.exec_buffer", max_length=_MAX_EXEC_BYTES
        )
        return {
            "control_reg0": check_int(fields["control_reg0"], f"{label}.control_reg0", 0, 0xFF),
            "control_reg1": check_int(fields["control_reg1"], f"{label}.control_reg1", 0, 0xFF),
            "selected_drive_index": check_int(
                fields["selected_drive_index"], f"{label}.selected_drive_index", 0, 3
            ),
            "last_st0": check_int(fields["last_st0"], f"{label}.last_st0", 0, 0xFF),
            "last_pcn": check_int(fields["last_pcn"], f"{label}.last_pcn", 0, 0xFF),
            "pcn": check_int_list(fields["pcn"], f"{label}.pcn", 4, 0, 0xFF),
            "phase": check_int(fields["phase"], f"{label}.phase", 0, 3),
            "command": check_int(fields["command"], f"{label}.command", 0, 0xFF),
            "cmd_buffer": check_bytes(
                fields["cmd_buffer"], f"{label}.cmd_buffer", max_length=_MAX_CMD_BYTES
            ),
            "cmd_needed": check_choice(
                fields["cmd_needed"], f"{label}.cmd_needed", (0, 1, 2, 5, 8)
            ),
            "result_buffer": result_buffer,
            "result_index": check_int(
                fields["result_index"], f"{label}.result_index", 0, len(result_buffer)
            ),
            "exec_buffer": exec_buffer,
            "exec_index": check_int(
                fields["exec_index"], f"{label}.exec_index", 0, len(exec_buffer)
            ),
            "exec_is_write": check_bool(fields["exec_is_write"], f"{label}.exec_is_write"),
            "exec_needed": check_int(
                fields["exec_needed"], f"{label}.exec_needed", 0, _MAX_EXEC_BYTES
            ),
            "exec_ctr": check_int_list(fields["exec_ctr"], f"{label}.exec_ctr", 4, 0, 0xFF),
            "format_fill": check_int(fields["format_fill"], f"{label}.format_fill", 0, 0xFF),
        }

    def apply_state(self, state: TC8566AFState) -> None:
        """Assign a state returned by parse_state()."""
        c, h, r, n = state["exec_ctr"]
        self.control_reg0 = state["control_reg0"]
        self.control_reg1 = state["control_reg1"]
        self._selected_drive_index = state["selected_drive_index"]
        self._last_st0 = state["last_st0"]
        self._last_pcn = state["last_pcn"]
        self._pcn = list(state["pcn"])
        self._phase = Phase(state["phase"])
        self._command = state["command"]
        self._cmd_buffer = bytearray(state["cmd_buffer"])
        self._cmd_needed = state["cmd_needed"]
        self._result_buffer = bytearray(state["result_buffer"])
        self._result_index = state["result_index"]
        self._exec_buffer = bytearray(state["exec_buffer"])
        self._exec_index = state["exec_index"]
        self._exec_is_write = state["exec_is_write"]
        self._exec_needed = state["exec_needed"]
        self._exec_ctr = (c, h, r, n)
        self._format_fill = state["format_fill"]
