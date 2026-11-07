"""MSX cassette port: the tape input on PSG register 14 bit 7 and the motor on
PPI Port C bit 4 (io-cassette).

The host converts a CAS file into an edge list (host/cas_image.py): unsigned
32-bit little-endian intervals in T-states between level changes, the first
interval at level 1. The core plays it. The position is a T-state count into
the tape. It is computed from the cycle count when it is needed (at a read, a
motor change, a seek or a save), so the port does no work between those
events.

The levels match openMSX's cassette port: with no tape, with the motor off and
past the end of the tape the input reads 1 (the player gives sample 0, and
the port reads `sample >= 0`). A machine with no port reads bit 7 as 0, and
the machine then builds no CassettePort at all.
"""
from __future__ import annotations

import hashlib
import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import TypedDict

from msx.clock import CoreClock


class TapeIdentity(TypedDict):
    size: int
    sha1: str


class CassetteState(TypedDict):
    identity: TapeIdentity | None
    position: int
    motor: int


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
    # the edge list). Interval i has level 1 when i is even.
    _ends: list[int] = field(default_factory=list, init=False, repr=False)
    _identity: TapeIdentity | None = field(default=None, init=False, repr=False)
    # The position at `_base_cycle`; while the motor runs the position grows
    # by one per T-state from there.
    _position: int = field(default=0, init=False, repr=False)
    _base_cycle: int = field(default=0, init=False, repr=False)
    _motor: bool = field(default=False, init=False, repr=False)
    # The interval of the last read; reads move it forward one step at a time.
    _cursor: int = field(default=0, init=False, repr=False)

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
        """Mount a copy of `edges` at position 0. Returns an error text and
        keeps the mounted tape when the list is not valid."""
        error = edge_list_error(edges)
        if error is not None:
            return error
        ends: list[int] = []
        total = 0
        for (interval,) in struct.iter_unpack("<I", edges):
            total += interval
            ends.append(total)
        self._ends = ends
        self._identity = tape_identity(bytes(edges))
        self._set_position(0)
        return None

    def eject(self) -> None:
        self._ends = []
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
        return True

    def read_level(self) -> int:
        """The tape input, 0 or 1, at the current cycle."""
        if not self._motor or not self._ends:
            return 1
        position = self.position()
        ends = self._ends
        if position >= ends[-1]:
            return 1
        cursor = self._cursor
        if cursor > 0 and ends[cursor - 1] > position:
            cursor = bisect_right(ends, position)
        while ends[cursor] <= position:
            cursor += 1
        self._cursor = cursor
        return 1 if cursor % 2 == 0 else 0

    # --- the saved state ---

    def snapshot_state(self) -> CassetteState:
        """The tape identity, the position at the current cycle and the motor."""
        return {"identity": self._identity, "position": self.position(),
                "motor": 1 if self._motor else 0}

    def apply_state(self, state: CassetteState) -> None:
        """Restore a snapshot_state() taken with the same tape mounted, after
        the clock is restored. The position counts from the current cycle."""
        self._motor = state["motor"] == 1
        self._set_position(state["position"])
