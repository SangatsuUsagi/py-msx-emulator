"""V9938 VDP for MSX2.

128 KB VRAM, 28 control registers, 16-colour programmable palette.
Ports 0x98–0x9B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

_VRAM_SIZE = 131072  # 128 KB
_NUM_REGS = 28

# TMS9918A-compatible initial palette, 9-bit packed as (R<<6)|(G<<3)|B.
_TMS_PALETTE: tuple[int, ...] = (
    0b000_000_000,  # 0  transparent → black
    0b000_000_000,  # 1  black
    0b001_110_001,  # 2  medium green
    0b011_111_011,  # 3  light green
    0b001_001_111,  # 4  dark blue
    0b010_010_111,  # 5  light blue
    0b101_001_001,  # 6  dark red
    0b010_111_111,  # 7  cyan
    0b111_010_010,  # 8  medium red
    0b111_100_100,  # 9  light red
    0b110_110_001,  # 10 dark yellow
    0b111_111_100,  # 11 light yellow
    0b001_101_001,  # 12 dark green
    0b110_010_101,  # 13 magenta
    0b101_101_101,  # 14 gray
    0b111_111_111,  # 15 white
)


@dataclass
class V9938:
    """V9938 VDP for MSX2: 128 KB VRAM, 28 registers, 16-colour palette."""

    vram: bytearray = field(default_factory=lambda: bytearray(_VRAM_SIZE))
    regs: list[int] = field(default_factory=lambda: [0] * _NUM_REGS)
    status: int = 0
    palette: list[int] = field(default_factory=lambda: list(_TMS_PALETTE))
    on_interrupt: Callable[[], None] | None = None
    _addr: int = field(default=0, init=False, repr=False)
    _latch: int | None = field(default=None, init=False, repr=False)
    _pal_latch: int | None = field(default=None, init=False, repr=False)
    _read_buf: int = field(default=0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)

    @property
    def display_height(self) -> int:
        """192 lines by default; 212 when R#9 bit 7 (LN) is set."""
        return 212 if (self.regs[9] & 0x80) else 192

    def write_port(self, port: int, value: int) -> None:
        value &= 0xFF
        if port == 0x98:
            self.vram[self._addr] = value
            self._addr = (self._addr + 1) & 0x1FFFF
        elif port == 0x99:
            if self._latch is None:
                self._latch = value
            else:
                low = self._latch
                self._latch = None
                if value & 0x80:
                    reg = value & 0x1F
                    if reg < _NUM_REGS:
                        self.regs[reg] = low
                else:
                    # Combine 14-bit address from this write with R#14 high bits.
                    self._addr = (self.regs[14] & 0x07) << 14 | (value & 0x3F) << 8 | low
                    if not (value & 0x40):  # bit6=0 → read mode: preload buffer
                        self._read_buf = self.vram[self._addr]
                        self._addr = (self._addr + 1) & 0x1FFFF
        elif port == 0x9A:
            if self._pal_latch is None:
                self._pal_latch = value
            else:
                rb, self._pal_latch = self._pal_latch, None
                r = (rb >> 4) & 0x07
                b = rb & 0x07
                g = value & 0x07
                idx = self.regs[16] & 0x0F
                self.palette[idx] = (r << 6) | (g << 3) | b
                self.regs[16] = (idx + 1) & 0x0F
        elif port == 0x9B:
            ptr = self.regs[17] & 0x3F
            if ptr < _NUM_REGS:
                self.regs[ptr] = value
            if not (self.regs[17] & 0x40):  # AII bit clear → auto-increment
                self.regs[17] = (self.regs[17] & 0xC0) | ((ptr + 1) & 0x3F)

    def read_port(self, port: int) -> int:
        if port == 0x98:
            result = self._read_buf
            self._read_buf = self.vram[self._addr]
            self._addr = (self._addr + 1) & 0x1FFFF
            return result
        if port == 0x99:
            result = self.status
            self.status &= ~0x80  # clear F flag
            self._latch = None
            return result & 0xFF
        return 0xFF
