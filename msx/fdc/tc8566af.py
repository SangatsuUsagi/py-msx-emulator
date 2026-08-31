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
from typing import TypedDict, cast

from msx.fdc.disk_drive import DiskDrive

SECTOR_SIZE: int = 512

# Main Status Register bits.
MSR_RQM: int = 0x80  # request for master: Data Register ready
MSR_DIO: int = 0x40  # data direction: 1 = FDC -> processor
MSR_NDM: int = 0x20  # non-DMA mode (always set by this model)
MSR_CB: int = 0x10   # FDC busy (Command/Execution/Result Phase in progress)

# Command opcodes (low 5 bits of the command byte; READ/WRITE DATA also use
# the top 3 bits for MT/MFM/SK, ignored by this functional model).
_CMD_MASK: int = 0x1F
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

# ST0 bits.
ST0_ABNORMAL: int = 0x40
ST0_INVALID: int = 0x80
ST0_SEEK_END: int = 0x20
ST0_NOT_READY: int = 0x08

# ST1 bits.
ST1_NO_DATA: int = 0x04
ST1_NOT_WRITABLE: int = 0x02

# ST3 bits.
ST3_WRITE_PROTECTED: int = 0x40
ST3_READY: int = 0x20
ST3_TRACK0: int = 0x10


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
    phase: int
    command: int
    cmd_buffer: bytes
    cmd_needed: int
    result_buffer: bytes
    result_index: int
    exec_buffer: bytes
    exec_index: int
    exec_is_write: bool
    exec_needed: int
    exec_ctr: tuple[int, int, int, int]
    format_fill: int


@dataclass
class TC8566AF:
    """TC8566AF controller owning all connected drives directly.

    Unlike the WD2793 (one externally-swapped ``.drive`` reference), the real
    chip's Control Register 0 addresses one of up to four drives it owns
    directly, and READ DATA/WRITE DATA/SEEK/RECALIBRATE/SENSE DEVICE STATUS
    each carry their own target drive in their first parameter byte (DS1:DS0)
    -- see openspec/changes/archive/2026-08-21-add-tc8566af-fdc/design.md.
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

    def reset(self) -> None:
        self.control_reg0 = 0
        self.control_reg1 = 0
        self._selected_drive_index = 0
        self._last_st0 = ST0_INVALID
        self._last_pcn = 0
        self.abort()

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
                if (self._command & _CMD_MASK) == CMD_FORMAT:
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
        opcode = value & _CMD_MASK
        needed = _PARAM_COUNT.get(opcode)
        if needed is None:
            self._invalid_command()
            return
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
        opcode = self._command & _CMD_MASK
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
            # status forever. Without this, a DISK ROM probing for a second
            # drive that doesn't exist (RECALIBRATE index=1 -> not ready ->
            # SENSE INTERRUPT STATUS) never sees its own acknowledgement take
            # effect and loops on SENSE INTERRUPT STATUS indefinitely --
            # matches openMSX's TC8566AF::resultsPhaseRead, which zeroes
            # status0 as soon as its first result byte is read.
            self._last_st0 = ST0_INVALID
            self._last_pcn = 0
        elif opcode == CMD_SENSE_DEVICE_STATUS:
            self._cmd_sense_device_status(params)
        elif opcode == CMD_RECALIBRATE:
            self._cmd_seek_class(params, ncn=0)
        elif opcode == CMD_SEEK:
            self._cmd_seek_class(params, ncn=params[1])
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
        head = (hd_ds >> 2) & 0x01
        drive = self._drive(index)
        st3 = index & 0x03
        if head:
            st3 |= 0x04
        if drive is not None and drive.has_disk:
            st3 |= ST3_READY
            if drive.write_protected:
                st3 |= ST3_WRITE_PROTECTED
            if drive.track == 0:
                st3 |= ST3_TRACK0
        self._result_buffer = bytearray([st3 & 0xFF])
        self._result_index = 0
        self._phase = Phase.RESULT

    def _cmd_seek_class(self, params: bytearray, *, ncn: int) -> None:
        """Shared SEEK/RECALIBRATE handling: both move a drive's head and
        report only through a later SENSE INTERRUPT STATUS (no Result Phase).

        Readiness here depends only on the drive existing, not on whether a
        disk is inserted -- the head/track-0 sensor is purely mechanical, so a
        real drive homes/seeks fine with no media loaded (matching openMSX's
        TC8566AF::doSeek, which sets Not Ready only for a non-existent
        "dummy" drive and otherwise runs the seek to completion regardless of
        isDiskInserted()). This differs from READ DATA/WRITE DATA/SENSE
        DEVICE STATUS, which genuinely do require a disk.

        A nonexistent drive (index beyond the configured drive count -- e.g.
        a DISK ROM probing for a second drive the machine doesn't have)
        still reports Seek End, not Abnormal Termination: openMSX's
        DummyDrive.isTrack00() always returns false ("National_FS-5500F1 2nd
        drive detection depends on this"), so TC8566AF::doSeek's RECALIBRATE
        path never takes its immediate-completion shortcut and instead steps
        the full 255-pulse limit before giving up -- reported as Seek End
        (+ Equipment Check, not modelled here -- see Deferred
        Specifications) rather than Abnormal Termination, since the seek
        mechanism itself did finish trying. This model's SEEK/RECALIBRATE
        are already instantaneous (no real step timing, a documented
        simplification), so that eventual completion is reported
        immediately here too, rather than looping. A DISK ROM polling SENSE
        INTERRUPT STATUS for Seek End to learn "no second drive" would
        otherwise never see it (found via FS-A1F hanging at boot on its
        drive-B probe)."""
        index = params[0] & 0x03
        self._selected_drive_index = index
        drive = self._drive(index)
        st0 = index & 0x03
        if (params[0] >> 2) & 1:
            st0 |= 0x04  # HD echoed
        if drive is None:
            st0 |= ST0_NOT_READY | ST0_SEEK_END
        else:
            drive.track = ncn & 0xFF
            st0 |= ST0_SEEK_END
        self._last_st0 = st0 & 0xFF
        self._last_pcn = drive.track if drive is not None else 0
        self._phase = Phase.IDLE

    def _cmd_read_data(self, params: bytearray) -> None:
        hd_ds, c, h, r, n, _eot, _gpl, _dtl = params
        index = hd_ds & 0x03
        self._selected_drive_index = index
        drive = self._drive(index)
        if drive is None or not drive.has_disk:
            self._abnormal_result(index, ST0_NOT_READY, 0, c, h, r, n)
            return
        data = drive.read_sector(c, h, r)
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
        if drive.read_sector(c, h, r) is None:
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
            "phase": self._phase.value,
            "command": self._command,
            "cmd_buffer": bytes(self._cmd_buffer),
            "cmd_needed": self._cmd_needed,
            "result_buffer": bytes(self._result_buffer),
            "result_index": self._result_index,
            "exec_buffer": bytes(self._exec_buffer),
            "exec_index": self._exec_index,
            "exec_is_write": self._exec_is_write,
            "exec_needed": self._exec_needed,
            "exec_ctr": self._exec_ctr,
            "format_fill": self._format_fill,
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore register/phase/buffer state produced by snapshot()."""
        typed_state = cast(TC8566AFState, state)
        self.control_reg0 = typed_state["control_reg0"]
        self.control_reg1 = typed_state["control_reg1"]
        self._selected_drive_index = typed_state["selected_drive_index"]
        self._last_st0 = typed_state["last_st0"]
        self._last_pcn = typed_state["last_pcn"]
        self._phase = Phase(typed_state["phase"])
        self._command = typed_state["command"]
        self._cmd_buffer = bytearray(typed_state["cmd_buffer"])
        self._cmd_needed = typed_state["cmd_needed"]
        self._result_buffer = bytearray(typed_state["result_buffer"])
        self._result_index = typed_state["result_index"]
        self._exec_buffer = bytearray(typed_state["exec_buffer"])
        self._exec_index = typed_state["exec_index"]
        self._exec_is_write = typed_state["exec_is_write"]
        self._exec_needed = typed_state["exec_needed"]
        c, h, r, n = typed_state["exec_ctr"]
        self._exec_ctr = (c, h, r, n)
        self._format_fill = typed_state["format_fill"]
