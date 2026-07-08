"""V9938 VDP for MSX2.

128 KB VRAM, 28 control registers, 16-colour programmable palette,
hardware command engine (full V9938 command set).
Ports 0x98–0x9C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.vdp.tracer import Tracer

_VRAM_SIZE = 131072  # 128 KB
_NUM_REGS = 28


def _apply_log(src: int, dst: int, log_op: int, mask: int = 0xF) -> int:
    """Apply V9938 logical operation (LOG[2:0]) at pixel level.

    LOG codes (see tracer._LOP_NAMES): 0=IMP, 1=AND, 2=OR, 3=XOR, 4=NOT.
    Codes 5-7 are undefined and fall through to IMP (plain source copy).
    """
    if log_op == 0:  # IMP
        return src
    if log_op == 1:  # AND
        return src & dst
    if log_op == 2:  # OR
        return src | dst
    if log_op == 3:  # XOR
        return src ^ dst
    if log_op == 4:  # NOT
        return (~src) & mask
    return src  # 5-7 undefined → IMP


# V9938 power-on default palette (the MSX2 standard palette), 9-bit packed as
# (R<<6)|(G<<3)|B. These are the real hardware reset values — matching openMSX's
# V9938_PALETTE (V9938 data book p.6) — NOT the TMS9918A approximations, so MSX2
# titles that rely on the default palette show their intended colours.
_MSX2_DEFAULT_PALETTE: tuple[int, ...] = (
    0b000_000_000,  # 0  transparent → black   R0 G0 B0
    0b000_000_000,  # 1  black                  R0 G0 B0
    0b001_110_001,  # 2  medium green           R1 G6 B1
    0b011_111_011,  # 3  light green            R3 G7 B3
    0b001_001_111,  # 4  dark blue              R1 G1 B7
    0b010_011_111,  # 5  light blue             R2 G3 B7
    0b101_001_001,  # 6  dark red               R5 G1 B1
    0b010_110_111,  # 7  cyan                   R2 G6 B7
    0b111_001_001,  # 8  medium red             R7 G1 B1
    0b111_011_011,  # 9  light red              R7 G3 B3
    0b110_110_001,  # 10 dark yellow            R6 G6 B1
    0b110_110_100,  # 11 light yellow           R6 G6 B4
    0b001_100_001,  # 12 dark green             R1 G4 B1
    0b110_010_101,  # 13 magenta                R6 G2 B5
    0b101_101_101,  # 14 gray                   R5 G5 B5
    0b111_111_111,  # 15 white                  R7 G7 B7
)

# Command codes in R46 upper nibble
_CMD_ABRT = 0x0
_CMD_POINT = 0x4
_CMD_PSET = 0x5
_CMD_SRCH = 0x6
_CMD_LINE = 0x7
_CMD_LMMV = 0x8
_CMD_LMMM = 0x9
_CMD_LMCM = 0xA
_CMD_LMMC = 0xB
_CMD_HMMV = 0xC
_CMD_HMMM = 0xD
_CMD_YMMM = 0xE
_CMD_HMMC = 0xF

# S2 status bits
_S2_CE = 0x01  # command executing
_S2_BD = 0x10  # border/colour detected (SRCH result)
_S2_TR = 0x80  # transfer ready (CPU may send next byte)

# ARG register (R#45) bit assignments
_ARG_MAJ = 0x01  # LINE: major axis (0=X, 1=Y)
_ARG_EQ = 0x02   # SRCH: 0=stop on equal, 1=stop on not-equal
_ARG_DIX = 0x04  # X direction (0=right, 1=left)
_ARG_DIY = 0x08  # Y direction (0=down, 1=up)

_CYCLES_PER_BYTE: int = 8  # calibrated from OpenMSX golden log (230K T-states / 128×212)
# Byte-unit commands (HMMV/HMMM/YMMM) process one VRAM byte (= ppb pixels) per
# _CYCLES_PER_BYTE; pixel-unit commands (LMMV/LMMM) process one pixel per
# _CYCLES_PER_PIXEL. openMSX's VDP-clock coefficients (HMMV≈48, HMMM≈88,
# LMMV≈96, LMMM≈120 per unit) put the logical (pixel) commands ~ppb× slower than
# the byte commands; expressing them per pixel (rather than per byte) reproduces
# that ratio in this emulator's calibrated T-state unit.
_CYCLES_PER_PIXEL: int = _CYCLES_PER_BYTE

# Display-relevant registers tracked in _reg_write_log for banded rendering.
# Command-engine registers (R#32-R#46) and SAT registers are excluded.
_DISPLAY_REGS: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 19, 23})

# S#2 status bits
_S2_HR = 0x20  # horizontal retrace (set during horizontal blanking)
_S2_VR = 0x40  # vertical retrace (set during vertical blanking)
_S2_RESERVED = 0x0C  # bits 2,3 read as 1 on the V9938 (matches openMSX reset 0x0C)

# Horizontal-retrace timing. One NTSC scanline is ~227 Z80 T-states; the active
# display occupies ~1024/1368 of the line (openMSX ratio), so HR reads as 1 for
# the remainder of the line (the blanking/retrace tail). Sub-line accurate to the
# extent the scanline-stepped CPU advances _line_cycle via tick().
_TSTATES_PER_LINE: int = 227
_HBLANK_START: int = _TSTATES_PER_LINE * 1024 // 1368  # ~169


@dataclass(slots=True)
class _RegChange:
    """A tracked register write, logged per scanline for banded rendering.

    Tagged-union alternative to the old (line, reg, int | tuple) log entry:
    a _RegChange sets regs[reg] = value; a _PaletteChange sets palette[idx] = rgb.
    Consumers dispatch by type (isinstance) rather than a reg == -1 sentinel,
    which maps directly to a Rust enum / C++ variant.
    """

    line: int
    reg: int
    value: int


@dataclass(slots=True)
class _PaletteChange:
    """A palette write, logged per scanline for banded rendering (see _RegChange)."""

    line: int
    idx: int
    rgb: int


@dataclass
class V9938:
    """V9938 VDP for MSX2: 128 KB VRAM, 28 registers, 16-colour palette,
    hardware command engine."""

    vram: bytearray = field(default_factory=lambda: bytearray(_VRAM_SIZE))
    regs: list[int] = field(default_factory=lambda: [0] * _NUM_REGS)
    status: int = 0
    palette: list[int] = field(default_factory=lambda: list(_MSX2_DEFAULT_PALETTE))
    on_interrupt: Callable[[], None] | None = None
    # Portability note: these Callable hooks (on_interrupt, tracer, _get_pc,
    # _get_cycle) are stored Python closures with no direct static-typed
    # analogue. A Rust/C++ port models them as trait objects or feature-flagged
    # fields resolved once, not per-call function pointers.
    tracer: Tracer | None = field(default=None, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    # Command engine
    cmd_regs: list[int] = field(default_factory=lambda: [0] * 15)  # R32–R46
    _status2: int = field(default=0, init=False, repr=False)
    _cmd_active: bool = field(default=False, init=False, repr=False)
    # OpenMSX 'transfer' latch: set on any R#44 (COL) write, consumed when the
    # command engine writes a dot. HMMC/LMMC do NOT auto-load the first dot on
    # dispatch (that caused a 1-pixel offset — OpenMSX bug#1014); the first dot
    # comes from a pending COL write instead.
    _cmd_transfer: bool = field(default=False, init=False, repr=False)
    _cmd_remaining: int = field(default=0, init=False, repr=False)
    _cmd_code: int = field(default=0, init=False, repr=False)
    _cmd_dx: int = field(default=0, init=False, repr=False)
    _cmd_dy: int = field(default=0, init=False, repr=False)
    _cmd_nx: int = field(default=0, init=False, repr=False)
    _cmd_ny: int = field(default=0, init=False, repr=False)
    _cmd_x: int = field(default=0, init=False, repr=False)
    _cmd_y: int = field(default=0, init=False, repr=False)
    _cmd_log: int = field(default=0, init=False, repr=False)
    _tr_delay: int = field(default=0, init=False, repr=False)  # cycles until TR re-asserts
    _status7: int = field(default=0, init=False, repr=False)  # POINT result
    _status8: int = field(default=0, init=False, repr=False)  # SRCH result X low
    _status9: int = field(default=0, init=False, repr=False)  # SRCH result X high
    _cmd_xstep: int = field(default=1, init=False, repr=False)  # DIX direction
    _cmd_ystep: int = field(default=1, init=False, repr=False)  # DIY direction
    _cmd_bpl: int = field(default=128, init=False, repr=False)  # bytes per line
    _cmd_ppb: int = field(default=2, init=False, repr=False)  # pixels per byte
    _cmd_bpp: int = field(default=4, init=False, repr=False)  # bits per pixel
    _lmcm_buf: list[int] = field(default_factory=list, init=False, repr=False)
    _lmcm_idx: int = field(default=0, init=False, repr=False)  # advancing LMCM read cursor
    # Standard internals
    _addr: int = field(default=0, init=False, repr=False)
    _latch: int | None = field(default=None, init=False, repr=False)
    _pal_latch: int | None = field(default=None, init=False, repr=False)
    _read_buf: int = field(default=0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _ie1_warned: bool = field(default=False, init=False, repr=False)
    _status1: int = field(default=0, init=False, repr=False)
    display_line: int = field(default=0, init=False, repr=False)
    _line_cycle: int = field(default=0, init=False, repr=False)  # T-states into current scanline
    _irq: bool = field(default=False, init=False, repr=False)
    _reg_write_log: list[_RegChange | _PaletteChange] = field(
        default_factory=list, init=False, repr=False
    )
    _frame_start_regs: list[int] = field(
        default_factory=lambda: [0] * _NUM_REGS, init=False, repr=False
    )
    _frame_start_palette: list[int] = field(
        default_factory=lambda: list(_MSX2_DEFAULT_PALETTE), init=False, repr=False
    )
    debug_disable_sprites: bool = field(default=False, repr=False)  # render background only

    # Public accessors mirroring the TMS9918A VDP field names, so cross-module
    # code (machine, state save/load) can use the same names for both VDP types.
    @property
    def latch(self) -> int | None:
        return self._latch

    @latch.setter
    def latch(self, value: int | None) -> None:
        self._latch = value

    @property
    def addr(self) -> int:
        return self._addr

    @addr.setter
    def addr(self, value: int) -> None:
        self._addr = value

    @property
    def read_buf(self) -> int:
        return self._read_buf

    @read_buf.setter
    def read_buf(self, value: int) -> None:
        self._read_buf = value

    @property
    def irq(self) -> bool:
        """Current interrupt-request line state (read-only)."""
        return self._irq

    @property
    def display_height(self) -> int:
        """192 lines by default; 212 when R#9 bit 7 (LN) is set."""
        return 212 if (self.regs[9] & 0x80) else 192

    @property
    def display_width(self) -> int:
        """256 normally; 512 for the wide bitmap modes SCREEN 6 (G5) and
        SCREEN 7 (G6), i.e. M5 set with M4 clear. SCREEN 8 (G7, M5+M4) is 256."""
        r0 = self.regs[0]
        m4 = (r0 >> 2) & 1
        m5 = (r0 >> 3) & 1
        return 512 if (m5 and not m4) else 256

    def increment_frame(self) -> None:
        """Advance the completed-frame counter. Called once per frame."""
        self._frame_count += 1

    def irq_pending(self) -> bool:
        ie0 = bool(self.regs[1] & 0x20)
        f = bool(self.status & 0x80)
        ie1 = bool(self.regs[0] & 0x10)
        fh = bool(self._status1 & 0x01)
        return (ie0 and f) or (ie1 and fh)

    def _update_irq(self) -> None:
        self._irq = self.irq_pending()

    def reset(self) -> None:
        """Restore power-on register/status/command-engine state (VRAM retained)."""
        self.regs = [0] * _NUM_REGS
        self.status = 0
        self.palette = list(_MSX2_DEFAULT_PALETTE)
        self.cmd_regs = [0] * 15
        self._status1 = 0
        self._status2 = 0
        self._status7 = 0
        self._status8 = 0
        self._status9 = 0
        self._cmd_active = False
        self._cmd_remaining = 0
        self._cmd_code = 0
        self._addr = 0
        self._latch = None
        self._pal_latch = None
        self._read_buf = 0
        self._irq = False
        self.display_line = 0
        self._line_cycle = 0

    def begin_scanline(self, line: int) -> None:
        if line == 0:
            self._frame_start_regs = self.regs[:]
            self._frame_start_palette = self.palette[:]
            self._reg_write_log.clear()
        self.display_line = line
        self._line_cycle = 0  # new scanline: restart the horizontal-retrace timer
        dh = self.display_height
        if line == dh:
            self.status |= 0x80  # VBlank F
            self._update_irq()
        # Line interrupt: R#19 is an 8-bit raster line that may target any line
        # in the field (0-255), including the border/vblank region — not only the
        # active display. Lines >= 256 can never match the 8-bit compare.
        # Subtracting R#23 (vertical scroll) is intentional and matches openMSX
        # (VDP.cc scheduleHScan): the split line is compared as (R#19 - R#23) & 0xFF.
        effective = (self.regs[19] - self.regs[23]) & 0xFF
        if line == effective:
            self._status1 |= 0x01  # FH
            if self.regs[0] & 0x10:  # IE1
                self._update_irq()

    # ------------------------------------------------------------------
    # Command timer
    # ------------------------------------------------------------------

    def tick(self, cycles: int) -> None:
        """Advance VDP command timer. Clears CE when _cmd_remaining reaches 0."""
        self._line_cycle += cycles  # horizontal position within the current scanline
        if self._cmd_remaining > 0:
            self._cmd_remaining -= cycles
            if self._cmd_remaining <= 0:
                self._cmd_active = False
                self._status2 &= ~(_S2_CE | _S2_TR)
        if self._tr_delay > 0:
            self._tr_delay -= cycles
            if self._tr_delay <= 0 and self._cmd_active:
                self._status2 |= _S2_TR

    # ------------------------------------------------------------------
    # Port I/O
    # ------------------------------------------------------------------

    def write_port(self, port: int, value: int) -> None:
        """Dispatch a V9938 VDP port write.

        0x98 = VRAM data (auto-increments the 17-bit address); 0x99 = control
        (two-byte latch: register write or VRAM address setup); 0x9A = palette
        data; 0x9B/0x9C = indirect register access (R#17-pointed register).
        """
        value &= 0xFF
        if port == 0x98:
            self.vram[self._addr] = value
            self._addr = (self._addr + 1) & 0x1FFFF
        elif port == 0x99:
            if self.tracer is not None:
                pc = self._get_pc() if self._get_pc is not None else 0
                cy = self._get_cycle() if self._get_cycle is not None else 0
                self.tracer.port99_write(pc, cy, value, frame=self._frame_count)
            if self._latch is None:
                self._latch = value
            else:
                low = self._latch
                self._latch = None
                if value & 0x80:
                    reg = value & 0x3F
                    if reg < _NUM_REGS:
                        self.regs[reg] = low
                        if reg in _DISPLAY_REGS:
                            self._reg_write_log.append(_RegChange(self.display_line, reg, low))
                        if reg <= 1:  # R#0 (IE1) / R#1 (IE0) affect the IRQ line
                            self._update_irq()
                    elif 32 <= reg <= 45:
                        self.cmd_regs[reg - 32] = low
                        if reg == 44:
                            self._cmd_transfer = True  # COL write → transfer latch
                            if self._cmd_active and self._cmd_code in (_CMD_HMMC, _CMD_LMMC):
                                self._cmd_data_write(low)
                    elif reg == 46:
                        self.cmd_regs[14] = low
                        self._dispatch_command()
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
                rgb = (r << 6) | (g << 3) | b
                self.palette[idx] = rgb
                self._reg_write_log.append(_PaletteChange(self.display_line, idx, rgb))
                self.regs[16] = (idx + 1) & 0x0F
        elif port == 0x9B:
            # During HMMC/LMMC: port 0x9B doubles as command data port.
            if self._cmd_active and self._cmd_code in (_CMD_HMMC, _CMD_LMMC):
                self._cmd_data_write(value)
                return
            ptr = self.regs[17] & 0x3F
            r17_before = self.regs[17]
            if ptr < _NUM_REGS:
                self.regs[ptr] = value
                if ptr in _DISPLAY_REGS:
                    self._reg_write_log.append(_RegChange(self.display_line, ptr, value))
                if ptr <= 1:  # R#0 (IE1) / R#1 (IE0) affect the IRQ line
                    self._update_irq()
            elif 32 <= ptr <= 45:
                self.cmd_regs[ptr - 32] = value
                if ptr == 44:
                    self._cmd_transfer = True  # COL write → transfer latch
            elif ptr == 46:
                self.cmd_regs[14] = value
                self._dispatch_command()
            if self.tracer is not None:
                pc = self._get_pc() if self._get_pc is not None else 0
                cy = self._get_cycle() if self._get_cycle is not None else 0
                self.tracer.port9b_write(pc, cy, value, r17=r17_before, frame=self._frame_count)
            if not (self.regs[17] & 0x80):  # AII (bit7) clear → auto-increment
                self.regs[17] = (self.regs[17] & 0xC0) | (((self.regs[17] & 0x3F) + 1) & 0x3F)
        elif port == 0x9C:
            self._cmd_data_write(value)

    def read_port(self, port: int) -> int:
        """Dispatch a V9938 VDP port read.

        0x98 = VRAM data (returns the read-ahead buffer, refills it, auto-
        increments the 17-bit address); 0x99 = status register S#n, where R#15
        selects which S#n is returned (reading also resets the control latch).
        """
        if port == 0x98:
            result = self._read_buf
            self._read_buf = self.vram[self._addr]
            self._addr = (self._addr + 1) & 0x1FFFF
            return result
        if port == 0x99:
            # Reading the status port resets the port-99 write latch (the
            # second-byte flip-flop) on real V9938 hardware, regardless of which
            # status register R#15 selects. Resetting it only for S#0 lets a
            # latch desync (e.g. a write interrupted mid-sequence) persist
            # forever, corrupting every subsequent R#nn / address write.
            self._latch = None
            if self.regs[15] == 2:
                # S#2 also reports the live horizontal/vertical retrace flags,
                # which software polls to time mid-screen register changes; bits
                # 2,3 always read as 1.
                s2 = self._status2 | _S2_RESERVED
                if self._line_cycle >= _HBLANK_START:
                    s2 |= _S2_HR
                if not (0 <= self.display_line < self.display_height):
                    s2 |= _S2_VR
                return s2
            if self.regs[15] == 7:
                # LMCM delivers each result byte through S#7 (handbook); fall
                # back to the POINT result when no LMCM transfer is active.
                if (self._cmd_active and self._cmd_code == _CMD_LMCM
                        and self._lmcm_idx < len(self._lmcm_buf)):
                    return self._cmd_data_read()
                return self._status7
            if self.regs[15] == 1:
                result = self._status1
                self._status1 &= ~0x01  # clear FH
                self._update_irq()
                return result & 0xFF
            if self.regs[15] == 8:
                return self._status8
            if self.regs[15] == 9:
                return self._status9
            # S#0: bit7=F (frame flag), bit6=5S, bit5=C, bits4-0 = 5th/last
            # sprite number. The renderer sets 5S/C during the sprite scan; a
            # real V9938 read of S#0 clears F, 5S and C together, so mask off
            # 0xE0. The idle last-sprite number 31 (0x1F) is still OR-ed into the
            # *returned* byte: returning 0 there stalls MSX2 C-BIOS cartridge
            # boot, which feeds S#0 bits 4:0 into its cartridge-scan loop counter.
            result = (self.status & 0xE0) | 0x1F
            self.status &= ~0xE0  # clear F, 5S and C flags together
            self._update_irq()
            return result & 0xFF
        if port == 0x9C:
            return self._cmd_data_read()
        return 0xFF

    # ------------------------------------------------------------------
    # Command engine helpers
    # ------------------------------------------------------------------

    def _cmd_geometry(self) -> tuple[int, int, int]:
        """Return (bytes_per_line, pixels_per_byte, bits_per_pixel) for the
        current screen mode. The command engine addresses raw VRAM linearly,
        independent of the display base register R#2.
        """
        r0 = self.regs[0]
        m3 = (r0 >> 1) & 1
        m4 = (r0 >> 2) & 1
        m5 = (r0 >> 3) & 1
        if m5 and m4:      # GRAPHIC 7 (SCREEN 8): 256 wide, 8 bpp
            return 256, 1, 8
        if m5 and m3:      # GRAPHIC 6 (SCREEN 7): 512 wide, 4 bpp
            return 256, 2, 4
        if m5:             # GRAPHIC 5 (SCREEN 6): 512 wide, 2 bpp
            return 128, 4, 2
        return 128, 2, 4   # GRAPHIC 4 (SCREEN 5): 256 wide, 4 bpp (default)

    def _vram_byte_addr(self, x: int, y: int) -> int:
        """Linear VRAM byte address for command-engine pixel (x, y).

        The X byte offset wraps within its own row so a blit that runs past the
        right edge stays on its row instead of spilling into the next row.
        In-bounds pixels (x < screen width) are unaffected.
        """
        return (y * self._cmd_bpl + (x // self._cmd_ppb) % self._cmd_bpl) & 0x1FFFF

    def _vram_pixel_read(self, x: int, y: int) -> int:
        """Return the pixel value at (x, y) for the current screen mode."""
        byte = self.vram[self._vram_byte_addr(x, y)]
        shift = (self._cmd_ppb - 1 - (x % self._cmd_ppb)) * self._cmd_bpp
        return (byte >> shift) & ((1 << self._cmd_bpp) - 1)

    def _vram_pixel_write(self, x: int, y: int, color: int, log: int) -> None:
        """Write a pixel at (x, y) applying the V9938 LOG operation."""
        mask = (1 << self._cmd_bpp) - 1
        src = color & mask
        if (log & 0x8) and src == 0:  # transparent: skip zero source pixels
            return
        addr = self._vram_byte_addr(x, y)
        existing = self.vram[addr]
        shift = (self._cmd_ppb - 1 - (x % self._cmd_ppb)) * self._cmd_bpp
        dst = (existing >> shift) & mask
        result = _apply_log(src, dst, log & 0x7, mask) & mask
        self.vram[addr] = (existing & ~(mask << shift) & 0xFF) | (result << shift)

    def _byte_cmd_cycles(self, nx_px: int, ny: int) -> int:
        """CE duration for a byte-unit command (HMMV/HMMM/YMMM).

        nx_px pixels span ceil(nx_px / ppb) VRAM bytes; each byte costs
        _CYCLES_PER_BYTE T-states. Clamped to a 4 T-state minimum.
        """
        byte_cols = (nx_px + self._cmd_ppb - 1) // self._cmd_ppb
        return max(4, byte_cols * ny * _CYCLES_PER_BYTE)

    def _pixel_cmd_cycles(self, nx_px: int, ny: int) -> int:
        """CE duration for a pixel-unit command (LMMV/LMMM).

        Logical commands process one pixel per _CYCLES_PER_PIXEL T-states (no
        ppb division), making them ~ppb× slower than the byte commands.
        """
        return max(4, nx_px * ny * _CYCLES_PER_PIXEL)

    def _dispatch_command(self) -> None:
        """Execute or start the command written to R46 (cmd_regs[14])."""
        r46 = self.cmd_regs[14]  # R#46: command byte (high nibble) + logic op (low)
        cmd = (r46 >> 4) & 0xF
        log = r46 & 0xF

        # Every command owns _cmd_code, not just the CPU-feed commands (HMMC/
        # LMMC/LMCM). The data-port handlers key off _cmd_code, so a completed
        # synchronous command (LMMV/LMMM/HMMV/HMMM/YMMM/LINE/SRCH) must overwrite
        # any stale HMMC/LMMC code, otherwise a later R#44/port-0x9B/0x9C write
        # is misrouted into the CPU-feed path and corrupts the just-drawn region.
        self._cmd_code = cmd

        self._cmd_bpl, self._cmd_ppb, self._cmd_bpp = self._cmd_geometry()
        px_mask = (1 << self._cmd_bpp) - 1

        sx = self.cmd_regs[0] | ((self.cmd_regs[1] & 0x01) << 8)
        sy = self.cmd_regs[2] | ((self.cmd_regs[3] & 0x03) << 8)
        dx = self.cmd_regs[4] | ((self.cmd_regs[5] & 0x01) << 8)
        dy = self.cmd_regs[6] | ((self.cmd_regs[7] & 0x03) << 8)
        nx = self.cmd_regs[8] | ((self.cmd_regs[9] & 0x01) << 8)
        ny = self.cmd_regs[10] | ((self.cmd_regs[11] & 0x03) << 8)
        clr = self.cmd_regs[12]
        arg = self.cmd_regs[13]
        xs = -1 if (arg & _ARG_DIX) else 1  # DIX: 1 = leftward
        ys = -1 if (arg & _ARG_DIY) else 1  # DIY: 1 = upward

        self._cmd_active = False
        self._status2 &= ~(_S2_CE | _S2_TR)

        if cmd == _CMD_ABRT or cmd not in (
            _CMD_POINT,
            _CMD_PSET,
            _CMD_SRCH,
            _CMD_LINE,
            _CMD_LMMV,
            _CMD_LMMM,
            _CMD_LMCM,
            _CMD_LMMC,
            _CMD_HMMV,
            _CMD_HMMM,
            _CMD_YMMM,
            _CMD_HMMC,
        ):
            return

        if cmd == _CMD_POINT:
            self._status7 = self._vram_pixel_read(sx, sy) & px_mask
            return

        if cmd == _CMD_PSET:
            self._vram_pixel_write(dx, dy, clr & px_mask, log)
            return

        if cmd == _CMD_SRCH:
            sw = self._cmd_bpl * self._cmd_ppb  # screen width in pixels
            stop_on_ne = bool(arg & _ARG_EQ)
            clr_px = clr & px_mask
            x = sx
            found = False
            while 0 <= x < sw:
                pix = self._vram_pixel_read(x, sy)
                hit = (pix != clr_px) if stop_on_ne else (pix == clr_px)
                if hit:
                    found = True
                    break
                x += xs
            # Result X coordinate goes to S#8/S#9; S#9 upper bits read as 1.
            self._status8 = x & 0xFF
            self._status9 = 0xFE | ((x >> 8) & 0x01)
            if found:
                self._status2 |= _S2_BD
            else:
                self._status2 &= ~_S2_BD
            return

        if cmd == _CMD_LINE:
            maj_y = bool(arg & _ARG_MAJ)
            clr_px = clr & px_mask
            # NX is always the major (long) side, NY always the minor (short);
            # MAJ only selects which screen axis the major runs along.
            major = nx
            minor = ny
            err = major // 2
            x, y = dx, dy
            for _ in range(major + 1):
                self._vram_pixel_write(x, y, clr_px, log)
                err -= minor
                if maj_y:
                    y += ys
                    if err < 0:
                        x += xs
                        err += major
                else:
                    x += xs
                    if err < 0:
                        y += ys
                        err += major
            return

        if cmd == _CMD_LMCM:
            self._lmcm_buf = []
            self._lmcm_idx = 0
            rows = ny if ny else 1024
            cols = nx if nx else self._cmd_bpl * self._cmd_ppb
            ppb = self._cmd_ppb
            for row in range(rows):
                yy = sy + row * ys
                col = 0
                while col < cols:
                    byte = 0
                    for k in range(ppb):
                        pix = (self._vram_pixel_read(sx + (col + k) * xs, yy)
                               if (col + k) < cols else 0)
                        byte = (byte << self._cmd_bpp) | (pix & px_mask)
                    self._lmcm_buf.append(byte)
                    col += ppb
            self._cmd_active = True
            self._status2 |= _S2_CE | _S2_TR
            return

        if cmd == _CMD_LMMV:
            actual_nx = nx if nx else self._cmd_bpl * self._cmd_ppb
            actual_ny = ny if ny else 1024
            clr_px = clr & px_mask
            for row in range(actual_ny):
                yy = dy + row * ys
                for col in range(actual_nx):
                    self._vram_pixel_write(dx + col * xs, yy, clr_px, log)
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._pixel_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_LMMM:
            actual_nx = nx if nx else self._cmd_bpl * self._cmd_ppb
            actual_ny = ny if ny else 1024
            for row in range(actual_ny):
                syy = sy + row * ys
                dyy = dy + row * ys
                for col in range(actual_nx):
                    src_pix = self._vram_pixel_read(sx + col * xs, syy)
                    self._vram_pixel_write(dx + col * xs, dyy, src_pix, log)
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._pixel_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_HMMV:
            actual_nx = nx if nx else self._cmd_bpl * self._cmd_ppb
            actual_ny = ny if ny else 1024
            for row in range(actual_ny):
                yy = dy + row * ys
                for col in range(actual_nx):
                    addr = self._vram_byte_addr(dx + col * xs, yy)
                    self.vram[addr] = clr
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_HMMM:
            actual_nx = nx if nx else self._cmd_bpl * self._cmd_ppb
            actual_ny = ny if ny else 1024
            for row in range(actual_ny):
                syy = sy + row * ys
                dyy = dy + row * ys
                for col in range(actual_nx):
                    src = self._vram_byte_addr(sx + col * xs, syy)
                    dst = self._vram_byte_addr(dx + col * xs, dyy)
                    self.vram[dst] = self.vram[src]
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_YMMM:
            # Y-direction copy: vertical strip at X=DX (NX ignored, X-range runs
            # to the screen edge per DIX); source row SY → destination row DY.
            actual_ny = ny if ny else 1024
            sw = self._cmd_bpl * self._cmd_ppb  # screen width in pixels
            x_count = (dx + 1) if (arg & _ARG_DIX) else (sw - dx)
            for row in range(actual_ny):
                syy = sy + row * ys
                dyy = dy + row * ys
                for c in range(x_count):
                    xx = dx + c * xs
                    src = self._vram_byte_addr(xx, syy)
                    dst = self._vram_byte_addr(xx, dyy)
                    self.vram[dst] = self.vram[src]
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(x_count, actual_ny)
            return

        # HMMC (0xF) or LMMC (0xB): CPU-feed transfer; tick() must not time out via _cmd_remaining.
        self._cmd_remaining = 0
        self._cmd_active = True
        self._cmd_dx = dx
        self._cmd_dy = dy
        self._cmd_nx = nx if nx else self._cmd_bpl * self._cmd_ppb
        self._cmd_ny = ny if ny else 1024
        self._cmd_x = 0
        self._cmd_y = 0
        self._cmd_log = log
        self._cmd_xstep = xs
        self._cmd_ystep = ys
        self._status2 |= _S2_CE
        # V9938 does NOT auto-load the first dot from R#44 at command start
        # (OpenMSX startLmmc/startHmmc set TR only). Writing R#44 (COL) sets the
        # transfer latch; if that happened during command setup the pending COL
        # is the first dot, otherwise the first CPU data-port write supplies it.
        # Unconditionally consuming CLR here shifted every transfer by one dot
        # (OpenMSX bug#1014, "one pixel offset").
        if self._cmd_transfer:
            self._cmd_data_write(clr)
        if self._cmd_active:
            self._tr_delay = 0
            self._status2 |= _S2_TR

    def _cmd_data_write(self, value: int) -> None:
        """Handle a byte arriving at port 0x9C during an active HMMC/LMMC."""
        if not self._cmd_active or self._cmd_code == _CMD_LMCM:
            return
        # A pending byte is being consumed → clear the transfer latch.
        self._cmd_transfer = False
        # TR=0 while VDP processes; tick() re-asserts TR after _CYCLES_PER_BYTE
        self._status2 &= ~_S2_TR
        self._tr_delay = _CYCLES_PER_BYTE
        px = self._cmd_dx + self._cmd_x * self._cmd_xstep
        py = self._cmd_dy + self._cmd_y * self._cmd_ystep
        if self._cmd_code == _CMD_LMMC:
            # LMMC: CPU sends one byte per pixel; color in lower bpp bits.
            mask = (1 << self._cmd_bpp) - 1
            self._vram_pixel_write(px, py, value & mask, self._cmd_log)
            self._cmd_x += 1
        else:
            # HMMC: high-speed byte copy, no logical operation; one byte = ppb pixels.
            self.vram[self._vram_byte_addr(px, py)] = value
            self._cmd_x += self._cmd_ppb
        if self._cmd_x >= self._cmd_nx:
            self._cmd_x = 0
            self._cmd_y += 1
            if self._cmd_y >= self._cmd_ny:
                self._cmd_active = False
                self._tr_delay = 0
                self._status2 &= ~(_S2_CE | _S2_TR)

    def _cmd_data_read(self) -> int:
        """Return next buffered byte for an active LMCM transfer."""
        if (not self._cmd_active or self._cmd_code != _CMD_LMCM
                or self._lmcm_idx >= len(self._lmcm_buf)):
            return 0xFF
        # Advancing index instead of pop(0): O(n) over a transfer, not O(n²).
        byte = self._lmcm_buf[self._lmcm_idx]
        self._lmcm_idx += 1
        if self._lmcm_idx >= len(self._lmcm_buf):
            self._cmd_active = False
            self._status2 &= ~(_S2_CE | _S2_TR)
        return byte
