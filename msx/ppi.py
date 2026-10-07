from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from msx.input import InputState
from msx.memory import Memory


@dataclass
class PPI:
    memory: Memory
    _input: InputState | None = field(default=None, repr=False)
    # Intel 8255 post-reset control word: mode 0, ports A/B/C all input.
    _POST_RESET_CONTROL: ClassVar[int] = 0x9B
    # PPI Port C (0xAA): bits 3-0 = keyboard row select; bits 7-4 = output control
    # lines (bit4 tape motor, bit5 tape out, bit6 CAPS LED, bit7 key click).
    _port_c: int = field(default=0, repr=False)
    # Last mode-set control word written to 0xAB, or the Intel 8255 post-reset
    # default before any write.
    _control: int = field(default=_POST_RESET_CONTROL, repr=False)
    # Whether a port 0xAB read returns _control. A CMOS 82C55A reads the
    # control word back (openMSX I8255::readControlPort models this); an NMOS
    # 8255A does not drive the bus there, so the MSX pull-ups give 0xFF.
    # Verified on a real SONY HB-F1XD (S1985 MSX-ENGINE): 0xAB reads 0xFF
    # (extras/HB-F1XD/PORTRD.BAS, 2026-10-05).
    control_readback: bool = True

    @property
    def control(self) -> int:
        """The stored control word, whether or not port 0xAB reads it back."""
        return self._control

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA8:
            self.memory.set_slot_register(value)
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

    def reset(self) -> None:
        """Restore the Intel 8255 post-reset defaults: control word back to
        _POST_RESET_CONTROL and Port C's output latches cleared."""
        self._control = self._POST_RESET_CONTROL
        self._port_c = 0

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
        # 0xAB: write-only in every MSX reference; see control_readback.
        if not self.control_readback:
            return 0xFF
        return self._control & 0xFF
