"""The core's trace buffer (machine-core "The core API records trace
events"): the VDP, the slot 1/2 ROM mappers, the CPU, the I/O bus and the
frame loop record events here while their trace kind is on, and the host
reads them with Machine.drain_trace().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from msx.boundary import (
    TRACE_CAPACITY,
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_LOST,
    TRACE_HISTORY_SIZE,
    TraceEvent,
)


@dataclass
class TraceLog:
    """The enabled trace kinds, the recorded events and the lost count.

    `sources` is the set of kinds this machine can produce. The getters
    give the CPU's instruction PC, the cycle count and the VDP's frame
    number; msx/machine_loader.py wires them at build time. While
    TRACE_DEBUG is on, the CPU also fills the instruction history: the
    last TRACE_HISTORY_SIZE (pc, opcode) pairs, a ring of fixed lists.
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
    # PORT-NOTE: closures over the machine, wired at build time like
    #   IOBus._get_pc; they stay inside the core.
    # Rust/C++ equivalent: pass the PC, cycle and frame into record(), or
    #   hold a narrow reference to a shared clock struct.
    # Kept as-is here because: the recording sites (VDP port writes, mapper
    #   bank writes) do not have the CPU or the cycle count at hand.
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, repr=False)

    def record(self, kind: int, a: int = 0, b: int = 0, c: int = 0, d: int = 0) -> None:
        """Append one event, or count it as lost when the buffer is full."""
        if len(self.events) >= TRACE_CAPACITY:
            self.lost += 1
            return
        get_pc = self._get_pc
        get_cycle = self._get_cycle
        get_frame = self._get_frame
        self.events.append(TraceEvent(
            kind,
            get_cycle() if get_cycle is not None else 0,
            get_frame() if get_frame is not None else 0,
            get_pc() if get_pc is not None else 0,
            a, b, c, d,
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
