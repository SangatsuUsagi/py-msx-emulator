from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from msx.cpu.z80 import Z80
from msx.debug.logger import DebugLogger
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

# NTSC: 3.579545 MHz / 60 Hz ≈ 59,659 T-states per frame
CYCLES_PER_FRAME: int = 59_659
LINES_PER_FRAME: int = 262
TSTATES_PER_LINE: int = CYCLES_PER_FRAME // LINES_PER_FRAME  # 227; carry remainder across lines
HANG_PC_REPEAT_THRESHOLD: int = 1000


@dataclass
class Machine:
    cpu: Z80
    vdp: VDP
    memory: Memory
    io: IOBus
    psg: PSG
    scc: SCC | None = field(default=None)
    dac: MajutsushiMapper | None = field(default=None)
    input: InputState = field(default_factory=InputState)
    cycles_per_frame: int = CYCLES_PER_FRAME
    lines_per_frame: int = LINES_PER_FRAME
    cycle_count: int = 0
    _logger: DebugLogger | None = field(default=None, repr=False)
    _debugger: Debugger | None = field(default=None, repr=False)
    _breakpoints: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_read: frozenset[int] = field(default_factory=frozenset, repr=False)
    _watch_write: frozenset[int] = field(default_factory=frozenset, repr=False)
    _last_pc: int = field(default=0, init=False, repr=False)
    _pc_repeat: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cpu.read_byte = self.memory.read
        self.cpu.write_byte = self.memory.write
        self.cpu.read_port = self.io.read_port
        self.cpu.write_port = self.io.write_port
        if not isinstance(self.vdp, V9938):
            self.vdp.on_interrupt = self._vblank_interrupt

    def _vblank_interrupt(self) -> None:
        self.cpu.int_pending = True

    def reset(self) -> None:
        self.cpu.reset()
        self.vdp.status = 0

    def set_breakpoints(self, addrs: list[int]) -> None:
        """Set breakpoint addresses (max 4). Replaces existing set."""
        self._breakpoints = frozenset(addrs[:4])

    def set_watchpoints(self, entries: list[tuple[int, str]]) -> None:
        """Set watchpoints. entries: [(addr, mode), ...] where mode in {r, w, rw}. Max 4."""
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
            if self._debugger is not None:
                self._debugger.enter()
        return val

    def _write_with_watch(self, addr: int, value: int) -> None:
        if addr in self._watch_write:
            pc = self.cpu.instruction_pc
            print(f"\n[WP] WRITE {addr:04X}h = {value:02X}h  PC={pc:04X}h")
            if self._debugger is not None:
                self._debugger.enter()
        self.memory.write(addr, value)

    def step(self) -> int:
        return self.cpu.step()

    def run_frame(self, skip_render: bool = False) -> bytearray:
        cpu_step = self.cpu.step
        is_v9938 = isinstance(self.vdp, V9938)
        vdp_tick = self.vdp.tick if is_v9938 else None
        vdp9938 = self.vdp if is_v9938 else None
        cpu = self.cpu
        cpf = self.cycles_per_frame
        lpf = self.lines_per_frame
        total = 0

        if self._breakpoints:
            try:
                for L in range(lpf):
                    line_end = (L + 1) * cpf // lpf
                    while total < line_end:
                        if cpu.registers.PC in self._breakpoints:
                            if self._debugger is not None:
                                self._debugger.enter()
                        if vdp9938:
                            cpu.int_pending = vdp9938._irq
                        n = cpu_step()
                        total += n
                        self.cycle_count += n
                        if vdp_tick:
                            vdp_tick(n)
                    if vdp9938:
                        vdp9938.begin_scanline(L)
                        cpu.int_pending = vdp9938._irq
            except KeyboardInterrupt:
                if self._debugger is not None:
                    self._debugger.enter()
                else:
                    raise
        elif self._logger is None:
            try:
                for L in range(lpf):
                    line_end = (L + 1) * cpf // lpf
                    while total < line_end:
                        if vdp9938:
                            cpu.int_pending = vdp9938._irq
                        n = cpu_step()
                        total += n
                        self.cycle_count += n
                        if vdp_tick:
                            vdp_tick(n)
                    if vdp9938:
                        vdp9938.begin_scanline(L)
                        cpu.int_pending = vdp9938._irq
            except KeyboardInterrupt:
                if self._debugger is not None:
                    self._debugger.enter()
                else:
                    raise
        else:
            try:
                for L in range(lpf):
                    line_end = (L + 1) * cpf // lpf
                    while total < line_end:
                        pc = cpu.registers.PC
                        if vdp9938:
                            cpu.int_pending = vdp9938._irq
                        n = cpu_step()
                        total += n
                        self.cycle_count += n
                        if vdp_tick:
                            vdp_tick(n)
                        if not (cpu.halted and cpu.iff1):
                            if pc == self._last_pc:
                                self._pc_repeat += 1
                                if self._pc_repeat >= HANG_PC_REPEAT_THRESHOLD:
                                    self._logger.on_hang_pc_loop(pc)
                            else:
                                self._pc_repeat = 0
                            self._last_pc = pc
                    if vdp9938:
                        vdp9938.begin_scanline(L)
                        cpu.int_pending = vdp9938._irq
            except KeyboardInterrupt:
                if self._debugger is not None:
                    self._debugger.enter()
                else:
                    raise

            if cpu.halted and not cpu.iff1:
                self._logger.on_hang_halt_di(cpu.registers.PC)

        if is_v9938:
            result = render_frame_v9938(self.vdp, skip_render=skip_render)
        else:
            result = render_frame(self.vdp, skip_render=skip_render)
            self.vdp._frame_count += 1
        return result


