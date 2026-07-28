"""YM2413 (OPLL) FM sound chip register interface.

Phase 1 scope: register file only (write-only chip, register-address latch +
data write). Synthesis (generate_samples) lands in a later phase; until then
the chip stores register writes but produces no sound.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Opll"]


@dataclass
class Opll:
    # Register file: index 0x00-0x38 covers user-tone, rhythm/test, and the
    # per-channel F-number/block/key-on/sustain/instrument/volume registers.
    _regs: bytearray = field(default_factory=lambda: bytearray(0x40), init=False, repr=False)
    _addr_latch: int = field(default=0, init=False, repr=False)

    # ------------------------------------------------------------------ I/O

    def write_addr(self, value: int) -> None:
        """Latch a register address (register-select write)."""
        self._addr_latch = value & 0xFF

    def write_data(self, value: int) -> None:
        """Write a data byte to the currently latched register address."""
        self.write_reg(self._addr_latch, value)

    def write_reg(self, index: int, value: int) -> None:
        """Write value directly to register index (bypasses the latch)."""
        index &= 0xFF
        if index < len(self._regs):
            self._regs[index] = value & 0xFF

    def read_reg(self, index: int) -> int:
        """Return the stored value of register index (0 if out of range)."""
        index &= 0xFF
        if index < len(self._regs):
            return self._regs[index]
        return 0

    def read(self) -> int:
        """OPLL is write-only; any read returns 0xFF."""
        return 0xFF

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register state and silence all channels."""
        self._regs = bytearray(0x40)
        self._addr_latch = 0
