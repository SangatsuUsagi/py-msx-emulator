from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger
    from msx.vdp.tracer import Tracer


@dataclass
class VDP:
    vram: bytearray = field(default_factory=lambda: bytearray(0x4000))
    regs: list[int] = field(default_factory=lambda: [0] * 8)
    addr: int = 0
    latch: int | None = None
    status: int = 0
    read_buf: int = 0
    on_interrupt: Callable[[], None] | None = None
    _logger: DebugLogger | None = field(default=None, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    tracer: Tracer | None = field(default=None, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, repr=False)
    debug_disable_sprites: bool = field(default=False, repr=False)

    @property
    def display_height(self) -> int:
        return 192

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0x98:
            self.vram[self.addr] = value
            self.addr = (self.addr + 1) & 0x3FFF
        elif port == 0x99:
            if self.tracer is not None:
                pc = self._get_pc() if self._get_pc is not None else 0
                cy = self._get_cycle() if self._get_cycle is not None else 0
                fr = self._get_frame() if self._get_frame is not None else 0
                self.tracer.port99_write(pc, cy, value, frame=fr)
            if self.latch is None:
                self.latch = value
            else:
                low = self.latch
                self.latch = None
                if value & 0x80:
                    # Register write: second byte = 0x80 | reg_num
                    reg = value & 0x07
                    self.regs[reg] = low
                    if self._logger is not None:
                        self._logger.on_vdp_reg_write(reg, low, self._frame_count)
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
