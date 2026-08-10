"""FM-PAC cartridge: banked ROM + YM2413 (OPLL) + 8 KB battery-backed SRAM.

Ground truth: openMSX `MSXFmPac`/`MSXMusicBase`. Window is the primary-slot
0x4000-0x7FFF page pair (bank-switched ROM, magic-unlocked SRAM, and the
memory-mapped OPLL registers all live here); addresses outside the window
read 0xFF and ignore writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from msx.opll import Opll, OpllState

__all__ = ["FmPac", "FmPacState", "SRAM_SIZE"]


class FmPacState(TypedDict):
    """Save-state schema for FmPac.snapshot()/FmPac.restore().

    Nests the carried OPLL's own OpllState, so restoring a FmPacState fully
    reinstates the device's persistent state (SRAM/bank/enable/magic
    registers and the OPLL) in one call, matching the state-save-load spec's
    "Mapper snapshot and restore API" requirement.
    """

    sram: bytes
    bank: int
    enable: int
    r1ffe: int
    r1fff: int
    opll: OpllState

_WINDOW_BASE = 0x4000
_WINDOW_SIZE = 0x4000
_BANK_SIZE = 0x4000
SRAM_SIZE = 8192

# Window-relative (address & 0x3FFF) offsets of the fixed registers.
_OFF_MAGIC_LO = 0x1FFE
_OFF_MAGIC_HI = 0x1FFF
_OFF_OPLL_ADDR = 0x3FF4
_OFF_OPLL_DATA = 0x3FF5
_OFF_ENABLE = 0x3FF6
_OFF_BANK = 0x3FF7

_MAGIC_R1FFE = 0x4D
_MAGIC_R1FFF = 0x69

_ENABLE_IO_OPLL = 0x01     # bit 0: gates the I/O-port (0x7C/0x7D) OPLL access
_ENABLE_WRITE_PROTECT = 0x10  # bit 4: freezes the magic registers
_ENABLE_MASK = 0x11


@dataclass
class FmPac:
    """FM-PAC device. Installed as a primary slot's mapper (`read`/`write`)."""

    rom: bytes
    opll: "Opll"
    sram: bytearray = field(default_factory=lambda: bytearray(SRAM_SIZE))
    _bank: int = field(default=0, init=False, repr=False)
    _enable: int = field(default=0, init=False, repr=False)
    _r1ffe: int = field(default=0, init=False, repr=False)
    _r1fff: int = field(default=0, init=False, repr=False)
    _sram_enabled: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.sram) != SRAM_SIZE:
            self.sram = bytearray(SRAM_SIZE)

    # ------------------------------------------------------------- CPU memory

    def read(self, addr: int) -> int:
        """Memory-mapped read within the 0x4000-0x7FFF window (bank-switched
        ROM, or SRAM/magic registers once unlocked; see the module docstring).
        """
        if not (_WINDOW_BASE <= addr < _WINDOW_BASE + _WINDOW_SIZE):
            return 0xFF
        off = addr - _WINDOW_BASE
        if off == _OFF_ENABLE:
            return self._enable
        if off == _OFF_BANK:
            return self._bank
        if self._sram_enabled:
            if off < _OFF_MAGIC_LO:
                return self.sram[off]
            if off == _OFF_MAGIC_LO:
                return self._r1ffe
            if off == _OFF_MAGIC_HI:
                return self._r1fff
            return 0xFF
        return self.rom[self._bank * _BANK_SIZE + off]

    def write(self, addr: int, value: int) -> None:
        """Memory-mapped write within the 0x4000-0x7FFF window: SRAM magic
        unlock, OPLL register address/data, the enable/bank registers, or
        SRAM data (only while unlocked).
        """
        if not (_WINDOW_BASE <= addr < _WINDOW_BASE + _WINDOW_SIZE):
            return
        off = addr - _WINDOW_BASE
        value &= 0xFF
        if off == _OFF_MAGIC_LO:
            if not (self._enable & _ENABLE_WRITE_PROTECT):
                self._r1ffe = value
                self._check_sram_enable()
            return
        if off == _OFF_MAGIC_HI:
            if not (self._enable & _ENABLE_WRITE_PROTECT):
                self._r1fff = value
                self._check_sram_enable()
            return
        if off == _OFF_OPLL_ADDR:
            self.opll.write_addr(value)
            return
        if off == _OFF_OPLL_DATA:
            self.opll.write_data(value)
            return
        if off == _OFF_ENABLE:
            self._enable = value & _ENABLE_MASK
            if self._enable & _ENABLE_WRITE_PROTECT:
                # Actual value doesn't matter, only that it no longer matches
                # the magic combination (mirrors openMSX MSXFmPac::writeMem).
                self._r1ffe = 0
                self._r1fff = 0
                self._check_sram_enable()
            return
        if off == _OFF_BANK:
            self._bank = value & 0x03
            return
        if self._sram_enabled and off < _OFF_MAGIC_LO:
            self.sram[off] = value

    def _check_sram_enable(self) -> None:
        self._sram_enabled = self._r1ffe == _MAGIC_R1FFE and self._r1fff == _MAGIC_R1FFF

    # ---------------------------------------------------------------- I/O ports

    def read_port(self, port: int) -> int:
        """I/O-port read; always 0xFF (the OPLL is write-only)."""
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        """I/O-port write, routed to the OPLL's address/data register by
        port parity, but only while the enable register's I/O bit is set.
        """
        if not (self._enable & _ENABLE_IO_OPLL):
            return
        if port & 1:
            self.opll.write_data(value)
        else:
            self.opll.write_addr(value)

    # --------------------------------------------------------------------- reset

    def reset(self) -> None:
        """Power-on reset: resets the carried OPLL and clears bank/enable/
        magic registers (SRAM is left un-paged, per the unlock check).
        """
        self.opll.reset()
        self._bank = 0
        self._enable = 0
        self._r1ffe = 0
        self._r1fff = 0
        self._sram_enabled = False

    # ---------------------------------------------------------------------- SRAM

    def save_sram(self, path: Path) -> None:
        """Write the raw 8 KB SRAM image to path (battery-save format)."""
        path.write_bytes(self.sram)

    # Loading a saved SRAM image is done by passing it as the `sram=`
    # constructor argument; __post_init__ blanks a wrong-size image.

    # ------------------------------------------------------------- save-state

    def snapshot(self) -> FmPacState:
        """Capture device state for save-state (paired with restore),
        including the carried OPLL's own state.
        """
        return {
            "sram": bytes(self.sram),
            "bank": self._bank,
            "enable": self._enable,
            "r1ffe": self._r1ffe,
            "r1fff": self._r1fff,
            "opll": self.opll.snapshot(),
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore device state produced by snapshot(), including the
        carried OPLL's own state.
        """
        typed_state = cast(FmPacState, state)
        self.sram[:] = typed_state["sram"]
        self._bank = typed_state["bank"]
        self._enable = typed_state["enable"]
        self._r1ffe = typed_state["r1ffe"]
        self._r1fff = typed_state["r1fff"]
        self.opll.restore(cast(dict[str, Any], typed_state["opll"]))
        self._check_sram_enable()
