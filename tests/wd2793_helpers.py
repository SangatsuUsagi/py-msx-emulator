"""Clock helpers for WD2793 tests.

A Type I command takes step time on the controller's clock record
(fdc-wd2793 "Type I step timing"). A WD2793 built alone owns its record, so
a test moves `wd._clock.cycle` itself.
"""
from __future__ import annotations

from msx.clock import CPU_CLOCK_HZ
from msx.fdc.wd2793 import WD2793, Type1Phase

# A seek of 255 steps is the longest Type I command that ends by itself.
_MAX_EVENTS = 300


def ms(milliseconds: float) -> int:
    """CPU cycles in `milliseconds`."""
    return int(CPU_CLOCK_HZ * milliseconds / 1000)


def wait_ms(wd: WD2793, milliseconds: float) -> None:
    """Move the controller's clock on by `milliseconds`."""
    wd._clock.cycle += ms(milliseconds)


def finish_type1(wd: WD2793) -> None:
    """Move the controller's clock to the end of its running Type I command."""
    for _ in range(_MAX_EVENTS):
        if wd._phase is Type1Phase.NONE:
            return
        wd._clock.cycle = max(wd._clock.cycle, wd._event_cycle)
        wd.get_track()  # any register access runs the due events
    raise AssertionError("the Type I command did not end")
