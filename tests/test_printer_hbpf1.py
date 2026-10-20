"""Tests for the HBP-F1 printer renderer (spec io-printer)."""
from __future__ import annotations

import io
from pathlib import Path

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
