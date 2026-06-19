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
    "Commands: rc | rv | rp | v | dm ADDR [SIZE] | dv VADDR [SIZE] | "
    "ba/br/bl ADDR | da [ADDR] | s [N] | te | td | c | q"
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
        print("\nDebugger entered. Type 'c' to resume, 'q' to exit.")
        pc = self._machine.cpu.registers.PC
        read = self._machine.cpu.read_byte
        mnem, size = disassemble(read, pc)
        raw_bytes = " ".join(f"{read((pc + i) & 0xFFFF):02X}" for i in range(size))
        print(f"  PC={pc:04X}h  {raw_bytes:<12}  {mnem}")
        while True:
            try:
                cyc = self._machine.cycle_count
                frm = self._machine.vdp._frame_count
                line = input(f"(msx-dbg cyc={cyc} frm={frm}) ").strip()
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

            if cmd == "c":
                return
            if cmd == "q":
                sys.exit(0)
            elif cmd == "rc":
                self._cmd_reg_cpu()
            elif cmd == "rv":
                self._cmd_reg_vdp()
            elif cmd == "rp":
                self._cmd_reg_palette()
            elif cmd == "v":
                self._cmd_vdp_status()
            elif cmd == "dm":
                self._cmd_dump(args)
            elif cmd == "dv":
                self._cmd_dump_vram(args)
            elif cmd == "ba":
                self._cmd_break(["a"] + args)
            elif cmd == "br":
                self._cmd_break(["r"] + args)
            elif cmd == "bl":
                self._cmd_break(["l"])
            elif cmd == "da":
                self._cmd_disasm(args)
            elif cmd == "s":
                self._cmd_step(args)
            elif cmd == "te":
                self._cmd_trace_enable()
            elif cmd == "td":
                self._cmd_trace_disable()
            elif cmd == "pdbg":
                self._cmd_palette_debug()
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
        cmd = vdp.cmd_regs
        for row_start in range(0, 15, 8):
            parts = [
                f"R#{32 + i}={cmd[i]:02X}"
                for i in range(row_start, min(row_start + 8, 15))
            ]
            print("  " + "  ".join(parts))

    def _cmd_reg_palette(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("rp: V9938 not active (MSX2 only)")
            return
        for row_start in range(0, 16, 8):
            parts = []
            for i in range(row_start, row_start + 8):
                p = vdp.palette[i]
                r = (p >> 6) & 0x7
                g = (p >> 3) & 0x7
                b = p & 0x7
                parts.append(f"#{i:X}={p:03X}({r},{g},{b})")
            print("  " + "  ".join(parts))

    def _cmd_vdp_status(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("vdp: V9938 not active (MSX2 only)")
            return
        _print_vdp_fancy(vdp)

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

    def _cmd_dump_vram(self, args: list[str]) -> None:
        if not args:
            print("Usage: dv VADDR [SIZE]")
            return
        try:
            addr = int(args[0], 16) & 0x1FFFF
            size = int(args[1], 16) if len(args) > 1 else 128
        except ValueError:
            print("dv: invalid address or size (hex expected)")
            return

        vram = self._machine.vdp.vram
        for row in range(0, size, 16):
            row_bytes = [vram[(addr + row + col) & 0x1FFFF] for col in range(16)]
            hex_part = " ".join(f"{b:02X}" for b in row_bytes)
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row_bytes)
            print(f"  {(addr + row) & 0x1FFFF:05X}: {hex_part}  {ascii_part}")

    def _cmd_break(self, args: list[str]) -> None:
        if not args:
            print("Usage: ba ADDR | br ADDR | bl")
            return
        sub = args[0].lower()
        current = list(self._machine._breakpoints)

        if sub == "l":
            if not current:
                print("  (no breakpoints)")
            else:
                for addr in sorted(current):
                    print(f"  {addr:04X}h")
            return

        if sub == "a":
            if len(args) < 2:
                print("Usage: ba ADDR")
                return
            try:
                addr = int(args[1], 16) & 0xFFFF
            except ValueError:
                print("ba: invalid address (hex expected)")
                return
            if len(current) >= 4:
                print("ba: maximum 4 breakpoints reached")
                return
            if addr not in current:
                current.append(addr)
            self._machine.set_breakpoints(current)
            print(f"  Breakpoint set at {addr:04X}h ({len(current)}/4)")
            return

        if sub == "r":
            if len(args) < 2:
                print("Usage: br ADDR")
                return
            try:
                addr = int(args[1], 16) & 0xFFFF
            except ValueError:
                print("br: invalid address (hex expected)")
                return
            if addr not in current:
                print(f"br: {addr:04X}h not in breakpoint list")
                return
            current.remove(addr)
            self._machine.set_breakpoints(current)
            print(f"  Breakpoint {addr:04X}h removed ({len(current)}/4)")
            return

        print(f"Unknown break sub-command: {sub!r}. Use a, r, or l.")

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

    def _cmd_step(self, args: list[str] | None = None) -> None:
        try:
            count = int(args[0]) if args else 1
        except (ValueError, IndexError):
            print("step: invalid count (decimal integer expected)")
            return
        for _ in range(count):
            self._machine.step()
        r = self._machine.cpu.registers
        print(f"  PC={r.PC:04X}  AF={r.AF:04X}  BC={r.BC:04X}  DE={r.DE:04X}  HL={r.HL:04X}")
        read = self._machine.cpu.read_byte
        mnem, size = disassemble(read, r.PC)
        raw_bytes = " ".join(f"{read((r.PC + i) & 0xFFFF):02X}" for i in range(size))
        print(f"  => {r.PC:04X}: {raw_bytes:<12}  {mnem}")

    def _cmd_trace_enable(self) -> None:
        from msx.vdp.v9938 import V9938
        from msx.vdp.tracer import Tracer
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("te: V9938 not active (MSX2 only)")
            return
        if vdp._get_pc is None:
            m = self._machine
            vdp._get_pc    = lambda: m.cpu.instruction_pc
            vdp._get_cycle = lambda: m.cycle_count
            vdp._get_frame = lambda: vdp._frame_count
        if vdp.tracer is None:
            vdp.tracer = Tracer(enabled=True, output=sys.stdout)
        else:
            vdp.tracer.enabled = True
        print("VDP trace enabled (stdout)")

    def _cmd_trace_disable(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938) or vdp.tracer is None or not vdp.tracer.enabled:
            print("VDP trace already disabled")
            return
        vdp.tracer.enabled = False
        print("VDP trace disabled")

    def _cmd_palette_debug(self) -> None:
        from msx.vdp.v9938 import V9938
        vdp = self._machine.vdp
        if not isinstance(vdp, V9938):
            print("pdbg: V9938 not active (MSX2 only)")
            return
        vdp.debug_palette_writes = not vdp.debug_palette_writes
        state = "ON" if vdp.debug_palette_writes else "OFF"
        print(f"Palette mid-frame write debug: {state}")


# ---------------------------------------------------------------------------
# VDP fancy display helpers (module-level, no instance state needed)
# ---------------------------------------------------------------------------

_CMD_NAMES: dict[int, str] = {
    0x0: "ABRT", 0x4: "POINT", 0x5: "PSET", 0x6: "SRCH",
    0x7: "LINE", 0x8: "LMMV", 0x9: "LMMM", 0xA: "LMCM",
    0xB: "LMMC", 0xC: "HMMV", 0xD: "HMMM", 0xE: "YMMM", 0xF: "HMMC",
}
_LOG_OPS: dict[int, str] = {
    0: "IMP", 1: "AND", 2: "OR", 3: "XOR", 4: "NOT",
}


def _decode_screen_mode(r0: int, r1: int) -> str:
    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1
    m3 = (r0 >> 1) & 1  # R#0 bit1
    m4 = (r0 >> 2) & 1  # R#0 bit2
    m5 = (r0 >> 3) & 1  # R#0 bit3
    modes: dict[tuple[int, ...], str] = {
        (0, 0, 0, 0, 0): "SCREEN1 (GRAPHIC1)",
        (1, 0, 0, 0, 0): "SCREEN0 (TEXT1/40col)",
        (0, 0, 1, 0, 0): "SCREEN2 (GRAPHIC2)",
        (0, 1, 0, 0, 0): "SCREEN3 (MULTICOLOR)",
        (0, 0, 0, 1, 0): "SCREEN4 (GRAPHIC3)",
        (0, 0, 1, 1, 0): "SCREEN5 (GRAPHIC4)",
        (0, 0, 0, 0, 1): "SCREEN6 (GRAPHIC5)",
        (0, 0, 1, 0, 1): "SCREEN7 (GRAPHIC6)",
        (0, 0, 1, 1, 1): "SCREEN8 (GRAPHIC7)",
    }
    return modes.get((m1, m2, m3, m4, m5), f"UNKNOWN (M={m1}{m2}{m3}{m4}{m5})")


def _decode_cmr(cmr: int) -> tuple[str, str]:
    cmd_code = (cmr >> 4) & 0xF
    log_code = cmr & 0x07
    transparent = bool(cmr & 0x08)
    cmd = _CMD_NAMES.get(cmd_code, f"CMD{cmd_code:X}")
    log = _LOG_OPS.get(log_code, f"LOG{log_code}")
    if transparent:
        log += "/T"
    return cmd, log


def _print_vdp_fancy(vdp: object) -> None:
    from msx.vdp.v9938 import V9938
    assert isinstance(vdp, V9938)
    r = vdp.regs          # R#0-R#27
    c = vdp.cmd_regs      # R#32-R#46 (index 0-14)
    s0 = vdp.status
    s2 = vdp._status2

    # --- Screen mode ---
    print(f"  Screen : {_decode_screen_mode(r[0], r[1])}")

    # --- VRAM layout ---
    _m4 = (r[0] >> 2) & 1  # R#0 bit2
    _m5 = (r[0] >> 3) & 1  # R#0 bit3
    name_base    = (r[2] & 0x60) << 10 if (_m4 or _m5) else (r[2] & 0x0F) << 10
    color_base   = ((r[10] & 0x07) << 14) | ((r[3]  & 0xFF) << 6)
    pattern_base = (r[4]  & 0x3F) << 11
    # R#5/R#11 → SAT base (512-byte aligned); colour table at SAT-0x200
    _attr_reg    = (((r[11] & 0x03) << 15) | (r[5] << 7)) & 0x1FFFF
    sprite_attr  = _attr_reg & ~0x1FF & 0x1FFFF
    _spr_col     = (sprite_attr - 0x200) & 0x1FFFF
    sprite_pat   = (r[6]  & 0x3F) << 11
    print(
        f"  VRAM   : Name={name_base:05X}  Color={color_base:05X}"
        f"  Pattern={pattern_base:05X}  SprAttr={sprite_attr:05X}  SprPat={sprite_pat:05X}"
    )

    # --- Display control ---
    disp    = "ON " if r[1] & 0x40 else "OFF"
    sprites = "OFF" if r[8] & 0x02 else "ON "
    height  = 212 if r[9] & 0x80 else 192
    timing  = "PAL " if r[9] & 0x02 else "NTSC"
    ilace   = " IL" if r[9] & 0x08 else "   "
    spr_sz  = "16x16" if r[1] & 0x02 else " 8x8 "
    spr_mag = "x2" if r[1] & 0x01 else "x1"
    fg      = (r[7] >> 4) & 0xF
    bg      = r[7] & 0xF
    ie0     = "IE0" if r[1] & 0x20 else "   "
    ie1     = "IE1" if r[0] & 0x10 else "   "
    print(
        f"  Disp   : EN={disp}  SPR={sprites}  {height}L  {timing}{ilace}"
        f"  Spr={spr_sz}/mag={spr_mag}  FG={fg:X} BG={bg:X}  {ie0} {ie1}"
    )

    # --- VDP command state ---
    sx  = c[0]  | (c[1]  & 0x01) << 8
    sy  = c[2]  | (c[3]  & 0x03) << 8
    dx  = c[4]  | (c[5]  & 0x01) << 8
    dy  = c[6]  | (c[7]  & 0x03) << 8
    nx  = c[8]  | (c[9]  & 0x01) << 8
    ny  = c[10] | (c[11] & 0x03) << 8
    clr = c[12]
    arg = c[13]
    cmr = c[14]
    cmd_name, log_op = _decode_cmr(cmr)
    dix  = "←" if arg & 0x04 else "→"
    diy  = "↑" if arg & 0x08 else "↓"
    if s2 & 0x01:
        rem = vdp._cmd_remaining
        if rem > 0:
            busy = f"BUSY({rem}cyc)"
        else:
            total = (vdp._cmd_nx // 2) * vdp._cmd_ny
            done  = vdp._cmd_y * (vdp._cmd_nx // 2) + vdp._cmd_x // 2
            busy  = f"BUSY(CPU-feed rem={total - done}/{total})"
    else:
        busy = "IDLE"
    print(
        f"  CMD    : {cmd_name}  {log_op}  [{busy}]"
        f"  SRC=({sx},{sy})  DST=({dx},{dy})  SIZE=({nx},{ny})"
        f"  CLR={clr:02X}  DIR={dix}{diy}"
    )

    # --- Status registers ---
    tr  = "TR" if s2 & 0x80 else "  "
    ce  = "CE" if s2 & 0x01 else "  "
    vf  = "F"  if s0 & 0x80 else " "
    sp  = f"5S={s0 & 0x40 and 1 or 0}"
    col = f"C={s0 & 0x20 and 1 or 0}"
    print(f"  Status : S#0={s0:02X} ({vf} {sp} {col})  S#2={s2:02X} ({tr} {ce})")
