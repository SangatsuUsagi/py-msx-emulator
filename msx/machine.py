from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.input import InputState
from msx.io import IOBus
from msx.mapper import MajutsushiMapper
from msx.memory import Memory
from msx.psg import PSG
from msx.scc import SCC
from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
from msx.vdp.vdp import VDP

if TYPE_CHECKING:
    from msx.debugger.prompt import Debugger
    from msx.fdc.interface import FloppyDisk

# NTSC: 3.579545 MHz / 60 Hz ≈ 59,659 T-states per frame
CYCLES_PER_FRAME: int = 59_659
LINES_PER_FRAME: int = 262
HANG_PC_REPEAT_THRESHOLD: int = 1000

# MSX1 (SCREEN 0-3) visible resolution, used for screenshots.
SCREEN_WIDTH: int = 256
SCREEN_HEIGHT: int = 192


@dataclass
class Machine:
    cpu: Z80
    vdp: VDP
    memory: Memory
    io: IOBus
    psg: PSG
    scc: SCC | None = field(default=None)
    dac: MajutsushiMapper | None = field(default=None)
    fdc: "FloppyDisk | None" = field(default=None)
    input: InputState = field(default_factory=InputState)
    cycles_per_frame: int = CYCLES_PER_FRAME
    lines_per_frame: int = LINES_PER_FRAME
    cycle_count: int = 0
    sram_save_path: "Path | None" = field(default=None, repr=False)
    _logger: DebugLogger | None = field(default=None, repr=False)
    _debugger: Debugger | None = field(default=None, repr=False)
    # Optional programmatic pause sink (e.g. the RPC server). When set, break
    # events call it with (reason, pc) instead of entering the interactive REPL.
    _pause_hook: Callable[[str, int], None] | None = field(
        default=None, init=False, repr=False
    )
    # Async pause plumbing used when a pause hook is installed: a break sets
    # _pause_requested so the debug frame loop returns at the break point, and
    # resume records _resume_skip_pc so continue does not immediately re-break
    # the instruction we are paused on.
    _pause_requested: bool = field(default=False, init=False, repr=False)
    _resume_skip_pc: int | None = field(default=None, init=False, repr=False)
    _breakpoints: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_read: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_write: frozenset[int] = field(default_factory=frozenset, repr=False)
    _last_pc: int = field(default=0, init=False, repr=False)
    _pc_repeat: int = field(default=0, init=False, repr=False)
    # Crash-signature auto-break conditions (debugger bh/bs).
    _break_halt_di: bool = field(default=False, init=False, repr=False)
    _sp_range: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _halt_di_seen: bool = field(default=False, init=False, repr=False)
    _sp_out_seen: bool = field(default=False, init=False, repr=False)
    # Targeted execution control (debugger g/so); one-shot, cleared on hit.
    _temp_breakpoint: int | None = field(default=None, init=False, repr=False)
    _stepout_sp: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Portability note: this wires the CPU's memory/IO bus by reassigning
        # bound methods onto Callable fields at runtime — the hottest path in
        # the emulator. Rust/C++ has no runtime method swap; a port expresses
        # the bus as a `trait MemoryBus` (or an `enum { Normal, Watchpoint }`)
        # whose concrete implementation is selected once behind a flag, so the
        # per-access dispatch stays branch-free. Kept as a comment, not a
        # rewrite, to avoid adding a per-access call/branch in Python.
        self.cpu.read_byte = self.memory.read
        self.cpu.write_byte = self.memory.write
        self.cpu.read_port = self.io.read_port
        self.cpu.write_port = self.io.write_port
        if not isinstance(self.vdp, V9938):
            # TMS9918A (MSX1) has a single VBlank interrupt source per frame and
            # no line/scanline interrupts (those are V9938+). The frame-end
            # interrupt fired once per frame via on_interrupt is therefore the
            # hardware-correct MSX1 model; there is no MSX1 equivalent to the
            # V9938 per-scanline IRQ polling done in run_frame().
            self.vdp.on_interrupt = self._vblank_interrupt

    def _vblank_interrupt(self) -> None:
        self.cpu.int_pending = True

    def reset(self) -> None:
        """Full power-on reset: CPU, PSG, SCC (if present), VDP, and the
        primary/secondary slot registers. Memory/VRAM contents are retained."""
        self.cpu.reset()
        self.psg.reset()
        if self.scc is not None:
            self.scc.reset()
        self.vdp.reset()
        # Power-on slot state: all pages select slot 0 (matches construction).
        self.memory.slot_register = 0x00
        self.memory.sub_slot_reg = 0x00

    def set_pause_hook(self, hook: Callable[[str, int], None] | None) -> None:
        """Install (or clear) a programmatic pause sink.

        When set, break events (breakpoints, watchpoints, Ctrl-C, and the
        crash-signature conditions) invoke `hook(reason, pc)` instead of
        entering the blocking interactive debugger REPL. `reason` is one of
        "user_request", "breakpoint", "watchpoint", or "step_complete".
        """
        self._pause_hook = hook

    def _enter_break(self, reason: str) -> None:
        """Dispatch a break event: notify the pause hook if one is installed,
        otherwise fall back to the interactive debugger REPL when attached.

        In hook mode this also raises _pause_requested so the debug frame loop
        returns at the break point instead of running on to the frame end."""
        if self._pause_hook is not None:
            self._pause_requested = True
            self._pause_hook(reason, self.cpu.registers.PC)
        elif self._debugger is not None:
            self._debugger.enter()

    def prepare_resume(self) -> None:
        """Arm a resume: clear the pause request and skip re-breaking on the
        instruction we are paused at, so continue does not immediately retrigger
        the same breakpoint. Called by the RPC continue handlers."""
        self._pause_requested = False
        self._resume_skip_pc = self.cpu.registers.PC

    def set_breakpoints(self, addrs: list[int]) -> None:
        """Set breakpoint addresses (max 4). Replaces existing set."""
        self._breakpoints = frozenset(addrs[:4])

    def set_break_halt_di(self, enabled: bool) -> None:
        """Enable/disable breaking when the CPU executes HALT with interrupts off."""
        self._break_halt_di = enabled
        self._halt_di_seen = False

    def set_sp_range(self, rng: tuple[int, int] | None) -> None:
        """Set the valid-RAM range for SP; break when SP leaves it. None disables."""
        self._sp_range = rng
        self._sp_out_seen = False

    def set_temp_breakpoint(self, addr: int | None) -> None:
        """Set a one-shot run-to breakpoint (debugger 'g'); cleared when hit."""
        self._temp_breakpoint = addr

    def set_step_out(self, sp: int) -> None:
        """Break (once) when SP rises above sp, i.e. the current routine returns."""
        self._stepout_sp = sp

    def _break_conditions_active(self) -> bool:
        """True when any execution-break condition needs the per-instruction loop."""
        return (
            bool(self._breakpoints)
            or self._break_halt_di
            or self._sp_range is not None
            or self._temp_breakpoint is not None
            or self._stepout_sp is not None
            # With a pause hook installed (RPC attached), always use the debug
            # loop so pause requests (breakpoints and watchpoints) can return at
            # the break point. The hot fast/logged loops stay untouched.
            or self._pause_hook is not None
        )

    def _post_step_break(self) -> bool:
        """Evaluate crash-signature break conditions after one CPU step.

        Returns True at most once per rising edge of each condition (so resuming
        from a still-true condition does not immediately re-break).
        """
        cpu = self.cpu
        if self._break_halt_di:
            if cpu.halted and not cpu.iff1:
                if not self._halt_di_seen:
                    self._halt_di_seen = True
                    return True
            else:
                self._halt_di_seen = False
        if self._sp_range is not None:
            sp = cpu.registers.SP
            if sp < self._sp_range[0] or sp > self._sp_range[1]:
                if not self._sp_out_seen:
                    self._sp_out_seen = True
                    return True
            else:
                self._sp_out_seen = False
        if self._stepout_sp is not None and cpu.registers.SP > self._stepout_sp:
            self._stepout_sp = None
            return True
        return False

    def set_watchpoints(self, entries: list[tuple[int, str]]) -> None:
        """Set watchpoints. entries: [(addr, mode), ...] where mode in {r, w, rw}. Max 4."""
        # Portability note: enabling watchpoints re-swaps cpu.read_byte/
        # write_byte between the plain memory bus and the watch variant at
        # runtime (see __post_init__). Rust/C++ selects the same behaviour via
        # an `enum { Normal, Watchpoint }` bus (or trait object) chosen once,
        # not by reassigning a function pointer per configuration change.
        r: set[int] = set()
        w: set[int] = set()
        for addr, mode in entries[:4]:
            if "r" in mode:
                r.add(addr)
            if "w" in mode:
                w.add(addr)
        self._watch_read = frozenset(r)
        self._watch_write = frozenset(w)
        if self._watch_read or self._watch_write:
            self.cpu.read_byte = self._read_with_watch
            self.cpu.write_byte = self._write_with_watch
        else:
            self.cpu.read_byte = self.memory.read
            self.cpu.write_byte = self.memory.write

    def _read_with_watch(self, addr: int) -> int:
        val = self.memory.read(addr)
        if addr in self._watch_read:
            pc = self.cpu.instruction_pc
            print(f"\n[WP] READ  {addr:04X}h = {val:02X}h  PC={pc:04X}h")
            self._enter_break("watchpoint")
        return val

    def _write_with_watch(self, addr: int, value: int) -> None:
        if addr in self._watch_write:
            pc = self.cpu.instruction_pc
            print(f"\n[WP] WRITE {addr:04X}h = {value:02X}h  PC={pc:04X}h")
            self._enter_break("watchpoint")
        self.memory.write(addr, value)

    def step(self) -> int:
        return self.cpu.step()

    def run_frame(self, skip_render: bool = False) -> bytearray:
        # Select the frame loop by mode (break conditions / hot / logger) and
        # let it run one frame; each arm owns its own Ctrl-C handling so the
        # logger arm's post-loop halt-DI check stays reachable.
        vdp9938 = self.vdp if isinstance(self.vdp, V9938) else None
        if self._break_conditions_active():
            self._run_frame_debug(vdp9938)
        elif self._logger is None:
            self._run_frame_fast(vdp9938)
        else:
            self._run_frame_logged(vdp9938)

        if vdp9938 is not None:
            result = render_frame_v9938(vdp9938, skip_render=skip_render)
        else:
            result = render_frame(self.vdp, skip_render=skip_render)
        # Frame counting is owned here (orchestration), for both VDP variants.
        self.vdp.increment_frame()
        return result

    def _on_frame_interrupt(self) -> None:
        """Ctrl-C handling shared by the frame loops: notify the pause hook or
        drop into the debugger if either is present, otherwise re-raise to
        abort the run."""
        if self._pause_hook is not None:
            self._pause_hook("user_request", self.cpu.registers.PC)
        elif self._debugger is not None:
            self._debugger.enter()
        else:
            raise

    def _run_frame_debug(self, vdp9938: V9938 | None) -> None:
        """Frame loop with active break conditions: checks breakpoints and
        post-step break conditions each instruction (debugger attached)."""
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        total = 0
        try:
            for line in range(lpf):
                line_end = (line + 1) * cpf // lpf
                while total < line_end:
                    pc = cpu.registers.PC
                    if pc == self._resume_skip_pc:
                        # First check after a resume: step past the breakpoint
                        # we are paused on without re-breaking.
                        self._resume_skip_pc = None
                    elif pc in self._breakpoints or pc == self._temp_breakpoint:
                        if pc == self._temp_breakpoint:
                            self._temp_breakpoint = None
                        self._enter_break("breakpoint")
                        if self._pause_requested:
                            return
                    if vdp9938 is not None:
                        cpu.int_pending = vdp9938.irq
                    n = cpu_step()
                    total += n
                    self.cycle_count += n
                    if vdp9938 is not None:
                        vdp9938.tick(n)
                    if self._post_step_break():
                        self._enter_break("breakpoint")
                    if self._pause_requested:
                        # A watchpoint (or post-step condition) requested a pause
                        # during this instruction; stop at the boundary.
                        return
                if vdp9938 is not None:
                    vdp9938.begin_scanline(line)
                    cpu.int_pending = vdp9938.irq
        except KeyboardInterrupt:
            self._on_frame_interrupt()

    def _run_frame_fast(self, vdp9938: V9938 | None) -> None:
        # Hot path (no debugger, no logger). Two frame-invariants are lifted
        # out of the inner loop: (1) the is_v9938 branch — split into a
        # V9938 loop and a plain loop so the per-instruction `if vdp9938 /
        # if vdp_tick` tests vanish; (2) cycle_count aggregation — summed
        # into a per-line local and flushed once per scanline instead of
        # once per instruction. Line granularity is the finest flush
        # allowed: io/dac read cycle_count *within* a frame, so a frame-end
        # flush would starve them; a one-scanline lag matches the existing
        # scanline-stepped timing. The duplicated loop body is the
        # readability cost of removing that per-instruction overhead.
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        total = 0
        try:
            if vdp9938 is not None:
                for line in range(lpf):
                    line_end = (line + 1) * cpf // lpf
                    line_cycles = 0
                    while total < line_end:
                        cpu.int_pending = vdp9938.irq
                        n = cpu_step()
                        total += n
                        line_cycles += n
                        vdp9938.tick(n)
                    self.cycle_count += line_cycles
                    vdp9938.begin_scanline(line)
                    cpu.int_pending = vdp9938.irq
            else:
                for line in range(lpf):
                    line_end = (line + 1) * cpf // lpf
                    line_cycles = 0
                    while total < line_end:
                        n = cpu_step()
                        total += n
                        line_cycles += n
                    self.cycle_count += line_cycles
        except KeyboardInterrupt:
            self._on_frame_interrupt()

    def _run_frame_logged(self, vdp9938: V9938 | None) -> None:
        """Frame loop with diagnostic logging: detects PC-loop / HALT+DI hangs."""
        assert self._logger is not None
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        total = 0
        try:
            for line in range(lpf):
                line_end = (line + 1) * cpf // lpf
                while total < line_end:
                    pc = cpu.registers.PC
                    if vdp9938 is not None:
                        cpu.int_pending = vdp9938.irq
                    n = cpu_step()
                    total += n
                    self.cycle_count += n
                    if vdp9938 is not None:
                        vdp9938.tick(n)
                    if not (cpu.halted and cpu.iff1):
                        if pc == self._last_pc:
                            self._pc_repeat += 1
                            if self._pc_repeat >= HANG_PC_REPEAT_THRESHOLD:
                                self._logger.on_hang_pc_loop(pc)
                        else:
                            self._pc_repeat = 0
                        self._last_pc = pc
                if vdp9938 is not None:
                    vdp9938.begin_scanline(line)
                    cpu.int_pending = vdp9938.irq
        except KeyboardInterrupt:
            self._on_frame_interrupt()

        if cpu.halted and not cpu.iff1:
            self._logger.on_hang_halt_di(cpu.registers.PC)


