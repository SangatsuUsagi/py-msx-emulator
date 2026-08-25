from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.input import InputState
from msx.io import IOBus
from msx.mapper import MajutsushiMapper, SCCICart
from msx.memory import Memory
from msx.mouse import MouseDevice
from msx.psg import PSG, JoystickPort
from msx.scc import SCC
from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
from msx.vdp.vdp import VDP, VdpDevice

if TYPE_CHECKING:
    from msx.debugger.prompt import Debugger
    from msx.fdc.interface import FloppyDisk
    from msx.fmpac import FmPac

# NTSC: 3.579545 MHz / 60 Hz ≈ 59,659 T-states per frame
CYCLES_PER_FRAME: int = 59_659
LINES_PER_FRAME: int = 262
HANG_PC_REPEAT_THRESHOLD: int = 1000

# MSX1 (SCREEN 0-3) visible resolution, used for screenshots.
SCREEN_WIDTH: int = 256
SCREEN_HEIGHT: int = 192


class PauseReason(str, enum.Enum):
    """Why the emulator paused — passed to the pause hook, and the single source
    of truth shared with the RPC adapter (msx/rpc_server.py imports it).

    Subclasses `str` so each member serializes to its wire value (e.g.
    ``"breakpoint"``) and compares equal to that string, keeping the JSON-RPC
    contract unchanged.

    PORT-NOTE: this is a closed string-tagged enum, used both as a Python enum
      and as its wire string.
    Rust equivalent: `enum PauseReason { UserRequest, Breakpoint, Watchpoint,
      StepComplete }` with `#[serde(rename_all = "snake_case")]` (or explicit
      `#[serde(rename = "...")]` per variant) so serde produces the same wire
      strings.
    C++ equivalent: `enum class PauseReason` plus an explicit to/from-string
      table at the JSON-RPC boundary, since C++ enums don't serialize to
      strings on their own.
    Kept as-is here because: this maps 1:1 to a closed target-language enum
      with no translation ambiguity -- noted for completeness, not a spot
      needing a Python-side change.
    """

    USER_REQUEST = "user_request"
    BREAKPOINT = "breakpoint"
    WATCHPOINT = "watchpoint"
    STEP_COMPLETE = "step_complete"


@dataclass
class Machine:
    cpu: Z80
    # VDP and V9938 are unrelated classes sharing this structural contract by
    # convention, not inheritance -- see VdpDevice's own docstring.
    vdp: VdpDevice
    memory: Memory
    io: IOBus
    psg: PSG
    scc: SCC | None = field(default=None)
    dac: MajutsushiMapper | None = field(default=None)
    fdc: "FloppyDisk | None" = field(default=None)
    fmpac: "FmPac | None" = field(default=None, repr=False)
    input: InputState = field(default_factory=InputState)
    cycles_per_frame: int = CYCLES_PER_FRAME
    lines_per_frame: int = LINES_PER_FRAME
    # Free-running T-state clock, never reset.
    #
    # PORT-NOTE: at 3.58 MHz a u32 wraps in ~20 minutes of emulated run time.
    # Rust/C++ equivalent: u64/uint64_t for this field; the per-frame totals
    #   threaded through _run_lines() are frame-relative and fit in u32.
    # Kept as-is here because: semantic necessity, not performance -- Python's
    #   arbitrary-precision int never wraps, so this width requirement is
    #   invisible in Python and must be sized explicitly at port time.
    cycle_count: int = 0
    sram_save_path: "Path | None" = field(default=None, repr=False)
    fmpac_sram_save_path: "Path | None" = field(default=None, repr=False)
    _logger: DebugLogger | None = field(default=None, repr=False)
    _debugger: Debugger | None = field(default=None, repr=False)
    # Optional programmatic pause sink (e.g. the RPC server). When set, break
    # events call it with (reason, pc) instead of entering the interactive REPL.
    _pause_hook: Callable[[PauseReason, int], None] | None = field(
        default=None, init=False, repr=False
    )
    # Async pause plumbing used when a pause hook is installed: a break sets
    # _pause_requested so the debug frame loop returns at the break point, and
    # resume records _resume_skip_pc so continue does not immediately re-break
    # the instruction we are paused on.
    _pause_requested: bool = field(default=False, init=False, repr=False)
    _resume_skip_pc: int | None = field(default=None, init=False, repr=False)
    # Set for the rest of the current run_frame() when a scanline loop was cut
    # short by Ctrl-C. Ctrl-C arrives as an exception rather than through
    # _enter_break, so it does not raise _pause_requested; this frame-scoped
    # flag is what stops run_frame() from running the remaining segments.
    _frame_interrupted: bool = field(default=False, init=False, repr=False)
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
    # One-shot run-to-frame breakpoint (debugger 'gf'); checked once per
    # run_frame() call, not per instruction — see set_frame_breakpoint.
    _frame_breakpoint: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # PORT-NOTE: this wires the CPU's memory/IO bus by reassigning bound
        #   methods onto Callable fields at runtime -- the hottest path in
        #   the emulator (see also set_watchpoints below, which re-swaps the
        #   same fields).
        # Rust equivalent: a `trait MemoryBus` (or an `enum { Normal,
        #   Watchpoint }`) whose concrete implementation is selected once
        #   behind a flag at construction, not reassigned at runtime.
        # C++ equivalent: same -- a virtual `MemoryBus` interface or a
        #   feature-flagged concrete type chosen once, not a runtime method
        #   swap (C++ has no equivalent of rebinding an instance method).
        # Kept as-is here because: read/written on every CPU memory/IO access;
        #   resolving the bus once (here and in set_watchpoints) keeps the
        #   per-access dispatch a single bound-method call with no added
        #   branch, which a per-access abstraction layer would cost in Python.
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
            for mapper in (self.memory._mapper, self.memory._mapper2):
                if isinstance(mapper, SCCICart):
                    mapper.resync_scc_mode()
        if self.fmpac is not None:
            self.fmpac.reset()
        self.vdp.reset()
        if self.fdc is not None:
            self.fdc.reset()
        # Power-on slot state: all pages select slot 0 (matches construction).
        self.memory.set_slot_register(0x00)
        self.memory.set_sub_slot_reg(0x00)
        if self.memory.ram_mapper is not None:
            self.memory.ram_mapper.reset()

    def set_pause_hook(self, hook: Callable[[PauseReason, int], None] | None) -> None:
        """Install (or clear) a programmatic pause sink.

        When set, break events (breakpoints, watchpoints, Ctrl-C, and the
        crash-signature conditions) invoke `hook(reason, pc)` instead of
        entering the blocking interactive debugger REPL. `reason` is a
        `PauseReason` member.

        PORT-NOTE: this is the *only* seam the core exposes to the RPC adapter
          (msx/rpc_server.py). The core has no knowledge of sockets, JSON, or
          threads -- it just calls this generic callback.
        Rust equivalent: a `trait PauseSink` (with `reason: PauseReason`), so
          the RPC layer can live in a separate, feature-gated crate.
        C++ equivalent: a `PauseSink` abstract interface, injected the same
          way, so the core stays free of socket/JSON/threading dependencies.
        Kept as-is here because: this is already the target shape (a generic
          callback seam), not a Python-specific pattern needing translation
          -- noted for completeness, and see the crate-split note in
          msx/rpc_server.py for how the RPC layer sits behind it.
        """
        self._pause_hook = hook

    def attach_mouse(self, device: MouseDevice, port: JoystickPort) -> None:
        """Attach an MSX mouse to the given joystick port for this machine's PSG.

        Delegates to `PSG.attach_mouse` rather than setting `self.psg._mouse`
        directly, so callers outside this package (e.g. the SDL2 frontend)
        never reach into `PSG`'s internals.
        """
        self.psg.attach_mouse(device, port)

    def enter_debugger_if_attached(self) -> bool:
        """Break into the attached interactive debugger REPL, if one is attached.

        Public accessor for `_debugger`, for callers outside this module (e.g.
        the SDL2 frontend's own Ctrl-C/debugger hotkey) that need to enter the
        debugger directly rather than going through `_enter_break`'s pause-hook
        routing. Returns True if a debugger was attached and entered, False
        otherwise.
        """
        if self._debugger is not None:
            self._debugger.enter()
            return True
        return False

    def _enter_break(self, reason: PauseReason) -> None:
        """Dispatch a break event: notify the pause hook if one is installed,
        otherwise fall back to the interactive debugger REPL when attached.

        In hook mode this also raises _pause_requested so the debug frame loop
        returns at the break point instead of running on to the frame end."""
        if self._pause_hook is not None:
            self._pause_requested = True
            self._pause_hook(reason, self.cpu.registers.PC)
        elif self._debugger is not None:
            self._debugger.enter()

    @property
    def is_paused(self) -> bool:
        """True while a break has requested a pause and no resume has been armed.

        Public read-only view of the pause flag, so callers (RPC, tests) do not
        reach into private state — a port would not expose the field itself."""
        return self._pause_requested

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

    def set_frame_breakpoint(self, frame: int | None) -> None:
        """Set a one-shot run-to-frame breakpoint (debugger 'gf'); cleared when hit."""
        self._frame_breakpoint = frame

    def _break_conditions_active(self) -> bool:
        """True when any execution-break condition needs the per-instruction loop."""
        return (
            bool(self._breakpoints)
            or self._break_halt_di
            or self._sp_range is not None
            or self._temp_breakpoint is not None
            or self._stepout_sp is not None
            # Watchpoints need the per-instruction loop so a hit can return at the
            # break boundary (via the pause hook) instead of running to frame end.
            # NOTE: mere RPC attachment does NOT force the debug loop — a bare
            # attached "continue" runs at fast-loop speed; only real break
            # conditions (breakpoints/watchpoints) route here. An async
            # debugger.pause is handled at frame granularity by the frontend.
            or bool(self._watch_read)
            or bool(self._watch_write)
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

    def _post_step_checks_armed(self) -> bool:
        """True when any condition _post_step_break() tests is armed.

        Lifted out of the per-instruction debug loop: with only breakpoints or
        watchpoints set (the common case) none of these are armed, so the call
        to _post_step_break() can be skipped entirely. Must be re-evaluated
        after every _enter_break(), which can hand control to the REPL where
        bh/bs/so arm them mid-loop."""
        return (
            self._break_halt_di
            or self._sp_range is not None
            or self._stepout_sp is not None
        )

    def set_watchpoints(self, entries: list[tuple[int, str]]) -> None:
        """Set watchpoints. entries: [(addr, mode), ...] where mode in {r, w, rw}. Max 4."""
        # PORT-NOTE: enabling watchpoints re-swaps cpu.read_byte/write_byte
        #   between the plain memory bus and the watch variant at runtime --
        #   same shape as __post_init__'s initial wiring above.
        # Rust equivalent: an `enum { Normal, Watchpoint }` bus (or trait
        #   object) chosen once per configuration change, not a reassigned
        #   function pointer.
        # C++ equivalent: same -- select a concrete `MemoryBus` implementation
        #   once per configuration change, not a runtime method swap.
        # Kept as-is here because: same hot-path reasoning as __post_init__'s
        #   bus wiring -- resolving once here keeps every memory access a
        #   single bound-method call with no added branch.
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
            self._enter_break(PauseReason.WATCHPOINT)
        return val

    def _write_with_watch(self, addr: int, value: int) -> None:
        if addr in self._watch_write:
            pc = self.cpu.instruction_pc
            print(f"\n[WP] WRITE {addr:04X}h = {value:02X}h  PC={pc:04X}h")
            self._enter_break(PauseReason.WATCHPOINT)
        self.memory.write(addr, value)

    def step(self) -> int:
        return self.cpu.step()

    def run_frame(self, skip_render: bool = False) -> bytearray:
        """Run one frame and return the rendered index framebuffer.

        Returns a `display_width * OUTPUT_H` buffer, or an empty bytearray when
        `skip_render` is set. Advances the VDP frame counter either way. A break
        (breakpoint, watchpoint, Ctrl-C) drops the rest of the frame: the frame
        is still rendered and counted, and the next call restarts at line 0.

        A V9938 frame is rendered at the start of vertical blanking, not at
        the end of the scanline loop: begin_scanline(vblank_start_line) raises
        the VBlank IRQ, so rendering after the whole loop would show the VRAM
        the game's VBlank ISR left behind — sprite attributes and name tables
        one frame early, while the display registers correctly come from the
        frame-start snapshot. Splitting the loop there renders the frame the
        ISR has not touched yet, and lets the renderer's sprite-collision /
        5S scan finish before the ISR reads S#0.
        The TMS9918A has no per-scanline hook and raises its frame-end IRQ
        from render_frame() itself, so its ISR already runs after the render.
        """
        self._frame_interrupted = False
        lpf = self.lines_per_frame
        vdp9938 = self.vdp if isinstance(self.vdp, V9938) else None
        # The scanline loops call begin_scanline(line) *after* running that
        # line's cycles, so the pre-render segment is the half-open range
        # [0, vblank_start_line + 1): its last iteration is the one whose
        # begin_scanline() raises the VBlank F flag. min() guards a
        # lines_per_frame shorter than the display (synthetic timings).
        vblank_split = (
            min(vdp9938.vblank_start_line + 1, lpf) if vdp9938 is not None else lpf
        )

        total = self._run_lines(vdp9938, first_line=0, end_line=vblank_split, total=0)
        if vdp9938 is not None:
            result = render_frame_v9938(vdp9938, skip_render=skip_render)
            # The VBlank ISR window. Skipped when the first segment stopped
            # short — a break via the pause hook (_pause_requested) or a Ctrl-C
            # handled by _on_frame_interrupt (_frame_interrupted) — so a paused
            # frame does not run on past the break point.
            if (
                vblank_split < lpf
                and not self._pause_requested
                and not self._frame_interrupted
            ):
                self._run_lines(
                    vdp9938, first_line=vblank_split, end_line=lpf, total=total
                )
        else:
            # vdp9938 is None here only because the isinstance(self.vdp, V9938)
            # check above was False, and self.vdp hasn't been reassigned since.
            assert isinstance(self.vdp, VDP)
            result = render_frame(self.vdp, skip_render=skip_render)
        # Frame counting is owned here (orchestration), for both VDP variants.
        self.vdp.increment_frame()
        # Frame breakpoint (debugger 'gf'): checked once per frame, after the
        # frame is fully rendered and counted, so it never forces the slower
        # per-instruction debug loop the way a PC breakpoint does.
        if self._frame_breakpoint is not None and self.vdp.frame_count == self._frame_breakpoint:
            self._frame_breakpoint = None
            self._enter_break(PauseReason.BREAKPOINT)
        return result

    def _on_frame_interrupt(self) -> None:
        """Ctrl-C handling shared by the frame loops: notify the pause hook or
        drop into the debugger if either is present, otherwise re-raise to
        abort the run.

        Flags the frame as cut short so run_frame() skips the segments after
        the one that was interrupted."""
        self._frame_interrupted = True
        if self._pause_hook is not None:
            self._pause_hook(PauseReason.USER_REQUEST, self.cpu.registers.PC)
        elif self._debugger is not None:
            self._debugger.enter()
        else:
            raise

    def _run_lines(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Run scanlines [first_line, end_line) with the loop arm this mode
        needs (break conditions / hot / logger); return the running T-state
        total, measured from the start of the frame.

        PORT-NOTE: the arms take `self` and `vdp9938` -- which aliases
          `self.vdp` -- at the same time, and alternate between mutating the
          VDP and mutating the Machine.
        Rust equivalent: drop the `vdp9938` parameter and dispatch on a
          `VdpKind` tag inside each arm instead, or split Machine into an
          owning bus struct plus debugger state so the two borrows are
          disjoint -- Rust's borrow checker rejects this double mutable
          borrow as written.
        C++ equivalent: no borrow-checker issue, but the same aliasing is
          worth avoiding for clarity -- prefer a `VdpKind` tag dispatch or
          the same struct split, since two mutable references/pointers to
          overlapping state is a well-known source of aliasing bugs there too.
        Kept as-is here because: semantic necessity, not performance -- this
          is a translation hazard specific to Rust's ownership model, not a
          Python optimization to preserve.
        """
        if self._break_conditions_active():
            return self._run_lines_debug(vdp9938, first_line, end_line, total)
        if self._logger is None:
            return self._run_lines_fast(vdp9938, first_line, end_line, total)
        return self._run_lines_logged(vdp9938, first_line, end_line, total)

    def _run_lines_debug(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop with active break conditions: checks breakpoints and
        post-step break conditions each instruction (debugger attached)."""
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        # Two loop-invariants lifted out of the inner loop: the post-step
        # condition test (see _post_step_checks_armed) and, as in the fast arm,
        # cycle_count aggregation into a per-line local flushed once a scanline.
        post_checks = self._post_step_checks_armed()
        line_cycles = 0
        try:
            for line in range(first_line, end_line):
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
                        self._enter_break(PauseReason.BREAKPOINT)
                        # The REPL may have armed bh/bs/so while it had control.
                        post_checks = self._post_step_checks_armed()
                        if self._pause_requested:
                            self.cycle_count += line_cycles
                            return total
                    if vdp9938 is not None:
                        cpu.int_pending = vdp9938.irq
                    n = cpu_step()
                    total += n
                    line_cycles += n
                    if vdp9938 is not None:
                        vdp9938.tick(n)
                    if post_checks and self._post_step_break():
                        self._enter_break(PauseReason.BREAKPOINT)
                        post_checks = self._post_step_checks_armed()
                    if self._pause_requested:
                        # A watchpoint (or post-step condition) requested a pause
                        # during this instruction; stop at the boundary.
                        self.cycle_count += line_cycles
                        return total
                self.cycle_count += line_cycles
                line_cycles = 0
                if vdp9938 is not None:
                    vdp9938.begin_scanline(line)
                    cpu.int_pending = vdp9938.irq
        except KeyboardInterrupt:
            self.cycle_count += line_cycles
            self._on_frame_interrupt()
        return total

    def _run_lines_fast(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Hot-path scanline loop (no debugger, no logger); see `_run_lines`."""
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
        # PORT-NOTE: Ctrl-C here is delivered as a Python KeyboardInterrupt
        #   exception, caught around the whole scanline loop with zero
        #   steady-state cost (no exception raised = no cost in CPython).
        #   Same shape in _run_lines_debug and _run_lines_logged.
        # Rust equivalent: install a SIGINT handler that sets an AtomicBool;
        #   check it once per scanline (not per instruction) alongside the
        #   existing per-line break-condition checks.
        # C++ equivalent: std::signal(SIGINT, ...) setting a
        #   std::atomic<bool>, polled the same way.
        # Kept as-is here because: the try/except costs nothing per
        #   instruction in CPython, and rewriting to a polled flag now would
        #   add a branch to this fast-path loop for no Python-side benefit.
        try:
            if vdp9938 is not None:
                for line in range(first_line, end_line):
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
                for line in range(first_line, end_line):
                    line_end = (line + 1) * cpf // lpf
                    line_cycles = 0
                    while total < line_end:
                        n = cpu_step()
                        total += n
                        line_cycles += n
                    self.cycle_count += line_cycles
        except KeyboardInterrupt:
            self._on_frame_interrupt()
        return total

    def _run_lines_logged(
        self, vdp9938: V9938 | None, first_line: int, end_line: int, total: int
    ) -> int:
        """Scanline loop with diagnostic logging: detects PC-loop / HALT+DI hangs."""
        assert self._logger is not None
        cpu = self.cpu
        cpu_step = cpu.step
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        try:
            for line in range(first_line, end_line):
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

        # Once per frame, not once per segment: only the segment that ends the
        # frame reports a HALT-with-interrupts-off hang.
        is_final_segment = end_line >= lpf
        if is_final_segment and cpu.halted and not cpu.iff1:
            self._logger.on_hang_halt_di(cpu.registers.PC)
        return total


