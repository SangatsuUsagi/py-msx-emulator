from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass, field
from typing import TextIO

# Keys are the (M1, M2, M3) mode-bit tuple (TMS9918A R0/R1 screen-mode bits).
_VDP_MODE_NAMES = {
    (0, 0, 0): "G1 (Screen 1)",
    (0, 0, 1): "G2 (Screen 2)",
    (0, 1, 0): "MC (Screen 3)",
    (1, 0, 0): "Text (Screen 0)",
}

# HALT+DI hang keys are offset past the 16-bit PC range so they never collide
# with PC-loop keys in the shared _reported_hangs set.
_HALT_DI_KEY_BASE = 0x10000


@dataclass
class DebugLogger:
    log_path: str | None = None
    _file: TextIO | None = field(default=None, init=False, repr=False)
    trace_buffer: deque[tuple[int, int]] = field(
        default_factory=lambda: deque(maxlen=64), init=False, repr=False
    )
    _reported_hangs: set[int] = field(default_factory=set, init=False, repr=False)
    # VDP state tracking for milestone detection
    _prev_bl: int = field(default=0, init=False, repr=False)
    _prev_mode: tuple[int, int, int] = field(default=(0, 0, 0), init=False, repr=False)

    def __post_init__(self) -> None:
        if self.log_path is not None:
            self._file = open(self.log_path, "a", encoding="utf-8")

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _emit(self, category: str, msg: str) -> None:
        line = f"[{category}]  {msg}\n"
        sys.stderr.write(line)
        if self._file is not None:
            self._file.write(line)
            self._file.flush()

    # ------------------------------------------------------------------
    # Boot diagnostic events
    # ------------------------------------------------------------------

    def on_slot_register_write(self, old: int, new: int, pc: int) -> None:
        self._emit("BOOT", f"slot_register: 0x{old:02X} → 0x{new:02X}  (PC={pc:04X})")

    def on_vdp_reg_write(self, reg: int, value: int, frame: int) -> None:
        if reg == 1:
            bl = (value >> 6) & 1
            m1 = (value >> 4) & 1
            m2 = (value >> 3) & 1
            if bl != self._prev_bl:
                state = "enabled" if bl else "disabled"
                self._emit("BOOT", f"VDP display {state}  R1=0x{value:02X}  frame={frame}")
                self._prev_bl = bl
            # mode uses R0.M3 which we don't have here; track M1/M2 only
            mode_key = (m1, m2, self._prev_mode[2])
            if (m1, m2) != (self._prev_mode[0], self._prev_mode[1]):
                name = _VDP_MODE_NAMES.get(mode_key, f"unknown (M1={m1} M2={m2})")
                self._emit("BOOT", f"VDP mode → {name}  R1=0x{value:02X}")
                self._prev_mode = mode_key
        if reg == 0:
            m3 = (value >> 1) & 1
            mode_key = (self._prev_mode[0], self._prev_mode[1], m3)
            if m3 != self._prev_mode[2]:
                name = _VDP_MODE_NAMES.get(mode_key, f"unknown (M3={m3})")
                self._emit("BOOT", f"VDP mode → {name}  R0=0x{value:02X}")
                self._prev_mode = mode_key

    def on_undefined_opcode(self, pc: int, opcode: int) -> None:
        self._emit("CPU", f"undefined opcode {opcode:02X}  PC={pc:04X}")

    # ------------------------------------------------------------------
    # CPU trace buffer and hang events
    # ------------------------------------------------------------------

    def on_step(self, pc: int, opcode: int) -> None:
        self.trace_buffer.append((pc, opcode))

    def dump_trace(self) -> None:
        for pc, op in self.trace_buffer:
            self._emit("CPU", f"trace  PC={pc:04X}  op={op:02X}")

    def on_hang_pc_loop(self, pc: int) -> None:
        if pc in self._reported_hangs:
            return
        self._reported_hangs.add(pc)
        self.dump_trace()
        self._emit("HANG", f"PC-loop detected  PC={pc:04X}")

    def on_hang_halt_di(self, pc: int) -> None:
        key = pc | _HALT_DI_KEY_BASE
        if key in self._reported_hangs:
            return
        self._reported_hangs.add(key)
        self.dump_trace()
        self._emit("HANG", f"HALT with interrupts disabled  PC={pc:04X}")

    # ------------------------------------------------------------------
    # I/O trace events
    # ------------------------------------------------------------------

    def on_io_read(self, port: int, value: int, pc: int) -> None:
        self._emit("IO", f"IN   port={port:02X}  val={value:02X}  PC={pc:04X}")

    def on_io_write(self, port: int, value: int, pc: int) -> None:
        self._emit("IO", f"OUT  port={port:02X}  val={value:02X}  PC={pc:04X}")
