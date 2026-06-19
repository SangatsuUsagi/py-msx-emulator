from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from msx.cpu.registers import Registers

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger


_execute: Callable[[Z80, int], int] | None = None


def _noop_read(_port: int) -> int:
    return 0xFF


def _noop_write(_port: int, _value: int) -> None:
    pass


@dataclass(slots=True)
class Z80:
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
    instruction_pc: int = 0
    _logger: DebugLogger | None = field(default=None, repr=False)

    def reset(self) -> None:
        self.registers.reset()
        self.halted = False
        self.iff1 = False
        self.iff2 = False
        self.im = 0
        self.int_pending = False
        self.nmi_pending = False

    def _fetch(self) -> int:
        b = self.read_byte(self.registers.PC)
        self.registers.PC = (self.registers.PC + 1) & 0xFFFF
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
        global _execute
        if _execute is None:
            from msx.cpu import opcodes_main
            _execute = opcodes_main.execute
        execute = _execute

        if self.nmi_pending:
            self.nmi_pending = False
            self.halted = False
            self.iff1 = False
            self._push(self.registers.PC)
            self.registers.PC = 0x0066
            return 11

        if self.int_pending and self.iff1:
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

        if self.halted:
            return 4

        r = self.registers
        pc = r.PC
        self.instruction_pc = pc
        opcode = self.read_byte(pc)
        r.PC = (pc + 1) & 0xFFFF
        r.R = (r.R + 1) & 0x7F
        if self._logger is not None:
            self._logger.on_step(pc, opcode)
        return execute(self, opcode)
