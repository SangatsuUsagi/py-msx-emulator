"""MSX cassette port: the tape input on PSG register 14 bit 7 and the motor on
PPI Port C bit 4 (io-cassette).

The host converts a CAS file into an edge list (host/cas_image.py): unsigned
32-bit little-endian intervals in T-states between level changes, the first
interval at level 1. The core plays it. The position is a T-state count into
the tape. It is computed from the cycle count when it is needed (at a read, a
motor change, a seek or a save), so the port does no work between those
events.

The input reads 1 in three cases: no tape, motor off, or past the end of the
tape. This matches openMSX's cassette port: the player gives sample 0, and the
port reads `sample >= 0`. A machine with no cassette port builds no
CassettePort, and PSG register 14 bit 7 then reads 0.
"""
from __future__ import annotations

import hashlib
import struct
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import TypedDict

from msx.boundary import TRACE_DEBUG, TRACE_EVENT_TAPE_MOTOR
from msx.clock import CoreClock
from msx.trace import TraceLog


class TapeIdentity(TypedDict):
    size: int
    sha1: str


class CassetteState(TypedDict):
    identity: TapeIdentity | None
    position: int
    motor: int


def _no_ends() -> array[int]:
    return array("q")


def edge_list_error(edges: bytes) -> str | None:
    """Why `edges` is not a valid edge list, or None when it is."""
    if len(edges) == 0:
        return "the tape edge list is empty"
    if len(edges) % 4 != 0:
        return f"the tape edge list has {len(edges)} bytes, not a multiple of 4"
    for (interval,) in struct.iter_unpack("<I", edges):
        if interval == 0:
            return "the tape edge list has an interval of 0"
    return None


def tape_identity(edges: bytes) -> TapeIdentity:
    """The size and the lowercase hex SHA-1 of an edge list."""
    return {"size": len(edges), "sha1": hashlib.sha1(edges).hexdigest()}


@dataclass
class CassettePort:
    """The tape deck behind a machine's cassette port."""

    clock: CoreClock
    # The end of each interval, in T-states from position 0 (prefix sums of
    # the edge list). Interval i has level 1 when i is even. A signed 64-bit
    # array: 8 bytes per edge, where a list of ints takes about 40. The sums
    # stay below 2^63; "q", not "Q", because PyPy reads "Q" items slowly.
    _ends: array[int] = field(default_factory=_no_ends, init=False, repr=False)
    _identity: TapeIdentity | None = field(default=None, init=False, repr=False)
    # The position at `_base_cycle`; while the motor runs the position grows
    # by one per T-state from there.
    _position: int = field(default=0, init=False, repr=False)
    _base_cycle: int = field(default=0, init=False, repr=False)
    _motor: bool = field(default=False, init=False, repr=False)
    # The interval of the last read; reads move it forward one step at a time.
    _cursor: int = field(default=0, init=False, repr=False)
    # The machine's trace buffer; a motor change records TRACE_EVENT_TAPE_MOTOR
    # while TRACE_DEBUG is on.
    _trace: TraceLog | None = field(default=None, init=False, repr=False)

    # --- the tape ---

    @property
    def mounted(self) -> bool:
        return self._identity is not None

    @property
    def motor(self) -> bool:
        return self._motor

    @property
    def length(self) -> int:
        return self._ends[-1] if self._ends else 0

    @property
    def identity(self) -> TapeIdentity | None:
        return self._identity

    def mount(self, edges: bytes) -> str | None:
        """Mount the edge list `edges` at position 0 (the port keeps its own
        prefix sums, not `edges`). Returns an error text and keeps the mounted
        tape when the list is not valid."""
        error = edge_list_error(edges)
        if error is not None:
            return error
        ends = _no_ends()
        total = 0
        for (interval,) in struct.iter_unpack("<I", edges):
            total += interval
            ends.append(total)
        self._ends = ends
        self._identity = tape_identity(edges)
        self._set_position(0)
        return None

    def eject(self) -> None:
        self._ends = _no_ends()
        self._identity = None
        self._set_position(0)

    def seek(self, position: int) -> bool:
        """Set the position; False (and no change) when it is outside the
        tape. The caller checks that a tape is mounted."""
        if position < 0 or position > self.length:
            return False
        self._set_position(position)
        return True

    def position(self) -> int:
        """The position at the current cycle."""
        if not self._motor:
            return self._position
        position = self._position + self.clock.cycle - self._base_cycle
        length = self.length
        return position if position < length else length

    def _set_position(self, position: int) -> None:
        self._position = position
        self._base_cycle = self.clock.cycle
        self._cursor = bisect_right(self._ends, position)

    # --- the hardware lines ---

    def set_motor(self, on: bool) -> bool:
        """Drive the motor line; returns whether the motor state changed."""
        if on == self._motor:
            return False
        self._position = self.position()
        self._base_cycle = self.clock.cycle
        self._motor = on
        trace = self._trace
        if trace is not None and trace.kinds & TRACE_DEBUG:
            # b is the low word as a signed int32: TraceEvent fields are
            # int32 in the C ABI. The host joins (b & 0xFFFFFFFF) | (c << 32).
            position = self._position
            low = position & 0xFFFFFFFF
            if low & 0x80000000:
                low -= 0x1_0000_0000
            trace.record(TRACE_EVENT_TAPE_MOTOR, 1 if on else 0,
                         low, position >> 32, 1 if self.mounted else 0)
        return True

    def read_level(self) -> int:
        """The tape input, 0 or 1, at the current cycle."""
        if not self._motor or not self._ends:
            return 1
        position = self.position()
        ends = self._ends
        if position >= ends[-1]:
            return 1  # past the end; this also keeps the walk below in range
        cursor = self._cursor
        # The position is before the last interval read. Not reached while
        # the motor runs (the position only grows, and seek() and
        # apply_state() reset the cursor); kept as a guard.
        if cursor > 0 and ends[cursor - 1] > position:
            cursor = bisect_right(ends, position)
        # Normal case: the position moved forward by a few intervals at most.
        while ends[cursor] <= position:
            cursor += 1
        self._cursor = cursor
        return 1 if cursor % 2 == 0 else 0

    # --- the saved state ---

    def snapshot_state(self) -> CassetteState:
        """The tape identity, the position at the current cycle and the motor."""
        return {"identity": self._identity, "position": self.position(),
                "motor": 1 if self._motor else 0}

    def apply_state(self, position: int, motor: int) -> None:
        """Restore the position and the motor of a snapshot_state() taken
        with the same tape mounted (the caller checks the identity), after
        the clock is restored. The position counts from the current cycle."""
        self._motor = motor == 1
        self._set_position(position)
