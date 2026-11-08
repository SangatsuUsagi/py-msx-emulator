"""Interactive debug REPL for MSX1 and MSX2 emulation.

Opened by the host after step_frame returns at a stop (host/debugger/stop.py).
Runs in the main thread; emulation is paused while the REPL is active.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from host.debugger.disasm import byte_reader, disassemble, instruction_bytes
from host.debugger.slots import slot_bank_text, slot_content_text
from host.debugger.stop import sigint_interrupts, watch_hit_line
from host.debugger.vdp_view import print_vdp_tms, print_vdp_v9938
from host.media import DiskFiles, swap_disk
from host.tape import TapeDeck, TapeError, seconds
from host.trace import TraceOutput
from host.watch import watch_kind, watch_text
from msx.boundary import (
    MAX_BREAKPOINTS,
    MAX_WATCHPOINTS,
    STATUS_OK,
    STOP_WATCHPOINT,
    TRACE_MAPPER,
    TRACE_VDP,
    VDP_KIND_TMS9918A,
    VDP_KIND_V9938,
    SlotInfo,
)

try:
    import readline
except ImportError:  # stock Windows CPython has no readline module
    readline = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from host.printer_output import PrinterOutput
    from msx.core_api import CoreApi


def _format_disasm(read: Callable[[int], int], addr: int) -> tuple[str, int]:
    """Disassemble one instruction at addr.

    Returns the shared '<raw hex bytes>  <mnemonic>' body (raw bytes padded to a
    fixed width) and the instruction length, so callers only supply the address
    prefix (PC=..., ADDR:, => ...).
    """
    mnem, size = disassemble(read, addr)
    raw_bytes = instruction_bytes(read, addr, size)
    return f"{raw_bytes:<12}  {mnem}", size


_TAPE_USAGE = "tape [blocks|load FILE|eject|rewind|seek SECONDS|seek #N]"
# The `tape` subcommands and how many arguments each takes.
_TAPE_ARGUMENTS = {"": 0, "blocks": 0, "load": 1, "eject": 0, "rewind": 0, "seek": 1}

_HELP = (
    "Commands: h/? | rc | rv | rp | v | dm ADDR [SIZE] | dv VADDR [SIZE] | dvf FILE | "
    "ba/br/bl ADDR | bh | bs [LOW HIGH|off] | wa/wd/wl ADDR | da [ADDR] | s [N] | "
    "g ADDR | gf N | so | te | td | ce | cd | ds | sl | st | ss | "
    "sv [TITLE] | ld FILE | fdd1/fdd2 [FILE|-] | "
    f"{_TAPE_USAGE} | reset | c | q"
)

# `da` disassembles a fixed window of instructions from the given address.
_DISASM_WINDOW = 10

# Cross-session REPL command history (only used when `readline` is available).
_HISTORY_FILE = Path.home() / ".msx_dbg_history"
_HISTORY_LENGTH = 500

# Loop-control commands handled inline in Debugger._repl_loop() (see below), not
# present in the _COMMANDS dispatch table. Listed here so Tab completion
# covers them too.
# The largest frame number, the range of the core's int64_t frame.
_MAX_FRAME = 0x7FFF_FFFF_FFFF_FFFF
_INLINE_COMMANDS = ("c", "q", "g", "gf", "so", "reset")

_readline_ready = False


def _setup_readline() -> None:
    """Enable line editing, history, and Tab completion for input().

    Idempotent and a no-op when the `readline` module is unavailable
    (e.g. stock Windows CPython without a readline backport).
    """
    global _readline_ready
    if readline is None or _readline_ready:
        return
    readline.set_history_length(_HISTORY_LENGTH)
    readline.set_completer(_complete_command)
    # macOS stdlib `readline` is actually libedit, which binds Tab via a
    # different parse_and_bind syntax than GNU readline. `readline.backend`
    # (Python 3.13+) reports it directly; older versions only expose it via
    # the module docstring.
    is_libedit = getattr(readline, "backend", None) == "libedit" or (
        readline.__doc__ is not None and "libedit" in readline.__doc__
    )
    if is_libedit:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    _readline_ready = True


def _complete_command(text: str, state: int) -> str | None:
    """readline completer: the state-th command name starting with text."""
    matches = [name for name in _COMMAND_NAMES if name.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


class Debugger:
    """Interactive debug prompt over a CoreApi, sharing the run's DiskFiles
    and TraceOutput."""

    def __init__(
        self, machine: CoreApi, disk_files: DiskFiles | None = None,
        trace: TraceOutput | None = None, rom_names: tuple[str, str] = ("", ""),
        printer: PrinterOutput | None = None, tape: TapeDeck | None = None,
    ) -> None:
        self._machine = machine
        # The mounted tape file and its blocks, shared with the RPC server of
        # the same run (host/tape.py).
        self._tape = tape if tape is not None else TapeDeck(machine)
        # The main and sub ROM file names `sl`/`st` print ("" for none): the
        # host loaded them, and the core keeps no name.
        self._rom_names = rom_names
        # The file of each drive's disk, shared with the frontend and the RPC
        # server of the same run (host/media.py DiskFiles).
        self._disk_files = disk_files if disk_files is not None else DiskFiles()
        # Where the run's VDP and mapper trace lines go (host/trace.py); the
        # CLI flags set it up, te/ce turn the traces on.
        self.trace = trace if trace is not None else TraceOutput()
        # --printer-log's file (host/printer_output.py); None without the flag.
        self._printer = printer

    def drain_printer(self) -> None:
        """Write the guest's printed bytes to --printer-log's file. The run
        loops call it after each step_frame, the step command after each
        step."""
        if self._printer is not None:
            self._printer.drain()

    def drain_trace(self) -> None:
        """Write the core's recorded trace events as text. The run loops call
        it after each step_frame, the step command after each step."""
        self.trace.drain(self._machine)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enter(self) -> None:
        """Suspend emulation and run the debug REPL until 'c' or 'q'."""
        print("\nDebugger entered. Type 'c' to resume, 'q' to exit.")
        pc = self._machine.cpu_registers().PC
        line, _ = _format_disasm(byte_reader(self._machine), pc)
        print(f"  PC={pc:04X}h  {line}")

        _setup_readline()
        if readline is not None:
            try:
                readline.read_history_file(_HISTORY_FILE)
            except OSError:  # no history file yet, or unreadable
                pass

        try:
            # Ctrl-C at the prompt exits (debug-monitor "Ctrl-C at the prompt
            # exits"), whatever handler the run loop installed.
            with sigint_interrupts():
                self._repl_loop()
        finally:
            if readline is not None:
                try:
                    readline.write_history_file(_HISTORY_FILE)
                except OSError:
                    pass

    def _repl_loop(self) -> None:
        """Read/dispatch commands until 'c' resumes or 'q' exits the process."""
        while True:
            try:
                cyc = self._machine.cycle_count
                frm = self._machine.frame_number()
                line = input(f"(msx-dbg cyc={cyc} frm={frm}) ").strip()
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                print("\nExiting emulator.")
                sys.exit(0)

            if not line:
                continue

            # shlex so quoted arguments keep spaces, e.g. fdd1 "MY DISK.dsk".
            try:
                parts = shlex.split(line)
            except ValueError as exc:  # unbalanced quotes
                print(f"Parse error: {exc}")
                continue
            if not parts:
                continue
            cmd = parts[0].lower()
            args = parts[1:]

            # Loop-control commands resume/exit the REPL and are handled inline;
            # every other command is a table lookup (_COMMANDS, below the class).
            if cmd == "c":
                return
            if cmd == "q":
                sys.exit(0)
            if cmd == "g":
                if self._cmd_goto(args):
                    return
                continue
            if cmd == "so":
                self._cmd_step_out()
                return
            if cmd == "gf":
                if self._cmd_goto_frame(args):
                    return
                continue
            if cmd == "reset":
                self._cmd_reset()
                return

            handler = _COMMANDS.get(cmd)
            if handler is None:
                print(f"Unknown command: {cmd!r}")
                print(f"  {_HELP}")
            else:
                handler(self, args)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_reg_cpu(self) -> None:
        r = self._machine.cpu_registers()
        f = r.AF & 0xFF
        print(
            f"AF={r.AF:04X}  BC={r.BC:04X}  DE={r.DE:04X}  HL={r.HL:04X}"
            f"  IX={r.IX:04X}  IY={r.IY:04X}  SP={r.SP:04X}  PC={r.PC:04X}"
        )
        print(
            f"  S={(f >> 7) & 1}  Z={(f >> 6) & 1}  H={(f >> 4) & 1}"
            f"  P/V={(f >> 2) & 1}  N={(f >> 1) & 1}  C={f & 1}"
        )

    def _is_unknown_vdp(self, kind: int) -> bool:
        """Print the kind of a VDP this host does not know (machine-c-abi "A
        host keeps working on an unknown core value")."""
        if kind in (VDP_KIND_TMS9918A, VDP_KIND_V9938):
            return False
        print(f"unknown VDP kind {kind}")
        return True

    def _cmd_reg_vdp(self) -> None:
        info = self._machine.vdp_info()
        if self._is_unknown_vdp(info.kind):
            return
        if info.kind == VDP_KIND_V9938:
            regs = info.regs
            for row_start in range(0, 28, 8):
                parts = [
                    f"R#{i}={regs[i]:02X}"
                    for i in range(row_start, min(row_start + 8, 28))
                ]
                print("  " + "  ".join(parts))
            cmd = info.cmd_regs
            for row_start in range(0, 15, 8):
                parts = [
                    f"R#{32 + i}={cmd[i]:02X}"
                    for i in range(row_start, min(row_start + 8, 15))
                ]
                print("  " + "  ".join(parts))
        else:
            parts = [f"R#{i}={info.regs[i]:02X}" for i in range(8)]
            print("  " + "  ".join(parts))

    def _cmd_reg_palette(self) -> None:
        info = self._machine.vdp_info()
        if self._is_unknown_vdp(info.kind):
            return
        if info.kind != VDP_KIND_V9938:
            print("rp: TMS9918A has no programmable palette (MSX2 / V9938 only)")
            return
        for row_start in range(0, 16, 8):
            parts = []
            for i in range(row_start, row_start + 8):
                p = info.palette[i]
                r = (p >> 6) & 0x7
                g = (p >> 3) & 0x7
                b = p & 0x7
                parts.append(f"#{i:X}={p:03X}({r},{g},{b})")
            print("  " + "  ".join(parts))

    def _cmd_vdp_status(self) -> None:
        info = self._machine.vdp_info()
        if self._is_unknown_vdp(info.kind):
            return
        if info.kind == VDP_KIND_V9938:
            print_vdp_v9938(info)
        else:
            print_vdp_tms(info)

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

        for row in range(0, size, 16):
            row_bytes = self._machine.read_memory((addr + row) & 0xFFFF, 16)
            print(f"  {(addr + row) & 0xFFFF:04X}: {_hex_ascii_row(row_bytes)}")

    def _cmd_dump_vram(self, args: list[str]) -> None:
        if not args:
            print("Usage: dv VADDR [SIZE]")
            return
        vram_mask = self._machine.vram_size() - 1
        try:
            addr = int(args[0], 16) & vram_mask
            size = int(args[1], 16) if len(args) > 1 else 128
        except ValueError:
            print("dv: invalid address or size (hex expected)")
            return

        addr_fmt = "05X" if vram_mask > 0xFFFF else "04X"
        for row in range(0, size, 16):
            row_bytes = self._machine.read_vram((addr + row) & vram_mask, 16)
            print(f"  {(addr + row) & vram_mask:{addr_fmt}}: {_hex_ascii_row(row_bytes)}")

    def _cmd_dump_vram_file(self, args: list[str]) -> None:
        if not args:
            print("Usage: dvf FILE  (write full VRAM as raw binary)")
            return
        vram = self._machine.read_vram(0, self._machine.vram_size())
        try:
            with open(args[0], "wb") as f:
                f.write(vram)
        except OSError as exc:
            print(f"dvf: {exc}")
            return
        print(f"dvf: wrote {len(vram)} bytes to {args[0]}")

    def _parse_hex_addr(self, arg: str, cmd: str) -> int | None:
        """Parse a 16-bit hex address, or print an error and return None."""
        try:
            return int(arg, 16) & 0xFFFF
        except ValueError:
            print(f"{cmd}: invalid address (hex expected)")
            return None

    def _break_list(self) -> None:
        """List the active breakpoints (bl)."""
        current = self._machine.breakpoints()
        if not current:
            print("  (no breakpoints)")
            return
        for addr in current:
            print(f"  {addr:04X}h")

    def _break_add(self, args: list[str]) -> None:
        """Add a breakpoint at ADDR (ba), up to MAX_BREAKPOINTS."""
        if not args:
            print("Usage: ba ADDR")
            return
        addr = self._parse_hex_addr(args[0], "ba")
        if addr is None:
            return
        current = list(self._machine.breakpoints())
        if addr not in current:
            if len(current) >= MAX_BREAKPOINTS:
                print(f"ba: maximum {MAX_BREAKPOINTS} breakpoints reached")
                return
            current.append(addr)
        self._machine.set_breakpoints(current)
        print(f"  Breakpoint set at {addr:04X}h ({len(current)}/{MAX_BREAKPOINTS})")

    def _break_remove(self, args: list[str]) -> None:
        """Remove the breakpoint at ADDR (br)."""
        if not args:
            print("Usage: br ADDR")
            return
        addr = self._parse_hex_addr(args[0], "br")
        if addr is None:
            return
        current = list(self._machine.breakpoints())
        if addr not in current:
            print(f"br: {addr:04X}h not in breakpoint list")
            return
        current.remove(addr)
        self._machine.set_breakpoints(current)
        print(f"  Breakpoint {addr:04X}h removed ({len(current)}/{MAX_BREAKPOINTS})")

    def _cmd_goto(self, args: list[str]) -> bool:
        """Set a one-shot run-to breakpoint. Returns True if emulation should resume."""
        if not args:
            print("Usage: g ADDR")
            return False
        addr = self._parse_hex_addr(args[0], "g")
        if addr is None:
            return False
        self._machine.set_temp_breakpoint(addr)
        print(f"  Running to {addr:04X}h ...")
        return True

    def _cmd_goto_frame(self, args: list[str]) -> bool:
        """Set a one-shot run-to-frame breakpoint. Returns True if emulation should resume."""
        if not args:
            print("Usage: gf N")
            return False
        try:
            target = int(args[0])
        except ValueError:
            print("gf: invalid frame number (decimal expected)")
            return False
        if target > _MAX_FRAME:
            print(f"gf: frame {target} is above {_MAX_FRAME}")
            return False
        current = self._machine.frame_number()
        if target <= current:
            print(f"gf: target frame {target} already reached (current: {current})")
            return False
        if self._machine.set_frame_breakpoint(target) != STATUS_OK:
            print(f"gf: {self._machine.last_error_text().decode('utf-8')}")
            return False
        print(f"  Running to frame {target} ...")
        return True

    def _cmd_reset(self) -> None:
        """Full power-on reset of CPU/PSG/SCC/VDP and slot registers (reset).

        Resumes emulation afterward, same as `c` — there is nothing left to
        inspect at the pre-reset PC.
        """
        self._machine.reset()
        pc = self._machine.cpu_registers().PC
        print(f"  Machine reset. Resuming at PC={pc:04X}h ...")

    def _cmd_step_out(self) -> None:
        """Run until the current routine returns (SP rises above its current value)."""
        sp = self._machine.cpu_registers().SP
        self._machine.set_step_out(sp)
        print(f"  Stepping out (SP={sp:04X}h) ...")

    def _cmd_break_halt(self) -> None:
        m = self._machine
        enabled = not m.break_halt_di()
        m.set_break_halt_di(enabled)
        print(f"  Break on HALT+DI {'enabled' if enabled else 'disabled'}")

    def _cmd_break_sp(self, args: list[str]) -> None:
        m = self._machine
        if not args:
            low, high = m.main_ram_range()
            m.set_sp_range(low, high)
            print(f"  Break on SP outside {low:04X}h-{high:04X}h (auto: machine RAM)")
            return
        if args[0].lower() == "off":
            m.set_sp_range(-1, -1)
            print("  SP-range break disabled")
            return
        if len(args) < 2:
            print("Usage: bs | bs off | bs LOW HIGH")
            return
        try:
            low = int(args[0], 16) & 0xFFFF
            high = int(args[1], 16) & 0xFFFF
        except ValueError:
            print("bs: invalid address (hex expected)")
            return
        if low > high:
            low, high = high, low
        m.set_sp_range(low, high)
        print(f"  Break on SP outside {low:04X}h-{high:04X}h")

    def _parse_watch_mode(self, mode: str) -> int | None:
        """The kind an r/w/rw watch mode spells, or print an error and
        return None."""
        kind = watch_kind(mode)
        if kind is None:
            print(f"wa: invalid mode {mode.lower().strip()!r} (use r, w, or rw)")
        return kind

    def _watch_list(self) -> None:
        """List the active watchpoints (wl)."""
        entries = list(self._machine.watchpoints())
        if not entries:
            print("  (no watchpoints)")
            return
        for addr, kind in entries:
            print(f"  {addr:04X}h [{watch_text(kind)}]")

    def _watch_add(self, args: list[str]) -> None:
        """Add/update a watchpoint at ADDR with an r/w/rw mode (wa)."""
        if not args:
            print("Usage: wa ADDR[,r|w|rw]")
            return
        tok = args[0]
        if "," in tok:
            addr_str, mode_str = tok.split(",", 1)
        elif len(args) > 1:
            addr_str, mode_str = tok, args[1]
        else:
            addr_str, mode_str = tok, "rw"
        kind = self._parse_watch_mode(mode_str)
        if kind is None:
            return
        addr = self._parse_hex_addr(addr_str, "wa")
        if addr is None:
            return
        entries = list(self._machine.watchpoints())
        if addr in [e[0] for e in entries]:
            entries = [(a, kind if a == addr else k) for a, k in entries]
        else:
            if len(entries) >= MAX_WATCHPOINTS:
                print(f"wa: maximum {MAX_WATCHPOINTS} watchpoints reached")
                return
            entries.append((addr, kind))
        self._machine.set_watchpoints(entries)
        print(
            f"  Watchpoint set at {addr:04X}h [{watch_text(kind)}] "
            f"({len(entries)}/{MAX_WATCHPOINTS})"
        )

    def _watch_remove(self, args: list[str]) -> None:
        """Remove the watchpoint at ADDR (wd)."""
        if not args:
            print("Usage: wd ADDR")
            return
        addr = self._parse_hex_addr(args[0], "wd")
        if addr is None:
            return
        entries = list(self._machine.watchpoints())
        if addr not in [e[0] for e in entries]:
            print(f"wd: {addr:04X}h not in watchpoint list")
            return
        entries = [(a, k) for a, k in entries if a != addr]
        self._machine.set_watchpoints(entries)
        print(f"  Watchpoint {addr:04X}h removed ({len(entries)}/{MAX_WATCHPOINTS})")

    def _cmd_disasm(self, args: list[str]) -> None:
        r = self._machine.cpu_registers()
        if args:
            addr = self._parse_hex_addr(args[0], "disasm")
            if addr is None:
                return
        else:
            addr = r.PC

        for _ in range(_DISASM_WINDOW):
            line, size = _format_disasm(byte_reader(self._machine), addr)
            print(f"  {addr:04X}: {line}")
            addr = (addr + size) & 0xFFFF

    def _cmd_step(self, args: list[str]) -> None:
        try:
            count = int(args[0]) if args else 1
        except ValueError:
            print("step: invalid count (decimal integer expected)")
            return
        for _ in range(count):
            self._machine.step()
            self.drain_trace()
            self.drain_printer()
            stop = self._machine.last_stop()
            if stop.reason == STOP_WATCHPOINT:
                print(watch_hit_line(stop))
        r = self._machine.cpu_registers()
        print(f"  PC={r.PC:04X}  AF={r.AF:04X}  BC={r.BC:04X}  DE={r.DE:04X}  HL={r.HL:04X}")
        line, _ = _format_disasm(byte_reader(self._machine), r.PC)
        print(f"  => {r.PC:04X}: {line}")

    def _cmd_trace_enable(self) -> None:
        trace = self.trace
        if trace.vdp_out is None:
            trace.set_vdp_output(sys.stdout, "stdout")
        self._machine.set_trace_kinds(self._machine.trace_kinds() | TRACE_VDP)
        print(f"VDP trace enabled ({trace.vdp_name})")

    def _cmd_trace_disable(self) -> None:
        kinds = self._machine.trace_kinds()
        if not kinds & TRACE_VDP:
            print("VDP trace already disabled")
            return
        self._machine.set_trace_kinds(kinds & ~TRACE_VDP)
        print("VDP trace disabled")

    def _cmd_mapper_trace_enable(self) -> None:
        if not self._machine.trace_sources() & TRACE_MAPPER:
            print("ce: no bank-switching ROM mapper present")
            return
        trace = self.trace
        if trace.map_out is None:
            trace.set_map_output(sys.stdout, "stdout")
        self._machine.set_trace_kinds(self._machine.trace_kinds() | TRACE_MAPPER)
        print(f"Mapper bank-switch trace enabled ({trace.map_name})")

    def _cmd_mapper_trace_disable(self) -> None:
        kinds = self._machine.trace_kinds()
        if not kinds & TRACE_MAPPER:
            print("Mapper trace already disabled")
            return
        self._machine.set_trace_kinds(kinds & ~TRACE_MAPPER)
        print("Mapper trace disabled")

    def _cmd_screenshot(self) -> None:
        """`ss`: the paused frame as a PNG (CoreApi.render_preview, which
        advances no frame, so it matches the v / dv dumps of this moment)."""
        from host.screenshot import save_screenshot
        frame = self._machine.render_preview()
        save_screenshot(frame.pixels, frame.width, frame.height)

    def _cmd_state_save(self, args: list[str]) -> None:
        """`sv [TITLE]`: save full machine state (CPU/VDP/mapper/RAM) to saves/states/.

        TITLE (a default is used when omitted) becomes the filename stem, matching
        the RPC `state.save` contract — it is not a file path. REPL stays open.
        """
        from host.state_files import save_state

        title = args[0] if args else "msx_dbg_checkpoint"
        frame = self._machine.render_preview()
        save_state(self._machine, frame, title, self._disk_files)

    def _cmd_state_load(self, args: list[str]) -> None:
        """`ld FILE`: restore machine state from a previously saved .state file.

        REPL stays open with no implicit resume, so the loaded state can be
        inspected or advanced precisely with `s` / `gf`.
        """
        from host.state_files import load_state

        if not args:
            print("Usage: ld FILE")
            return
        path = Path(args[0])
        try:
            load_state(self._machine, path)
        except (ValueError, OSError) as exc:
            print(f"ld: {exc}")
            return
        print(f"  Loaded state from {path}")

    def _cmd_disable_sprites(self) -> None:
        hidden = not self._machine.sprites_hidden()
        self._machine.set_sprites_hidden(hidden)
        state = "OFF (background only)" if hidden else "ON"
        print(f"Sprite rendering: {state}")

    def _cmd_slot_active(self) -> None:
        layout = self._machine.slot_layout()
        print(f"  {'Page':<6}{'Addr':<12}{'Prim':<6}{'Sec':<5}{'Content':<33}Bank")
        _page_addrs = ["0000-3FFF", "4000-7FFF", "8000-BFFF", "C000-FFFF"]
        for page in range(4):
            prim = (layout.primary >> (page * 2)) & 0x03
            sec = -1
            sec_str = "-"
            if layout.expanded & (1 << prim):
                sec = (layout.secondary[prim] >> (page * 2)) & 0x03
                sec_str = str(sec)
            info = self._machine.slot_info(prim, sec, page)
            content = slot_content_text(info, self._rom_names)
            bank = slot_bank_text(info, page)
            print(f"  P{page}    {_page_addrs[page]:<12}{prim:<6}{sec_str:<5}{content:<33}{bank}")

    def _cmd_slot_tree(self) -> None:
        layout = self._machine.slot_layout()
        _roles = {1: "cartridge slot", 2: "cartridge slot"}
        for prim in range(4):
            if layout.expanded & (1 << prim):
                _print_expanded_slot(
                    self._machine, prim, layout.secondary[prim], self._rom_names)
            else:
                role = _roles.get(prim)
                role_str = f"  [{role}]" if role else ""
                info = self._machine.slot_info(prim, -1, -1)
                print(f"  Primary {prim}{role_str}  {_slot_text(info, self._rom_names)}")

    def _cmd_fdd(self, args: list[str], drive_index: int) -> None:
        """`fddN [FILE|-]`: show, swap, or eject the disk in floppy drive N.

        No argument prints the current mount; a path mounts that *.dsk (relative
        to the cwd or absolute, ~ expanded); `-` ejects. The FDC sees the new
        disk once emulation resumes. Errors are reported without changing the current disk.
        """
        label = f"fdd{drive_index + 1}"
        drives = self._machine.drive_count()
        if drives == 0:
            print(f"{label}: this machine has no floppy interface")
            return
        if drive_index >= drives:
            print(f"{label}: machine has only {drives} drive(s)")
            return
        if not args:
            if self._machine.disk_mounted(drive_index):
                print(f"{label}: {self._disk_files.path(drive_index)}")
            else:
                print(f"{label}: empty")
            return
        arg = args[0]
        if arg == "-":
            swap_disk(self._machine, self._disk_files, drive_index, None)
            print(f"{label}: ejected")
            return
        path = Path(arg).expanduser()
        if not path.exists():
            print(f"{label}: file not found: {path}")
            return
        try:
            swap_disk(self._machine, self._disk_files, drive_index, path)
        except (ValueError, OSError) as exc:
            print(f"{label}: cannot mount {path}: {exc}")
            return
        print(f"{label}: mounted {path}")

    def _cmd_tape(self, args: list[str]) -> None:
        """`tape [blocks|load FILE|eject|rewind|seek SECONDS|seek #N]`: show
        or change the cassette. An error is printed and changes nothing."""
        sub = args[0] if args else ""
        if _TAPE_ARGUMENTS.get(sub) != len(args[1:]):
            print(f"tape: usage: {_TAPE_USAGE}")
            return
        try:
            if sub == "":
                self._tape_status()
            elif sub == "blocks":
                self._tape_blocks()
            elif sub == "load":
                self._tape_load(args[1])
            elif sub == "eject":
                self._tape.eject()
                print("tape: ejected")
            elif sub == "rewind":
                self._tape.rewind()
                self._tape_status()
            else:
                self._tape_seek(args[1])
                self._tape_status()
        except TapeError as exc:
            print(f"tape: {exc}")

    def _tape_load(self, path_text: str) -> None:
        path = Path(path_text).expanduser()
        warnings = self._tape.load(path)
        print(f"tape: mounted {path} ({len(self._tape.blocks)} blocks)")
        for warning in warnings:
            print(f"tape: warning: {warning}")

    def _tape_seek(self, target: str) -> None:
        if target.startswith("#"):
            try:
                number = int(target[1:])
            except ValueError:
                raise TapeError(f"invalid block number {target!r}") from None
            self._tape.seek_block(number)
            return
        try:
            value = float(target)
        except ValueError:
            raise TapeError(f"invalid position {target!r}: give seconds or #N") from None
        self._tape.seek_seconds(value)

    def _tape_status(self) -> None:
        status = self._tape.status()
        if not status.mounted:
            print("tape: empty")
            return
        motor = "on" if status.motor else "off"
        print(f"tape: {status.path}  {status.position_seconds:.2f} s (block {status.block})"
              f" / {status.length_seconds:.2f} s  motor {motor}")

    def _tape_blocks(self) -> None:
        if not self._tape.status().mounted:
            print("tape: empty")
            return
        for block in self._tape.blocks:
            name = block.name if block.name is not None else ""
            print(f"  #{block.number:<3} {block.type:<7} {name:<6}  "
                  f"{seconds(block.start):8.2f} s  {block.length} bytes")


def _hex_ascii_row(row_bytes: bytes) -> str:
    """One dump row: the bytes in hex, then as ASCII (. for a non-printable)."""
    hex_part = " ".join(f"{b:02X}" for b in row_bytes)
    ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row_bytes)
    return f"{hex_part}  {ascii_part}"


# ---------------------------------------------------------------------------
# VDP fancy display helpers (module-level, no instance state needed)
# ---------------------------------------------------------------------------


def _print_expanded_slot(
    machine: CoreApi, prim: int, raw: int, rom_names: tuple[str, str]
) -> None:
    print(f"  Primary {prim} [EXPANDED]  secondary-select(raw)={raw:02X}h")
    parts = []
    for pg in range(4):
        s = (raw >> (pg * 2)) & 0x03
        parts.append(f"P{pg}->{prim}-{s}")
    print(f"    page-map: {'  '.join(parts)}")
    for sec in range(4):
        info = machine.slot_info(prim, sec, -1)
        print(f"    {prim}-{sec}  {_slot_text(info, rom_names)}")


def _slot_text(info: SlotInfo, rom_names: tuple[str, str]) -> str:
    """The `st` line text of a slot: its content, then its size if any."""
    content = slot_content_text(info, rom_names)
    if info.size_kb:
        return f"{content}  {info.size_kb}KB"
    return content


# REPL command dispatch table (see Debugger._repl_loop). Loop-control commands
# (c/q/g/gf/so/reset) are handled inline in _repl_loop(); everything else maps here.
# The short lambdas adapt each command's argument shape to its _cmd_* handler.
_COMMANDS: dict[str, Callable[["Debugger", list[str]], None]] = {
    "h": lambda d, a: print(f"  {_HELP}"),
    "?": lambda d, a: print(f"  {_HELP}"),
    "rc": lambda d, a: d._cmd_reg_cpu(),
    "rv": lambda d, a: d._cmd_reg_vdp(),
    "rp": lambda d, a: d._cmd_reg_palette(),
    "v": lambda d, a: d._cmd_vdp_status(),
    "dm": lambda d, a: d._cmd_dump(a),
    "dv": lambda d, a: d._cmd_dump_vram(a),
    "dvf": lambda d, a: d._cmd_dump_vram_file(a),
    "ba": lambda d, a: d._break_add(a),
    "br": lambda d, a: d._break_remove(a),
    "bl": lambda d, a: d._break_list(),
    "bh": lambda d, a: d._cmd_break_halt(),
    "bs": lambda d, a: d._cmd_break_sp(a),
    "wa": lambda d, a: d._watch_add(a),
    "wd": lambda d, a: d._watch_remove(a),
    "wl": lambda d, a: d._watch_list(),
    "da": lambda d, a: d._cmd_disasm(a),
    "s": lambda d, a: d._cmd_step(a),
    "te": lambda d, a: d._cmd_trace_enable(),
    "td": lambda d, a: d._cmd_trace_disable(),
    "ce": lambda d, a: d._cmd_mapper_trace_enable(),
    "cd": lambda d, a: d._cmd_mapper_trace_disable(),
    "ds": lambda d, a: d._cmd_disable_sprites(),
    "sl": lambda d, a: d._cmd_slot_active(),
    "st": lambda d, a: d._cmd_slot_tree(),
    "ss": lambda d, a: d._cmd_screenshot(),
    "sv": lambda d, a: d._cmd_state_save(a),
    "ld": lambda d, a: d._cmd_state_load(a),
    "fdd1": lambda d, a: d._cmd_fdd(a, 0),
    "fdd2": lambda d, a: d._cmd_fdd(a, 1),
    "tape": lambda d, a: d._cmd_tape(a),
}

# Full REPL command-name set for Tab completion (_complete_command, above):
# every _COMMANDS key plus the inline-handled loop-control commands.
_COMMAND_NAMES: tuple[str, ...] = tuple(sorted(set(_COMMANDS) | set(_INLINE_COMMANDS)))
