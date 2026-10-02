"""R#13 blink timer and its two consumers: TEXT2 character blink and
GRAPHIC4-7 page alternation (openspec vdp-v9938 "V9938 R#13 blink timer",
"V9938 TEXT2 mode", "V9938 interlace mode (R#9 IL/EO)")."""
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame
from tests.render_geometry import active_region


def _active(vdp: V9938) -> bytearray:
    return active_region(render_frame(vdp), vdp.display_height)


def _write_reg(vdp: V9938, reg: int, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------


def test_r13_zero_holds_off() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0x00)
    for _ in range(50):
        vdp.increment_frame()
    assert vdp.blink_state is False
    assert vdp.blink_count == 0


def test_r13_on_time_only_holds_on() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0xF0)
    for _ in range(200):
        vdp.increment_frame()
    assert vdp.blink_state is True
    assert vdp.blink_count == 0


def test_r13_off_time_only_holds_off() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0x0F)
    for _ in range(200):
        vdp.increment_frame()
    assert vdp.blink_state is False
    assert vdp.blink_count == 0


def test_periodic_r13_alternates_on_and_off() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0x21)
    assert vdp.blink_state is True
    assert vdp.blink_count == 20
    for _ in range(19):
        vdp.increment_frame()
    assert vdp.blink_state is True
    vdp.increment_frame()  # 20th advance: ON period over
    assert vdp.blink_state is False
    assert vdp.blink_count == 10
    for _ in range(10):
        vdp.increment_frame()  # 30th advance: OFF period over
    assert vdp.blink_state is True
    assert vdp.blink_count == 20


def test_rewriting_r13_restarts_the_on_period() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0x22)
    for _ in range(25):
        vdp.increment_frame()
    assert vdp.blink_state is False
    _write_reg(vdp, 13, 0x22)
    assert vdp.blink_state is True
    assert vdp.blink_count == 20


def test_reset_clears_the_timer() -> None:
    vdp = V9938()
    _write_reg(vdp, 13, 0x22)
    vdp.reset()
    assert vdp.blink_state is False
    assert vdp.blink_count == 0


# ---------------------------------------------------------------------------
# GRAPHIC4-7 page alternation
# ---------------------------------------------------------------------------


def _screen5_page1(vdp: V9938) -> None:
    vdp.regs[0] = 0x06  # GRAPHIC4
    vdp.regs[1] = 0x40  # BL
    vdp.regs[2] = 0x3F  # page 1
    vdp.vram[0x0000] = 0xCD  # page 0 content
    vdp.vram[0x8000] = 0xAB  # page 1 content


def test_blink_on_shows_even_page() -> None:
    vdp = V9938()
    _screen5_page1(vdp)
    vdp.blink_state = True
    assert _active(vdp)[0] == 0x0C


def test_blink_off_shows_r2_page() -> None:
    vdp = V9938()
    _screen5_page1(vdp)
    vdp.blink_state = False
    assert _active(vdp)[0] == 0x0A


def test_blink_off_with_eo_alternates_by_field() -> None:
    vdp = V9938()
    _screen5_page1(vdp)
    vdp.regs[9] = 0x04  # EO
    vdp.frame_count = 0
    assert _active(vdp)[0] == 0x0C
    vdp.frame_count = 1
    assert _active(vdp)[0] == 0x0A


def test_blink_on_with_eo_stays_on_even_page() -> None:
    vdp = V9938()
    _screen5_page1(vdp)
    vdp.regs[9] = 0x04  # EO
    vdp.blink_state = True
    vdp.frame_count = 1  # second field
    assert _active(vdp)[0] == 0x0C


def test_screen7_blink_on_shows_page_0() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x0A  # GRAPHIC6
    vdp.regs[1] = 0x40
    vdp.regs[2] = 0x3F
    vdp.vram[0x00000] = 0x3E
    vdp.vram[0x10000] = 0x5E
    vdp.blink_state = True
    assert _active(vdp)[0] == 3
    vdp.blink_state = False
    assert _active(vdp)[0] == 5


# ---------------------------------------------------------------------------
# TEXT2 character blink
# ---------------------------------------------------------------------------


def _text2_blink_setup(vdp: V9938) -> None:
    vdp.regs[0] = 0x04  # M4
    vdp.regs[1] = 0x50  # M1 + BL -> TEXT2
    vdp.regs[2] = 0x03  # name table at 0x0000
    vdp.regs[4] = 0x02  # pattern generator at 0x1000
    vdp.regs[3] = 0x2F  # blink table A13-A9 -> 0x0A00
    vdp.regs[10] = 0x00
    vdp.regs[7] = 0xF1  # plain: fg 15, bg 1
    vdp.regs[12] = 0x4A  # blink: fg 4, bg 10
    vdp.vram[0x1000] = 0xF0  # tile 0, pattern row 0: 4 fg dots, 2 bg dots
    vdp.vram[0x0A00] = 0x80  # row 0: column 0 blinks, column 1 does not


def test_text2_blink_attribute_uses_r12_colours_while_on() -> None:
    vdp = V9938()
    _text2_blink_setup(vdp)
    vdp.blink_state = True
    buf = _active(vdp)
    assert buf[16] == 4  # column 0 foreground
    assert buf[16 + 5] == 10  # column 0 background
    assert buf[16 + 6] == 15  # column 1 keeps R#7 foreground
    assert buf[16 + 6 + 5] == 1  # column 1 keeps R#7 background


def test_text2_blink_attribute_uses_r7_colours_while_off() -> None:
    vdp = V9938()
    _text2_blink_setup(vdp)
    vdp.blink_state = False
    buf = _active(vdp)
    assert buf[16] == 15
    assert buf[16 + 5] == 1


def test_text2_blink_foreground_0_falls_back_to_blink_background() -> None:
    vdp = V9938()
    _text2_blink_setup(vdp)
    vdp.regs[12] = 0x0A
    vdp.blink_state = True
    buf = _active(vdp)
    assert buf[16] == 10
    assert buf[16 + 5] == 10


def test_text2_blink_background_0_follows_tp() -> None:
    vdp = V9938()
    _text2_blink_setup(vdp)
    vdp.regs[12] = 0x40  # blink bg colour 0
    vdp.regs[8] = 0x00  # TP=0: colour 0 shows the backdrop (R#7 low nibble)
    vdp.blink_state = True
    assert _active(vdp)[16 + 5] == 1
    vdp.regs[8] = 0x20  # TP=1: palette entry 0
    assert _active(vdp)[16 + 5] == 0


def test_text2_blink_table_address_from_r3_and_r10() -> None:
    """R#3 = 0x2F, R#10 = 0x01 -> base 0x04A00; row 1 column 9 is bit 6 of
    byte base + 11."""
    vdp = V9938()
    _text2_blink_setup(vdp)
    vdp.regs[10] = 0x01
    vdp.vram[0x0A00] = 0x80  # old (R#10 = 0) location: must not be read
    vdp.vram[0x04A00 + 11] = 0x40
    vdp.blink_state = True
    buf = _active(vdp)
    scan = 8  # character row 1, pattern row 0
    col = 9
    assert buf[scan * 512 + 16 + col * 6] == 4  # blinking
    assert buf[scan * 512 + 16 + (col + 1) * 6] == 15  # neighbour does not
    assert buf[16] == 15  # row 0 column 0: old table location no longer read


def test_r12_write_is_logged_for_banded_rendering() -> None:
    """R#12 is a display register: a mid-frame write splits render bands
    like R#7, so TEXT2 blink colours change from the next scanline."""
    vdp = V9938()
    vdp.begin_scanline(0)
    vdp.display_line = 50
    _write_reg(vdp, 12, 0x4A)
    assert any(getattr(e, "reg", None) == 12 for e in vdp._reg_write_log)
