from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, TypedDict

from msx.cassette import CassettePort
from msx.clock import CoreClock
from msx.dac import EventDac
from msx.input import InputLines
from msx.machine_spec import PpiControlRead
from msx.memory import Memory

# Key-click output while Port C bit 7 is 1: the PSG single-channel maximum
# (12287) scaled by openMSX's click-to-PSG ratio -- KeyClick writes 0xFF/0x80
# to a DACSound8U (127/128 of full scale), and its machine configs give the PPI
# sound volume 16000 against the PSG's 21000. 12287 * 127/128 * 16000/21000.
KEY_CLICK_LEVEL: int = 9288
# Control word bit 3: 1 sets the Port C upper half (bits 7-4) to input.
_CTRL_PORT_C_UPPER_INPUT = 0x08
# Port C bit 4: the tape motor, 0 = on.
_PORT_C_MOTOR_OFF = 0x10


class PpiState(TypedDict):
    """Save-state schema for PPI.snapshot_state()/apply_state(): the Port C
    latch, the last mode-set control word and the port-0xAB bus latch. Port A
    is Memory.slot_register, saved with the memory; Port B is input."""

    port_c: int
    control: int
    bus_latch: int


@dataclass
class PPI:
    """The 8255 PPI: the primary slot register, the keyboard row select and
    matrix read, and the key-click bit, on ports 0xA8-0xAB."""

    memory: Memory
    _input: InputLines | None = field(default=None, repr=False)
    # Intel 8255 post-reset control word: mode 0, ports A/B/C all input.
    _POST_RESET_CONTROL: ClassVar[int] = 0x9B
    # PPI Port C (0xAA): bits 3-0 = keyboard row select; bits 7-4 = output control
    # lines (bit4 tape motor, bit5 tape out, bit6 CAPS LED, bit7 key click).
    _port_c: int = field(default=0, repr=False)
    # Last mode-set control word written to 0xAB, or the Intel 8255 post-reset
    # default before any write.
    _control: int = field(default=_POST_RESET_CONTROL, repr=False)
    control_read: PpiControlRead = PpiControlRead.CONTROL
    # S3527 port 0xAB read value: a read of 0xA8-0xAA leaves the byte read, a
    # write to 0xA8-0xAB the inverse of the byte written (real HB-10,
    # 2026-10-08). Kept in every mode; the 0xA9 write and the power-on
    # value were not measured.
    _bus_latch: int = field(default=0xFF, repr=False)
    # Key click (Port C bit 7) as a 1-bit DAC; the frontend mixes its output.
    click: EventDac = field(default_factory=EventDac, repr=False)
    # The emulated time (msx/clock.py) that timestamps the clicks;
    # build_machine gives the machine's record.
    _clock: CoreClock = field(default_factory=CoreClock, repr=False)
    # The tape deck whose motor Port C bit 4 drives (0 = on), or None when
    # the machine has no cassette port.
    cassette: CassettePort | None = field(default=None, repr=False)

    @property
    def control(self) -> int:
        """The stored control word, whatever port 0xAB reads."""
        return self._control

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        self._bus_latch = value ^ 0xFF
        if port == 0xA8:
            self.memory.set_slot_register(value)
        elif port == 0xAA:
            self._set_port_c(value)
        elif port == 0xAB:
            self._write_control(value)

    def _write_control(self, value: int) -> None:
        if value & 0x80:
            # Mode-set word: leaves the existing output latches unchanged.
            # Intel 8255 datasheets disagree on whether a mode change resets
            # output latches to 0; this follows openMSX's I8255 core (shared
            # across its MSX/SC-3000/SVI machines) rather than clearing them.
            self._control = value
            # The mode-set word drives the stored latch again (openMSX
            # I8255::writeControlPort calls outputPortC).
            self._drive_motor()
        else:
            # Port C bit set/reset: bits 3-1 select the bit index, bit 0 the value.
            bit = (value >> 1) & 0x07
            if value & 0x01:
                self._set_port_c(self._port_c | (1 << bit))
            else:
                self._set_port_c(self._port_c & ~(1 << bit) & 0xFF)

    def _set_port_c(self, value: int) -> None:
        # The click sounds only on a bit 7 change (openMSX MSXPPI::writeC1).
        if (self._port_c ^ value) & 0x80:
            self.click.write(self._clock.cycle, KEY_CLICK_LEVEL if value & 0x80 else 0)
        self._port_c = value
        self._drive_motor()

    def _drive_motor(self) -> None:
        """Drive the tape motor from Port C bit 4, only while the control
        word sets the Port C upper half to output (bit 3 = 0), as openMSX's
        I8255::outputPortC does. In input mode the motor keeps its state.
        The key click and the row select do not follow this rule (a known
        difference, add-cassette-playback design Non-Goals)."""
        if self.cassette is not None and not self._control & _CTRL_PORT_C_UPPER_INPUT:
            self.cassette.set_motor(not self._port_c & _PORT_C_MOTOR_OFF)

    def reset(self) -> None:
        """Restore the Intel 8255 post-reset defaults: control word back to
        _POST_RESET_CONTROL and Port C's output latches cleared, which also
        silences the key click (openMSX KeyClick::reset). The control word
        sets Port C to input, so the tape motor keeps its state."""
        self._control = self._POST_RESET_CONTROL
        self._port_c = 0
        self.click.reset(0)

    def snapshot_state(self) -> PpiState:
        return {"port_c": self._port_c, "control": self._control, "bus_latch": self._bus_latch}

    def apply_state(self, state: PpiState) -> None:
        """Restore a snapshot_state(). The key click holds the level Port C
        bit 7 implies, with no click recorded for the change."""
        self._port_c = state["port_c"]
        self._control = state["control"]
        self._bus_latch = state["bus_latch"]
        self.click.reset(KEY_CLICK_LEVEL if self._port_c & 0x80 else 0)

    def read_port(self, port: int) -> int:
        if port == 0xAB:
            return self._read_control_port()
        if port == 0xA8:
            value = self.memory.slot_register & 0xFF
        elif port == 0xA9:
            row = self._port_c & 0x0F
            value = 0xFF
            if self._input is not None and row < len(self._input.matrix):
                value = self._input.matrix[row] & 0xFF
        else:
            value = self._port_c & 0xFF
        self._bus_latch = value
        return value

    def _read_control_port(self) -> int:
        # 0xAB: write-only in every MSX reference; see PpiControlRead.
        if self.control_read is PpiControlRead.OPEN_BUS:
            return 0xFF
        if self.control_read is PpiControlRead.BUS_LATCH:
            return self._bus_latch
        return self._control & 0xFF
