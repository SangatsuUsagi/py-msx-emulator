from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger
    from msx.vdp.tracer import Tracer


class FramebufferFormat(Enum):
    """Meaning of each byte in a VDP index framebuffer (see VDP.to_rgb24).

    PALETTE_INDEX4 — the low 4 bits index the (fixed or programmable) 16-colour
    palette. GRB332 — the whole byte is a direct SCREEN 8 (G7) colour. A port
    carries this alongside the buffer instead of re-deriving it from registers.
    """

    PALETTE_INDEX4 = auto()
    GRB332 = auto()


# ---------------------------------------------------------------------------
# Palette-index → packed RGB24 conversion (shared by the VDP.to_rgb24 methods)
# ---------------------------------------------------------------------------


def _channel_tables_indexed(lut16: list[bytes]) -> tuple[bytes, bytes, bytes]:
    """Three 256-byte (R, G, B) translate tables for a 16-entry RGB24 LUT.

    Table index i maps through (i & 0x0F), folding the 4-bit palette-index mask
    into the table so bytes.translate needs no separate masking step.
    """
    return (
        bytes(lut16[i & 0x0F][0] for i in range(256)),
        bytes(lut16[i & 0x0F][1] for i in range(256)),
        bytes(lut16[i & 0x0F][2] for i in range(256)),
    )


def _translate_rgb24(src: bytearray, channels: tuple[bytes, bytes, bytes]) -> bytes:
    """Map an 8-bit-index buffer to packed RGB24 via per-channel bytes.translate.

    Each of the three 256-byte tables maps a source byte to one output channel;
    the strided slice assignment interleaves them. Three C-level translate calls
    replace a per-pixel Python loop (~5-8x faster per frame).
    """
    rtab, gtab, btab = channels
    out = bytearray(len(src) * 3)
    out[0::3] = src.translate(rtab)
    out[1::3] = src.translate(gtab)
    out[2::3] = src.translate(btab)
    return bytes(out)


# Standard TMS9918A hardware palette — 16 (R, G, B) triples.
# Index 0 = transparent (rendered as black).
TMS9918A_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0,   0,   0),    # 0  transparent / black
    (0,   0,   0),    # 1  black
    (33,  200, 66),   # 2  medium green
    (94,  220, 120),  # 3  light green
    (84,  85,  237),  # 4  dark blue
    (125, 118, 252),  # 5  light blue
    (212, 82,  77),   # 6  dark red
    (66,  235, 245),  # 7  cyan
    (252, 85,  84),   # 8  medium red
    (255, 121, 120),  # 9  light red
    (212, 193, 84),   # 10 dark yellow
    (230, 206, 128),  # 11 light yellow
    (33,  176, 59),   # 12 dark green
    (201, 91,  186),  # 13 magenta
    (204, 204, 204),  # 14 grey
    (255, 255, 255),  # 15 white
)
_TMS_CHANNELS: tuple[bytes, bytes, bytes] = _channel_tables_indexed(
    [bytes(c) for c in TMS9918A_PALETTE]
)


@dataclass
class VDP:
    """TMS9918A VDP for MSX1: 16 KB VRAM, 8 registers.

    Integer-width contract (for a Rust/C++ port; consistent with the CPU
    Registers width contract): ``vram`` bytes and ``regs`` / ``status`` entries
    are u8; the VRAM address (``addr``) is 14-bit (kept masked ``& 0x3FFF``).
    Sprite X positions can go negative before clipping (``x_byte -= 32``) and
    must be typed signed (i16) in a port.
    """

    vram: bytearray = field(default_factory=lambda: bytearray(0x4000))
    regs: list[int] = field(default_factory=lambda: [0] * 8)
    addr: int = 0
    latch: int | None = None
    status: int = 0
    read_buf: int = 0
    on_interrupt: Callable[[], None] | None = None
    _logger: DebugLogger | None = field(default=None, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    # Portability note: these Callable hooks (on_interrupt above, plus the
    # tracer / _get_pc / _get_cycle below) are stored Python closures with no
    # direct static-typed analogue. A Rust/C++ port models them as trait objects
    # or feature-flagged fields resolved once, not per-call function pointers.
    tracer: Tracer | None = field(default=None, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)
    debug_disable_sprites: bool = field(default=False, repr=False)

    @property
    def display_height(self) -> int:
        return 192

    def increment_frame(self) -> None:
        """Advance the completed-frame counter. Called once per frame."""
        self._frame_count += 1

    def reset(self) -> None:
        """Restore power-on register/status state (VRAM is retained)."""
        self.regs = [0] * 8
        self.status = 0
        self.addr = 0
        self.latch = None
        self.read_buf = 0

    def write_port(self, port: int, value: int) -> None:
        """Dispatch a TMS9918A VDP port write.

        0x98 = VRAM data (writes at the current address, auto-increments it);
        0x99 = control (two-byte latch: the second byte either writes a register
        when bit 7 is set, or sets up the VRAM address otherwise).
        """
        value = value & 0xFF
        if port == 0x98:
            self.vram[self.addr] = value
            self.addr = (self.addr + 1) & 0x3FFF
        elif port == 0x99:
            if self.tracer is not None:
                pc = self._get_pc() if self._get_pc is not None else 0
                cy = self._get_cycle() if self._get_cycle is not None else 0
                self.tracer.port99_write(pc, cy, value, frame=self._frame_count)
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
        """Dispatch a TMS9918A VDP port read.

        0x98 = VRAM data (returns the read-ahead buffer, refills it, auto-
        increments the address); 0x99 = status register (clears VBlank + 5th-
        sprite flags and resets the control write latch as a side effect).
        """
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

    @property
    def framebuffer_format(self) -> FramebufferFormat:
        """TMS9918A framebuffers are always 4-bit palette indices."""
        return FramebufferFormat.PALETTE_INDEX4

    def to_rgb24(self, src: bytearray) -> bytes:
        """Convert a palette-index framebuffer to packed RGB24.

        TMS9918A maps each 4-bit index through the fixed hardware palette. V9938
        overrides this with its programmable palette / SCREEN 8 / banded paths.
        Keeping conversion on the VDP lets the frontend stay display-only.
        """
        return _translate_rgb24(src, _TMS_CHANNELS)
