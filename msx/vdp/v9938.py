"""V9938 VDP for MSX2.

128 KB VRAM, 28 control registers, 16-colour programmable palette,
hardware command engine (full V9938 command set).
Ports 0x98–0x9B (the chip decodes two address bits, so a machine that decodes
the block more coarsely mirrors these four).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from msx.vdp.vdp import FramebufferFormat

if TYPE_CHECKING:
    from msx.vdp.tracer import Tracer

_VRAM_SIZE = 131072  # 128 KB
_NUM_REGS = 28


def _apply_log(src: int, dst: int, log_op: int, mask: int = 0xF) -> int:
    """Apply V9938 logical operation (LOG[2:0]) at pixel level.

    LOG codes (see tracer._LOP_NAMES): 0=IMP, 1=AND, 2=OR, 3=XOR, 4=NOT.
    Codes 5-7 are undefined and perform no write at all; callers filter them
    out before reaching here (see _vram_pixel_write).
    """
    if log_op == 0:  # IMP
        return src
    if log_op == 1:  # AND
        return src & dst
    if log_op == 2:  # OR
        return src | dst
    if log_op == 3:  # XOR
        return src ^ dst
    return (~src) & mask  # 4 = NOT


# Screen-edge clipping (openMSX VDPCmdEngine.cc clipNX_* / clipNY_*). A command
# never runs past the left or right edge: the line ends there. With DIY set
# (upward) it never runs past the top either. An origin already past the right
# edge processes exactly one element per line.

def _is_command_mode(r0: int) -> bool:
    """True when R#0 selects a mode the command engine runs in.

    Handbook Sec. 6.2: command actions are only defined in GRAPHIC 4-7. Every
    other mode is openMSX's scrMode -1, where a dispatch finishes immediately
    without touching VRAM. (The V9958 CMD bit, which enables commands in the
    non-bitmap modes, is out of scope for the V9938.)
    """
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1
    return bool(m5 or (m4 and m3))


def _clip_nx_dot(x: int, nx: int, dix: bool, dots_per_line: int) -> int:
    """Clip a dot-unit NX against the line edge, from a single X origin."""
    if x >= dots_per_line:
        return 1
    count = nx if nx else dots_per_line
    return min(count, x + 1) if dix else min(count, dots_per_line - x)


def _clip_nx_dot_pair(sx: int, dx: int, nx: int, dix: bool, dots_per_line: int) -> int:
    """Clip a dot-unit NX for a command with both a source and a destination X."""
    if sx >= dots_per_line or dx >= dots_per_line:
        return 1
    count = nx if nx else dots_per_line
    if dix:
        return min(count, min(sx, dx) + 1)
    return min(count, dots_per_line - max(sx, dx))


def _clip_nx_byte(x: int, nx: int, dix: bool, ppb: int, bpl: int) -> int:
    """Clip a byte-unit NX against the line edge, from a single X origin.

    NX is truncated to whole bytes first, as the byte-unit commands address
    VRAM a byte at a time.
    """
    col = x // ppb
    if col >= bpl:
        return 1
    count = nx // ppb
    if count == 0:
        count = bpl
    return min(count, col + 1) if dix else min(count, bpl - col)


def _clip_nx_byte_pair(sx: int, dx: int, nx: int, dix: bool, ppb: int, bpl: int) -> int:
    """Clip a byte-unit NX for a command with both a source and a destination X."""
    src_col = sx // ppb
    dst_col = dx // ppb
    if src_col >= bpl or dst_col >= bpl:
        return 1
    count = nx // ppb
    if count == 0:
        count = bpl
    if dix:
        return min(count, min(src_col, dst_col) + 1)
    return min(count, bpl - max(src_col, dst_col))


def _clip_ny(y: int, ny: int, diy: bool) -> int:
    """Clip NY against the top border (NY = 0 means 1024 lines)."""
    count = ny if ny else 1024
    return min(count, y + 1) if diy else count


def _clip_ny_pair(sy: int, dy: int, ny: int, diy: bool) -> int:
    """Clip NY for a command with both a source and a destination Y."""
    count = ny if ny else 1024
    return min(count, min(sy, dy) + 1) if diy else count


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

# Line-interrupt (horizontal scanline) bits
_R0_IE1 = 0x10  # R#0 bit4: horizontal (line) interrupt enable
_S1_FH = 0x01   # S#1 bit0: horizontal-scanline interrupt flag (line match)

# ARG register (R#45) bit assignments
_ARG_MAJ = 0x01  # LINE: major axis (0=X, 1=Y)
_ARG_EQ = 0x02   # SRCH: 0=stop on equal, 1=stop on not-equal
_ARG_DIX = 0x04  # X direction (0=right, 1=left)
_ARG_DIY = 0x08  # Y direction (0=down, 1=up)
# Expansion-VRAM select. This machine has 128 KB and no expansion RAM, so a
# source marked MXS reads as 0xFF and a destination marked MXD swallows the
# write (openMSX: doPoint/doPset are gated on hasExtendedVRAM, false for 128 KB).
_ARG_MXS = 0x10  # source in expansion RAM
_ARG_MXD = 0x20  # destination in expansion RAM

_CYCLES_PER_BYTE: int = 8  # calibrated from OpenMSX golden log (230K T-states / 128×212)
# Byte-unit commands (HMMV/HMMM/YMMM) process one VRAM byte (= ppb pixels) per
# _CYCLES_PER_BYTE; pixel-unit commands (LMMV/LMMM) process one pixel per
# _CYCLES_PER_PIXEL. openMSX's VDP-clock coefficients (HMMV≈48, HMMM≈88,
# LMMV≈96, LMMM≈120 per unit) put the logical (pixel) commands ~ppb× slower than
# the byte commands; expressing them per pixel (rather than per byte) reproduces
# that ratio in this emulator's calibrated T-state unit.
_CYCLES_PER_PIXEL: int = _CYCLES_PER_BYTE

# Display-relevant registers tracked in _reg_write_log for banded rendering.
# Command-engine registers (R#32-R#46) and SAT registers are excluded. R#18 is
# tracked so a mid-frame display-adjust change (per-region dot scroll, e.g. a
# split screen that dot-scrolls only its lower half) shifts the correct bands.
_DISPLAY_REGS: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 18, 19, 23})

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
    hardware command engine.

    Integer-width contract (for a Rust/C++ port; consistent with the CPU
    Registers width contract): ``vram`` bytes and ``regs`` / ``cmd_regs`` /
    ``status`` entries are u8; the VRAM address (``_addr``) is 17-bit (kept
    masked ``& 0x1FFFF``); palette entries are packed 9-bit GRB
    (``(r << 6) | (g << 3) | b``). Values that are NOT a hardware-register
    width and must be typed signed in a port: screen / sprite / command
    coordinates that can go negative before clipping (e.g. ``x_byte -= 32``,
    ``_cmd_x`` / ``_cmd_y``, the Bresenham error terms) → i16; the command
    T-state countdown ``_cmd_remaining``, decremented below zero and tested
    ``<= 0`` → i32."""

    vram: bytearray = field(default_factory=lambda: bytearray(_VRAM_SIZE))
    regs: list[int] = field(default_factory=lambda: [0] * _NUM_REGS)
    status: int = 0
    palette: list[int] = field(default_factory=lambda: list(_MSX2_DEFAULT_PALETTE))
    # Not read by V9938 itself (interrupts are signalled via the polled `irq`
    # property instead); kept for structural compatibility with the MSX1 VDP
    # class, since some call sites (e.g. tests/vdp/test_renderer_g4.py) pass a
    # V9938 instance through msx/vdp/renderer.py's (MSX1) render_frame/_finalize,
    # which does read this field.
    on_interrupt: Callable[[], None] | None = None
    # Observation seam for scanline timing (tests, tracing): called with the
    # raster line at the end of begin_scanline(). A field, not a monkey-patched
    # method, so the same observation is expressible in a Rust/C++ port.
    on_scanline: Callable[[int], None] | None = None
    # Portability note: these Callable hooks (on_interrupt, on_scanline, tracer,
    # _get_pc, _get_cycle) are stored Python closures with no direct static-typed
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
    _cmd_sx: int = field(default=0, init=False, repr=False)  # LMCM source origin
    _cmd_sy: int = field(default=0, init=False, repr=False)
    _cmd_nx: int = field(default=0, init=False, repr=False)
    _cmd_ny: int = field(default=0, init=False, repr=False)
    _cmd_x: int = field(default=0, init=False, repr=False)
    _cmd_y: int = field(default=0, init=False, repr=False)
    _cmd_log: int = field(default=0, init=False, repr=False)
    _tr_delay: int = field(default=0, init=False, repr=False)  # cycles until TR re-asserts
    # The POINT result and each LMCM dot land in the colour register R#44
    # (cmd_regs[12]) as on hardware, where S#7 simply reads that register back;
    # there is no separate S#7 latch.
    _status8: int = field(default=0, init=False, repr=False)  # SRCH result X low
    _status9: int = field(default=0, init=False, repr=False)  # SRCH result X high
    _cmd_xstep: int = field(default=1, init=False, repr=False)  # DIX direction
    _cmd_ystep: int = field(default=1, init=False, repr=False)  # DIY direction
    _cmd_bpl: int = field(default=128, init=False, repr=False)  # bytes per line
    _cmd_ppb: int = field(default=2, init=False, repr=False)  # pixels per byte
    _cmd_bpp: int = field(default=4, init=False, repr=False)  # bits per pixel
    # Standard internals
    _addr: int = field(default=0, init=False, repr=False)
    _latch: int | None = field(default=None, init=False, repr=False)
    _pal_latch: int | None = field(default=None, init=False, repr=False)
    _read_buf: int = field(default=0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _ie1_warned: bool = field(default=False, init=False, repr=False)
    _status1: int = field(default=0, init=False, repr=False)
    # Sprite collision coordinates (S#3-S#6), already biased by +12 dots / +8
    # lines the way the hardware reports them. Written by the sprite renderer
    # when it flags C, cleared by a read of S#5.
    collision_x: int = field(default=0, init=False, repr=False)
    collision_y: int = field(default=0, init=False, repr=False)
    display_line: int = field(default=0, init=False, repr=False)
    _line_cycle: int = field(default=0, init=False, repr=False)  # T-states into current scanline
    # Precomputed interrupt-request line state. A plain attribute (not a
    # @property) so the per-instruction `cpu.int_pending = vdp.irq` read in the
    # machine loop is a slot load, not a descriptor call. Updated only in
    # _update_irq()/reset(); read-only by convention for external consumers.
    irq: bool = field(default=False, init=False, repr=False)
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
    # Instance-owned RGB24 conversion cache (see to_rgb24): the indexed-mode
    # channel tables and the palette snapshot they were built from. Per-instance
    # rather than a module global so two VDPs / a reset don't collide.
    _rgb_lut_key: tuple[int, ...] = field(default=(), init=False, repr=False)
    _rgb_channels: tuple[bytes, bytes, bytes] = field(
        default_factory=lambda: (b"", b"", b""), init=False, repr=False
    )

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

    def _set_addr_high(self, r14: int) -> None:
        """Writing R#14 relocates A14-A16 of the live VRAM address pointer.

        On the V9938 R#14 IS the top 3 bits of the address counter, so a write
        to it moves the current pointer's bank immediately (some software sets
        the low address first, then selects the bank via R#14 before streaming
        VRAM data). Keeping R#14 only as a static register would leave the
        pointer in the previous bank and write to the wrong VRAM address.
        """
        self._addr = ((r14 & 0x07) << 14) | (self._addr & 0x3FFF)

    @property
    def read_buf(self) -> int:
        return self._read_buf

    @read_buf.setter
    def read_buf(self, value: int) -> None:
        self._read_buf = value

    @property
    def display_height(self) -> int:
        """192 lines by default; 212 when R#9 bit 7 (LN) is set."""
        return 212 if (self.regs[9] & 0x80) else 192

    @property
    def vblank_start_line(self) -> int:
        """Raster line whose begin_scanline() raises the VBlank F flag (S#0 b7).

        Single source for the vertical-blanking boundary: Machine.run_frame()
        reads it to decide where to split the frame and render."""
        return self.display_height

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
        ie1 = bool(self.regs[0] & _R0_IE1)
        fh = bool(self._status1 & _S1_FH)
        return (ie0 and f) or (ie1 and fh)

    def _update_irq(self) -> None:
        self.irq = self.irq_pending()

    def _apply_r0_write(self, new_r0: int) -> None:
        """Handle side effects of writing R#0 before the new value is stored.

        IE1 falling edge clears FH (openMSX VDP.cc:1182): a split program that
        re-enables IE1 in the vblank ISR must not see a stale FH latched while
        IE1 was off.

        R#0 also selects the screen mode, and the command engine only runs in
        the bitmap modes. Leaving them aborts the command in progress
        (openMSX VDPCmdEngine::updateDisplayMode → commandDone).
        """
        if (self.regs[0] & _R0_IE1) and not (new_r0 & _R0_IE1):
            self._status1 &= ~_S1_FH
        if self._cmd_active and not _is_command_mode(new_r0):
            self._cmd_active = False
            self._cmd_code = _CMD_ABRT
            self._cmd_remaining = 0
            self._tr_delay = 0
            self._status2 &= ~_S2_CE

    @property
    def framebuffer_format(self) -> FramebufferFormat:
        """SCREEN 8 (G7, M4+M5) stores direct GRB332 pixels; all other modes
        store 4-bit palette indices."""
        r0 = self.regs[0]
        is_g7 = bool((r0 >> 2) & 1) and bool((r0 >> 3) & 1)
        return FramebufferFormat.GRB332 if is_g7 else FramebufferFormat.PALETTE_INDEX4

    def to_rgb24(self, src: bytearray) -> bytes:
        """Convert a palette-index / SCREEN 8 framebuffer to packed RGB24
        (programmable palette, GRB332, and mid-frame banded paths)."""
        # Lazy import: v9938_renderer imports this module at load time, so the
        # dependency is one-directional at import and resolved here at call time.
        from msx.vdp import v9938_renderer
        return v9938_renderer.to_rgb24(self, src)

    def reset(self) -> None:
        """Restore power-on register/status/command-engine state (VRAM retained)."""
        self.regs = [0] * _NUM_REGS
        self.status = 0
        self.palette = list(_MSX2_DEFAULT_PALETTE)
        self.cmd_regs = [0] * 15
        self._status1 = 0
        self._status2 = 0
        self._status8 = 0
        self._status9 = 0
        self.collision_x = 0
        self.collision_y = 0
        self._cmd_active = False
        self._cmd_remaining = 0
        self._cmd_code = 0
        self._cmd_transfer = False
        self._tr_delay = 0
        self._cmd_x = 0
        self._cmd_y = 0
        self._addr = 0
        self._latch = None
        self._pal_latch = None
        self._read_buf = 0
        self.irq = False
        self.display_line = 0
        self._line_cycle = 0

    def begin_scanline(self, line: int) -> None:
        if line == 0:
            self._frame_start_regs = self.regs[:]
            self._frame_start_palette = self.palette[:]
            self._reg_write_log.clear()
        self.display_line = line
        self._line_cycle = 0  # new scanline: restart the horizontal-retrace timer
        dh = self.vblank_start_line
        if line == dh:
            self.status |= 0x80  # VBlank F
            self._update_irq()
        # Line interrupt: R#19 is an 8-bit raster line that may target any line
        # in the field (0-255), including the border/vblank region — not only the
        # active display. Lines >= 256 can never match the 8-bit compare.
        # Subtracting R#23 (vertical scroll) is intentional and matches openMSX
        # (VDP.cc scheduleHScan): the split line is compared as (R#19 - R#23) & 0xFF.
        # FH (S#1 bit0) is gated by IE1: openMSX only raises irqHorizontal when
        # IE1 is set (VDP.cc:412). A split program that parks the compare in the
        # bottom border with IE1 cleared must NOT latch FH, or re-enabling IE1 in
        # the vblank ISR fires a spurious interrupt (see extras notes).
        # regs are u8 (0-255); the & 0xFF gives the 8-bit wrapping difference
        # (regs[19] - regs[23] may go negative — a port uses u8 wrapping_sub).
        effective = (self.regs[19] - self.regs[23]) & 0xFF
        if line == effective and (self.regs[0] & _R0_IE1):
            self._status1 |= _S1_FH
            self._update_irq()
        if self.on_scanline is not None:
            self.on_scanline(line)

    # ------------------------------------------------------------------
    # Command timer
    # ------------------------------------------------------------------

    def tick(self, cycles: int) -> None:
        """Advance VDP command timer. Clears CE when _cmd_remaining reaches 0."""
        self._line_cycle += cycles  # horizontal position within the current scanline
        if self._cmd_remaining > 0:
            self._cmd_remaining -= cycles
            if self._cmd_remaining <= 0:
                # Completion clears CE only: TR keeps its value until the next
                # R#44 write / S#7 read taken with no command running
                # (openMSX commandDone(), which never touches TR).
                self._cmd_active = False
                self._cmd_code = _CMD_ABRT
                self._status2 &= ~_S2_CE
        if self._tr_delay > 0:
            self._tr_delay -= cycles
            if self._tr_delay <= 0 and self._cmd_active:
                self._status2 |= _S2_TR

    # ------------------------------------------------------------------
    # Port I/O
    # ------------------------------------------------------------------

    def _write_command_register(self, index: int, value: int) -> None:
        """Write one of the command registers R#32-R#46.

        R#44 (CLR) is the CPU's data path into a running HMMC/LMMC: every write
        stages a dot, and the engine consumes it at once while a transfer is
        active (openMSX setCmdReg case 0x0C). With no command running the same
        write clears TR instead. R#46 (CMR) dispatches.
        """
        if index == 46:
            self.cmd_regs[14] = value
            self._dispatch_command()
            return
        self.cmd_regs[index - 32] = value
        if index == 44:
            self._cmd_transfer = True  # COL write → transfer latch
            if self._cmd_active:
                if self._cmd_code in (_CMD_HMMC, _CMD_LMMC):
                    self._cmd_data_write(value)
            else:
                self._status2 &= ~_S2_TR

    def write_port(self, port: int, value: int) -> None:
        """Dispatch a V9938 VDP port write.

        The V9938 decodes two address bits, so it has exactly four ports and
        any wider decode on the machine side mirrors them (Handbook Table 4.3;
        openMSX VDP::writeIO switches on `port & 0x03`):
        port 0 = VRAM data (auto-increments the 17-bit address); port 1 =
        control (two-byte latch: register write or VRAM address setup);
        port 2 = palette data; port 3 = indirect register access
        (R#17-pointed register).
        """
        value &= 0xFF
        port &= 0x03
        if port == 0:
            self.vram[self._addr] = value
            self._addr = (self._addr + 1) & 0x1FFFF
        elif port == 1:
            self._write_control_port(value)
        elif port == 2:
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
        else:
            self._write_indirect_register(value)

    def _write_control_port(self, value: int) -> None:
        """Port 1: two-byte latch, register write or VRAM address setup."""
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
                    if reg == 0:
                        self._apply_r0_write(low)
                    self.regs[reg] = low
                    if reg in _DISPLAY_REGS:
                        self._reg_write_log.append(_RegChange(self.display_line, reg, low))
                    if reg <= 1:  # R#0 (IE1) / R#1 (IE0) affect the IRQ line
                        self._update_irq()
                    elif reg == 14:
                        self._set_addr_high(low)
                elif 32 <= reg <= 46:
                    self._write_command_register(reg, low)
            else:
                # Combine 14-bit address from this write with R#14 high bits.
                self._addr = (self.regs[14] & 0x07) << 14 | (value & 0x3F) << 8 | low
                if not (value & 0x40):  # bit6=0 → read mode: preload buffer
                    self._read_buf = self.vram[self._addr]
                    self._addr = (self._addr + 1) & 0x1FFFF

    def _write_indirect_register(self, value: int) -> None:
        """Port 3: the byte goes to whichever register R#17 points at.
        Streaming command data to R#44 is just this path with R#17 = 44 and
        AII set, so there is no separate data port."""
        ptr = self.regs[17] & 0x3F
        r17_before = self.regs[17]
        if ptr < _NUM_REGS:
            if ptr == 0:
                self._apply_r0_write(value)
            self.regs[ptr] = value
            if ptr in _DISPLAY_REGS:
                self._reg_write_log.append(_RegChange(self.display_line, ptr, value))
            if ptr <= 1:  # R#0 (IE1) / R#1 (IE0) affect the IRQ line
                self._update_irq()
            elif ptr == 14:
                self._set_addr_high(value)
        elif 32 <= ptr <= 46:
            self._write_command_register(ptr, value)
        if self.tracer is not None:
            pc = self._get_pc() if self._get_pc is not None else 0
            cy = self._get_cycle() if self._get_cycle is not None else 0
            self.tracer.port9b_write(pc, cy, value, r17=r17_before, frame=self._frame_count)
        # Auto-increment the R#17 pointer from its value BEFORE this write.
        # The MSX2 BIOS writes a full R#0-R#23 table via auto-increment; when
        # the pointer reaches R#17 the table stores a value into R#17 itself,
        # and the sequence must continue to R#18 (not restart from the value
        # just written). The AII bit reflects the post-write R#17.
        if not (r17_before & 0x80):  # AII (bit7) clear → auto-increment
            self.regs[17] = (self.regs[17] & 0xC0) | ((ptr + 1) & 0x3F)

    def read_port(self, port: int) -> int:
        """Dispatch a V9938 VDP port read.

        Two address bits, as in write_port: port 0 = VRAM data (returns the
        read-ahead buffer, refills it, auto-increments the 17-bit address);
        port 1 = status register S#n, where R#15 selects which S#n is returned
        (reading also resets the control latch). Ports 2 and 3 are write-only
        and read as 0xFF (openMSX VDP::readIO cases 2/3).
        """
        port &= 0x03
        if port == 0:
            result = self._read_buf
            self._read_buf = self.vram[self._addr]
            self._addr = (self._addr + 1) & 0x1FFFF
            return result
        if port == 1:
            return self._read_status_register()
        return 0xFF  # ports 2 and 3 (palette / indirect register) are write-only

    def _read_status_register(self) -> int:
        """Port 1: S#n selected by R#15. Reading also resets the port-99
        write latch (the second-byte flip-flop) on real V9938 hardware,
        regardless of which status register R#15 selects. Resetting it only
        for S#0 lets a latch desync (e.g. a write interrupted mid-sequence)
        persist forever, corrupting every subsequent R#nn / address write.
        """
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
            # S#7 reads back the colour register, which holds the POINT
            # result or the dot an LMCM has staged. The read doubles as the
            # LMCM handshake: taking the dot lets the engine fetch the next
            # one. With no command running it clears TR instead (openMSX
            # VDP.cc readStatusReg case 7 → VDPCmdEngine::resetColor).
            result = self.cmd_regs[12]
            if self._cmd_active and self._cmd_code == _CMD_LMCM:
                self._advance_lmcm()
            elif not self._cmd_active:
                self._status2 &= ~_S2_TR
            return result
        if self.regs[15] == 1:
            result = self._status1
            self._status1 &= ~_S1_FH  # reading S#1 clears FH
            self._update_irq()
            return result & 0xFF
        if self.regs[15] == 8:
            return self._status8
        if self.regs[15] == 9:
            # Reading S#9 is the only thing that clears BD (openMSX VDP.cc
            # readStatusReg case 9 → resetBD); a SRCH that finds nothing
            # leaves the previous flag standing.
            result = self._status9
            self._status2 &= ~_S2_BD
            return result
        if self.regs[15] == 3:
            # S#3-S#6: sprite collision X/Y, as reported by the sprite
            # renderer (Handbook Sec. 5.3.3). The unused high bits read as 1
            # (openMSX VDP::peekStatusReg cases 3-6: `| 0xFE` / `| 0xFC`).
            return self.collision_x & 0xFF
        if self.regs[15] == 4:
            return ((self.collision_x >> 8) & 0x01) | 0xFE
        if self.regs[15] == 5:
            result = self.collision_y & 0xFF
            self.collision_x = 0  # reading S#5 resets the coordinate pair
            self.collision_y = 0
            return result
        if self.regs[15] == 6:
            return ((self.collision_y >> 8) & 0x03) | 0xFC
        if self.regs[15] >= 10:
            return 0xFF  # non-existent status register
        # S#0: bit7=F (frame flag), bit6=5S, bit5=C, bits4-0 = 5th/9th
        # sprite number. The renderer already stores the real overflowing
        # sprite's index in bits 4:0 alongside 5S (see v9938_renderer.py's
        # `(vdp.status & 0xA0) | 0x40 | (i & 0x1F)`), so preserve it here
        # when 5S is set. When idle (no 5th/9th sprite this frame), fall
        # back to 31 (0x1F): returning 0 there stalls MSX2 C-BIOS
        # cartridge boot, which feeds S#0 bits 4:0 into its cartridge-scan
        # loop counter.
        if self.status & 0x40:
            result = self.status & 0xFF
        else:
            result = (self.status & 0xE0) | 0x1F
        self.status &= ~0xE0  # clear F, 5S and C flags together
        self._update_irq()
        return result & 0xFF

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

    def _vram_byte_addr_col(self, col: int, y: int) -> int:
        """Linear VRAM address of byte column `col` on row y (byte-unit commands)."""
        return (y * self._cmd_bpl + col % self._cmd_bpl) & 0x1FFFF

    def _vram_pixel_read(self, x: int, y: int) -> int:
        """Return the pixel value at (x, y) for the current screen mode."""
        byte = self.vram[self._vram_byte_addr(x, y)]
        shift = (self._cmd_ppb - 1 - (x % self._cmd_ppb)) * self._cmd_bpp
        return (byte >> shift) & ((1 << self._cmd_bpp) - 1)

    def _vram_pixel_write(self, x: int, y: int, color: int, log: int) -> None:
        """Write a pixel at (x, y) applying the V9938 LOG operation."""
        mask = (1 << self._cmd_bpp) - 1
        src = color & mask
        if (log & 0x7) > 4:  # undefined logical operation: no write (openMSX DummyOp)
            return
        if (log & 0x8) and src == 0:  # transparent: skip zero source pixels
            return
        addr = self._vram_byte_addr(x, y)
        existing = self.vram[addr]
        shift = (self._cmd_ppb - 1 - (x % self._cmd_ppb)) * self._cmd_bpp
        dst = (existing >> shift) & mask
        result = _apply_log(src, dst, log & 0x7, mask) & mask
        self.vram[addr] = (existing & ~(mask << shift) & 0xFF) | (result << shift)

    def _byte_cmd_cycles(self, byte_cols: int, ny: int) -> int:
        """CE duration for a byte-unit command (HMMV/HMMM/YMMM).

        Each of the byte_cols × ny VRAM bytes costs _CYCLES_PER_BYTE T-states.
        Clamped to a 4 T-state minimum.
        """
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
        # LMMC/LMCM). The transfer handlers key off _cmd_code, so a completed
        # synchronous command (LMMV/LMMM/HMMV/HMMM/YMMM/LINE/SRCH) must overwrite
        # any stale HMMC/LMMC code, otherwise a later R#44 write is misrouted
        # into the transfer path and corrupts the just-drawn region.
        self._cmd_code = cmd

        self._cmd_bpl, self._cmd_ppb, self._cmd_bpp = self._cmd_geometry()
        px_mask = (1 << self._cmd_bpp) - 1
        sw = self._cmd_bpl * self._cmd_ppb  # screen width in dots

        sx = self.cmd_regs[0] | ((self.cmd_regs[1] & 0x01) << 8)
        sy = self.cmd_regs[2] | ((self.cmd_regs[3] & 0x03) << 8)
        dx = self.cmd_regs[4] | ((self.cmd_regs[5] & 0x01) << 8)
        dy = self.cmd_regs[6] | ((self.cmd_regs[7] & 0x03) << 8)
        nx = self.cmd_regs[8] | ((self.cmd_regs[9] & 0x03) << 8)
        ny = self.cmd_regs[10] | ((self.cmd_regs[11] & 0x03) << 8)
        clr = self.cmd_regs[12]
        arg = self.cmd_regs[13]
        dix = bool(arg & _ARG_DIX)
        diy = bool(arg & _ARG_DIY)
        xs = -1 if dix else 1  # DIX: 1 = leftward
        ys = -1 if diy else 1  # DIY: 1 = upward
        src_ext = bool(arg & _ARG_MXS)  # source in (absent) expansion RAM
        dst_ext = bool(arg & _ARG_MXD)  # destination in (absent) expansion RAM

        self._cmd_active = False
        self._status2 &= ~_S2_CE  # completion leaves TR alone (openMSX commandDone)

        # Commands are only defined in the bitmap modes; anywhere else the
        # dispatch finishes immediately without touching VRAM. Codes 1-3 are
        # reserved and behave as STOP, as does STOP itself.
        if cmd == _CMD_ABRT or cmd < _CMD_POINT or not _is_command_mode(self.regs[0]):
            self._cmd_code = _CMD_ABRT
            self._cmd_remaining = 0
            self._tr_delay = 0
            return

        if cmd == _CMD_POINT:
            # The sampled dot lands in the colour register, where S#7 reads it.
            self.cmd_regs[12] = 0xFF if src_ext else self._vram_pixel_read(sx, sy) & px_mask
            return

        if cmd == _CMD_PSET:
            if not dst_ext:
                self._vram_pixel_write(dx, dy, clr & px_mask, log)
            return

        if cmd == _CMD_SRCH:
            stop_on_ne = bool(arg & _ARG_EQ)
            clr_px = clr & px_mask
            x = sx
            found = False
            while 0 <= x < sw:
                pix = 0xFF if src_ext else self._vram_pixel_read(x, sy)
                hit = (pix != clr_px) if stop_on_ne else (pix == clr_px)
                if hit:
                    found = True
                    break
                x += xs
            # Result X coordinate goes to S#8/S#9; S#9 upper bits read as 1.
            # Hardware exposes the scan counter whether or not the colour was
            # found. BD is only ever SET here: a search that runs into the
            # border leaves it untouched, and only an S#9 read clears it.
            self._status8 = x & 0xFF
            self._status9 = 0xFE | ((x >> 8) & 0x01)
            if found:
                self._status2 |= _S2_BD
            return

        if cmd == _CMD_LINE:
            self._draw_line(dx, dy, nx, ny & 0x3FF, clr & px_mask, log, arg, xs, ys, sw)
            return

        if cmd == _CMD_LMCM:
            self._cmd_sx = sx
            self._cmd_sy = sy
            self._cmd_nx = _clip_nx_dot(sx, nx, dix, sw)
            self._cmd_ny = _clip_ny(sy, ny, diy)
            self._cmd_x = 0
            self._cmd_y = 0
            self._cmd_xstep = xs
            self._cmd_ystep = ys
            self._cmd_active = True
            self._status2 |= _S2_CE | _S2_TR
            # The first dot is fetched at once (openMSX startLmcm sets its
            # transfer flag); every S#7 read takes one and stages the next.
            self.cmd_regs[12] = 0xFF if src_ext else self._vram_pixel_read(sx, sy) & px_mask
            return

        if cmd == _CMD_LMMV:
            actual_nx = _clip_nx_dot(dx, nx, dix, sw)
            actual_ny = _clip_ny(dy, ny, diy)
            clr_px = clr & px_mask
            if not dst_ext:
                for row in range(actual_ny):
                    yy = dy + row * ys
                    for col in range(actual_nx):
                        self._vram_pixel_write(dx + col * xs, yy, clr_px, log)
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._pixel_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_LMMM:
            actual_nx = _clip_nx_dot_pair(sx, dx, nx, dix, sw)
            actual_ny = _clip_ny_pair(sy, dy, ny, diy)
            if not dst_ext:
                for row in range(actual_ny):
                    syy = sy + row * ys
                    dyy = dy + row * ys
                    for col in range(actual_nx):
                        src_pix = (px_mask if src_ext
                                   else self._vram_pixel_read(sx + col * xs, syy))
                        self._vram_pixel_write(dx + col * xs, dyy, src_pix, log)
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._pixel_cmd_cycles(actual_nx, actual_ny)
            return

        if cmd == _CMD_HMMV:
            # Byte-unit: NX is truncated to whole bytes and the engine walks
            # byte columns, so an NX that is not a multiple of ppb covers fewer
            # dots rather than part-writing an extra byte.
            byte_cols = _clip_nx_byte(dx, nx, dix, self._cmd_ppb, self._cmd_bpl)
            actual_ny = _clip_ny(dy, ny, diy)
            start_col = dx // self._cmd_ppb
            if not dst_ext:
                for row in range(actual_ny):
                    yy = dy + row * ys
                    for c in range(byte_cols):
                        self.vram[self._vram_byte_addr_col(start_col + c * xs, yy)] = clr
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(byte_cols, actual_ny)
            return

        if cmd == _CMD_HMMM:
            byte_cols = _clip_nx_byte_pair(sx, dx, nx, dix, self._cmd_ppb, self._cmd_bpl)
            actual_ny = _clip_ny_pair(sy, dy, ny, diy)
            src_col = sx // self._cmd_ppb
            dst_col = dx // self._cmd_ppb
            if not dst_ext:
                for row in range(actual_ny):
                    syy = sy + row * ys
                    dyy = dy + row * ys
                    for c in range(byte_cols):
                        src = self._vram_byte_addr_col(src_col + c * xs, syy)
                        dst = self._vram_byte_addr_col(dst_col + c * xs, dyy)
                        self.vram[dst] = 0xFF if src_ext else self.vram[src]
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(byte_cols, actual_ny)
            return

        if cmd == _CMD_YMMM:
            # Y-direction copy: vertical strip at X=DX (NX ignored, the strip
            # runs to whichever screen edge DIX points at); source row SY →
            # destination row DY, both at the same X.
            byte_cols = _clip_nx_byte(dx, 0, dix, self._cmd_ppb, self._cmd_bpl)
            actual_ny = _clip_ny_pair(sy, dy, ny, diy)
            start_col = dx // self._cmd_ppb
            if not dst_ext:
                for row in range(actual_ny):
                    syy = sy + row * ys
                    dyy = dy + row * ys
                    for c in range(byte_cols):
                        col = start_col + c * xs
                        self.vram[self._vram_byte_addr_col(col, dyy)] = \
                            self.vram[self._vram_byte_addr_col(col, syy)]
            self._cmd_active = True
            self._status2 |= _S2_CE
            self._cmd_remaining = self._byte_cmd_cycles(byte_cols, actual_ny)
            return

        # HMMC (0xF) or LMMC (0xB): CPU-feed transfer; tick() must not time out via _cmd_remaining.
        self._cmd_remaining = 0
        self._cmd_active = True
        self._cmd_dx = dx
        self._cmd_dy = dy
        if cmd == _CMD_HMMC:
            self._cmd_nx = _clip_nx_byte(dx, nx, dix, self._cmd_ppb,
                                         self._cmd_bpl) * self._cmd_ppb
        else:
            self._cmd_nx = _clip_nx_dot(dx, nx, dix, sw)
        self._cmd_ny = _clip_ny(dy, ny, diy)
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

    def _draw_line(self, dx: int, dy: int, nx: int, ny: int, colour: int,
                   log: int, arg: int, xs: int, ys: int, sw: int) -> None:
        """Bresenham line from (dx, dy), following openMSX executeLine.

        NX is always the major (long) side and NY the minor; MAJ (ARG bit 0)
        only chooses which screen axis the major side runs along. The line
        covers NX + 1 dots and terminates early when X crosses the line width
        or an upward DIY walks Y above the top border.
        """
        maj_y = bool(arg & _ARG_MAJ)
        dst_ext = bool(arg & _ARG_MXD)
        err = (nx - 1) // 2 if nx else 0  # openMSX ASX
        x = dx
        y = dy
        steps = 0  # openMSX ANX
        while True:
            if not dst_ext:
                self._vram_pixel_write(x, y, colour, log)
            if not maj_y:
                # X is the major axis: the end test happens before Y advances,
                # and the X-edge test uses an AND against the line width.
                x += xs
                done = steps == nx or bool(x & sw)
                steps += 1
                if done:
                    return
                if err < ny:
                    err += nx
                    y += ys
                    if ys < 0 and y < 0:  # above the top border: stop
                        return
                err = (err - ny) & 0x3FF
            else:
                # Y is the major axis: Y advances before the end test.
                y += ys
                if ys < 0 and y < 0:
                    return
                if err < ny:
                    err += nx
                    x += xs
                err = (err - ny) & 0x3FF
                done = steps == nx or bool(x & sw)
                steps += 1
                if done:
                    return

    def _cmd_data_write(self, value: int) -> None:
        """Consume one CPU transfer byte (an R#44 write) for an active HMMC/LMMC."""
        if not self._cmd_active or self._cmd_code == _CMD_LMCM:
            return
        # A pending byte is being consumed → clear the transfer latch.
        self._cmd_transfer = False
        # TR=0 while VDP processes; tick() re-asserts TR after _CYCLES_PER_BYTE
        self._status2 &= ~_S2_TR
        self._tr_delay = _CYCLES_PER_BYTE
        px = self._cmd_dx + self._cmd_x * self._cmd_xstep
        py = self._cmd_dy + self._cmd_y * self._cmd_ystep
        dst_ext = bool(self.cmd_regs[13] & _ARG_MXD)
        if self._cmd_code == _CMD_LMMC:
            # LMMC: CPU sends one byte per pixel; color in lower bpp bits.
            mask = (1 << self._cmd_bpp) - 1
            if not dst_ext:
                self._vram_pixel_write(px, py, value & mask, self._cmd_log)
            self._cmd_x += 1
        else:
            # HMMC: high-speed byte copy, no logical operation; one byte = ppb pixels.
            if not dst_ext:
                self.vram[self._vram_byte_addr(px, py)] = value
            self._cmd_x += self._cmd_ppb
        if self._cmd_x >= self._cmd_nx:
            self._cmd_x = 0
            self._cmd_y += 1
            if self._cmd_y >= self._cmd_ny:
                self._cmd_active = False
                self._cmd_code = _CMD_ABRT
                self._tr_delay = 0
                self._status2 &= ~_S2_CE  # TR survives completion

    def _advance_lmcm(self) -> None:
        """Take the staged LMCM dot and fetch the next one.

        Driven by the CPU reading S#7 (or the data port): one dot per read,
        walking the source rectangle in the DIX/DIY directions.
        """
        self._cmd_x += 1
        if self._cmd_x >= self._cmd_nx:
            self._cmd_x = 0
            self._cmd_y += 1
            if self._cmd_y >= self._cmd_ny:
                self._cmd_active = False
                self._cmd_code = _CMD_ABRT
                self._status2 &= ~_S2_CE  # TR survives completion
                return
        x = self._cmd_sx + self._cmd_x * self._cmd_xstep
        y = self._cmd_sy + self._cmd_y * self._cmd_ystep
        if self.cmd_regs[13] & _ARG_MXS:
            self.cmd_regs[12] = 0xFF
        else:
            self.cmd_regs[12] = self._vram_pixel_read(x, y) & ((1 << self._cmd_bpp) - 1)
