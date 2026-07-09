from __future__ import annotations

import time
from dataclasses import dataclass, field

_SPIN_THRESHOLD = 0.001  # switch from sleep to spin 1 ms before deadline


@dataclass
class FrameTimer:
    fps: float = 60.0
    speed: float = 1.0
    _frame_interval: float = field(init=False, repr=False)
    _next_deadline: float = field(init=False, repr=False)
    _fps_frame_count: int = field(default=0, init=False, repr=False)
    _fps_last_time: float = field(init=False, repr=False)
    _fps_measured: float = field(default=0.0, init=False, repr=False)
    _last_tick_time: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._frame_interval = self._compute_interval()
        now = time.perf_counter()
        self._next_deadline = now + self._frame_interval
        self._fps_last_time = now
        self._last_tick_time = now

    def _compute_interval(self) -> float:
        # fps*speed <= 0 means "paused" (speed 0) or an invalid rate; fall back
        # to a safe 60 Hz interval instead of dividing by zero.
        rate = self.fps * self.speed
        if rate <= 0:
            return 1.0 / 60.0
        return 1.0 / rate

    def tick(self) -> float:
        """Block until the next frame deadline; return the measured frame time.

        The return value (and `_last_tick_time`) is the real elapsed seconds since
        the previous tick, for callers that display an actual frame time; it does
        not feed back into deadline scheduling.
        """
        # Bulk sleep down to _SPIN_THRESHOLD before deadline
        remaining = self._next_deadline - time.perf_counter()
        if remaining > _SPIN_THRESHOLD:
            time.sleep(remaining - _SPIN_THRESHOLD)
        # Spin for the final stretch (Rust port: use std::hint::spin_loop() here)
        while time.perf_counter() < self._next_deadline:
            pass

        now = time.perf_counter()
        elapsed = now - self._last_tick_time
        self._last_tick_time = now

        # Clamp to avoid debt accumulation
        self._next_deadline = now + self._frame_interval

        # FPS measurement
        self._fps_frame_count += 1
        fps_elapsed = now - self._fps_last_time
        if fps_elapsed >= 1.0:
            self._fps_measured = self._fps_frame_count / fps_elapsed
            self._fps_frame_count = 0
            self._fps_last_time = now

        return elapsed

    @property
    def fps_measured(self) -> float:
        return self._fps_measured
