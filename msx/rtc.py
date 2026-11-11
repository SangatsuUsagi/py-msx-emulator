"""RP-5C01 Real-Time Clock for MSX2 (ports 0xB4 address / 0xB5 data).

Follows the RP5C01 data sheet (references/docs/RICOH_RP5C01.md): 4 register
blocks of 13 nibbles each, selected by the low two bits of the mode register
(reg 13). Block 0 holds the time digits, block 1 the alarm and settings
(12/24-hour select, leap-year counter), blocks 2/3 CMOS RAM that the MSX2
BIOS stores its boot configuration in and reads back during power-on.

The chip runs from the machine's battery, so every register keeps its value
while the machine is off: the four blocks are one battery-backed medium
(`cmos`, 52 nibbles, one per byte, block 0 first) that the host persists.
The emulator does not run the clock while the machine is off: at creation
the counters start from the time the host supplies (RtcTime) and overwrite
the time digits.

The time is a set of counters (a 16384 Hz fraction, seconds to years, the
weekday and the leap-year counter) advanced by emulated cycles only, so a run
is deterministic. A write to block 0 or 1 sets the counters from the digits,
which is how software sets the clock.

The chip is a 4-bit device: only the low nibble of each data byte is
meaningful, and the MSX floats the high nibble to 1s (reads return 0xF0 |
nibble).
"""
from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import TypedDict

from msx.boundary import RTC_CMOS_SIZE, RTC_DEFAULT_START, RtcTime
from msx.clock import CPU_CLOCK_HZ, CoreClock
from msx.snapshot_schema import (
    SchemaError,
    check_int,
    check_int_list,
    check_object,
)
from msx.sram import SramImage

MODE_REG = 13
TEST_REG = 14
RESET_REG = 15

MODE_TIMER_ENABLE = 0x08
MODE_BLOCK_SELECT = 0x03
REGS_PER_BLOCK = 13
# Blocks 0 and 1 hold the clock (time digits, alarm and settings); blocks 2
# and 3 are RAM.
_CLOCK_BLOCKS = 2

# Block 0 registers. Each value is a ones digit in this register and a tens
# digit in the next one; the weekday is one digit.
SECONDS_REG = 0
MINUTES_REG = 2
HOURS_REG = 4
DAY_WEEK_REG = 6
DAY_REG = 7
MONTH_REG = 9
YEAR_REG = 11

# Block 1 registers.
HOUR_MODE_REG = 10   # bit 0: 1 = 24-hour, 0 = 12-hour (see _write_time_regs)
LEAP_YEAR_REG = 11   # 0 = this year is a leap year
HOUR_MODE_24H = 0x1
_ALARM_FIRST_REG = 2
_ALARM_LAST_REG = 8

# The internal divider rate: the seconds counter carries once per TICK_HZ
# ticks.
TICK_HZ = 16384

# The most uncounted cycles a state holds: about 2.5 emulated years. A
# snapshot saves no more than this, so a longer run with no RTC access
# loses the excess.
# PORT-NOTE: _take_ticks multiplies the uncounted cycles by TICK_HZ (2^14).
#   With this bound (2^48) the product plus the tick remainder stays below
#   2^63, so it fits an i64 or u64. A native core counting more cycles than
#   this between two RTC accesses must clamp the same way.
_MAX_UNCOUNTED_CYCLES = 1 << 48

# TEST register bits: the counter advances once per tick instead of by carry.
TEST_SECONDS = 0x1
TEST_MINUTES = 0x2
TEST_DAYS = 0x4
TEST_YEARS = 0x8

# RESET register bits.
RESET_ALARM = 0x1      # clear block 1 registers 2-8
RESET_FRACTION = 0x2   # clear the divider

# Per-block write/read masks; 0-bits are ignored on write and read back as 0.
_MASK: tuple[tuple[int, ...], ...] = (
    (0xF, 0x7, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0xF, 0x1, 0xF, 0xF),  # block 0: time
    (0x0, 0x0, 0xF, 0x7, 0xF, 0x3, 0x7, 0xF, 0x3, 0x0, 0x1, 0x3, 0x0),  # block 1: alarm
    (0xF,) * 13,                                                        # block 2: RAM
    (0xF,) * 13,                                                        # block 3: RAM
)

_DAYS_IN_MONTH: tuple[int, ...] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class RtcState(TypedDict):
    """Save-state schema for RTC.snapshot()/apply_state(). `days` and
    `months` are 0-based and can be -1 (a day or month register set to 0).
    The cycles not yet counted are saved as a count, because the machine's
    cycle counter itself is not part of a snapshot."""

    fraction: int
    seconds: int
    minutes: int
    hours: int
    day_week: int
    days: int
    months: int
    years: int
    leap_year: int
    tick_remainder: int       # cycles * TICK_HZ below one tick, < CPU_CLOCK_HZ
    uncounted_cycles: int
    addr: int
    mode: int
    test: int
    regs: list[int]           # the 4 blocks, as stored (one byte each)


_RTC_STATE_KEYS: frozenset[str] = frozenset(RtcState.__annotations__)


@dataclass
class RTC:
    """RP-5C01 RTC: ports 0xB4 (register select) and 0xB5 (4-bit data)."""

    _addr: int = 0
    _mode: int = MODE_TIMER_ENABLE
    _test: int = 0
    # The persisted image of the 4 blocks, the initial bytes of `cmos`
    # (blank when not RTC_CMOS_SIZE bytes).
    sram: InitVar[bytes | bytearray | None] = None
    # Blocks 0-3, one nibble per byte: the battery-backed medium the host
    # persists. A write or restore that changes a byte marks it dirty.
    cmos: SramImage = field(init=False, repr=False)
    # The time the counters start from at creation, supplied by the host.
    start: RtcTime = RTC_DEFAULT_START
    # The emulated time (msx/clock.py); build_machine gives the machine's
    # record. A bare RTC reads its own record, which stays at cycle 0, so
    # it stays at its start time.
    _clock: CoreClock = field(default_factory=CoreClock, repr=False)
    # Cycles not yet counted are _base_age + (clock.cycle - _base_cycle). Two
    # fields, so a state with more uncounted cycles than the clock's count
    # needs no base below zero (an unsigned clock in a native core).
    _base_cycle: int = field(default=0, repr=False)
    _base_age: int = field(default=0, repr=False)
    # cycles * TICK_HZ left over below one tick at the last count.
    _tick_rem: int = field(default=0, repr=False)
    # Time counters. _days and _months are 0-based and can be -1.
    _fraction: int = field(default=0, repr=False)
    _seconds: int = field(default=0, repr=False)
    _minutes: int = field(default=0, repr=False)
    _hours: int = field(default=0, repr=False)
    _day_week: int = field(default=0, repr=False)
    _days: int = field(default=0, repr=False)
    _months: int = field(default=0, repr=False)
    _years: int = field(default=0, repr=False)
    _leap_year: int = field(default=0, repr=False)

    def __post_init__(self, sram: bytes | bytearray | None) -> None:
        self.cmos = SramImage.of(RTC_CMOS_SIZE, sram)
        self._start_counters()
        # The start digits replace the saved ones, which the next start
        # replaces again: that alone is no change for the host to save.
        self.cmos.mark_persisted()

    def _start_counters(self) -> None:
        """Set the counters from `start` and write them to the time digits."""
        # PORT-NOTE: `%` below is floor modulo; year - 1980 goes negative for
        #   a start before 1980.
        # Rust/C++/Zig equivalent: rem_euclid (Rust), a floor-adjusting
        #   helper (C++), @mod (Zig) -- plain `%` truncates there.
        # Kept as-is here because: semantic necessity -- the start year is
        #   any calendar year the host supplies, not only the chip's
        #   1980-2079.
        st = self.start
        self._fraction = 0
        self._seconds = st.second
        self._minutes = st.minute
        self._hours = st.hour
        # 1970-01-01 was a Thursday; Sunday = 0.
        self._day_week = (_days_from_civil(st.year, st.month, st.day) + 4) % 7
        self._days = st.day - 1
        self._months = st.month - 1
        self._years = (st.year - 1980) % 100
        self._leap_year = st.year % 4
        self._write_time_regs()

    def reset(self) -> None:
        """Machine reset: MODE to Timer-EN with block 0, TEST to 0, the
        register-select latch to 0. The time and the stored registers stay.
        RESET is write-only and acts at once, so it holds nothing to reset."""
        self._count_time()
        self._addr = 0
        self._mode = MODE_TIMER_ENABLE
        self._test = 0

    def snapshot(self) -> RtcState:
        """Capture the RTC for save-state (paired with apply_state)."""
        return {
            "fraction": self._fraction,
            "seconds": self._seconds,
            "minutes": self._minutes,
            "hours": self._hours,
            "day_week": self._day_week,
            "days": self._days,
            "months": self._months,
            "years": self._years,
            "leap_year": self._leap_year,
            "tick_remainder": self._tick_rem,
            "uncounted_cycles": min(self._uncounted_cycles(), _MAX_UNCOUNTED_CYCLES),
            "addr": self._addr,
            "mode": self._mode,
            "test": self._test,
            "regs": list(self.cmos.data()),
        }

    def parse_state(self, obj: object, label: str) -> RtcState:
        """Validate a snapshot() object without touching the chip. A counter
        set from the digits stays below 0x100 until the next count reduces
        it, so 0-0xFF (-1-0xFF for days and months) bounds every counter."""
        state = check_object(obj, label, _RTC_STATE_KEYS)
        return {
            "fraction": check_int(state["fraction"], f"{label}.fraction", 0, TICK_HZ - 1),
            "seconds": check_int(state["seconds"], f"{label}.seconds", 0, 0xFF),
            "minutes": check_int(state["minutes"], f"{label}.minutes", 0, 0xFF),
            "hours": check_int(state["hours"], f"{label}.hours", 0, 0xFF),
            "day_week": check_int(state["day_week"], f"{label}.day_week", 0, 0xFF),
            "days": check_int(state["days"], f"{label}.days", -1, 0xFF),
            "months": check_int(state["months"], f"{label}.months", -1, 0xFF),
            "years": check_int(state["years"], f"{label}.years", 0, 0xFF),
            "leap_year": check_int(state["leap_year"], f"{label}.leap_year", 0, 3),
            "tick_remainder": check_int(
                state["tick_remainder"], f"{label}.tick_remainder", 0, CPU_CLOCK_HZ - 1
            ),
            "uncounted_cycles": check_int(
                state["uncounted_cycles"], f"{label}.uncounted_cycles", 0,
                _MAX_UNCOUNTED_CYCLES,
            ),
            "addr": check_int(state["addr"], f"{label}.addr", 0, 0x0F),
            "mode": check_int(state["mode"], f"{label}.mode", 0, 0x0F),
            "test": check_int(state["test"], f"{label}.test", 0, 0x0F),
            "regs": check_int_list(state["regs"], f"{label}.regs", RTC_CMOS_SIZE, 0, 0xFF),
        }

    def apply_state(self, state: RtcState) -> None:
        """Assign an already-parsed RtcState (see parse_state)."""
        self._fraction = state["fraction"]
        self._seconds = state["seconds"]
        self._minutes = state["minutes"]
        self._hours = state["hours"]
        self._day_week = state["day_week"]
        self._days = state["days"]
        self._months = state["months"]
        self._years = state["years"]
        self._leap_year = state["leap_year"]
        self._tick_rem = state["tick_remainder"]
        self._base_cycle = self._clock.cycle
        self._base_age = state["uncounted_cycles"]
        self._addr = state["addr"]
        self._mode = state["mode"]
        self._test = state["test"]
        self.cmos.restore(bytes(state["regs"]))

    def _uncounted_cycles(self) -> int:
        return self._base_age + (self._clock.cycle - self._base_cycle)

    # Loading a saved image is done by passing it as the `sram=` constructor
    # argument; __post_init__ blanks a wrong-size image.

    def read_port(self, port: int) -> int:
        """Read the register-select port (0xB4, write-only) or data port (0xB5)."""
        if (port & 1) == 0:
            return 0xFF  # 0xB4 is write-only; high bits float to 1
        reg = self._addr
        if reg < MODE_REG and _is_clock_block(self._mode & MODE_BLOCK_SELECT):
            self._count_time()
        return 0xF0 | self._read_reg(reg)

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
        return self._read_masked(self._mode & MODE_BLOCK_SELECT, reg)

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == MODE_REG:
            self._count_time()
            self._mode = value
            return
        if reg == TEST_REG:
            self._count_time()
            self._test = value
            return
        if reg == RESET_REG:
            # Count first, so the divider clear drops the fraction of the
            # moment of the write.
            self._count_time()
            if value & RESET_ALARM:
                for alarm_reg in range(_ALARM_FIRST_REG, _ALARM_LAST_REG + 1):
                    self.cmos.write(REGS_PER_BLOCK + alarm_reg, 0)
            if value & RESET_FRACTION:
                self._fraction = 0
            return
        block = self._mode & MODE_BLOCK_SELECT
        if _is_clock_block(block):
            self._count_time()
        self.cmos.write(block * REGS_PER_BLOCK + reg, value & _MASK[block][reg])
        if _is_clock_block(block):
            self._read_time_regs()

    # -- time counting ------------------------------------------------------

    def _count_time(self) -> None:
        """Advance the counters by the ticks since the last count, carry them
        and write them to the time digits. A day carries into the month only
        when days are added, so a date that software sets digit by digit
        (02-31 on its way to 12-31) is kept."""
        ticks = self._take_ticks()
        self._carry(ticks)
        self._wrap_counters()
        self._write_time_regs()

    def _take_ticks(self) -> int:
        """The whole ticks in the cycles since the last count. The part
        below one tick stays for the next count."""
        total = self._uncounted_cycles() * TICK_HZ + self._tick_rem
        self._base_cycle = self._clock.cycle
        self._base_age = 0
        self._tick_rem = total % CPU_CLOCK_HZ
        return total // CPU_CLOCK_HZ

    def _carry(self, ticks: int) -> None:
        """Add the ticks and carry each counter into the next. A TEST bit
        makes its counter advance once per tick instead of by carry."""
        if self._mode & MODE_TIMER_ENABLE:
            self._fraction += ticks
        if self._test & TEST_SECONDS:
            self._seconds += ticks
        else:
            self._seconds += self._fraction // TICK_HZ
        if self._test & TEST_MINUTES:
            self._minutes += ticks
        else:
            self._minutes += self._seconds // 60
        self._hours += self._minutes // 60
        if self._test & TEST_DAYS:
            carry_days = ticks
        else:
            carry_days = self._hours // 24
        if carry_days > 0:
            self._days += carry_days
            self._day_week += carry_days
            month_len = _days_in_month(self._months, self._leap_year)
            while self._days >= month_len:
                self._days -= month_len
                self._months += 1
                month_len = _days_in_month(self._months, self._leap_year)
        if self._test & TEST_YEARS:
            carry_years = ticks
        elif self._months > 0:
            carry_years = self._months // 12
        else:
            carry_years = 0
        self._years += carry_years
        self._leap_year += carry_years

    def _wrap_counters(self) -> None:
        """Bring each counter back into its range after a carry."""
        self._fraction %= TICK_HZ
        self._seconds %= 60
        self._minutes %= 60
        self._hours %= 24
        self._day_week %= 7
        if self._months > 0:
            self._months %= 12  # a -1 month (register 0) stays -1
        self._years %= 100
        self._leap_year %= 4

    def _write_time_regs(self) -> None:
        """Write the counters to block 0's digits and block 1's leap-year
        register. In 12-hour mode (block 1 register 10 bit 0 clear) a PM
        hour 12-23 is written as 20-31."""
        hours = self._hours
        if not self._is_24h() and hours >= 12:
            hours = hours - 12 + 20
        day = self._days + 1
        month = self._months + 1
        digits = (
            self._seconds % 10, self._seconds // 10,
            self._minutes % 10, self._minutes // 10,
            hours % 10, hours // 10,
            self._day_week,
            day % 10, day // 10,
            month % 10, month // 10,
            self._years % 10, self._years // 10,
        )
        for reg in range(REGS_PER_BLOCK):
            self.cmos.write(reg, digits[reg])
        self.cmos.write(REGS_PER_BLOCK + LEAP_YEAR_REG, self._leap_year)

    def _read_time_regs(self) -> None:
        """Set the counters from block 0's digits and block 1's leap-year
        register (a write to block 0 or 1 sets the clock). In 12-hour mode
        an hour of 20 or more is a PM hour."""
        self._seconds = self._digit_pair(SECONDS_REG)
        self._minutes = self._digit_pair(MINUTES_REG)
        self._hours = self._digit_pair(HOURS_REG)
        self._day_week = self._read_masked(0, DAY_WEEK_REG)
        self._days = self._digit_pair(DAY_REG) - 1
        self._months = self._digit_pair(MONTH_REG) - 1
        self._years = self._digit_pair(YEAR_REG)
        self._leap_year = self._read_masked(1, LEAP_YEAR_REG)
        if not self._is_24h() and self._hours >= 20:
            self._hours = self._hours - 20 + 12

    def _digit_pair(self, reg: int) -> int:
        """The block 0 value with its ones digit in `reg` and its tens digit
        in `reg + 1`."""
        return self._read_masked(0, reg) + 10 * self._read_masked(0, reg + 1)

    def _is_24h(self) -> bool:
        """Block 1 register 10 bit 0: 1 = 24-hour mode, 0 = 12-hour mode."""
        return (self._read_masked(1, HOUR_MODE_REG) & HOUR_MODE_24H) != 0

    def _read_masked(self, block: int, reg: int) -> int:
        return self.cmos.read(block * REGS_PER_BLOCK + reg) & _MASK[block][reg]


def _is_clock_block(block: int) -> bool:
    """Blocks 0 and 1 hold the clock; an access to them counts the time."""
    return block < _CLOCK_BLOCKS


def _days_in_month(month: int, leap_year: int) -> int:
    """Days in the 0-based `month` (a -1 month counts as December); February
    has 29 when the leap-year counter is 0."""
    # PORT-NOTE: month -1 relies on Python's floor modulo (-1 % 12 == 11).
    #   C/C++/Rust `%` truncates (-1 % 12 == -1): use rem_euclid / @mod.
    index = month % 12
    if index == 1 and leap_year == 0:
        return 29
    return _DAYS_IN_MONTH[index]


def check_start(start: list[int], label: str) -> None:
    """Reject a start time that is not a real calendar date and time with a
    year of 0-9999 (the build checks it)."""
    year, month, day, hour, minute, second = start
    if not (0 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= 31
            and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise SchemaError(f"{label}: invalid date/time {start}")
    if _civil_from_days(_days_from_civil(year, month, day)) != (year, month, day):
        raise SchemaError(f"{label}: invalid date {year}-{month}-{day}")


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 for a Gregorian date (integer-only)."""
    # PORT-NOTE: `//` is floor division. The era term handles a negative y
    #   explicitly, and year >= 0 keeps y >= -1, so the other divisions see
    #   only non-negative operands and truncating division gives the same.
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
