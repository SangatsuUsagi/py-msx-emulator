"""RP-5C01 Real-Time Clock for MSX2 (ports 0xB4 address / 0xB5 data).

Faithful to openMSX's RP5C01 and the RP5C01 data sheet
(references/docs/RICOH_RP5C01.md): 4 register blocks of 13 nibbles each, selected by
the low two bits of the mode register (reg 13). Blocks 0/1 are the clock and
alarm/settings (block 0 time digits are the start time the host supplied once,
advanced by emulated time);
blocks 2/3 are 13 nibbles each of battery-backed CMOS RAM that must persist
written values — the MSX2 BIOS stores its boot configuration there and reads it
back during power-on, looping forever if the read-back is invalid.

The chip is a 4-bit device: only the low nibble of each data byte is meaningful,
and the MSX floats the high nibble to 1s (reads return 0xF0 | nibble).

The core never reads the host clock: the host passes the start time once
(RtcTime) and the time advances with emulated cycles only, so a run is
deterministic. Calendar arithmetic is integer-only (days-from-civil).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from msx.boundary import RTC_DEFAULT_START, RtcTime
from msx.snapshot_schema import I64_MAX, check_int, check_int_list, check_object

MODE_REG = 13
TEST_REG = 14
RESET_REG = 15

# Emulated time base: Z80 T-states per second (3.579545 MHz).
CPU_CLOCK_HZ = 3_579_545

MODE_TIMER_ENABLE = 0x08
MODE_BLOCK_SELECT = 0x03
REGS_PER_BLOCK = 13

# Block 1, register 10: 12/24-hour select (bit 0 -- see _encode_hour).
HOUR_MODE_REG = 10
HOUR_MODE_24H = 0x1

# Blocks 2/3 (26 nibbles of battery-backed CMOS RAM), persisted to disk.
SRAM_SIZE = 26

# Per-block write/read masks; 0-bits are ignored on write and read back as 0.
_MASK: tuple[tuple[int, ...], ...] = (
    (0xF, 0x7, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0xF, 0x1, 0xF, 0xF),  # block 0: time
    (0x0, 0x0, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0x0, 0x1, 0x3, 0x0),  # block 1: alarm
    (0xF,) * 13,                                                        # block 2: RAM
    (0xF,) * 13,                                                        # block 3: RAM
)


class RtcState(TypedDict):
    """Save-state schema for RTC.snapshot()/apply_state(). The time is saved
    as the start time plus the emulated cycles since it, because the
    machine's cycle counter itself is not part of a snapshot."""

    start: list[int]          # year, month, day, hour, minute, second
    elapsed_cycles: int
    addr: int
    mode: int
    test: int
    reset: int
    regs: list[int]


_RTC_STATE_KEYS: frozenset[str] = frozenset(RtcState.__annotations__)


@dataclass
class RTC:
    """RP-5C01 RTC: ports 0xB4 (register select) and 0xB5 (4-bit data)."""

    _addr: int = 0
    _mode: int = MODE_TIMER_ENABLE
    _test: int = 0
    _reset: int = 0
    # 4 blocks x 13 nibbles. Blocks 2/3 hold the BIOS CMOS config.
    _regs: list[int] = field(default_factory=lambda: [0] * (4 * REGS_PER_BLOCK), repr=False)
    # Blocks 2/3's persisted image, seeded into _regs[26:52] at construction;
    # save_sram() reads current state back from _regs, not from this field.
    sram: bytearray = field(default_factory=lambda: bytearray(SRAM_SIZE), repr=False)
    # Set when a write or restore changes a block 2/3 nibble; cleared by
    # mark_sram_persisted() once the host has written save_sram().
    _sram_dirty: bool = field(default=False, init=False, repr=False)
    # Time at emulated cycle _start_cycle, supplied once by the host.
    start: RtcTime = RTC_DEFAULT_START
    # Emulated cycle source (machine.cycle_count), wired by build_machine. A
    # bare RTC without one stays at its start time.
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    _start_cycle: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if len(self.sram) != SRAM_SIZE:
            self.sram = bytearray(SRAM_SIZE)
        self._regs[2 * REGS_PER_BLOCK:4 * REGS_PER_BLOCK] = list(self.sram)
        # Default to 24-hour (register 10 defaults to all-zero storage
        # otherwise, which would read as 12-hour) -- matches this
        # implementation's behaviour before 12-hour support existed.
        self._regs[REGS_PER_BLOCK + HOUR_MODE_REG] = HOUR_MODE_24H

    def _elapsed_seconds(self) -> int:
        return self._elapsed_cycles() // CPU_CLOCK_HZ

    def snapshot(self) -> RtcState:
        """Capture the RTC for save-state (paired with apply_state)."""
        st = self.start
        return {
            "start": [st.year, st.month, st.day, st.hour, st.minute, st.second],
            "elapsed_cycles": self._elapsed_cycles(),
            "addr": self._addr,
            "mode": self._mode,
            "test": self._test,
            "reset": self._reset,
            "regs": list(self._regs),
        }

    def parse_state(self, obj: object, label: str) -> RtcState:
        """Validate a snapshot() object without touching the chip."""
        state = check_object(obj, label, _RTC_STATE_KEYS)
        start = check_int_list(state["start"], f"{label}.start", 6, 0, 9999)
        _check_start(start, f"{label}.start")
        return {
            "start": start,
            "elapsed_cycles": check_int(
                state["elapsed_cycles"], f"{label}.elapsed_cycles", 0, I64_MAX
            ),
            "addr": check_int(state["addr"], f"{label}.addr", 0, 0x0F),
            "mode": check_int(state["mode"], f"{label}.mode", 0, 0x0F),
            "test": check_int(state["test"], f"{label}.test", 0, 0x0F),
            "reset": check_int(state["reset"], f"{label}.reset", 0, 0x0F),
            "regs": check_int_list(
                state["regs"], f"{label}.regs", 4 * REGS_PER_BLOCK, 0, 0x0F
            ),
        }

    def apply_state(self, state: RtcState) -> None:
        """Assign an already-parsed RtcState (see parse_state)."""
        y, mo, d, h, mi, sec = state["start"]
        self.start = RtcTime(y, mo, d, h, mi, sec)
        now = self._get_cycle() if self._get_cycle is not None else 0
        self._start_cycle = now - state["elapsed_cycles"]
        self._addr = state["addr"]
        self._mode = state["mode"]
        self._test = state["test"]
        self._reset = state["reset"]
        cmos = slice(2 * REGS_PER_BLOCK, 4 * REGS_PER_BLOCK)
        if state["regs"][cmos] != self._regs[cmos]:
            self._sram_dirty = True
        self._regs = list(state["regs"])

    def _elapsed_cycles(self) -> int:
        if self._get_cycle is None:
            return 0
        return self._get_cycle() - self._start_cycle

    def save_sram(self) -> bytes:
        """Blocks 2/3's current 26 nibbles, one raw byte per nibble
        (battery-save format, no packing)."""
        return bytes(self._regs[2 * REGS_PER_BLOCK:4 * REGS_PER_BLOCK])

    @property
    def sram_dirty(self) -> bool:
        return self._sram_dirty

    def mark_sram_persisted(self) -> None:
        self._sram_dirty = False

    # Loading a saved image is done by passing it as the `sram=` constructor
    # argument; __post_init__ blanks a wrong-size image.

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
        index = block * REGS_PER_BLOCK + reg
        masked = value & _MASK[block][reg]
        if block >= 2 and self._regs[index] != masked:
            self._sram_dirty = True
        self._regs[index] = masked

    def _refresh_time_regs(self) -> None:
        """Write the current time into block 0 and the leap counter into
        block 1. Hour registers 4/5 are encoded against HOUR_MODE_REG's
        current value, since that register can be written at any time."""
        st = self.start
        total = (_days_from_civil(st.year, st.month, st.day) * 86_400
                 + st.hour * 3_600 + st.minute * 60 + st.second
                 + self._elapsed_seconds())
        days = total // 86_400
        secs = total % 86_400
        year, month, day = _civil_from_days(days)
        hour = secs // 3_600
        minute = (secs // 60) % 60
        second = secs % 60
        weekday = (days + 4) % 7          # 1970-01-01 was a Thursday; Sun=0
        rp_year = (year - 1980) % 100
        hour_lo, hour_hi = self._encode_hour(hour)
        self._regs[0:REGS_PER_BLOCK] = [
            second % 10, second // 10,
            minute % 10, minute // 10,
            hour_lo, hour_hi,
            weekday,
            day % 10, day // 10,
            month % 10, month // 10,
            rp_year % 10, rp_year // 10,
        ]
        self._regs[1 * REGS_PER_BLOCK + 11] = (year - 1980) % 4

    def _encode_hour(self, hour: int) -> tuple[int, int]:
        """BCD-encode `hour` (0-23) into (units, tens) registers per block 1
        register 10 bit 0: 24-hour (hour % 10, hour // 10) when set -- the
        default, see __post_init__ -- else 12-hour, tens digit offset by 2
        for PM (RP/RF5C01A datasheet; +20 encoding confirmed against
        openMSX's own time<->registers conversion)."""
        if self._regs[REGS_PER_BLOCK + HOUR_MODE_REG] & HOUR_MODE_24H:
            return hour % 10, hour // 10
        hour12 = hour % 12 or 12
        return hour12 % 10, hour12 // 10 + (2 if hour >= 12 else 0)


def _check_start(start: list[int], label: str) -> None:
    """Reject a start time that is not a real calendar date and time."""
    year, month, day, hour, minute, second = start
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59
            and second <= 59):
        raise ValueError(f"{label}: invalid date/time {start}")
    if _civil_from_days(_days_from_civil(year, month, day)) != (year, month, day):
        raise ValueError(f"{label}: invalid date {year}-{month}-{day}")


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 for a Gregorian date (integer-only)."""
    y = year - 1 if month <= 2 else year
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = (month + 9) % 12
    doy = (153 * mp + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146_097 + doe - 719_468


def _civil_from_days(days: int) -> tuple[int, int, int]:
    """Gregorian (year, month, day) for a day count since 1970-01-01."""
    z = days + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return year, month, day
