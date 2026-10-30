"""The MSX CPU clock, shared by the chips that count time in its cycles.

CoreClock is the machine's position in emulated time. The Machine owns one,
and build_machine gives the same record to every component that reads or
advances these values. Each value has one writer: the machine loop adds to
`cycle`, the VDP to `frame`, and the CPU sets `instruction_pc`. A component
built alone keeps its own record, so it reads cycle 0 (machine-core "Core
components read the emulated time from one clock record").

A native core holds the record as a struct that the components point to:
`const struct msx_clock *` in C (the writer a non-const pointer),
`*const Clock` in Zig. No component needs a closure or the Machine type to
read the time.
"""
from __future__ import annotations

from dataclasses import dataclass

# Z80 T-states per second (3.579545 MHz). The SCC and the OPLL run from this
# clock; the RTC derives emulated seconds from the cycle count.
CPU_CLOCK_HZ = 3_579_545


@dataclass
class CoreClock:
    # T-states since power-on (Machine.cycle_count). The machine loop adds
    # to it once per scanline and at a stop, so a device reads the count at
    # the start of the current scanline's batch.
    #
    # PORT-NOTE: at 3.58 MHz a u32 wraps in ~20 minutes of emulated run time.
    # Rust/C++ equivalent: u64/uint64_t for this field; the per-frame totals
    #   threaded through Machine._run_lines() are frame-relative and fit in u32.
    # Kept as-is here because: semantic necessity, not performance -- Python's
    #   arbitrary-precision int never wraps, so this width requirement is
    #   invisible in Python and must be sized explicitly at port time.
    cycle: int = 0
    # Completed VDP frames (VdpDevice.frame_count).
    frame: int = 0
    # Address of the instruction in execution: the PC at its opcode fetch.
    instruction_pc: int = 0
