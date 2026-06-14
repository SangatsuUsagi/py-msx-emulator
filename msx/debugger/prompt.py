"""Interactive debug REPL for MSX2 / V9938 emulation.

Triggered by breakpoint hit or KeyboardInterrupt in Machine.run_frame().
Runs in the main thread; emulation is paused while the REPL is active.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from msx.debugger.disasm import disassemble

if TYPE_CHECKING:
    from msx.machine import Machine
    from msx.vdp.v9938 import V9938


_HELP = (
    "Commands: reg cpu | reg vdp | vdp | dump ADDR [SIZE] | "
    "break add/remove/list ADDR | disasm [ADDR] | step | cont | quit"
)


class Debugger:
    """Interactive debug prompt attached to a Machine instance."""

    def __init__(self, machine: Machine) -> None:
        self._machine = machine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enter(self) -> None:
        """Suspend emulation and run the debug REPL until 'cont' or 'quit'."""
        print("\nDebugger entered. Type 'cont' to resume, 'quit' to exit.")
        print(f"  PC={self._machine.cpu.registers.PC:04X}h")
        while True:
            try:
                line = input("(msx-dbg) ").strip()
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                print("\nExiting emulator.")
                sys.exit(0)

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in ("cont", "c"):
                return
            if cmd in ("quit", "q"):
                sys.exit(0)
            elif cmd == "reg":
                self._cmd_reg(args)
            elif cmd == "vdp":
                self._cmd_vdp_status()
            elif cmd == "dump":
                self._cmd_dump(args)
            elif cmd == "break":
                self._cmd_break(args)
            elif cmd == "disasm":
                self._cmd_disasm(args)
            elif cmd in ("step", "s"):
                self._cmd_step()
            else:
                print(f"Unknown command: {cmd!r}")
                print(f"  {_HELP}")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_reg(self, args: list[str]) -> None:
        if not args:
            print(f"Usage: reg cpu | reg vdp")
            return
        sub = args[0].lower()
        if sub == "cpu":
            self._cmd_reg_cpu()
        elif sub == "vdp":
            self._cmd_reg_vdp()
        else:
            print(f"Unknown register group: {sub!r}. Use 'reg cpu' or 'reg vdp'.")

    def _cmd_reg_cpu(self) -> None:
        r = self._machine.cpu.registers
        f = r.F
        print(
            f"AF={r.AF:04X}  BC={r.BC:04X}  DE={r.DE:04X}  HL={r.HL:04X}"
            f"  IX={r.IX:04X}  IY={r.IY:04X}  SP={r.SP:04X}  PC={r.PC:04X}"
        )
        print(
            f"  S={(f >> 7) & 1}  Z={(f >> 6) & 1}  H={(f >> 4) & 1}"
            f"  P/V={(f >> 2) & 1}  N={(f >> 1) & 1}  C={f & 1}"
        )

    def _cmd_reg_vdp(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("reg vdp: V9938 not active (MSX2 only)")
            return
        regs = vdp.regs
        for row_start in range(0, 28, 8):
            parts = [
                f"R#{i}={regs[i]:02X}"
                for i in range(row_start, min(row_start + 8, 28))
            ]
            print("  " + "  ".join(parts))

    def _cmd_vdp_status(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("vdp: V9938 not active (MSX2 only)")
            return
        print(f"  S#0={vdp.status:02X}  S#2={vdp._status2:02X}")

    def _cmd_dump(self, args: list[str]) -> None:
        if not args:
            print("Usage: dump ADDR [SIZE]")
            return
        try:
            addr = int(args[0], 16) & 0xFFFF
            size = int(args[1], 16) if len(args) > 1 else 128
        except ValueError:
            print("dump: invalid address or size (hex expected)")
            return

        read = self._machine.cpu.read_byte
        for row in range(0, size, 16):
            row_bytes = [read((addr + row + col) & 0xFFFF) for col in range(16)]
            hex_part = " ".join(f"{b:02X}" for b in row_bytes)
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row_bytes)
            print(f"  {(addr + row) & 0xFFFF:04X}: {hex_part}  {ascii_part}")

    def _cmd_break(self, args: list[str]) -> None:
        if not args:
            print("Usage: break add ADDR | break remove ADDR | break list")
            return
        sub = args[0].lower()
        current = list(self._machine._breakpoints)

        if sub == "list":
            if not current:
                print("  (no breakpoints)")
            else:
                for addr in sorted(current):
                    print(f"  {addr:04X}h")
            return

        if sub == "add":
            if len(args) < 2:
                print("Usage: break add ADDR")
                return
            try:
                addr = int(args[1], 16) & 0xFFFF
            except ValueError:
                print("break add: invalid address (hex expected)")
                return
            if len(current) >= 4:
                print("break add: maximum 4 breakpoints reached")
                return
            if addr not in current:
                current.append(addr)
            self._machine.set_breakpoints(current)
            print(f"  Breakpoint set at {addr:04X}h ({len(current)}/4)")
            return

        if sub == "remove":
            if len(args) < 2:
                print("Usage: break remove ADDR")
                return
            try:
                addr = int(args[1], 16) & 0xFFFF
            except ValueError:
                print("break remove: invalid address (hex expected)")
                return
            if addr not in current:
                print(f"break remove: {addr:04X}h not in breakpoint list")
                return
            current.remove(addr)
            self._machine.set_breakpoints(current)
            print(f"  Breakpoint {addr:04X}h removed ({len(current)}/4)")
            return

        print(f"Unknown break sub-command: {sub!r}. Use add, remove, or list.")

    def _cmd_disasm(self, args: list[str]) -> None:
        r = self._machine.cpu.registers
        if args:
            try:
                addr = int(args[0], 16) & 0xFFFF
            except ValueError:
                print("disasm: invalid address (hex expected)")
                return
        else:
            addr = r.PC

        read = self._machine.cpu.read_byte
        for _ in range(10):
            mnem, size = disassemble(read, addr)
            raw_bytes = " ".join(f"{read((addr + i) & 0xFFFF):02X}" for i in range(size))
            print(f"  {addr:04X}: {raw_bytes:<12}  {mnem}")
            addr = (addr + size) & 0xFFFF

    def _cmd_step(self) -> None:
        self._machine.step()
        r = self._machine.cpu.registers
        print(f"  PC={r.PC:04X}  AF={r.AF:04X}  BC={r.BC:04X}  DE={r.DE:04X}  HL={r.HL:04X}")
