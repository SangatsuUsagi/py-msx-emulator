"""Cartridge ROM mapper bank-switch tracer for py-msx-emulator.

Emits one timestamped MAP_BANK record per bank-register write that changes a
mapper window's selected bank. Parallel to the VDP register tracer
(`msx/vdp/tracer.py`); see extras/DEBUGGER_GUIDE.md for the command interface.

Record format:
    CY=XXXXXXXXXX FR=NNNNNN PC=XXXX MAP_BANK win=W OLDh->NEWh addr=XXXXh
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import IO


@dataclass
class MapperTracer:
    """Bank-switch tracer for cartridge ROM mappers.

    Disabled by default; set enabled=True to activate. Not thread-safe:
    single-threaded emulator use only.
    """

    enabled: bool = False
    output: IO[str] = field(default_factory=lambda: sys.stdout)

    def bank_change(
        self,
        window: int,
        old: int,
        new: int,
        addr: int,
        pc: int,
        cycle: int,
        frame: int = 0,
    ) -> None:
        """Emit a MAP_BANK record for a bank-register write that changed a bank.

        Args:
            window: Mapper window index whose bank changed (0-based).
            old: Previous bank index.
            new: New bank index.
            addr: CPU address the bank-register write targeted.
            pc: Program counter of the writing instruction.
            cycle: Cumulative T-state count.
            frame: VDP frame count (incremented each VBLANK).
        """
        if not self.enabled:
            return
        print(
            f"CY={cycle:010d} FR={frame:06d} PC={pc:04X} "
            f"MAP_BANK win={window} {old:02X}h->{new:02X}h addr={addr:04X}h",
            file=self.output,
        )
        self.output.flush()
