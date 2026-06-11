"""V9938 VDP for MSX2.

128 KB VRAM, 28 control registers, 16-colour programmable palette,
hardware command engine (full V9938 command set).
Ports 0x98–0x9C.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

_VRAM_SIZE = 131072  # 128 KB
_NUM_REGS = 28


def _apply_log(src: int, dst: int, log_op: int) -> int:
    """Apply V9938 logical operation (LOG[2:0]) at 4-bit pixel level."""
    if log_op == 0:
        return src
    if log_op == 1:
        return src & dst
    if log_op == 2:
        return src | dst
    if log_op == 3:
        return src ^ dst
    if log_op == 4:
        return (~src) & 0xF
    return src

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

# Command codes in R46 upper nibble
_CMD_STOP = 0x0
_CMD_POINT = 0x4
_CMD_PSET = 0x5
_CMD_SRCH = 0x6
_CMD_LMMV = 0x8
_CMD_LMMM = 0x9
_CMD_LMCM = 0xA
_CMD_LMMC = 0xB
_CMD_HMMV = 0xC
_CMD_HMMM = 0xD
_CMD_YMMM = 0xE
_CMD_HMMC = 0xF

# S2 status bits
_S2_CE = 0x01   # command executing
_S2_TR = 0x80   # transfer ready (CPU may send next byte)


@dataclass
class V9938:
    """V9938 VDP for MSX2: 128 KB VRAM, 28 registers, 16-colour palette,
    hardware command engine."""

    vram: bytearray = field(default_factory=lambda: bytearray(_VRAM_SIZE))
    regs: list[int] = field(default_factory=lambda: [0] * _NUM_REGS)
    status: int = 0
    palette: list[int] = field(default_factory=lambda: list(_TMS_PALETTE))
    on_interrupt: Callable[[], None] | None = None
    # Command engine
    cmd_regs: list[int] = field(default_factory=lambda: [0] * 15)  # R32–R46
    _status2: int = field(default=0, init=False, repr=False)
    _cmd_active: bool = field(default=False, init=False, repr=False)
    _cmd_code: int = field(default=0, init=False, repr=False)
    _cmd_dx: int = field(default=0, init=False, repr=False)
    _cmd_dy: int = field(default=0, init=False, repr=False)
    _cmd_nx: int = field(default=0, init=False, repr=False)
    _cmd_ny: int = field(default=0, init=False, repr=False)
    _cmd_x: int = field(default=0, init=False, repr=False)
    _cmd_y: int = field(default=0, init=False, repr=False)
    _cmd_log: int = field(default=0, init=False, repr=False)
    _status7: int = field(default=0, init=False, repr=False)  # POINT result
    _lmcm_buf: list[int] = field(default_factory=list, init=False, repr=False)
    # Standard internals
    _addr: int = field(default=0, init=False, repr=False)
    _latch: int | None = field(default=None, init=False, repr=False)
    _pal_latch: int | None = field(default=None, init=False, repr=False)
    _read_buf: int = field(default=0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)

    @property
    def display_height(self) -> int:
        """192 lines by default; 212 when R#9 bit 7 (LN) is set."""
        return 212 if (self.regs[9] & 0x80) else 192

    # ------------------------------------------------------------------
    # Port I/O
    # ------------------------------------------------------------------

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
            elif 32 <= ptr <= 45:
                self.cmd_regs[ptr - 32] = value
            elif ptr == 46:
                self.cmd_regs[14] = value
                if not self._cmd_active:
                    self._dispatch_command()
            if not (self.regs[17] & 0x40):  # AII bit clear → auto-increment
                self.regs[17] = (self.regs[17] & 0xC0) | ((ptr + 1) & 0x3F)
        elif port == 0x9C:
            self._cmd_data_write(value)

    def read_port(self, port: int) -> int:
        if port == 0x98:
            result = self._read_buf
            self._read_buf = self.vram[self._addr]
            self._addr = (self._addr + 1) & 0x1FFFF
            return result
        if port == 0x99:
            if self.regs[15] == 2:
                return self._status2
            if self.regs[15] == 7:
                return self._status7
            result = self.status
            self.status &= ~0x80  # clear F flag
            self._latch = None
            return result & 0xFF
        if port == 0x9C:
            return self._cmd_data_read()
        return 0xFF

    # ------------------------------------------------------------------
    # Command engine helpers
    # ------------------------------------------------------------------

    def _vram_byte_addr(self, x: int, y: int) -> int:
        """G4 byte address for pixel (x, y): 256-pixel wide, 2 pixels/byte."""
        return (y * 128 + x // 2) & 0x1FFFF

    def _vram_pixel_read(self, x: int, y: int) -> int:
        """Return 4-bit pixel at (x, y) in G4 VRAM."""
        byte = self.vram[self._vram_byte_addr(x, y)]
        return (byte >> 4) if (x & 1) == 0 else (byte & 0xF)

    def _vram_pixel_write(self, x: int, y: int, color: int, log: int) -> None:
        """Write 4-bit pixel at (x, y) with V9938 LOG operation."""
        src = color & 0xF
        if (log & 0x8) and src == 0:  # transparent: skip zero source pixels
            return
        addr = self._vram_byte_addr(x, y)
        existing = self.vram[addr]
        hi = (x & 1) == 0
        dst = (existing >> 4) if hi else (existing & 0xF)
        result = _apply_log(src, dst, log & 0x7) & 0xF
        if hi:
            self.vram[addr] = (result << 4) | (existing & 0x0F)
        else:
            self.vram[addr] = (existing & 0xF0) | result

    def _dispatch_command(self) -> None:
        """Execute or start the command written to R46 (cmd_regs[14])."""
        cmr = self.cmd_regs[14]
        cmd = (cmr >> 4) & 0xF
        log = cmr & 0xF

        sx = self.cmd_regs[0] | ((self.cmd_regs[1] & 0x01) << 8)
        sy = self.cmd_regs[2] | ((self.cmd_regs[3] & 0x03) << 8)
        dx = self.cmd_regs[4] | ((self.cmd_regs[5] & 0x01) << 8)
        dy = self.cmd_regs[6] | ((self.cmd_regs[7] & 0x03) << 8)
        nx = self.cmd_regs[8] | ((self.cmd_regs[9] & 0x01) << 8)
        ny = self.cmd_regs[10] | ((self.cmd_regs[11] & 0x03) << 8)
        clr = self.cmd_regs[12]

        self._cmd_active = False
        self._status2 &= ~(_S2_CE | _S2_TR)

        if cmd == _CMD_STOP or cmd not in (
            _CMD_POINT, _CMD_PSET, _CMD_SRCH,
            _CMD_LMMV, _CMD_LMMM, _CMD_LMCM, _CMD_LMMC,
            _CMD_HMMV, _CMD_HMMM, _CMD_YMMM, _CMD_HMMC,
        ):
            return

        if cmd == _CMD_POINT:
            self._status7 = self._vram_pixel_read(sx, sy) & 0xF
            return

        if cmd == _CMD_PSET:
            self._vram_pixel_write(dx, dy, clr & 0xF, log)
            return

        if cmd == _CMD_SRCH:
            arg = self.cmd_regs[13]
            direction = -1 if (arg & 1) else 1
            stop_on_ne = bool(arg & 2)
            clr_px = clr & 0xF
            x = sx
            found = False
            while 0 <= x < 256:
                pix = self._vram_pixel_read(x, sy)
                hit = (pix != clr_px) if stop_on_ne else (pix == clr_px)
                if hit:
                    found = True
                    break
                x += direction
            if found:
                self._status2 = x & 0xFF           # found X in S2
            else:
                self._status2 = 0x10               # BD flag: not found
            return

        if cmd == _CMD_LMCM:
            self._lmcm_buf = []
            rows = ny if ny else 1024
            cols = nx if nx else 512
            for row in range(rows):
                for col in range(0, cols, 2):
                    hi = self._vram_pixel_read(sx + col, sy + row)
                    lo = self._vram_pixel_read(sx + col + 1, sy + row) if col + 1 < cols else 0
                    self._lmcm_buf.append((hi << 4) | lo)
            self._cmd_active = True
            self._cmd_code = cmd
            self._status2 |= _S2_CE | _S2_TR
            return

        if cmd == _CMD_LMMV:
            clr_px = clr & 0xF
            for row in range(ny if ny else 1024):
                for col in range(nx if nx else 512):
                    self._vram_pixel_write(dx + col, dy + row, clr_px, log)
            return

        if cmd == _CMD_LMMM:
            for row in range(ny if ny else 1024):
                for col in range(nx if nx else 512):
                    src_pix = self._vram_pixel_read(sx + col, sy + row)
                    self._vram_pixel_write(dx + col, dy + row, src_pix, log)
            return

        if cmd == _CMD_HMMV:
            for row in range(ny if ny else 1024):
                for col in range(nx if nx else 512):
                    addr = self._vram_byte_addr(dx + col, dy + row)
                    self.vram[addr] = clr
            return

        if cmd == _CMD_HMMM:
            for row in range(ny if ny else 1024):
                for col in range(nx if nx else 512):
                    src = self._vram_byte_addr(sx + col, sy + row)
                    dst = self._vram_byte_addr(dx + col, dy + row)
                    self.vram[dst] = self.vram[src]
            return

        if cmd == _CMD_YMMM:
            # Y-strip copy: SX→DX for NY rows; NX ignored (copies to screen edge)
            cols = 256 - max(sx, dx)
            for row in range(ny if ny else 1024):
                for col in range(cols):
                    src = self._vram_byte_addr(sx + col, sy + row)
                    dst = self._vram_byte_addr(dx + col, sy + row)
                    self.vram[dst] = self.vram[src]
            return

        # HMMC (0xF) or LMMC (0xB): CPU-feed transfer
        self._cmd_active = True
        self._cmd_code = cmd
        self._cmd_dx = dx
        self._cmd_dy = dy
        self._cmd_nx = nx if nx else 512
        self._cmd_ny = ny if ny else 1024
        self._cmd_x = 0
        self._cmd_y = 0
        self._cmd_log = log
        self._status2 |= _S2_CE | _S2_TR

    def _cmd_data_write(self, value: int) -> None:
        """Handle a byte arriving at port 0x9C during an active HMMC/LMMC."""
        if not self._cmd_active or self._cmd_code == _CMD_LMCM:
            return
        px = self._cmd_dx + self._cmd_x
        py = self._cmd_dy + self._cmd_y
        if self._cmd_code == _CMD_LMMC:
            # Pixel-level write with LOG: high nibble → px, low nibble → px+1
            self._vram_pixel_write(px, py, (value >> 4) & 0xF, self._cmd_log)
            self._vram_pixel_write(px + 1, py, value & 0xF, self._cmd_log)
        else:
            # HMMC: high-speed byte copy, no logical operation
            self.vram[self._vram_byte_addr(px, py)] = value
        # advance cursor: G4 = 2 pixels/byte
        self._cmd_x += 2
        if self._cmd_x >= self._cmd_nx:
            self._cmd_x = 0
            self._cmd_y += 1
            if self._cmd_y >= self._cmd_ny:
                self._cmd_active = False
                self._status2 &= ~(_S2_CE | _S2_TR)

    def _cmd_data_read(self) -> int:
        """Return next buffered byte for an active LMCM transfer."""
        if not self._cmd_active or self._cmd_code != _CMD_LMCM or not self._lmcm_buf:
            return 0xFF
        byte = self._lmcm_buf.pop(0)
        if not self._lmcm_buf:
            self._cmd_active = False
            self._status2 &= ~(_S2_CE | _S2_TR)
        return byte
