"""Text of the VDP trace and the mapper trace, from the core's trace events
(diag-vdp-tracer, diag-mapper-tracer).

The core records raw events (msx/boundary.py TraceEvent); this module turns
them into VDP Trace Log Format v1.2 lines (openspec/specs/diag-vdp-tracer/spec.md) and
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

# The V9938 command codes (upper 4 bits of R#46) that name a command; the
# trace and the debugger's `v` share it and each spell an undefined code
# their own way.
VDP_COMMAND_NAMES: dict[int, str] = {
    0x0: "ABRT", 0x4: "POINT", 0x5: "PSET", 0x6: "SRCH",
    0x7: "LINE", 0x8: "LMMV", 0x9: "LMMM", 0xA: "LMCM",
    0xB: "LMMC", 0xC: "HMMV", 0xD: "HMMM", 0xE: "YMMM", 0xF: "HMMC",
}

# The logical operations (bits 0-2 of R#46); bit 3 selects the transparent
# form (TIMP ...).
VDP_LOGICAL_OP_NAMES: dict[int, str] = {
    0: "IMP", 1: "AND", 2: "OR", 3: "XOR", 4: "NOT",
}

# R#46 bit 3: the logical operation skips source colour 0.
_LOP_TRANSPARENT = 0x08

# R#46 starts a command; R#32-R#45 are its parameters.
_CMD_REG = 46
_CMD_PARAM_FIRST = 32
_CMD_PARAM_LAST = 45


def _trace_lop_name(lop: int) -> str:
    """The trace's text of R#46 bits 0-3: TIMP for a transparent IMP, ????
    for an undefined operation."""
    name = VDP_LOGICAL_OP_NAMES.get(lop & 0x07)
    if name is None:
        return "????"
    if lop & _LOP_TRANSPARENT:
        return "T" + name
    return name


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
        if reg == _CMD_REG:
            name = VDP_COMMAND_NAMES.get((data >> 4) & 0xF, "????")
            lop = _trace_lop_name(data & 0xF)
            params = self._format_params()
            self._params.clear()
            return f"{prefix} VDP_CMD {name:<4s}/{lop:<4s} ({data:02X}h) R32-45={params}{suffix}"
        if _CMD_PARAM_FIRST <= reg <= _CMD_PARAM_LAST:
            self._params[reg] = data
        return f"{prefix} VDP_REG R#{reg:02d}={data:02X}h{suffix}"

    def _format_params(self) -> str:
        """R#32-R#45 as ['XX',...], with '--' for a register not written."""
        parts = []
        for reg in range(_CMD_PARAM_FIRST, _CMD_PARAM_LAST + 1):
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
        """Write VDP trace lines to `stream`, called `name` in messages."""
        self.vdp_out = stream
        self.vdp_name = name

    def set_map_output(self, stream: IO[str], name: str) -> None:
        """Write mapper trace lines to `stream`, called `name` in messages."""
        self.map_out = stream
        self.map_name = name

    def drain(self, core: CoreApi) -> None:
        """Read the core's trace buffer and write each line to its stream;
        report lost events on stderr."""
        events = core.drain_trace()
        if not events:
            return
        # The stderr lines (debug log, lost events) and the --log lines of
        # this drain, written once each at the end: stderr is line-buffered,
        # so a write per line is a system call per line.
        err_lines: list[str] = []
        log_lines: list[str] = []
        for event in events:
            if event.kind == TRACE_EVENT_MAP_BANK:
                if self.map_out is not None:
                    print(map_bank_line(event), file=self.map_out)
            elif event.kind == TRACE_EVENT_LOST:
                err_lines.append(f"trace: {event.a} events lost")
            elif event.kind in DEBUG_EVENT_KINDS:
                if self._debug is not None:
                    lines = self._debug.feed(event)
                    err_lines.extend(lines)
                    log_lines.extend(lines)
            else:
                # The remaining kinds are the VDP port events.
                line = self._vdp.feed(event)
                if line is not None and self.vdp_out is not None:
                    print(line, file=self.vdp_out)
        if err_lines:
            sys.stderr.write("\n".join(err_lines) + "\n")
        if log_lines and self._debug_file is not None:
            self._debug_file.write("\n".join(log_lines) + "\n")
        for stream in (self.vdp_out, self.map_out, self._debug_file):
            if stream is not None:
                stream.flush()
