from msx.vdp.vdp import VDP
from msx.vdp.renderer import render_frame

# Sprite attribute table at 0x1000  (R5=0x20, (0x20&0x7F)<<7=0x1000)
# Sprite pattern table at 0x0000  (R6=0x00, (0x00&0x07)<<11=0x0000)
_SAT = 0x1000
_SPT = 0x0000


def make_sprite_vdp() -> VDP:
    """G1 mode VDP with sprites configured."""
    vdp = VDP()
    vdp.regs[1] = 0x40   # BL=1, G1, SI=0, MAG=0
    vdp.regs[2] = 0x0E   # name table at 0x3800
    vdp.regs[4] = 0x07   # pattern gen at 0x3800 (won't conflict)
    vdp.regs[3] = 0x20   # colour table at 0x0800
    vdp.regs[5] = 0x20   # SAT at 0x1000
    vdp.regs[6] = 0x00   # SPT at 0x0000
    vdp.regs[7] = 0x01   # backdrop = colour 1
    return vdp


def _sat_entry(vdp: VDP, idx: int, y: int, x: int, pat: int, attr: int) -> None:
    vdp.vram[_SAT + idx * 4 + 0] = y & 0xFF
    vdp.vram[_SAT + idx * 4 + 1] = x & 0xFF
    vdp.vram[_SAT + idx * 4 + 2] = pat & 0xFF
    vdp.vram[_SAT + idx * 4 + 3] = attr & 0xFF


# ---------------------------------------------------------------------------
# Terminator
# ---------------------------------------------------------------------------

def test_terminator_stops_sprite_processing() -> None:
    vdp = make_sprite_vdp()
    # Sprite 0: Y=0xD0 (terminator)
    _sat_entry(vdp, 0, 0xD0, 0, 0, 0x0F)
    # Sprite 1 (after terminator): would paint pixel (0,1)
    _sat_entry(vdp, 1, 0x00, 0, 0, 0x0F)   # y_top=1, colour=15
    vdp.vram[_SPT + 0] = 0xFF               # pattern all set

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] != 15, "sprite after terminator must not be rendered"


# ---------------------------------------------------------------------------
# 8×8 sprite pixel rendering
# ---------------------------------------------------------------------------

def test_8x8_sprite_renders_pixels() -> None:
    vdp = make_sprite_vdp()
    # Sprite 0: Y=0 (y_top=1), X=0, pattern 0, colour=15
    _sat_entry(vdp, 0, 0, 0, 0, 0x0F)
    # Pattern 0, row 0: only bit 7 set → pixel at x=0
    vdp.vram[_SPT + 0] = 0x80
    # Terminator for remaining sprites
    _sat_entry(vdp, 1, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] == 15   # y=1 (y_top), x=0 → pixel set
    assert buf[1 * 256 + 1] == 1    # bit 6 clear → backdrop


def test_8x8_colour_zero_transparent() -> None:
    vdp = make_sprite_vdp()
    _sat_entry(vdp, 0, 0, 0, 0, 0x00)   # colour=0 (transparent)
    vdp.vram[_SPT + 0] = 0xFF
    _sat_entry(vdp, 1, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] == 1   # transparent → backdrop, not painted


# ---------------------------------------------------------------------------
# Y off-by-one
# ---------------------------------------------------------------------------

def test_y_off_by_one() -> None:
    vdp = make_sprite_vdp()
    # Y=5 → sprite appears at screen line 6
    _sat_entry(vdp, 0, 5, 0, 0, 0x0F)
    vdp.vram[_SPT + 0] = 0x80
    _sat_entry(vdp, 1, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[6 * 256 + 0] == 15   # appears at line 6
    assert buf[5 * 256 + 0] != 15   # not at line 5


# ---------------------------------------------------------------------------
# 16×16 sprite pattern layout
# ---------------------------------------------------------------------------

def test_16x16_top_row_uses_pattern_pair() -> None:
    vdp = make_sprite_vdp()
    vdp.regs[1] = 0x42   # SI=1 (bit 1), BL=1 → 16×16 sprites
    # Sprite 0: Y=0 (y_top=1), X=0, pattern 0, colour=15
    _sat_entry(vdp, 0, 0, 0, 0, 0x0F)
    # TMS9918A 16×16 layout: base+0=upper-left, base+1=lower-left,
    #                          base+2=upper-right, base+3=lower-right
    vdp.vram[_SPT + 0 * 8 + 0] = 0x80   # upper-left: pixel 0 set
    vdp.vram[_SPT + 2 * 8 + 0] = 0x01   # upper-right: pixel 7 (=15 in 16px) set
    _sat_entry(vdp, 1, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] == 15    # upper-left pixel 0
    assert buf[1 * 256 + 15] == 15   # upper-right pixel 15
    assert buf[1 * 256 + 8] == 1     # upper-right pixel 8 (bit 7 of upper-right = 0)


def test_16x16_bottom_row_uses_second_pattern_pair() -> None:
    vdp = make_sprite_vdp()
    vdp.regs[1] = 0x42   # SI=1
    # Y=0 → top=1; rows 0–7 = top half, rows 8–15 = bottom half
    _sat_entry(vdp, 0, 0, 0, 0, 0x0F)
    # TMS9918A layout: base+1=lower-left, row 0 of bottom half (src_row-8=0):
    vdp.vram[_SPT + 1 * 8 + 0] = 0x80   # lower-left: pixel 0 set
    _sat_entry(vdp, 1, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[9 * 256 + 0] == 15   # line 9 = y_top+8 = bottom half row 0


# ---------------------------------------------------------------------------
# Sprite priority
# ---------------------------------------------------------------------------

def test_lower_index_sprite_wins() -> None:
    vdp = make_sprite_vdp()
    # Sprite 0: colour=5; Sprite 1: colour=10; both paint pixel (0,1)
    _sat_entry(vdp, 0, 0, 0, 0, 0x05)
    _sat_entry(vdp, 1, 0, 0, 1, 0x0A)
    vdp.vram[_SPT + 0 * 8 + 0] = 0x80   # pattern 0, pixel 0 set
    vdp.vram[_SPT + 1 * 8 + 0] = 0x80   # pattern 1, pixel 0 set
    _sat_entry(vdp, 2, 0xD0, 0, 0, 0)

    buf = render_frame(vdp)

    assert buf[1 * 256 + 0] == 5   # sprite 0 (lower index) wins


# ---------------------------------------------------------------------------
# 5th-sprite detection
# ---------------------------------------------------------------------------

def test_fifth_sprite_sets_status_flag() -> None:
    vdp = make_sprite_vdp()
    # Put 5 sprites on line 1 (Y=0 → y_top=1)
    for i in range(5):
        _sat_entry(vdp, i, 0, i * 8, i, 0x0F)
        vdp.vram[_SPT + i * 8 + 0] = 0xFF
    _sat_entry(vdp, 5, 0xD0, 0, 0, 0)

    render_frame(vdp)

    assert vdp.status & 0x40, "5th-sprite flag (bit 6) must be set"
    assert (vdp.status & 0x1F) == 4, "bits 4:0 should hold the 5th sprite index (4)"


def test_four_sprites_no_fifth_flag() -> None:
    vdp = make_sprite_vdp()
    for i in range(4):
        _sat_entry(vdp, i, 0, i * 8, i, 0x0F)
        vdp.vram[_SPT + i * 8 + 0] = 0xFF
    _sat_entry(vdp, 4, 0xD0, 0, 0, 0)

    render_frame(vdp)

    assert not (vdp.status & 0x40), "5th-sprite flag must not be set with only 4 sprites"


# ---------------------------------------------------------------------------
# Coincidence flag
# ---------------------------------------------------------------------------

def test_coincidence_flag_set_when_sprites_overlap() -> None:
    vdp = make_sprite_vdp()
    # Two sprites with non-transparent colour both covering pixel (0,1)
    _sat_entry(vdp, 0, 0, 0, 0, 0x05)
    _sat_entry(vdp, 1, 0, 0, 1, 0x0A)
    vdp.vram[_SPT + 0 * 8 + 0] = 0x80
    vdp.vram[_SPT + 1 * 8 + 0] = 0x80
    _sat_entry(vdp, 2, 0xD0, 0, 0, 0)

    render_frame(vdp)

    assert vdp.status & 0x20, "coincidence flag (bit 5) must be set"


def test_no_coincidence_when_no_overlap() -> None:
    vdp = make_sprite_vdp()
    # Two sprites at different X positions
    _sat_entry(vdp, 0, 0, 0, 0, 0x05)    # X=0
    _sat_entry(vdp, 1, 0, 16, 1, 0x0A)   # X=16 (no overlap)
    vdp.vram[_SPT + 0 * 8 + 0] = 0x80    # only pixel 0
    vdp.vram[_SPT + 1 * 8 + 0] = 0x80    # only pixel 0 of its own space
    _sat_entry(vdp, 2, 0xD0, 0, 0, 0)

    render_frame(vdp)

    assert not (vdp.status & 0x20), "coincidence flag must not be set"
