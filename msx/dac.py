"""Write-driven mono DAC channel shared by the Majutsushi cartridge DAC and the
PPI key click.

Owners convert their raw output (a cartridge byte, a Port C bit) to a signed
16-bit level and write it with the CPU cycle of the write; generate_samples()
places each level change at its sub-frame sample position (zero-order hold).
Same split as openMSX's DACSound8U (format conversion, done by the owner here)
over DACSound16S (the level stream, this class), including DACSound16S's rule
that a write with no level change is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Upper bound on recorded level changes per audio buffer, the same bound as the
# PSG's write buffer; caps memory if generate_samples is never called (headless).
EVENT_DAC_MAX_EVENTS: int = 4096


@dataclass
class EventDac:
    # Current output level; follows every write, even one the bound drops.
    _level: int = field(default=0, repr=False)
    # Level at the start of the next rendered frame.
    _held: int = field(default=0, repr=False)
    # Level changes as (cycle, level), consumed by generate_samples.
    _events: list[tuple[int, int]] = field(default_factory=list, repr=False)

    def write(self, cycle: int, level: int) -> None:
        if level == self._level:
            return
        self._level = level
        if len(self._events) < EVENT_DAC_MAX_EVENTS:
            self._events.append((cycle, level))

    def generate_samples(
        self, n: int, frame_start: int = 0, frame_end: int = 0
    ) -> bytearray | None:
        """Return n signed 16-bit LE mono PCM samples, or None for a silent
        frame (no level change and a held level of 0).

        frame_start / frame_end: machine.cycle_count at the frame boundaries.
        """
        events = self._events
        value = self._held
        if not events and value == 0:
            return None
        self._events = []

        cycles = frame_end - frame_start if frame_end > frame_start else 1
        out = bytearray(n * 2)
        ev_idx = 0
        for i in range(n):
            threshold = frame_start + i * cycles // n
            while ev_idx < len(events) and events[ev_idx][0] <= threshold:
                value = events[ev_idx][1]
                ev_idx += 1
            out[i * 2] = value & 0xFF
            out[i * 2 + 1] = (value >> 8) & 0xFF

        # The bound may have dropped changes, so the true level is _level, not
        # the last recorded event.
        self._held = self._level
        return out

    def reset(self, level: int) -> None:
        """Drop pending changes and hold `level` without recording an event."""
        self._events = []
        self._level = level
        self._held = level
