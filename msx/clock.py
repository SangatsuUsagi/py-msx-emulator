"""The MSX CPU clock, shared by the chips that count time in its cycles."""
from __future__ import annotations

# Z80 T-states per second (3.579545 MHz). The SCC and the OPLL run from this
# clock; the RTC derives emulated seconds from the cycle count.
CPU_CLOCK_HZ = 3_579_545
