"""RP-5C01 Real-Time Clock stub for MSX2.

Ports 0xB4 (register address) and 0xB5 (data). All reads return 0x00;
writes are accepted silently. This satisfies BIOS initialisation without
implementing real-time accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RTC:
    """RTC stub: responds to ports 0xB4/0xB5, returns zero on all reads."""

    _reg: int = 0  # address latch — accepted but ignored

    def read_port(self, port: int) -> int:
        """Return 0x00 for any RTC read (port 0xB4 or 0xB5)."""
        return 0x00

    def write_port(self, port: int, value: int) -> None:
        """Accept writes silently (port 0xB4 sets address latch, 0xB5 is data)."""
        if port == 0xB4:
            self._reg = value & 0x0F
