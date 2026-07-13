"""RP-5C01 Real-Time Clock for MSX2 (ports 0xB4 address / 0xB5 data).

Faithful to openMSX's RP5C01: 4 register blocks of 13 nibbles each, selected by
the low two bits of the mode register (reg 13). Blocks 0/1 are the clock and
alarm/settings (block 0 time digits reflect the host clock captured at power-on);
blocks 2/3 are 13 nibbles each of battery-backed CMOS RAM that must persist
written values — the MSX2 BIOS stores its boot configuration there and reads it
back during power-on, looping forever if the read-back is invalid.

The chip is a 4-bit device: only the low nibble of each data byte is meaningful,
and the MSX floats the high nibble to 1s (reads return 0xF0 | nibble).

Porting seam: the host clock is read exactly once, at construction, and converted
to fixed BCD nibbles (`_time_block0` / `_leap`); nothing in the read path touches
a date library. A port injects a timestamp there and computes the digits with its
own calendar routine — the rest of the device is plain integer state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MODE_REG = 13
TEST_REG = 14
RESET_REG = 15

MODE_TIMER_ENABLE = 0x08
MODE_BLOCK_SELECT = 0x03
REGS_PER_BLOCK = 13

# Per-block write/read masks; 0-bits are ignored on write and read back as 0.
_MASK: tuple[tuple[int, ...], ...] = (
    (0xF, 0x7, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0xF, 0x1, 0xF, 0xF),  # block 0: time
    (0x0, 0x0, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0x0, 0x1, 0x3, 0x0),  # block 1: alarm
    (0xF,) * 13,                                                        # block 2: RAM
    (0xF,) * 13,                                                        # block 3: RAM
)


@dataclass
class RTC:
    """RP-5C01 RTC: ports 0xB4 (register select) and 0xB5 (4-bit data)."""

    _addr: int = 0
    _mode: int = MODE_TIMER_ENABLE
    _test: int = 0
    _reset: int = 0
    # 4 blocks x 13 nibbles. Blocks 2/3 hold the BIOS CMOS config.
    _regs: list[int] = field(default_factory=lambda: [0] * (4 * REGS_PER_BLOCK), repr=False)
    # Clock reference captured once at construction (frozen), deterministic within a
    # run and non-zero, which is all the BIOS power-on check needs.
    _epoch: datetime = field(default_factory=datetime.now, repr=False)
    # Block-0 BCD nibbles + block-1 leap counter, derived once from _epoch.
    _time_block0: list[int] = field(default_factory=list, init=False, repr=False)
    _leap: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._time_block0, self._leap = self._time_to_nibbles(self._epoch)

    @staticmethod
    def _time_to_nibbles(now: datetime) -> tuple[list[int], int]:
        """Convert a timestamp to the 13 block-0 BCD nibbles + the leap counter."""
        year = (now.year - 1980) % 100
        rp_wday = (now.weekday() + 1) % 7  # Python Mon=0..Sun=6 -> RP5C01 Sun=0
        block0 = [
            now.second % 10, now.second // 10,
            now.minute % 10, now.minute // 10,
            now.hour % 10, now.hour // 10,
            rp_wday,
            now.day % 10, now.day // 10,
            now.month % 10, now.month // 10,
            year % 10, year // 10,
        ]
        return block0, (now.year - 1980) % 4

    def read_port(self, port: int) -> int:
        """Read the register-select port (0xB4, write-only) or data port (0xB5)."""
        if (port & 1) == 0:
            return 0xFF  # 0xB4 is write-only; high bits float to 1
        return 0xF0 | self._read_reg(self._addr)

    def write_port(self, port: int, value: int) -> None:
        """Write the register-select latch (0xB4) or the selected register (0xB5)."""
        if (port & 1) == 0:
            self._addr = value & 0x0F
        else:
            self._write_reg(self._addr, value & 0x0F)

    # -- register access ---------------------------------------------------

    def _read_reg(self, reg: int) -> int:
        if reg == MODE_REG:
            return self._mode & 0x0F
        if reg in (TEST_REG, RESET_REG):
            return 0x0F  # write-only
        block = self._mode & MODE_BLOCK_SELECT
        if block <= 1:
            self._refresh_time_regs()
        return self._regs[block * REGS_PER_BLOCK + reg] & _MASK[block][reg]

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == MODE_REG:
            self._mode = value
            return
        if reg == TEST_REG:
            self._test = value
            return
        if reg == RESET_REG:
            self._reset = value
            return
        block = self._mode & MODE_BLOCK_SELECT
        self._regs[block * REGS_PER_BLOCK + reg] = value & _MASK[block][reg]

    def _refresh_time_regs(self) -> None:
        """Copy the (frozen) BCD time into block 0 and the leap counter into
        block 1. Integer-only: no date library on the read path."""
        self._regs[0:REGS_PER_BLOCK] = self._time_block0
        self._regs[1 * REGS_PER_BLOCK + 11] = self._leap
