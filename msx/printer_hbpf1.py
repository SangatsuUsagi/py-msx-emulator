"""Sony HBP-F1 printer: renders the PC-PR201-style command stream to pages.

A minimal interpreter of the HBP-F1C control codes (manual excerpt at
references/docs/HBP-F1C 取扱説明書.md): ANK pica/elite/condensed, kanji mode
with JIS pairs and `00 xx` half-width pairs, line/page movement, margins,
double width, dot space and repeat. Every other control code is consumed with
its documented parameter length and written to an unsupported-code log, so
the stream never desynchronises and the log ranks what to implement next.

No printer font ROM is used: ANK glyphs come from the MSX main ROM font and
kanji from the MSX Kanji ROM, scaled nearest-neighbour to the head's cells.
One dot is 1/180 inch in both directions.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from PIL import Image as _PIL_Image

PAGE_WIDTH = 1586  # head line width in dots (manual p.86)
PAGE_HEIGHT = 1980  # 11-inch power-on page length at 180 dpi (p.105)
DPI = 180

_BAND = 24  # line band height: the 24-dot head
_ANK_HEIGHT = 18
_FULL_WIDTH = 24
_HALF_WIDTH = 12
_MIN_PRINT_WIDTH = 72  # ESC L / ESC / ignored below this (p.100)

_PICA = 18
_ELITE = 15
_CONDENSED = 10

_POWER_ON_LINE_PITCH = 30  # 1/6 inch
_POWER_ON_RIGHT_MARGIN = 80 * _PICA

_KANJI_LEVEL2_BASE = 0x20000

_NO_DECORATION = 0
_OUTLINE = 1  # ESC p1
_ITALIC = 2  # ESC p2
_BOLD = 3  # ESC p3

_ESC = 0x1B
_SUB = 0x1A

_CONTROL_NAMES = {
    0x00: "NUL",
    0x02: "STX",
    0x03: "ETX",
    0x04: "EOT",
    0x05: "ENQ",
    0x06: "ACK",
    0x07: "BEL",
    0x08: "BS",
    0x09: "HT (horizontal tab)",
    0x0B: "VT (vertical tab)",
    0x11: "DC1",
    0x18: "CAN (cancel line)",
}


def _digits(data: bytes | bytearray) -> int | None:
    """ASCII decimal digits -> int, or None if any byte is not a digit."""
    value = 0
    for b in data:
        if b < 0x30 or b > 0x39:
            return None
        value = value * 10 + (b - 0x30)
    return value


@dataclass
class PrinterFonts:
    """Glyph sources: the MSX 8x8 font and an optional Kanji ROM image."""

    ank: bytes  # 2048 bytes, 8 bytes per code 0x00-0xFF, bit 7 = leftmost dot
    kanji: bytes | None = None  # 128 KB or 256 KB MSX Kanji ROM


@dataclass
class PrinterPage:
    """One page of dots; 1 = ink."""

    width: int = PAGE_WIDTH
    height: int = PAGE_HEIGHT
    pixels: bytearray = field(default_factory=bytearray)
    _inked: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.pixels:
            self.pixels = bytearray(self.width * self.height)

    def get(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]

    def set(self, x: int, y: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = 1
            self._inked = True

    def has_ink(self) -> bool:
        return self._inked


def ank_glyph(fonts: PrinterFonts, code: int) -> list[int]:
    """8 rows of 8-bit masks for an MSX font code."""
    base = (code & 0xFF) * 8
    rows = []
    for r in range(8):
        rows.append(fonts.ank[base + r] if base + r < len(fonts.ank) else 0)
    return rows


def kanji_glyph(fonts: PrinterFonts, hi: int, lo: int) -> list[int] | None:
    """16 rows of 16-bit masks for a JIS code, or None when the ROM has none.

    Glyph number per the MSX Kanji ROM layout (references/docs/Kanji display
    - MSX Wiki.md, MSX Datapack 6.5.2). Rows 0x29-0x2F (ku 9-15) are not in
    the ROM: their numbers would overlap level-1 kanji, so they count as
    missing.
    """
    rom = fonts.kanji
    if rom is None:
        return None
    ku = hi - 0x20
    ten = lo - 0x20
    if ten < 1 or ten > 94 or ku < 1 or ku > 84 or 9 <= ku <= 15:
        return None
    if ku < 16:
        address = (ku * 96 + ten) * 32
    elif ku < 48:
        address = (ku * 96 + ten - 512) * 32
    else:
        address = _KANJI_LEVEL2_BASE + ((ku - 48) * 96 + ten) * 32
    if address + 32 > len(rom):
        return None
    rows = []
    for r in range(16):
        block = (r // 8) * 16 + (r % 8)
        rows.append((rom[address + block] << 8) | rom[address + block + 8])
    return rows


@dataclass
class GlyphMask:
    """A scaled glyph as dot rows; bit (width - 1 - x) of a row is dot x.

    The offsets place mask column/row 0 relative to the glyph box origin, so
    decorations can spill outside the box (italic right, outline all round).
    """

    width: int
    height: int
    rows: list[int]
    x_offset: int = 0
    y_offset: int = 0


def scale_glyph(rows: list[int], src_w: int, dst_w: int, dst_h: int) -> GlyphMask:
    """Nearest-neighbour scale MSB-left source rows to a dst_w x dst_h mask."""
    src_h = len(rows)
    out = []
    for dy in range(dst_h):
        row = rows[dy * src_h // dst_h]
        dst = 0
        if row:
            for dx in range(dst_w):
                sx = dx * src_w // dst_w
                dst = (dst << 1) | ((row >> (src_w - 1 - sx)) & 1)
        out.append(dst)
    return GlyphMask(dst_w, dst_h, out)


def embolden(rows: list[int]) -> list[int]:
    """Bold: OR each source row with itself one source dot to the right."""
    out = []
    for row in rows:
        out.append(row | (row >> 1))
    return out


def italicise(mask: GlyphMask) -> GlyphMask:
    """Shear right: row dy moves (height - 1 - dy) // 4 dots; the bottom stays."""
    max_shift = (mask.height - 1) // 4
    out = []
    for dy in range(mask.height):
        shift = (mask.height - 1 - dy) // 4
        out.append((mask.rows[dy] << max_shift) >> shift)
    return GlyphMask(mask.width + max_shift, mask.height, out,
                     mask.x_offset, mask.y_offset)


def outline(mask: GlyphMask) -> GlyphMask:
    """白抜き: the dots 8-adjacent to the glyph but not in it (a 1-dot ring)."""
    padded = [0]
    for row in mask.rows:
        padded.append(row << 1)
    padded.append(0)
    out = []
    for i in range(len(padded)):
        grown = 0
        for j in range(i - 1, i + 2):
            if 0 <= j < len(padded):
                p = padded[j]
                grown |= p | (p << 1) | (p >> 1)
        out.append(grown & ~padded[i])
    return GlyphMask(mask.width + 2, mask.height + 2, out,
                     mask.x_offset - 1, mask.y_offset - 1)


def draw_mask(page: PrinterPage, mask: GlyphMask, x0: int, y0: int,
              clip_top: int, clip_bottom: int) -> None:
    """OR a mask onto the page at box origin (x0, y0), rows clipped to
    [clip_top, clip_bottom) -- the line band the head can reach."""
    for i in range(mask.height):
        row = mask.rows[i]
        y = y0 + mask.y_offset + i
        if row == 0 or y < clip_top or y >= clip_bottom:
            continue
        for dx in range(mask.width):
            if (row >> (mask.width - 1 - dx)) & 1:
                page.set(x0 + mask.x_offset + dx, y)


class HbpF1Printer:
    """HBP-F1 printer device for `PrinterPort`; always ready."""

    def __init__(self, fonts: PrinterFonts, on_page: Callable[[PrinterPage], None],
                 log: TextIO) -> None:
        self._fonts = fonts
        self._on_page = on_page
        self._log = log
        self._counts: dict[str, int] = {}
        self._offset = 0
        # Command collection: bytes of the command in progress, how many more
        # to collect (or a terminator byte to collect up to), the handler to
        # run when complete, and a count of bulk bytes to drop unbuffered.
        self._cmd = bytearray()
        self._cmd_offset = 0
        self._need = 0
        self._terminator = -1
        self._handler: Callable[[], None] | None = None
        self._skip = 0
        self.page = PrinterPage()
        self._y = 0
        self._power_on()

    # --- PrinterDevice -------------------------------------------------------

    def is_busy(self) -> bool:
        return False

    def write_byte(self, value: int) -> None:
        value &= 0xFF
        offset = self._offset
        self._offset += 1
        if self._skip > 0:
            self._skip -= 1
            return
        if self._terminator >= 0:
            self._cmd.append(value)
            if value == self._terminator:
                self._terminator = -1
                self._finish()
            return
        if self._need > 0:
            self._cmd.append(value)
            self._need -= 1
            if self._need == 0:
                self._finish()
            return
        self._cmd = bytearray((value,))
        self._cmd_offset = offset
        self._start(value)

    def close(self) -> None:
        if self.page.has_ink():
            self._on_page(self.page)
            self.page = PrinterPage()
        self._log.write("# summary\n")
        ranked = sorted(self._counts.items(), key=lambda item: (-item[1], item[0]))
        for name, count in ranked:
            self._log.write(f"{count}\t{name}\n")
        self._log.close()

    # --- state ---------------------------------------------------------------

    def _power_on(self) -> None:
        self._ank_pitch = _PICA
        self._kanji = False
        self._kanji_pitch = _FULL_WIDTH
        self._half_phase = 0
        self._line_pitch = _POWER_ON_LINE_PITCH
        self._unit_120 = True
        self._reverse = False
        self._double = False
        self._vertical_double = False
        self._underline = False
        self._decoration = _NO_DECORATION
        self._extra_feed = 0
        self._left = 0
        self._right = _POWER_ON_RIGHT_MARGIN
        self._x = self._left

    # --- byte dispatch -------------------------------------------------------

    def _collect(self, count: int, handler: Callable[[], None]) -> None:
        self._need = count
        self._handler = handler

    def _collect_until(self, terminator: int, handler: Callable[[], None]) -> None:
        self._terminator = terminator
        self._handler = handler

    def _finish(self) -> None:
        handler = self._handler
        self._handler = None
        if handler is not None:
            handler()

    def _start(self, value: int) -> None:
        if value < 0x20:
            self._control(value)
        elif self._kanji:
            if 0x21 <= value <= 0x7E:
                self._collect(1, self._jis_pair)
            else:
                self._unsupported("invalid kanji first byte")
        else:
            self._print_ank(value)

    def _control(self, value: int) -> None:
        if value == 0x0D:
            self._carriage_return()
        elif value == 0x0A:
            self._line_feed()
        elif value == 0x0C:
            self._form_feed()
        elif value == 0x0E:
            self._double = True
        elif value == 0x0F:
            self._double = False
        elif value == 0x00 and self._kanji:
            self._collect(1, self._half_pair)
        elif value == 0x01:
            self._collect(1, self._graphic)
        elif value == _ESC:
            self._collect(1, self._esc)
        elif value == _SUB:
            self._collect(1, self._sub)
        elif value == 0x02:
            self._collect(1, self._stx)
        elif value == 0x1D:
            self._collect_until(0x1E, self._log_handler("GS..RS (page length)"))
        elif value == 0x1F:
            self._collect(1, self._log_handler("US (VFU feed)"))
        else:
            self._unsupported(_CONTROL_NAMES.get(value, f"control {value:02X}"))

    def _log_handler(self, name: str) -> Callable[[], None]:
        def handler() -> None:
            self._unsupported(name)
        return handler

    def _esc(self) -> None:
        c = self._cmd[1]
        if 0x01 <= c <= 0x08:
            if self._kanji:
                self._x += c * (2 if self._double else 1)
        elif c in (0x4E, 0x48):  # N, H
            self._select_ank(_PICA)
        elif c == 0x45:  # E
            self._select_ank(_ELITE)
        elif c == 0x51:  # Q
            self._select_ank(_CONDENSED)
        elif c == 0x50:  # P
            self._select_ank(_PICA)
            self._unsupported("ESC P (proportional)")
        elif c == 0x4B:  # K
            self._enter_kanji()
        elif c == 0x74:  # t
            self._enter_kanji()
            self._unsupported("ESC t (vertical kanji mode)")
        elif c == 0x41:  # A
            self._line_pitch = 30
        elif c == 0x42:  # B
            self._line_pitch = 24
        elif c == 0x54:  # T
            self._collect(2, self._esc_t)
        elif c == 0x4C:  # L
            self._collect(3, self._esc_left_margin)
        elif c == 0x2F:  # /
            self._collect(3, self._esc_right_margin)
        elif c == 0x52:  # R
            self._collect(4, self._esc_repeat)
        elif c == 0x58:  # X
            self._underline = True
        elif c == 0x59:  # Y
            self._underline = False
        elif c == 0x72:  # r
            self._reverse = True
        elif c == 0x66:  # f
            self._reverse = False
        elif c == 0x63:  # c
            self._collect(1, self._esc_c)
        elif c == 0x43:  # C
            self._collect(1, self._esc_color_or_script)
        elif c == 0x70:  # p
            self._collect(1, self._esc_p)
        elif c == 0x68:  # h
            self._collect(1, self._esc_h)
        else:
            self._esc_unsupported(c)

    def _esc_unsupported(self, c: int) -> None:
        if c == 0x22:  # ESC ": emphasis off, a default re-select
            return
        if c in (0x53, 0x49, 0x4A):  # S, I, J: N columns of 1/2/3 bytes
            self._collect(4, self._bit_image_handler(c))
        elif c in (0x56, 0x57, 0x55):  # V, W, U: one 1/2/3-byte column repeated
            per = {0x56: 1, 0x57: 2, 0x55: 3}[c]
            self._collect(4 + per, self._log_handler(f"ESC {chr(c)} (bit image repeat)"))
        elif c == 0x46:  # F
            self._collect(4, self._log_handler("ESC F (dot addressing)"))
        elif c == 0x2B:  # +: m1 m2, 72 data bytes, EOT
            self._collect(2, self._gaiji)
        elif c in (0x28, 0x29):  # ( ): tab positions up to "."
            self._collect_until(0x2E, self._log_handler(f"ESC {chr(c)} (horizontal tab)"))
        else:
            names = {
                0x21: "ESC ! (emphasis)",
                0x32: "ESC 2 (clear tabs)",
                0x71: "ESC q (half-width vertical pair)",
                0x44: "ESC D (bit image pitch)",
                0x4D: "ESC M (bit image pitch)",
            }
            self._unsupported(names.get(c, "unknown ESC"))

    def _bit_image_handler(self, c: int) -> Callable[[], None]:
        def handler() -> None:
            count = _digits(self._cmd[2:6])
            per = {0x53: 1, 0x49: 2, 0x4A: 3}[c]
            data = per * count if count is not None else 0
            self._unsupported(f"ESC {chr(c)} (bit image)", data)
            self._skip = data
        return handler

    def _gaiji(self) -> None:
        self._unsupported("ESC + (gaiji)", 73)
        self._skip = 73

    def _esc_t(self) -> None:
        n = _digits(self._cmd[2:4])
        if n is None:
            self._unsupported("ESC T (malformed)")
        elif self._unit_120:
            self._line_pitch = (3 * n + 1) // 2
        else:
            self._line_pitch = n

    def _esc_left_margin(self) -> None:
        n = _digits(self._cmd[2:5])
        if n is None:
            self._unsupported("ESC L (malformed)")
            return
        left = n * self._ank_pitch
        if self._right - left < _MIN_PRINT_WIDTH:
            return
        if self._x == self._left:
            self._x = left
        self._left = left

    def _esc_right_margin(self) -> None:
        n = _digits(self._cmd[2:5])
        if n is None:
            self._unsupported("ESC / (malformed)")
            return
        right = min(n * self._ank_pitch, PAGE_WIDTH)
        if right - self._left < _MIN_PRINT_WIDTH:
            return
        self._right = right

    def _esc_repeat(self) -> None:
        n = _digits(self._cmd[2:5])
        if n is None:
            self._unsupported("ESC R (malformed)")
            return
        code = self._cmd[5]
        for _ in range(n):
            self._print_ank(code)

    def _esc_c(self) -> None:
        if self._cmd[2] == 0x31:
            self._power_on()
        else:
            self._unsupported("unknown ESC c")

    def _esc_color_or_script(self) -> None:
        c = self._cmd[2]
        if c == 0x53:
            self._select_ank(_PICA)
            self._unsupported("ESC CS (superscript)")
        elif c == 0x73:
            self._select_ank(_PICA)
            self._unsupported("ESC Cs (subscript)")
        elif c == 0x54:
            self._unsupported("ESC CT (test print)")
        elif c == 0x30:
            return  # black: the only ink rendered
        else:
            self._unsupported("ESC C (color)")

    def _esc_p(self) -> None:
        c = self._cmd[2]
        if 0x30 <= c <= 0x33:  # p0 off, p1 outline, p2 italic, p3 bold
            self._decoration = c - 0x30
        elif c in (0x40, 0x60):  # standard kana/ANK fonts
            return
        elif c in (0x41, 0x42, 0x61, 0x62, 0x5A):
            self._unsupported(f"ESC p{chr(c)} (font)")
        else:
            self._unsupported("unknown ESC p")

    def _esc_h(self) -> None:
        c = self._cmd[2]
        if c == 0x30:
            return
        if c == 0x31:
            self._unsupported("ESC h1 (half-width vertical)")
        else:
            self._unsupported("unknown ESC h")

    def _sub(self) -> None:
        c = self._cmd[1]
        pitches = {0x51: 24, 0x4E: 27, 0x45: 30, 0x50: 36}
        if c in pitches:
            self._kanji_pitch = pitches[c]
        elif c == 0x46:  # F
            self._unit_120 = True
        elif c == 0x47:  # G
            self._unit_120 = False
        elif c == 0x56:  # V
            self._vertical_double = True
        elif c == 0x57:  # W
            self._vertical_double = False
        elif c == 0x55:
            self._select_ank(_PICA)
            self._unsupported("SUB U (superscript)")
        elif c == 0x4C:
            self._select_ank(_PICA)
            self._unsupported("SUB L (subscript)")
        elif c == 0x32:
            self._unsupported("SUB 2 (clear gaiji)")
        else:
            self._unsupported("unknown SUB")

    def _stx(self) -> None:
        if self._cmd[1] == 0x4B:
            self._collect(1, self._log_handler("STX K (hagaki mode)"))
        else:
            self._unsupported("unknown STX")

    def _select_ank(self, pitch: int) -> None:
        self._ank_pitch = pitch
        self._kanji = False

    def _enter_kanji(self) -> None:
        self._kanji = True

    # --- printing ------------------------------------------------------------

    def _print_ank(self, code: int) -> None:
        if self._kanji:
            self._print_half(code)
            return
        width = self._ank_pitch * (2 if self._double else 1)
        self._make_room(width)
        if code not in (0x7F, 0xFE, 0xFF):
            self._draw_glyph(ank_glyph(self._fonts, code), 8, width, _ANK_HEIGHT)
        self._finish_cell(width)

    def _print_half(self, code: int, blank: bool = False) -> None:
        advance = (self._kanji_pitch + self._half_phase) // 2
        self._half_phase ^= 1
        scale = 2 if self._double else 1
        self._make_room(advance * scale)
        if not blank and code not in (0x7F, 0xFE, 0xFF):
            self._draw_glyph(ank_glyph(self._fonts, code), 8, _HALF_WIDTH * scale, _BAND)
        self._finish_cell(advance * scale)

    def _half_pair(self) -> None:
        self._print_half(self._cmd[1])

    def _graphic(self) -> None:
        b = self._cmd[1]
        if 0x40 <= b <= 0x5F:
            self._print_ank(b - 0x40)
        else:
            self._unsupported("SOH (invalid graphic code)")

    def _jis_pair(self) -> None:
        hi = self._cmd[0]
        lo = self._cmd[1]
        if 0x29 <= hi <= 0x2B:
            self._unsupported("half-width kanji code")
            self._print_half(0, blank=True)
            return
        scale = 2 if self._double else 1
        advance = self._kanji_pitch * scale
        self._make_room(advance)
        rows = kanji_glyph(self._fonts, hi, lo)
        if rows is None:
            self._unsupported("kanji glyph missing")
        else:
            self._draw_glyph(rows, 16, _FULL_WIDTH * scale, _BAND)
        self._finish_cell(advance)

    def _band_height(self) -> int:
        return _BAND * (2 if self._vertical_double else 1)

    def _draw_glyph(self, rows: list[int], src_w: int, box_w: int, box_h: int) -> None:
        """Scale, decorate and draw a glyph at the current x, bottom-aligned
        in the line band (box height doubled under vertical double)."""
        band = self._band_height()
        if self._vertical_double:
            box_h *= 2
        if self._decoration == _BOLD:
            rows = embolden(rows)
        mask = scale_glyph(rows, src_w, box_w, box_h)
        if self._decoration == _ITALIC:
            mask = italicise(mask)
        elif self._decoration == _OUTLINE:
            mask = outline(mask)
        draw_mask(self.page, mask, self._x, self._y + band - box_h, self._y, self._y + band)

    def _finish_cell(self, advance: int) -> None:
        band = self._band_height()
        if self._underline:
            for dx in range(advance):
                self.page.set(self._x + dx, self._y + band - 1)
        if self._vertical_double:
            self._extra_feed = band - _BAND
        self._x += advance

    def _make_room(self, advance: int) -> None:
        if self._x + advance > self._right and self._x > self._left:
            self._carriage_return()
            self._line_feed()

    # --- movement ------------------------------------------------------------

    def _carriage_return(self) -> None:
        # The lower half of a vertically doubled line is fed past once
        # (X88000 PC-PR201 model: head height x (zoom - 1) on the next CR/LF).
        self._y += self._extra_feed
        self._extra_feed = 0
        self._x = self._left
        self._half_phase = 0

    def _line_feed(self) -> None:
        self._carriage_return()
        if self._reverse:
            self._y = max(0, self._y - self._line_pitch)
            return
        self._y += self._line_pitch
        if self._y + _BAND > PAGE_HEIGHT:
            self._emit_page()
            self._y = max(0, self._y - PAGE_HEIGHT)

    def _form_feed(self) -> None:
        self._emit_page()
        self._y = 0
        self._extra_feed = 0
        self._carriage_return()

    def _emit_page(self) -> None:
        self._on_page(self.page)
        self.page = PrinterPage()

    # --- unsupported-code log ------------------------------------------------

    def _unsupported(self, name: str, data: int = 0) -> None:
        hex_bytes = " ".join(f"{b:02X}" for b in self._cmd)
        if data:
            hex_bytes += f" (+{data} data)"
        self._log.write(f"{self._cmd_offset}\t{hex_bytes}\t{name}\n")
        self._counts[name] = self._counts.get(name, 0) + 1


class PngPageWriter:
    """`on_page` sink writing page-001.png, page-002.png, ... into a directory."""

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._count = 0

    def __call__(self, page: PrinterPage) -> None:
        self._count += 1
        # 1 = ink -> black (0), 0 -> white (255).
        table = bytes([255, 0] + [0] * 254)
        gray = bytes(page.pixels).translate(table)
        image = _PIL_Image.frombytes("L", (page.width, page.height), gray).convert("1")
        image.save(self._out_dir / f"page-{self._count:03d}.png", dpi=(DPI, DPI))
