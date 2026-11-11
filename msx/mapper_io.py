"""S1985 MSX-ENGINE latch of the RAM mapper ports 0xFC-0xFF.

On a machine with the S1985 (machine-spec mapper_io "s1985"), the engine
latches each write to 0xFC-0xFF and drives a read of these ports itself,
with or without a RAM mapper: (latch & 0x1F) | 0x80. Verified on a real
SONY HB-F1XD (2026-11-11); openMSX models the same in its S1985 machines.
A write also goes to the machine's RAM mapper, if there is one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from msx.ram_mapper import RamMapper

S1985_READ_BACK_MASK = 0x1F
S1985_READ_BACK_BASE = 0x80


@dataclass
class S1985MapperLatch:
    """The four port latches (one per page, power-on 0) and the RAM mapper
    that receives every write (None on a machine without one)."""

    # A reference to the machine's RAM mapper, not owned here: the latch
    # only forwards port writes to it.
    ram_mapper: RamMapper | None = None
    latches: list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    def read_port(self, port: int) -> int:
        """The engine's read-back of the port's latch."""
        latch = self.latches[_page(port)]
        return (latch & S1985_READ_BACK_MASK) | S1985_READ_BACK_BASE

    def write_port(self, port: int, value: int) -> None:
        """Latch the value and pass the write on to the RAM mapper."""
        self.latches[_page(port)] = value & 0xFF
        if self.ram_mapper is not None:
            self.ram_mapper.write_port(port, value)

    def reset(self) -> None:
        """Clear the latches. The RAM mapper resets on its own."""
        self.latches[:] = [0, 0, 0, 0]

    def snapshot(self) -> list[int]:
        """The four latches, page 0 first."""
        return list(self.latches)

    def apply_state(self, latches: list[int]) -> None:
        """Assign four already-validated latches (see snapshot)."""
        self.latches[:] = latches


def _page(port: int) -> int:
    """The page (0-3) whose latch a port of 0xFC-0xFF addresses."""
    return (port - 0xFC) & 0x03
