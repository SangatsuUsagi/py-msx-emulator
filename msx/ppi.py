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
    # Last 8255 mode-set control word written to 0xAB.
    _control: int = field(default=0, repr=False)

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
            # Mode-set word: store it; the keyboard row (Port C low nibble) is
            # left untouched.
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
        # 0xAB: the 8255 control word is not readable.
        return 0xFF
