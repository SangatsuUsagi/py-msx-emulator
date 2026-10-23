"""Value types that cross the core boundary (allium/core-frontend.allium).

Plain data only: the host and the frontend build these and hand them to the
core, or receive them from it. No module here reads files or the host clock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RtcTime:
    """A calendar date and time, supplied by the host once to start the RTC.

    The weekday is not stored: the RTC derives it from the date.
    """

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int


# Start time when the host supplies none (a Saturday).
RTC_DEFAULT_START = RtcTime(2000, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class RgbFrame:
    """One rendered frame as packed RGB24, row-major (width * height * 3 bytes).

    `width` is 256 or 512; `height` is the constant output height (212).
    """

    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class FrameOutput:
    """What Machine.step_frame returns for one frame."""

    video: RgbFrame | None   # absent when the frame was not rendered
    audio: bytes             # signed 16-bit LE mono PCM, before any output filter
    frame_number: int
