from __future__ import annotations

from dataclasses import dataclass, field

from msx.input import InputState

_REG_IO_PORT_A = 14


@dataclass
class PSG:
    regs: list[int] = field(default_factory=lambda: [0] * 16)
    latch: int = 0
    _input: InputState | None = field(default=None, repr=False)

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA0:
            self.latch = value & 0x0F
        elif port == 0xA1:
            self.regs[self.latch] = value

    def read_port(self, port: int) -> int:
        if port == 0xA2:
            if self.latch == _REG_IO_PORT_A and self._input is not None:
                return self._input.joystick
            return self.regs[self.latch]
        return 0xFF
