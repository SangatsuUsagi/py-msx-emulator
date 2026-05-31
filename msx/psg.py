from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PSG:
    regs: list[int] = field(default_factory=lambda: [0] * 16)
    latch: int = 0

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA0:
            self.latch = value & 0x0F
        elif port == 0xA1:
            self.regs[self.latch] = value

    def read_port(self, port: int) -> int:
        if port == 0xA2:
            return self.regs[self.latch]
        return 0xFF
