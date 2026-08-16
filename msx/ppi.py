from __future__ import annotations

from dataclasses import dataclass, field

from msx.input import InputState
from msx.memory import Memory


@dataclass
class PPI:
    memory: Memory
    _input: InputState | None = field(default=None, repr=False)
    # PPI Port C (0xAA): bits 3-0 = keyboard row select; bits 7-4 = output control
    # lines (bit4 tape motor, bit5 tape out, bit6 CAPS LED, bit7 key click).
    _port_c: int = field(default=0, repr=False)
    # Last mode-set control word written to 0xAB, or the Intel 8255 post-reset
    # default (mode 0, ports A/B/C all input) before any write. Read back
    # verbatim by port 0xAB, matching openMSX's I8255::readControlPort.
    _control: int = field(default=0x9B, repr=False)

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA8:
            self.memory.slot_register = value
        elif port == 0xAA:
            self._port_c = value
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
                self._port_c |= 1 << bit
            else:
                self._port_c &= ~(1 << bit) & 0xFF

    def read_port(self, port: int) -> int:
        if port == 0xA8:
            return self.memory.slot_register & 0xFF
        if port == 0xA9:
            row = self._port_c & 0x0F
            if self._input is not None and row < len(self._input.matrix):
                return self._input.matrix[row]
            return 0xFF
        if port == 0xAA:
            return self._port_c & 0xFF
        # 0xAB: MSX firmware documents this as write-only, but returns the
        # last mode-set control word written, matching openMSX rather than
        # floating the bus at 0xFF.
        return self._control & 0xFF
