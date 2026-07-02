"""VDP register write tracer for py-msx-emulator.

Produces log lines in the canonical VDP Trace Log Format v1.0.
See extras/vdp_trace_format.md for the full format specification.
The OpenMSX counterpart (extras/vdp_trace.tcl) emits the same format.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import IO

# Command code (upper 4 bits of R#46) → mnemonic
_CMD_NAMES: dict[int, str] = {
    0x0: "ABRT",
    0x1: "????",
    0x2: "????",
    0x3: "????",
    0x4: "POINT",
    0x5: "PSET",
    0x6: "SRCH",
    0x7: "LINE",
    0x8: "LMMV",
    0x9: "LMMM",
    0xA: "LMCM",
    0xB: "LMMC",
    0xC: "HMMV",
    0xD: "HMMM",
    0xE: "YMMM",
    0xF: "HMMC",
}


def _cmd_name(val: int) -> str:
    return _CMD_NAMES.get((val >> 4) & 0xF, "????")


# Logical operation (lower 4 bits of R#46) → mnemonic
_LOP_NAMES: list[str] = [
    "IMP", "AND", "OR", "XOR", "NOT", "????", "????", "????",
    "TIMP", "TAND", "TOR", "TXOR", "TNOT", "????", "????", "????",
]


def _lop_name(val: int) -> str:
    return _LOP_NAMES[val & 0xF]


@dataclass
class Tracer:
    """VDP register write tracer.

    Hooks into V9938.write_port() at ports 0x99 and 0x9B to emit
    VDP_REG / VDP_CMD records in the canonical VDP Trace Log Format.

    Disabled by default; set enabled=True or pass output to activate.
    Not thread-safe: single-threaded emulator use only.
    """

    enabled: bool = False
    output: IO[str] = field(default_factory=lambda: sys.stdout)

    # Two-byte write protocol state for port 0x99.
    # -1 = waiting for first byte; >=0 = first byte latched.
    _latch: int = field(default=-1, init=False, repr=False)

    # Buffered command parameter registers R#32–R#45.
    # None = not yet written in the current command sequence.
    # Cleared after R#46 is written (VDP_CMD emitted).
    _param_buf: dict[int, int] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------------
    # Public hooks

    def port99_write(self, pc: int, cycle: int, val: int, frame: int = 0) -> None:
        """Hook for OUT (0x99), A — V9938 two-byte register-write protocol.

        Args:
            pc: Program counter at the time of the write.
            cycle: Cumulative T-state count.
            val: Byte written to port 0x99.
            frame: VDP frame count (incremented each VBLANK).
        """
        if not self.enabled:
            return

        val &= 0xFF

        if self._latch == -1:
            self._latch = val
            return

        data = self._latch
        self._latch = -1

        if val & 0x80:
            reg = val & 0x3F
            self._handle_reg_write(pc, cycle, frame, reg, data, via="")
        # else: VRAM address set — not a register write, ignore

    def port9b_write(self, pc: int, cycle: int, val: int, r17: int, frame: int = 0) -> None:
        """Hook for OUT (0x9B), A — V9938 indirect register write.

        Args:
            pc: Program counter at the time of the write.
            cycle: Cumulative T-state count.
            val: Byte written to port 0x9B.
            r17: Value of VDP R#17 BEFORE the auto-increment is applied.
                 Lower 6 bits select the target register.
            frame: VDP frame count (incremented each VBLANK).
        """
        if not self.enabled:
            return

        val &= 0xFF
        reg = r17 & 0x3F
        self._handle_reg_write(pc, cycle, frame, reg, val, via=";port 9Bh")

    # ------------------------------------------------------------------
    # Internal

    def _handle_reg_write(
        self, pc: int, cycle: int, frame: int, reg: int, data: int, via: str
    ) -> None:
        suffix = f"  {via}" if via else ""
        prefix = f"CY={cycle:010d} FR={frame:06d} PC={pc:04X}"
        if 32 <= reg <= 45:
            self._param_buf[reg] = data
            self._emit(f"{prefix} VDP_REG R#{reg:02d}={data:02X}h{suffix}")
        elif reg == 46:
            params = self._fmt_params()
            name = _cmd_name(data)
            lop = _lop_name(data)
            self._emit(
                f"{prefix} VDP_CMD {name:<4s}/{lop:<4s} ({data:02X}h) R32-45={params}{suffix}"
            )
            self._param_buf.clear()
        else:
            self._emit(f"{prefix} VDP_REG R#{reg:02d}={data:02X}h{suffix}")

    def _fmt_params(self) -> str:
        """Format _param_buf as ['XX','XX',...] with '--' for unwritten registers."""
        parts = []
        for i in range(32, 46):  # R#32–R#45 inclusive (14 entries)
            v = self._param_buf.get(i)
            parts.append(f"'{v:02X}'" if v is not None else "'--'")
        return "[" + ",".join(parts) + "]"

    def _emit(self, line: str) -> None:
        print(line, file=self.output)
        self.output.flush()
