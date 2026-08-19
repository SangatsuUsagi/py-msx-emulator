from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from msx.cpu import opcodes_main as _opcodes_main
from msx.cpu.registers import Registers

if TYPE_CHECKING:
    from msx.diagnostics.logger import DebugLogger


# PORT-NOTE: this module-level binding aliases opcodes_main._DISPATCH so the
#   hot fetch path does a single indexed call instead of the two-stage
#   _execute -> execute -> _DISPATCH lookup a naive translation would produce.
#   opcodes_main references Z80 only for type checking (TYPE_CHECKING-guarded),
#   so this top-level import does not create a runtime import cycle; the list
#   is populated in place by opcodes_main._build_dispatch() at import, so this
#   reference stays valid for the module's lifetime.
# Rust equivalent: no separate alias needed — call the dispatch function/table
#   directly from the fetch loop; module-level `static`/`const` tables are
#   already directly nameable without an indirection layer.
# C++ equivalent: same — reference the dispatch table directly, or hold a
#   pointer/reference to it as a member if ownership needs to be explicit.
# Kept as-is here because: avoids one extra Python attribute lookup per
#   instruction fetch; this indirection exists only to work around Python's
#   two-hop module-attribute-then-call cost, which Rust/C++ don't have.
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH


def _noop_read(_port: int) -> int:
    return 0xFF


def _noop_write(_port: int, _value: int) -> None:
    pass


@dataclass(slots=True)
class Z80:
    # PORT-NOTE: these bus hooks are stored as Python closures (bound methods
    #   assigned at wiring time by Machine.__post_init__).
    # Rust equivalent: a trait object (`Box<dyn Bus>`) or a feature-flagged
    #   field resolved once at construction, not a runtime method swap.
    # C++ equivalent: a virtual `Bus` interface pointer, or a `std::function`
    #   assigned once at construction — Rust/C++ have no equivalent of
    #   reassigning an instance's bound method at runtime.
    # Kept as-is here because: read on every memory/port access (the hottest
    #   accessor pattern after opcode dispatch itself); CPython's bound-method
    #   call here is already close to the cheapest indirection Python offers,
    #   and resolving it once at construction (as Machine.__post_init__ does)
    #   keeps the per-access cost to a single call with no branch.
    read_byte: Callable[[int], int]
    write_byte: Callable[[int, int], None]
    read_port: Callable[[int], int] = field(default=_noop_read)
    write_port: Callable[[int, int], None] = field(default=_noop_write)
    registers: Registers = field(default_factory=Registers)
    halted: bool = False
    iff1: bool = False
    iff2: bool = False
    im: int = 0
    int_pending: bool = False
    nmi_pending: bool = False
    ei_pending: bool = False
    instruction_pc: int = 0
    # Extra T-states added per M1 (opcode fetch). 0 = pure datasheet Z80 (keeps
    # the core reusable for non-MSX systems); MSX machines configure 1 (the MSX
    # M1 wait state, matching openMSX Z80 WAIT_CYCLES=1). Supplied at construction
    # from the machine config.
    m1_wait_states: int = 0
    _logger: DebugLogger | None = field(default=None, repr=False)

    def reset(self) -> None:
        self.registers.reset()
        self.halted = False
        self.iff1 = False
        self.iff2 = False
        self.im = 0
        self.int_pending = False
        self.nmi_pending = False
        self.ei_pending = False

    def _fetch(self) -> int:
        b = self.read_byte(self.registers.PC)
        self.registers.PC = (self.registers.PC + 1) & 0xFFFF
        # R is a 7-bit refresh counter: only bits 0-6 increment. On real
        # hardware bit 7 is preserved (sticky) across increments; here we mask
        # it to 0x7F, so bit 7 reads as 0. A port replicating exact R behaviour
        # must keep bit 7 separately rather than masking it away.
        self.registers.R = (self.registers.R + 1) & 0x7F
        return b

    def _fetch_word(self) -> int:
        lo = self._fetch()
        hi = self._fetch()
        return (hi << 8) | lo

    def _push(self, value: int) -> None:
        r = self.registers
        r.SP = (r.SP - 1) & 0xFFFF
        self.write_byte(r.SP, (value >> 8) & 0xFF)
        r.SP = (r.SP - 1) & 0xFFFF
        self.write_byte(r.SP, value & 0xFF)

    def _pop(self) -> int:
        r = self.registers
        lo = self.read_byte(r.SP)
        r.SP = (r.SP + 1) & 0xFFFF
        hi = self.read_byte(r.SP)
        r.SP = (r.SP + 1) & 0xFFFF
        return (hi << 8) | lo

    def step(self) -> int:
        if self.nmi_pending:
            self.nmi_pending = False
            self.halted = False
            # NMI saves the pre-NMI interrupt-enable state so RETN (IFF1<-IFF2)
            # can restore it; only IFF1 is cleared during the handler.
            self.iff2 = self.iff1
            self.iff1 = False
            self._push(self.registers.PC)
            self.registers.PC = 0x0066
            return 11

        # EI enables interrupts only *after* the instruction following it, so an
        # interrupt accepted here is suppressed for exactly one instruction when
        # ei_pending is set. ei_pending is cleared below so the delay lasts a
        # single instruction (e.g. the RET in C-BIOS's EI;RET interrupt epilogue
        # must run before the next interrupt is taken).
        if self.int_pending and self.iff1 and not self.ei_pending:
            self.int_pending = False
            self.halted = False
            self.iff1 = False
            self.iff2 = False
            if self.im == 0 or self.im == 1:
                # IM 0: MSX always places 0xFF (RST 38H) on the data bus
                self._push(self.registers.PC)
                self.registers.PC = 0x0038
                return 13
            if self.im == 2:
                vec_addr = (self.registers.I << 8) | 0xFF
                lo = self.read_byte(vec_addr)
                hi = self.read_byte((vec_addr + 1) & 0xFFFF)
                self._push(self.registers.PC)
                self.registers.PC = (hi << 8) | lo
                return 19

        # One instruction has now elapsed since EI (this step); allow the next
        # interrupt check to fire normally.
        self.ei_pending = False

        if self.halted:
            # A halted CPU keeps executing internal NOPs, each an M1 cycle.
            return 4 + self.m1_wait_states

        r = self.registers
        pc = r.PC
        self.instruction_pc = pc
        opcode = self.read_byte(pc)
        r.PC = (pc + 1) & 0xFFFF
        r.R = (r.R + 1) & 0x7F
        if self._logger is not None:
            self._logger.on_step(pc, opcode)
        # + the M1 wait for this opcode fetch. Prefixed instructions add one more
        # per prefix byte inside their prefix handlers (each is its own M1).
        return _DISPATCH[opcode](self) + self.m1_wait_states
