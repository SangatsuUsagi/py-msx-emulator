"""Tests for the HBP-F1 printer renderer (spec io-printer)."""
from __future__ import annotations

import io
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from msx.printer import PrinterPort
from msx.printer_hbpf1 import (
    INK_BLACK,
    INK_C,
    INK_M,
    INK_Y,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    HbpF1Printer,
    PngPageWriter,
    PrinterFonts,
    PrinterPage,
)


class _Log(io.StringIO):
    """StringIO that survives the device closing it."""

    def close(self) -> None:
        pass


def _marker_font() -> bytearray:
    # Every glyph: one dot at its top-left, so each glyph's x is the start of
    # an ink run on its top row.
    font = bytearray(2048)
    for code in range(256):
        font[code * 8] = 0x80
    return font


def _make(data: bytes, font: bytes | None = None,
          kanji: bytes | None = None) -> tuple[HbpF1Printer, list[PrinterPage], _Log]:
    pages: list[PrinterPage] = []
    log = _Log()
    printer = HbpF1Printer(PrinterFonts(ank=bytes(font if font else _marker_font()),
                                        kanji=kanji),
                           pages.append, log)
    for b in data:
        printer.write_byte(b)
    return printer, pages, log


def _has_ink(page: PrinterPage, x: int, y: int) -> int:
    return 1 if page.get(x, y) else 0


def _starts(page: PrinterPage, y: int) -> list[int]:
    """x of each ink run's first dot on row y."""
    starts = []
    prev = 0
    for x in range(PAGE_WIDTH):
        dot = page.get(x, y)
        if dot and not prev:
            starts.append(x)
        prev = dot
    return starts


ANK_TOP = 24 - 18  # ANK glyph top row within a line band
LF = b"\x0a"


def _log_lines(log: _Log) -> list[list[str]]:
    lines = []
    for line in log.getvalue().splitlines():
        if line.startswith("# summary"):
            break
        lines.append(line.split("\t"))
    return lines


# --- device and pages --------------------------------------------------------

def test_device_is_always_ready() -> None:
    printer, _, _ = _make(b"")
    assert PrinterPort(device=printer).read_port(0x90) == 0xFD


def test_ff_emits_a_page() -> None:
    _, pages, _ = _make(b"\x41\x0c")
    assert len(pages) == 1 and pages[0].has_ink()


def test_close_emits_only_an_inked_page() -> None:
    printer, pages, _ = _make(b"\x41\x0c")
    printer.close()
    assert len(pages) == 1


def test_close_with_pending_ink() -> None:
    printer, pages, _ = _make(b"\x41")
    printer.close()
    assert len(pages) == 1 and pages[0].has_ink()


# --- power-on state and ANK --------------------------------------------------

def test_first_character_position() -> None:
    printer, _, _ = _make(b"\x41")
    assert _starts(printer.page, ANK_TOP) == [0]


def test_reset_restores_pica_after_kanji_mode() -> None:
    printer, _, log = _make(b"\x1b\x4b\x1b\x63\x31\x41\x42")
    assert _starts(printer.page, ANK_TOP) == [0, 18]
    assert log.getvalue() == ""


def test_pitch_per_mode() -> None:
    printer, _, _ = _make(b"\x41\x41\x1b\x45\x41\x41\x1b\x51\x41\x41")
    assert _starts(printer.page, ANK_TOP) == [0, 18, 36, 51, 66, 76]


def test_0x7f_prints_as_space() -> None:
    printer, _, _ = _make(b"\x7f\x41")
    assert _starts(printer.page, ANK_TOP) == [18]


def test_graphic_character() -> None:
    font = bytearray(2048)
    font[0x01 * 8] = 0xFF
    printer, _, _ = _make(b"\x01\x41", font=font)
    row = [_has_ink(printer.page, x, ANK_TOP) for x in range(20)]
    assert row == [1] * 18 + [0, 0]


# --- kanji mode --------------------------------------------------------------

def _kanji_rom() -> bytes:
    rom = bytearray(262144)
    # JIS 0x3441: ku 20, ten 33 -> glyph number 20*96 + 33 - 512.
    address = (20 * 96 + 33 - 512) * 32
    rom[address] = 0x80  # top-left block, row 0: dot (0, 0)
    rom[address + 31] = 0x01  # bottom-right block, row 7: dot (15, 15)
    return bytes(rom)


def test_jis_kanji_from_the_kanji_rom() -> None:
    printer, _, log = _make(b"\x1b\x4b\x34\x41", kanji=_kanji_rom())
    page = printer.page
    inked = [(x, y) for y in range(24) for x in range(30) if page.get(x, y)]
    # Each source dot becomes a 2x2 pen dot at (1.5 sx, 1.5 sy).
    assert inked == [(0, 0), (1, 0), (0, 1), (1, 1), (22, 22), (23, 22), (22, 23), (23, 23)]
    assert log.getvalue() == ""


def test_missing_kanji_glyph() -> None:
    printer, _, log = _make(b"\x1b\x4b\x34\x41\x00\x41")
    assert _starts(printer.page, 0) == [24]
    assert _log_lines(log) == [["2", "34 41", "kanji glyph missing"]]


def test_half_width_kanji_rows_are_unsupported() -> None:
    printer, _, log = _make(b"\x1b\x4b\x29\x41\x00\x41")
    assert _starts(printer.page, 0) == [12]
    assert _log_lines(log) == [["2", "29 41", "half-width kanji code"]]


def test_half_width_pair() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x00\x41\x00\x42")
    assert _starts(printer.page, 0) == [0, 12]
    # 12 x 24 cell: the 8x8 glyph's top-left dot covers 3 rows (24/8).
    assert [_has_ink(printer.page, 0, y) for y in range(4)] == [1, 1, 1, 0]


def test_kanji_pitch_27_half_width_alternation() -> None:
    printer, _, _ = _make(b"\x1a\x4e\x1b\x4b\x00\x41\x00\x41\x00\x41")
    assert _starts(printer.page, 0) == [0, 13, 27]


def test_full_width_kanji_wraps_to_the_next_line() -> None:
    # PrintJisKanjiWraps: 60 kanji fill 1440 dots; the 61st starts line 2.
    printer, _, _ = _make(b"\x1b\x4b" + b"\x34\x41" * 61 + b"\x00\x41")
    assert _starts(printer.page, 30) == [24]  # half-width A after the wrapped kanji


def test_wrapped_line_restarts_the_alternation() -> None:
    printer, _, _ = _make(b"\x1a\x4e\x1b\x4b" + b"\x00\x41" * 109)
    assert _starts(printer.page, 30) == [0, 13, 27]


def test_ank_mode_select_leaves_kanji_mode() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1b\x48\x41\x42")
    assert _starts(printer.page, ANK_TOP) == [0, 18]


def test_vertical_kanji_mode_is_drawn_horizontally() -> None:
    printer, _, log = _make(b"\x1b\x74\x00\x41\x00\x42")
    assert _starts(printer.page, 0) == [0, 12]
    assert _log_lines(log) == [["0", "1B 74", "ESC t (vertical kanji mode)"]]


# --- line and page movement --------------------------------------------------

def test_line_pitch_in_1_180_units() -> None:
    printer, _, _ = _make(b"\x1a\x47\x1b\x54\x33\x30" + LF + b"\x41")
    assert _starts(printer.page, 30 + ANK_TOP) == [0]


def test_line_pitch_in_1_120_units() -> None:
    printer, _, _ = _make(b"\x1b\x54\x32\x30" + LF + b"\x41")
    assert _starts(printer.page, 30 + ANK_TOP) == [0]


def test_cr_returns_without_feeding() -> None:
    printer, _, _ = _make(b"\x41\x0d\x42")
    assert _starts(printer.page, ANK_TOP) == [0]


def test_reverse_feed_stops_at_the_page_top() -> None:
    printer, _, _ = _make(b"\x1b\x72" + LF + LF + b"\x1b\x66" + LF + b"\x41")
    assert _starts(printer.page, 30 + ANK_TOP) == [0]
    assert _starts(printer.page, ANK_TOP) == []


def test_line_full_wraps() -> None:
    printer, _, _ = _make(b"\x41" * 81)
    assert len(_starts(printer.page, ANK_TOP)) == 80
    assert _starts(printer.page, 30 + ANK_TOP) == [0]


def test_page_overflow_continues_on_a_new_page() -> None:
    printer, pages, _ = _make(LF * 66 + b"\x41")
    assert len(pages) == 1
    assert _starts(printer.page, ANK_TOP) == [0]


# --- margins -----------------------------------------------------------------

def test_left_margin_in_pica() -> None:
    printer, _, _ = _make(b"\x1b\x4c\x30\x31\x30\x41")
    assert _starts(printer.page, ANK_TOP) == [180]


def test_left_margin_set_in_kanji_mode_uses_the_ank_pitch() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1b\x4c\x30\x31\x30\x0d\x00\x41")
    assert _starts(printer.page, 0) == [180]


def test_margin_leaving_under_72_dots_is_ignored() -> None:
    printer, _, _ = _make(b"\x1b\x4c\x30\x37\x39\x41")
    assert _starts(printer.page, ANK_TOP) == [0]


# --- character modifiers -----------------------------------------------------

def test_double_width() -> None:
    font = bytearray(2048)
    font[0x41 * 8] = 0xFE  # 7 of 8 columns: ink ends 4 dots before a 36-dot cell edge
    printer, _, _ = _make(b"\x0e\x41\x0f\x41", font=font)
    row = [_has_ink(printer.page, x, ANK_TOP) for x in range(40)]
    assert row[0:32] == [1] * 32 and row[32:36] == [0] * 4 and row[36] == 1


def test_dot_space_in_kanji_mode() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1b\x05\x00\x41")
    assert _starts(printer.page, 0) == [5]


def test_repeat() -> None:
    printer, _, _ = _make(b"\x1b\x52\x30\x30\x33\x41")
    assert _starts(printer.page, ANK_TOP) == [0, 18, 36]


# --- unsupported-code log ----------------------------------------------------

def test_bit_image_skipped_with_its_data() -> None:
    printer, _, log = _make(b"\x1b\x4a\x30\x30\x30\x32" + b"\x41" * 6 + b"\x41")
    assert _log_lines(log) == [["0", "1B 4A 30 30 30 32 (+6 data)", "ESC J (bit image)"]]
    assert _starts(printer.page, ANK_TOP) == [0]


def test_unknown_esc_consumes_two_bytes() -> None:
    printer, _, log = _make(b"\x1b\x7a\x41")
    assert _log_lines(log) == [["0", "1B 7A", "unknown ESC"]]
    assert _starts(printer.page, ANK_TOP) == [0]


def test_font_select_logged_default_reselect_silent() -> None:
    _, _, log = _make(b"\x1b\x70\x41\x1b\x70\x40")
    assert _log_lines(log) == [["0", "1B 70 41", "ESC pA (font)"]]


def test_page_length_sequence_skipped() -> None:
    printer, _, log = _make(b"\x1d\x41\x40\x40\x40\x1e\x41")
    assert _log_lines(log) == [["0", "1D 41 40 40 40 1E", "GS..RS (page length)"]]
    assert _starts(printer.page, ANK_TOP) == [0]


def test_summary_on_close() -> None:
    printer, _, log = _make(b"\x1b\x21\x1b\x21\x1b\x32")
    printer.close()
    summary = log.getvalue().split("# summary\n")[1].splitlines()
    assert summary == ["2\tESC ! (emphasis)", "1\tESC 2 (clear tabs)"]


# --- PNG output --------------------------------------------------------------

def test_pages_numbered_in_order(tmp_path: Path) -> None:
    printer = HbpF1Printer(PrinterFonts(ank=bytes(_marker_font())),
                           PngPageWriter(tmp_path), _Log())
    for b in b"\x41\x0c\x42\x0c":
        printer.write_byte(b)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["page-001.png", "page-002.png"]
    with Image.open(tmp_path / "page-001.png") as image:
        assert image.size == (PAGE_WIDTH, PAGE_HEIGHT)
        assert image.mode == "P"
        rgb = image.convert("RGB")
        assert rgb.getpixel((0, ANK_TOP)) == (0, 0, 0)  # black ink
        assert rgb.getpixel((5, ANK_TOP)) == (255, 255, 255)  # paper
        assert round(image.info["dpi"][0]) == 180


# --- decoration, underline, vertical double ----------------------------------

def _bar_font() -> bytearray:
    # A: a left-edge vertical bar (column 0 on every row).
    font = bytearray(2048)
    for r in range(8):
        font[0x41 * 8 + r] = 0x80
    return font


def _ink_row(page: PrinterPage, y: int, width: int) -> list[int]:
    return [x for x in range(width) if page.get(x, y)]


def test_bold_thickens_strokes() -> None:
    printer, _, _ = _make(b"\x1b\x70\x33\x41")
    assert _ink_row(printer.page, ANK_TOP, 18) == [0, 1, 2, 3, 4]


def test_italic_slants_toward_the_top() -> None:
    printer, _, _ = _make(b"\x1b\x70\x32\x41", font=_bar_font())
    assert _ink_row(printer.page, 23, 18)[0] == 0
    assert _ink_row(printer.page, ANK_TOP, 18)[0] == 4


def test_outline_draws_a_ring_around_the_glyph() -> None:
    font = bytearray(2048)
    for r in range(8):
        font[0x41 * 8 + r] = 0xFF
    printer, _, _ = _make(b"\x1b\x70\x31\x41", font=font)
    page = printer.page
    assert page.get(5, 10) == 0
    assert _has_ink(page, 18, 10) and _has_ink(page, 5, 5)
    assert _ink_row(page, 24, 40) == []


def test_a_new_decoration_replaces_the_old() -> None:
    printer, _, _ = _make(b"\x1b\x70\x33\x1b\x70\x32\x41", font=_bar_font())
    assert _ink_row(printer.page, ANK_TOP, 18) == [4, 5, 6]


def test_decoration_off() -> None:
    printer, _, log = _make(b"\x1b\x70\x33\x1b\x70\x30\x41")
    assert _ink_row(printer.page, ANK_TOP, 18) == [0, 1, 2]
    assert log.getvalue() == ""


def test_underline_spans_the_underlined_cells() -> None:
    printer, _, _ = _make(b"\x1b\x58\x41\x41\x1b\x59\x41")
    assert _ink_row(printer.page, 23, 60) == list(range(36))


def test_underline_in_kanji_mode() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1b\x58\x00\x41")
    assert _ink_row(printer.page, 23, 30) == list(range(12))


def test_ank_glyph_doubled_downward() -> None:
    printer, _, _ = _make(b"\x1a\x56\x41", font=_bar_font())
    column = [y for y in range(60) if printer.page.get(0, y)]
    assert column == list(range(12, 48))


def test_half_width_glyph_doubled() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1a\x56\x00\x41", font=_bar_font())
    column = [y for y in range(60) if printer.page.get(0, y)]
    assert column == list(range(48))


def test_next_line_clears_the_doubled_glyph() -> None:
    printer, _, _ = _make(b"\x1a\x56\x41\x1a\x57" + LF + b"\x41")
    assert _starts(printer.page, 54 + ANK_TOP) == [0]


def test_underline_under_a_doubled_glyph() -> None:
    printer, _, _ = _make(b"\x1a\x56\x1b\x58\x41")
    assert _ink_row(printer.page, 47, 30) == list(range(18))
    assert _ink_row(printer.page, 23, 30) == []


def test_reset_clears_decoration() -> None:
    printer, _, _ = _make(b"\x1b\x70\x33\x1b\x63\x31\x41")
    assert _ink_row(printer.page, ANK_TOP, 18) == [0, 1, 2]


# --- pen glyph scaling -------------------------------------------------------

def test_kanji_one_dot_strokes_are_two_dots_wide() -> None:
    rom = bytearray(262144)
    address = (20 * 96 + 33 - 512) * 32  # JIS 0x3441
    for block in (0, 16):  # top-left and bottom-left 8x8 blocks
        for r in range(8):
            rom[address + block + r] = 0x50  # source columns 1 and 3
    printer, _, _ = _make(b"\x1b\x4b\x34\x41", kanji=bytes(rom))
    for y in range(24):
        assert _ink_row(printer.page, y, 24) == [1, 2, 4, 5]


def test_pica_one_dot_stroke_is_three_dots_wide() -> None:
    font = bytearray(2048)
    font[0x41 * 8] = 0x40  # column 1 of row 0
    printer, _, _ = _make(b"\x41", font=font)
    assert _ink_row(printer.page, ANK_TOP, 18) == [2, 3, 4]


def test_condensed_width_falls_back_to_nearest_neighbour() -> None:
    font = bytearray(2048)
    font[0x41 * 8] = 0xA0  # columns 0 and 2 of row 0
    printer, _, _ = _make(b"\x1b\x51\x41", font=font)
    assert _ink_row(printer.page, ANK_TOP, 10) == [0, 1, 3]


# --- colour ------------------------------------------------------------------

def _inks_in(page: PrinterPage, x0: int, x1: int, y0: int, y1: int) -> set[int]:
    inks = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if page.get(x, y):
                inks.add(page.get(x, y))
    return inks


def test_colour_applies_to_later_glyphs() -> None:
    printer, _, _ = _make(b"\x1b\x43\x32\x41")
    assert _inks_in(printer.page, 0, 18, 0, 24) == {INK_Y | INK_M}


def test_colour_applies_to_the_underline() -> None:
    printer, _, _ = _make(b"\x1b\x43\x35\x1b\x58\x41")
    assert _inks_in(printer.page, 0, 18, 23, 24) == {INK_C}


def test_invalid_colour_is_ignored_and_logged() -> None:
    printer, _, log = _make(b"\x1b\x43\x32\x1b\x43\x37\x41")
    assert _inks_in(printer.page, 0, 18, 0, 24) == {INK_Y | INK_M}
    assert _log_lines(log) == [["3", "1B 43 37", "ESC C (invalid colour)"]]


def test_overprint_mixes_inks() -> None:
    font = bytearray(2048)
    for r in range(8):
        font[0x41 * 8 + r] = 0xFF
    printer, _, _ = _make(b"\x1b\x43\x36\x41\x0d\x1b\x43\x31\x41", font=font)
    assert _inks_in(printer.page, 0, 18, 0, 24) == {INK_BLACK}


def test_reset_restores_black() -> None:
    printer, _, _ = _make(b"\x1b\x43\x32\x1b\x63\x31\x41")
    assert _inks_in(printer.page, 0, 18, 0, 24) == {INK_BLACK}


def test_colours_in_the_png(tmp_path: Path) -> None:
    printer = HbpF1Printer(PrinterFonts(ank=bytes(_marker_font())),
                           PngPageWriter(tmp_path), _Log())
    for b in b"\x1b\x43\x36\x41\x0c":
        printer.write_byte(b)
    with Image.open(tmp_path / "page-001.png") as image:
        rgb = image.convert("RGB")
        assert rgb.getpixel((0, ANK_TOP)) == (255, 230, 0)  # yellow
        assert rgb.getpixel((5, ANK_TOP)) == (255, 255, 255)  # paper


# --- edge cases (review Q1-Q3) -----------------------------------------------

def test_dot_space_outside_kanji_mode_is_logged() -> None:
    printer, _, log = _make(b"\x1b\x05\x41")
    assert _starts(printer.page, ANK_TOP) == [0]
    assert _log_lines(log) == [["0", "1B 05", "ESC n (dot space outside kanji mode)"]]


def test_reset_keeps_a_pending_extra_feed() -> None:
    printer, _, _ = _make(b"\x1a\x56\x41\x1b\x63\x31" + LF + b"\x41")
    # 54 = 24 (lower half of the doubled line) + 30 (line pitch)
    assert _starts(printer.page, 54 + ANK_TOP) == [0]


def test_unterminated_page_length_is_abandoned() -> None:
    printer, _, log = _make(b"\x1d" + b"\x40" * 255 + b"\x41")
    assert _log_lines(log) == [["0", "1D (+255 data)", "GS..RS (page length) (unterminated)"]]
    assert _starts(printer.page, ANK_TOP) == [0]


# ---------------------------------------------------------------------------
# allium/hbp-f1-printer.allium obligations not covered by the scenarios above
# ---------------------------------------------------------------------------

def test_hbp_f1_constants() -> None:
    # config block: page geometry and the cell sizes of manual p.86 / p.105
    from msx import printer_hbpf1 as m
    assert (m.PAGE_WIDTH, m.PAGE_HEIGHT, m.DPI) == (1586, 1980, 180)
    assert (m._BAND, m._ANK_HEIGHT, m._FULL_WIDTH, m._HALF_WIDTH) == (24, 18, 24, 12)
    assert (m._PICA, m._ELITE, m._CONDENSED) == (18, 15, 10)
    assert m._MIN_PRINT_WIDTH == 72
    assert (m._POWER_ON_LINE_PITCH, m._POWER_ON_RIGHT_MARGIN) == (30, 1440)
    assert (m._LINE_PITCH_ESC_A, m._LINE_PITCH_ESC_B) == (30, 24)
    assert (m._MAX_UNTIL_BYTES, m._GAIJI_TRAILER_BYTES) == (256, 73)


# --- contract GlyphScaler / GlyphDecorator -----------------------------------

def test_scaler_fits_the_box() -> None:
    # FitsBox: a solid glyph fills exactly the box, at every size in use.
    from msx.printer_hbpf1 import scale_glyph
    for src, rows, dst_w, dst_h in ((8, [0xFF] * 8, 18, 18), (8, [0xFF] * 8, 10, 18),
                                    (8, [0xFF] * 8, 12, 24), (16, [0xFFFF] * 16, 24, 24),
                                    (16, [0xFFFF] * 16, 48, 48)):
        mask = scale_glyph(rows, src, dst_w, dst_h)
        assert (mask.width, mask.height, mask.x_offset, mask.y_offset) == (dst_w, dst_h, 0, 0)
        assert mask.rows == [(1 << dst_w) - 1] * dst_h
        assert mask == scale_glyph(rows, src, dst_w, dst_h)  # value equality


def test_embolden_widens_by_one_source_dot() -> None:
    # BoldAtSourceResolution
    from msx.printer_hbpf1 import embolden
    assert embolden([0x80, 0x10, 0x00]) == [0xC0, 0x18, 0x00]


def test_italicise_shears_from_the_bottom() -> None:
    # ItalicShear: row dy moves (height-1-dy)//4 right; the mask widens by the max.
    from msx.printer_hbpf1 import GlyphMask, italicise
    mask = italicise(GlyphMask(4, 9, [0b1000] * 9))
    assert mask.width == 4 + 2
    assert mask.rows[8] == 0b100000  # bottom row unmoved (in the wider mask)
    assert mask.rows[0] == 0b001000  # top row moved 2 dots right


def test_outline_is_a_ring_around_the_glyph() -> None:
    # OutlineRing: one dot all round, the stroke itself blank, offsets -1.
    from msx.printer_hbpf1 import GlyphMask, outline
    mask = outline(GlyphMask(1, 1, [1]))
    assert (mask.width, mask.height, mask.x_offset, mask.y_offset) == (3, 3, -1, -1)
    assert mask.rows == [0b111, 0b101, 0b111]


# --- kanji ROM coverage -------------------------------------------------------

def test_kanji_rows_not_in_the_rom_are_missing() -> None:
    # PrintJisKanji guidance: rows 0x2C-0x2F and level 2 on a 128 KB ROM.
    from msx.printer_hbpf1 import kanji_glyph
    full = PrinterFonts(ank=bytes(2048), kanji=bytes(262144))
    level1_only = PrinterFonts(ank=bytes(2048), kanji=bytes(131072))
    assert kanji_glyph(full, 0x2D, 0x21) is None
    assert kanji_glyph(full, 0x30, 0x21) is not None
    assert kanji_glyph(full, 0x50, 0x21) is not None
    assert kanji_glyph(level1_only, 0x50, 0x21) is None
    assert kanji_glyph(full, 0x75, 0x21) is None  # past row 0x74


def test_invalid_kanji_first_byte_is_logged_and_dropped() -> None:
    printer, _, log = _make(b"\x1b\x4b\x20\x80\x00\x41")
    assert [line[2] for line in _log_lines(log)] == ["invalid kanji first byte"] * 2
    assert _starts(printer.page, 0) == [0]  # the 00 41 pair still lands at x = 0


# --- modes, pitches, framing ---------------------------------------------------

def test_partly_rendered_mode_selects_print_pica_and_log() -> None:
    # SelectAnkMode: ESC P, SUB U, SUB L, ESC CS, ESC Cs leave kanji mode, print
    # pica, and are logged; ESC CT is logged as test print, not as a colour.
    for command, name in ((b"\x1b\x50", "ESC P (proportional)"),
                          (b"\x1a\x55", "SUB U (superscript)"),
                          (b"\x1a\x4c", "SUB L (subscript)"),
                          (b"\x1b\x43\x53", "ESC CS (superscript)"),
                          (b"\x1b\x43\x73", "ESC Cs (subscript)")):
        printer, _, log = _make(b"\x1b\x4b" + command + b"\x41\x41")
        assert _starts(printer.page, ANK_TOP) == [0, 18], name
        assert [line[2] for line in _log_lines(log)] == [name]
    _, _, log = _make(b"\x1b\x43\x54")
    assert [line[2] for line in _log_lines(log)] == ["ESC CT (test print)"]


def test_kanji_pitches() -> None:
    # SetKanjiPitch: SUB Q / E / P = 24 / 30 / 36 full-width advances.
    for letter, pitch in ((0x51, 24), (0x45, 30), (0x50, 36)):
        printer, _, log = _make(bytes((0x1A, letter)) + b"\x1b\x4b\x29\x41\x29\x41\x00\x41")
        # two blank half-width cells (rows 0x29) advance pitch/2 each
        assert _starts(printer.page, 0) == [pitch], letter


def test_line_pitch_esc_a_and_b() -> None:
    # SetLinePitchFixed: ESC B = 24 dots, ESC A = 30.
    printer, _, _ = _make(b"\x1b\x42" + LF + b"\x41\x1b\x41" + LF + b"\x41")
    assert _starts(printer.page, 24 + ANK_TOP) == [0]
    assert _starts(printer.page, 54 + ANK_TOP) == [0]


def test_line_unit_back_to_1_120() -> None:
    # SelectLineUnit: SUB F after SUB G restores the 1/120-inch unit.
    printer, _, _ = _make(b"\x1a\x47\x1a\x46\x1b\x54\x32\x30" + LF + b"\x41")
    assert _starts(printer.page, 30 + ANK_TOP) == [0]


def test_malformed_line_pitch_is_logged() -> None:
    printer, _, log = _make(b"\x1b\x54\x33\x58" + LF + b"\x41")
    assert [line[2] for line in _log_lines(log)] == ["ESC T (malformed)"]
    assert _starts(printer.page, 30 + ANK_TOP) == [0]  # pitch unchanged


def test_right_margin_wraps_earlier() -> None:
    # SetRightMargin: ESC /010 (180 dots) wraps the 11th pica character.
    printer, _, _ = _make(b"\x1b\x2f\x30\x31\x30" + b"\x41" * 11)
    assert len(_starts(printer.page, ANK_TOP)) == 10
    assert _starts(printer.page, 30 + ANK_TOP) == [0]


def test_right_margin_under_72_dots_is_ignored() -> None:
    printer, _, _ = _make(b"\x1b\x2f\x30\x30\x33" + b"\x41" * 11)
    assert len(_starts(printer.page, ANK_TOP)) == 11


def test_invalid_graphic_code_is_logged() -> None:
    printer, _, log = _make(b"\x01\x20\x41")
    assert [line[2] for line in _log_lines(log)] == ["SOH (invalid graphic code)"]
    assert _starts(printer.page, ANK_TOP) == [0]


def test_repeat_in_kanji_mode_prints_half_width() -> None:
    printer, _, _ = _make(b"\x1b\x4b\x1b\x52\x30\x30\x32\x41")
    assert _starts(printer.page, 0) == [0, 12]


def test_default_reselects_are_silent() -> None:
    # DefaultReselectIsSilent: ESC p@, ESC p`, ESC h0, ESC ".
    _, _, log = _make(b"\x1b\x70\x40\x1b\x70\x60\x1b\x68\x30\x1b\x22")
    assert log.getvalue() == ""


def test_terminated_tab_list_is_logged_once() -> None:
    # UnsupportedCodeConsumed: ESC ( ... "." consumed up to the terminator.
    printer, _, log = _make(b"\x1b\x28\x30\x31\x30\x2c\x30\x32\x30\x2e\x41")
    assert _log_lines(log) == [["0", "1B 28 30 31 30 2C 30 32 30 2E",
                                "ESC ( (horizontal tab)"]]
    assert _starts(printer.page, ANK_TOP) == [0]


def test_summary_ties_sorted_by_name() -> None:
    # UnsupportedCodeLog SummaryOrder: most frequent first, ties by name.
    printer, _, log = _make(b"\x1b\x58\x1b\x21\x1b\x32")  # X is underline (supported)
    printer.close()
    summary = log.getvalue().split("# summary\n")[1].splitlines()
    assert summary == ["1\tESC ! (emphasis)", "1\tESC 2 (clear tabs)"]


# --- reset, carriage return, page movement ---------------------------------------

def test_reset_restores_every_formatting_setting() -> None:
    # FormattingRestored: margins, pitches, enlargements, underline, colour.
    printer, _, _ = _make(b"\x1b\x4c\x30\x30\x35\x1b\x45\x0e\x1a\x56\x1b\x58"
                          b"\x1b\x43\x32\x1a\x50\x1b\x42\x1b\x72\x1b\x63\x31\x41\x41")
    assert printer._left == 0 and printer._right == 1440
    assert (printer._ank_pitch, printer._kanji_pitch, printer._line_pitch) == (18, 24, 30)
    assert not (printer._double or printer._vertical_double or printer._underline)
    assert printer._ink == INK_BLACK and printer._unit_120 and not printer._reverse
    assert _starts(printer.page, ANK_TOP) == [0, 18]


def test_bare_cr_applies_the_extra_feed() -> None:
    # CarriageReturn: the pending lower-half feed is applied by CR alone.
    printer, _, _ = _make(b"\x1a\x56\x41\x1a\x57\x0d\x41")
    assert _starts(printer.page, 24 + ANK_TOP) == [0]


def test_cr_extra_feed_past_the_page_bottom_continues_on_a_new_page() -> None:
    # 65 LF -> y = 1950; the doubled A leaves a 24-dot feed that CR applies,
    # crossing the 1980-dot page bottom.
    printer, pages, _ = _make(LF * 65 + b"\x1a\x56\x41\x1a\x57\x0d\x41")
    assert len(pages) == 1
    assert _starts(printer.page, ANK_TOP) == [0]


def test_form_feed_returns_to_the_left_margin() -> None:
    printer, _, _ = _make(b"\x1b\x4c\x30\x31\x30\x41\x41\x0c\x41")
    assert _starts(printer.page, ANK_TOP) == [180]


# --- invariants ---------------------------------------------------------------

_COMMANDS = st.sampled_from([
    b"A", b"\x0d", b"\x0a", b"\x0c", b"\x0e", b"\x0f", b"\x1b\x4b", b"\x1b\x48",
    b"\x1b\x45", b"\x1b\x51", b"\x00\x41", b"\x34\x41", b"\x1a\x56", b"\x1a\x57",
    b"\x1a\x47", b"\x1a\x46", b"\x1b\x54\x39\x39", b"\x1b\x54\x30\x30", b"\x1b\x72",
    b"\x1b\x66", b"\x1b\x4c\x30\x37\x30", b"\x1b\x4c\x30\x30\x30",
    b"\x1b\x2f\x30\x31\x30", b"\x1b\x2f\x30\x38\x30", b"\x1b\x63\x31",
    b"\x1b\x70\x31", b"\x1b\x70\x32", b"\x1b\x58", b"\x1b\x43\x35",
    b"\x1b\x52\x30\x32\x30\x41",
    b"\x0a" * 60,  # reach the page bottom within a short example
])


@settings(max_examples=60, deadline=None)
@given(st.lists(_COMMANDS, max_size=80))
def test_printer_invariants_property(commands: list[bytes]) -> None:
    # HeadStaysOnPage, MarginsLeaveRoom, PendingFeedIsOneBand after every command.
    printer, _, _ = _make(b"")
    for command in commands:
        for b in command:
            printer.write_byte(b)
        assert 0 <= printer._y and printer._y + 24 <= PAGE_HEIGHT
        assert printer._right - printer._left >= 72 and printer._right <= PAGE_WIDTH
        assert printer._extra_feed in (0, 24)
