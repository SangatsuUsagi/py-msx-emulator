"""The core's trace buffer (machine-core "The core API records trace
events"): the VDP, the slot 1/2 ROM mappers, the CPU, the I/O bus and the
frame loop record events here while their trace kind is on, and the host
reads them with Machine.drain_trace().
"""
from __future__ import annotations

from dataclasses import dataclass, field

from msx.boundary import (
    TRACE_CAPACITY,
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_LOST,
    TRACE_HISTORY_SIZE,
    TRACE_LOST_MAX,
    TraceEvent,
)
from msx.clock import CoreClock


@dataclass
class TraceLog:
    """The enabled trace kinds, the recorded events and the lost count.

    `sources` is the set of kinds this machine can produce. Each event is
    stamped with the clock record's cycle count, frame number and
    instruction address; msx/machine_loader.py gives the machine's record
    at build time. While
    TRACE_DEBUG is on, the CPU also fills the instruction history: the
    last TRACE_HISTORY_SIZE (pc, opcode) pairs, a ring of fixed lists.

    PORT-NOTE: `events` grows to TRACE_CAPACITY, then `record` counts the
    rest as lost. A native core keeps a fixed array of TRACE_CAPACITY + 1
    records (the extra one for the TRACE_EVENT_LOST record of `drain`) and
    a count. `record`'s default arguments become four explicit arguments.
    """

    kinds: int = 0
    sources: int = 0
    events: list[TraceEvent] = field(default_factory=list, repr=False)
    lost: int = 0
    _history_pc: list[int] = field(
        default_factory=lambda: [0] * TRACE_HISTORY_SIZE, repr=False)
    _history_op: list[int] = field(
        default_factory=lambda: [0] * TRACE_HISTORY_SIZE, repr=False)
    _history_len: int = field(default=0, repr=False)
    _history_next: int = field(default=0, repr=False)
    # The emulated time (msx/clock.py) that stamps each event;
    # build_machine gives the machine's record.
    _clock: CoreClock = field(default_factory=CoreClock, repr=False)

    def record(self, kind: int, a: int = 0, b: int = 0, c: int = 0, d: int = 0) -> None:
        """Append one event, or count it as lost when the buffer is full."""
        if len(self.events) >= TRACE_CAPACITY:
            if self.lost < TRACE_LOST_MAX:
                self.lost += 1
            return
        clock = self._clock
        self.events.append(TraceEvent(
            kind, clock.cycle, clock.frame, clock.instruction_pc, a, b, c, d,
        ))

    def push_history(self, pc: int, opcode: int) -> None:
        """Keep one fetched instruction in the history, dropping the oldest
        when it is full."""
        i = self._history_next
        self._history_pc[i] = pc
        self._history_op[i] = opcode
        self._history_next = (i + 1) % TRACE_HISTORY_SIZE
        if self._history_len < TRACE_HISTORY_SIZE:
            self._history_len += 1

    def clear_history(self) -> None:
        """Forget the instruction history (set_trace_kinds turning
        TRACE_DEBUG on)."""
        self._history_len = 0
        self._history_next = 0

    def record_hang(self, kind: int, pc: int) -> None:
        """Record the history entries, oldest first, then the hang event
        `kind` (TRACE_EVENT_HANG_*) with `pc`."""
        start = (self._history_next - self._history_len) % TRACE_HISTORY_SIZE
        for n in range(self._history_len):
            i = (start + n) % TRACE_HISTORY_SIZE
            self.record(TRACE_EVENT_CPU_HISTORY, self._history_pc[i], self._history_op[i])
        self.record(kind, pc)

    def drain(self) -> tuple[TraceEvent, ...]:
        """The recorded events, then one TRACE_EVENT_LOST record when events
        were lost; empties the buffer and resets the lost count."""
        events = self.events
        if self.lost:
            events.append(TraceEvent(TRACE_EVENT_LOST, 0, 0, 0, self.lost))
            self.lost = 0
        self.events = []
        return tuple(events)
