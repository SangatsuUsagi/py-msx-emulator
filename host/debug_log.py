"""The --debug log text from the core's TRACE_DEBUG events
(diag-debug-logger).

The core records raw events (msx/boundary.py TRACE_EVENT_*); this module
keeps the state the log needs between them (the last VDP display and mode
bits, the hangs already reported, the instruction history of the next hang)
and turns them into `[CATEGORY]  message` lines.
"""
from __future__ import annotations

from host.tape import seconds
from msx.boundary import (
    TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_HANG_HALT_DI,
    TRACE_EVENT_HANG_PC_LOOP,
    TRACE_EVENT_IO_READ,
    TRACE_EVENT_IO_WRITE,
    TRACE_EVENT_TAPE_MOTOR,
    TRACE_EVENT_UNDEFINED_OP,
    TRACE_EVENT_VDP_MODE_REG,
    TraceEvent,
)

# Keys are the (M1, M2, M3) mode-bit tuple (TMS9918A R0/R1 screen-mode bits).
_VDP_MODE_NAMES = {
    (0, 0, 0): "G1 (Screen 1)",
    (0, 0, 1): "G2 (Screen 2)",
    (0, 1, 0): "MC (Screen 3)",
    (1, 0, 0): "Text (Screen 0)",
}

# HALT+DI hang keys are offset past the 16-bit PC range so they never collide
# with PC-loop keys in the shared set of reported hangs.
_HALT_DI_KEY_BASE = 0x10000

# The event kinds this module formats.
DEBUG_EVENT_KINDS = frozenset({
    TRACE_EVENT_IO_READ, TRACE_EVENT_IO_WRITE, TRACE_EVENT_VDP_MODE_REG,
    TRACE_EVENT_UNDEFINED_OP, TRACE_EVENT_CPU_HISTORY,
    TRACE_EVENT_HANG_PC_LOOP, TRACE_EVENT_HANG_HALT_DI, TRACE_EVENT_TAPE_MOTOR,
})


def _line(category: str, message: str) -> str:
    return f"[{category}]  {message}"


# The VDP register bits the debug log follows: R#1 BL (display enable), M1
# and M2, and R#0 M3 (the screen mode bits).
_R1_BL_BIT = 6
_R1_M1_BIT = 4
_R1_M2_BIT = 3
_R0_M3_BIT = 1


def _tape_motor_line(event: TraceEvent) -> str:
    state = "on" if event.a else "off"
    position = (event.b & 0xFFFFFFFF) | (event.c << 32)
    tape = "tape mounted" if event.d else "no tape"
    return _line(
        "IO", f"tape motor {state}  position={position} ({seconds(position):.3f} s)  {tape}",
    )


class DebugLogFormatter:
    """Debug log lines from TRACE_DEBUG events, one run's state."""

    def __init__(self) -> None:
        self._prev_bl = 0
        self._prev_mode = (0, 0, 0)
        self._reported_hangs: set[int] = set()
        # History lines of the next hang event.
        self._pending: list[str] = []

    def feed(self, event: TraceEvent) -> list[str]:
        """The lines for `event` (none for a history entry, which waits for
        its hang, or for a hang already reported)."""
        kind = event.kind
        if kind == TRACE_EVENT_IO_READ:
            return [_line("IO", f"IN   port={event.a:02X}  val={event.b:02X}  PC={event.pc:04X}")]
        if kind == TRACE_EVENT_IO_WRITE:
            return [_line("IO", f"OUT  port={event.a:02X}  val={event.b:02X}  PC={event.pc:04X}")]
        if kind == TRACE_EVENT_VDP_MODE_REG:
            return self._vdp_reg_write(event.a, event.b, event.frame)
        if kind == TRACE_EVENT_UNDEFINED_OP:
            return [_line("CPU", f"undefined opcode {event.b:02X}  PC={event.a:04X}")]
        if kind == TRACE_EVENT_CPU_HISTORY:
            self._pending.append(_line("CPU", f"trace  PC={event.a:04X}  op={event.b:02X}"))
            return []
        if kind == TRACE_EVENT_HANG_PC_LOOP:
            return self._hang(event.a, f"PC-loop detected  PC={event.a:04X}")
        if kind == TRACE_EVENT_HANG_HALT_DI:
            return self._hang(
                event.a | _HALT_DI_KEY_BASE,
                f"HALT with interrupts disabled  PC={event.a:04X}",
            )
        if kind == TRACE_EVENT_TAPE_MOTOR:
            return [_tape_motor_line(event)]
        return []

    def _hang(self, key: int, message: str) -> list[str]:
        history = self._pending
        self._pending = []
        if key in self._reported_hangs:
            return []
        self._reported_hangs.add(key)
        return history + [_line("HANG", message)]

    def _vdp_reg_write(self, reg: int, value: int, frame: int) -> list[str]:
        lines: list[str] = []
        if reg == 1:
            bl = (value >> _R1_BL_BIT) & 1
            m1 = (value >> _R1_M1_BIT) & 1
            m2 = (value >> _R1_M2_BIT) & 1
            if bl != self._prev_bl:
                state = "enabled" if bl else "disabled"
                message = f"VDP display {state}  R1=0x{value:02X}  frame={frame}"
                lines.append(_line("BOOT", message))
                self._prev_bl = bl
            # The mode uses R0.M3, which this write does not carry; track M1/M2 only.
            mode_key = (m1, m2, self._prev_mode[2])
            if (m1, m2) != (self._prev_mode[0], self._prev_mode[1]):
                name = _VDP_MODE_NAMES.get(mode_key, f"unknown (M1={m1} M2={m2})")
                lines.append(_line("BOOT", f"VDP mode → {name}  R1=0x{value:02X}"))
                self._prev_mode = mode_key
        elif reg == 0:
            m3 = (value >> _R0_M3_BIT) & 1
            mode_key = (self._prev_mode[0], self._prev_mode[1], m3)
            if m3 != self._prev_mode[2]:
                name = _VDP_MODE_NAMES.get(mode_key, f"unknown (M3={m3})")
                lines.append(_line("BOOT", f"VDP mode → {name}  R0=0x{value:02X}"))
                self._prev_mode = mode_key
        return lines
