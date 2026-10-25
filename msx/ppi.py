from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, TypedDict

from msx.dac import EventDac
from msx.input import InputLines
from msx.memory import Memory

# Key-click output while Port C bit 7 is 1: the PSG single-channel maximum
# (12287) scaled by openMSX's click-to-PSG ratio -- KeyClick writes 0xFF/0x80
# to a DACSound8U (127/128 of full scale), and its machine configs give the PPI
# sound volume 16000 against the PSG's 21000. 12287 * 127/128 * 16000/21000.
KEY_CLICK_LEVEL: int = 9288


class PpiControlRead(str, Enum):
    """What a port 0xAB read returns; the 8255A data sheet calls it illegal."""

    # The last mode-set word: CMOS 82C55A, as openMSX I8255::readControlPort.
    CONTROL = "control"
    # 0xFF: nothing drives the bus. Verified on a real SONY HB-F1XD (S1985
    # MSX-ENGINE), extras/HB-F1XD/PORTRD.BAS, 2026-10-05.
    OPEN_BUS = "open_bus"
    # The byte latched by the last PPI access (see PPI._bus_latch). Verified
    # on a real SONY HB-10 (S3527 MSX-ENGINE), extras/HB-10/PORTRD1-4.BAS,
    # 2026-10-08.
    BUS_LATCH = "bus_latch"


class PpiState(TypedDict):
    """Save-state schema for PPI.snapshot_state()/apply_state(): the Port C
    latch, the last mode-set control word and the port-0xAB bus latch. Port A
    is Memory.slot_register, saved with the memory; Port B is input."""

    port_c: int
    control: int
    bus_latch: int


@dataclass
class PPI:
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
    # PORTRD2-4.BAS). Kept in every mode; the 0xA9 write and the power-on
    # value were not measured.
    _bus_latch: int = field(default=0xFF, repr=False)
    # Key click (Port C bit 7) as a 1-bit DAC; the frontend mixes its output.
    click: EventDac = field(default_factory=EventDac, repr=False)
    # Cycle source for click timestamps, wired by build_machine.
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)

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
            cycle = self._get_cycle() if self._get_cycle is not None else 0
            self.click.write(cycle, KEY_CLICK_LEVEL if value & 0x80 else 0)
        self._port_c = value

    def reset(self) -> None:
        """Restore the Intel 8255 post-reset defaults: control word back to
        _POST_RESET_CONTROL and Port C's output latches cleared, which also
        silences the key click (openMSX KeyClick::reset)."""
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
