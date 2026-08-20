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
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from msx.machine import Machine


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


def attach_to_machine(
    machine: "Machine", *, output: IO[str] | None = None
) -> "MapperTracer | None":
    """Attach an enabled MapperTracer to the cartridge ROM mapper(s) in slots 1/2.

    Wires the PC/cycle/frame accessors the same way the `ce` debugger command
    does. Returns the tracer, or None when no bank-switching ROM mapper is
    present (flat mapper or empty slot), so callers can report the inert case.
    """
    # PORT-NOTE: this attaches by reflection — `getattr`/`hasattr` probing for
    #   `_tracer` and injecting `_get_pc`/`_get_cycle`/`_get_frame` closures at
    #   runtime (the `BankTracingMapper` base already gives every mapper the hook
    #   fields statically, so this reflection is only for the optional-attach
    #   dance, not because the fields' shape is unknown).
    # Rust equivalent: a `SupportsTracing` trait implemented by every mapper
    #   (matching `BankTracingMapper`'s hook fields), with a typed accessor object
    #   injected via a setter method, not closures assigned by attribute name.
    # C++ equivalent: same — a `SupportsTracing` interface/mixin every mapper
    #   implements, with a typed accessor object passed to an explicit
    #   `attach_tracer()` method, not runtime attribute injection.
    # Kept as-is here because: port target/shape not decided yet — called
    #   once per debugger-session attach (not a hot path), and Rust/C++ have
    #   no equivalent of Python's runtime getattr/hasattr probing, so this
    #   needs a real interface at port time, not a mechanical translation.
    mem = machine.memory
    targets = []
    for attr in ("_mapper", "_mapper2"):
        mp = getattr(mem, attr, None)
        if mp is not None and hasattr(mp, "_tracer"):
            targets.append(mp)
    if not targets:
        return None
    tracer = MapperTracer(enabled=True, output=output or sys.stdout)
    for mp in targets:
        mp._tracer = tracer
        if mp._get_pc is None:
            mp._get_pc = lambda: machine.cpu.instruction_pc
            mp._get_cycle = lambda: machine.cycle_count
            mp._get_frame = lambda: machine.vdp._frame_count
    return tracer
