from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class VDP:
    vram: bytearray = field(default_factory=lambda: bytearray(0x4000))
    regs: list[int] = field(default_factory=lambda: [0] * 8)
    addr: int = 0
    latch: int | None = None
    status: int = 0
    read_buf: int = 0
    on_interrupt: Callable[[], None] | None = None

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0x98:
            self.vram[self.addr] = value
            self.addr = (self.addr + 1) & 0x3FFF
        elif port == 0x99:
            if self.latch is None:
                self.latch = value
            else:
                low = self.latch
                self.latch = None
                if value & 0x80:
                    # Register write: second byte = 0x80 | reg_num
                    self.regs[value & 0x07] = low
                else:
                    # VRAM address setup
                    self.addr = ((value & 0x3F) << 8) | low
                    if not (value & 0x40):
                        # Read mode: preload read-ahead buffer
                        self.read_buf = self.vram[self.addr]
                        self.addr = (self.addr + 1) & 0x3FFF

    def read_port(self, port: int) -> int:
        if port == 0x98:
            result = self.read_buf
            self.read_buf = self.vram[self.addr]
            self.addr = (self.addr + 1) & 0x3FFF
            return result
        if port == 0x99:
            result = self.status
            self.status &= ~0xC0  # clear VBlank (bit 7) and 5th-sprite flag (bit 6)
            self.latch = None     # reading status resets the address latch
            return result & 0xFF
        return 0xFF
