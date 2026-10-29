"""Text of the VDP trace and the mapper trace, from the core's trace events
(diag-vdp-tracer, diag-mapper-tracer).

The core records raw events (msx/boundary.py TraceEvent); this module turns
them into VDP Trace Log Format v1.2 lines (extras/vdp_trace_format.md) and
MAP_BANK lines, and writes them to the run's trace streams.
"""
from __future__ import annotations

import sys
from typing import IO, TYPE_CHECKING

from host.debug_log import DEBUG_EVENT_KINDS, DebugLogFormatter
from msx.boundary import (
    TRACE_EVENT_LOST,
    TRACE_EVENT_MAP_BANK,
    TRACE_EVENT_VDP_PORT9B,
    TRACE_EVENT_VDP_PORT99,
    TraceEvent,
)

if TYPE_CHECKING:
    from msx.core_api import CoreApi

# Command code (upper 4 bits of R#46) -> mnemonic.
_CMD_NAMES: dict[int, str] = {
    0x0: "ABRT", 0x1: "????", 0x2: "????", 0x3: "????",
    0x4: "POINT", 0x5: "PSET", 0x6: "SRCH", 0x7: "LINE",
    0x8: "LMMV", 0x9: "LMMM", 0xA: "LMCM", 0xB: "LMMC",
    0xC: "HMMV", 0xD: "HMMM", 0xE: "YMMM", 0xF: "HMMC",
}

# Logical operation (lower 4 bits of R#46) -> mnemonic.
_LOP_NAMES: list[str] = [
    "IMP", "AND", "OR", "XOR", "NOT", "????", "????", "????",
    "TIMP", "TAND", "TOR", "TXOR", "TNOT", "????", "????", "????",
]


class VdpTraceFormatter:
    """VDP_REG / VDP_CMD lines from VDP port events.

    Keeps the port 0x99 two-byte latch of the trace (it is not the VDP's own
    latch: a data-port access does not reset it) and the R#32-R#45 values
    written since the last R#46 write.
    """

    def __init__(self) -> None:
        # -1 = waiting for the first byte; >= 0 = first byte latched.
        self._latch = -1
        self._params: dict[int, int] = {}

    def feed(self, event: TraceEvent) -> str | None:
        """The line for `event`, or None when it completes no register
        write (a first byte, a VRAM address set-up, another kind)."""
        if event.kind == TRACE_EVENT_VDP_PORT99:
            if self._latch == -1:
                self._latch = event.a
                return None
            data = self._latch
            self._latch = -1
            if not event.a & 0x80:
                return None  # VRAM address set-up, not a register write
            return self._reg_write(event, event.a & 0x3F, data, "")
        if event.kind == TRACE_EVENT_VDP_PORT9B:
            return self._reg_write(event, event.b & 0x3F, event.a, ";port 9Bh")
        return None

    def _reg_write(self, event: TraceEvent, reg: int, data: int, via: str) -> str:
        suffix = f"  {via}" if via else ""
        prefix = f"CY={event.cycle:010d} FR={event.frame:06d} PC={event.pc:04X}"
        if reg == 46:
            name = _CMD_NAMES[(data >> 4) & 0xF]
            lop = _LOP_NAMES[data & 0xF]
            params = self._format_params()
            self._params.clear()
            return f"{prefix} VDP_CMD {name:<4s}/{lop:<4s} ({data:02X}h) R32-45={params}{suffix}"
        if 32 <= reg <= 45:
            self._params[reg] = data
        return f"{prefix} VDP_REG R#{reg:02d}={data:02X}h{suffix}"

    def _format_params(self) -> str:
        """R#32-R#45 as ['XX',...], with '--' for a register not written."""
        parts = []
        for reg in range(32, 46):
            value = self._params.get(reg)
            parts.append(f"'{value:02X}'" if value is not None else "'--'")
        return "[" + ",".join(parts) + "]"


def map_bank_line(event: TraceEvent) -> str:
    """The MAP_BANK line of a bank-change event."""
    return (
        f"CY={event.cycle:010d} FR={event.frame:06d} PC={event.pc:04X} "
        f"MAP_BANK win={event.a} {event.b:02X}h->{event.c:02X}h addr={event.d:04X}h"
    )


class TraceOutput:
    """Where one run's trace lines go: a stream and a name (for the debugger
    messages) per trace, None until the trace is first turned on. The debug
    log (--debug) goes to stderr and, with --log, to a file too."""

    def __init__(self) -> None:
        self.vdp_out: IO[str] | None = None
        self.vdp_name = ""
        self.map_out: IO[str] | None = None
        self.map_name = ""
        self._vdp = VdpTraceFormatter()
        self._debug: DebugLogFormatter | None = None
        self._debug_file: IO[str] | None = None

    def set_debug_output(self, file: IO[str] | None) -> None:
        """Write the debug log from now on: to stderr, and to `file` when
        given (the --log file, which the caller opens and closes)."""
        self._debug = DebugLogFormatter()
        self._debug_file = file

    def set_vdp_output(self, stream: IO[str], name: str) -> None:
        self.vdp_out = stream
        self.vdp_name = name

    def set_map_output(self, stream: IO[str], name: str) -> None:
        self.map_out = stream
        self.map_name = name

    def drain(self, core: CoreApi) -> None:
        """Read the core's trace buffer and write each line to its stream;
        report lost events on stderr."""
        events = core.drain_trace()
        if not events:
            return
        for event in events:
            if event.kind == TRACE_EVENT_MAP_BANK:
                if self.map_out is not None:
                    print(map_bank_line(event), file=self.map_out)
            elif event.kind == TRACE_EVENT_LOST:
                print(f"trace: {event.a} events lost", file=sys.stderr)
            elif event.kind in DEBUG_EVENT_KINDS:
                if self._debug is not None:
                    self._write_debug(self._debug.feed(event))
            else:
                line = self._vdp.feed(event)
                if line is not None and self.vdp_out is not None:
                    print(line, file=self.vdp_out)
        for stream in (self.vdp_out, self.map_out, self._debug_file):
            if stream is not None:
                stream.flush()

    def _write_debug(self, lines: list[str]) -> None:
        for line in lines:
            sys.stderr.write(line + "\n")
            if self._debug_file is not None:
                self._debug_file.write(line + "\n")
