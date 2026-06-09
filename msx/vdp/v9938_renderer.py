"""render_frame() for V9938 VDP — SCREEN 0–3 (TMS9918A-compatible modes).

SCREEN 4–8 dispatch stubs are included but return blank frames; those modes
are implemented in later phases.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.vdp.v9938 import V9938

_W = 256
_TILE_H = 192  # TMS9918A-compatible modes always render 24 tile rows (192 px)

# Precomputed tile-row lookup: _ROW_BYTES[pat][fg][bg] → 8-byte slice.
_ROW_BYTES: list[list[list[bytes]]] = [
    [
        [
            bytes((fg if (pat >> (7 - px)) & 1 else bg) for px in range(8))
            for bg in range(16)
        ]
        for fg in range(16)
    ]
    for pat in range(256)
]


def render_frame(vdp: "V9938", skip_render: bool = False) -> bytearray:
    """Render one frame; return bytearray of palette indices (length 256×display_height)."""
    vdp._frame_count += 1
    if skip_render:
        _finalize(vdp)
        return bytearray(0)

    h = vdp.display_height
    r0 = vdp.regs[0]
    r1 = vdp.regs[1]
    border = vdp.regs[7] & 0x0F

    if not (r1 & 0x40):  # BL clear → blank display
        _finalize(vdp)
        return bytearray([border] * (_W * h))

    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 3) & 1
    m5 = (r0 >> 4) & 1

    # Pre-fill with border; tile renderers only write the first _TILE_H rows.
    buf = bytearray([border] * (_W * h))

    if m5:
        if m4 and m2:
            _render_g7(vdp, buf, h)   # SCREEN 8 (Graphic 7)
        elif m4:
            _render_g5(vdp, buf, h)   # SCREEN 6 (Graphic 5)
        else:
            _render_g4(vdp, buf, h)   # SCREEN 5 (Graphic 4)
    elif m4:
        if m2:
            _render_g6(vdp, buf, h)   # SCREEN 7 (Graphic 6)
        else:
            _render_g2(vdp, buf)      # SCREEN 4 (Graphic 3) — same tiles as G2; sprites deferred to Phase 7
    elif m1:
        _render_text(vdp, buf)
    elif m3:
        _render_g2(vdp, buf)
        _render_sprites(vdp, buf)
    elif m2:
        _render_mc(vdp, buf)
        _render_sprites(vdp, buf)
    else:
        _render_g1(vdp, buf)
        _render_sprites(vdp, buf)

    _finalize(vdp)
    return buf


def _finalize(vdp: "V9938") -> None:
    vdp.status |= 0x80  # set F flag (VBlank)
    if (vdp.regs[1] & 0x20) and vdp.on_interrupt is not None:
        vdp.on_interrupt()


def _backdrop(vdp: "V9938") -> int:
    return vdp.regs[7] & 0x0F


def _color(c: int, backdrop: int) -> int:
    return backdrop if c == 0 else c


# ---------------------------------------------------------------------------
# Graphic 1 (SCREEN 1) — 32×24 tiles, colour per 8-tile group
# ---------------------------------------------------------------------------

def _render_g1(vdp: "V9938", buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    col_base  = vdp.regs[3] << 6
    bd = _backdrop(vdp)

    for row in range(24):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            cb = vdp.vram[(col_base + tile // 8) & 0x3FFF]
            fg = _color((cb >> 4) & 0x0F, bd)
            bg = _color(cb & 0x0F, bd)
            pat_tile = pat_base + tile * 8
            bx = col * 8
            for py in range(8):
                pat = vdp.vram[(pat_tile + py) & 0x3FFF]
                rs = (row * 8 + py) * _W + bx
                buf[rs:rs + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Graphic 2 (SCREEN 2) — 32×24 tiles, per-row per-tile colour
# ---------------------------------------------------------------------------

def _render_g2(vdp: "V9938", buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x04) << 11
    col_base  = (vdp.regs[3] & 0x80) << 6
    bd = _backdrop(vdp)

    for row in range(24):
        band_offset = (row // 8) * 0x800
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            tile_off = band_offset + tile * 8
            bx = col * 8
            for py in range(8):
                off = tile_off + py
                pat = vdp.vram[(pat_base + off) & 0x3FFF]
                cb  = vdp.vram[(col_base + off) & 0x3FFF]
                fg = _color((cb >> 4) & 0x0F, bd)
                bg = _color(cb & 0x0F, bd)
                rs = (row * 8 + py) * _W + bx
                buf[rs:rs + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text (SCREEN 0) — 40×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------

def _render_text(vdp: "V9938", buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    fg = _color((vdp.regs[7] >> 4) & 0x0F, 1)
    bg = vdp.regs[7] & 0x0F

    for row in range(24):
        for col in range(40):
            tile = vdp.vram[(name_base + row * 40 + col) & 0x3FFF]
            for py in range(8):
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x3FFF]
                y = row * 8 + py
                for px in range(6):
                    c = fg if (pat >> (7 - px)) & 1 else bg
                    buf[y * _W + 8 + col * 6 + px] = c


# ---------------------------------------------------------------------------
# Multicolor (SCREEN 3) — 64×48 blocks of 4×4 pixels
# ---------------------------------------------------------------------------

def _render_mc(vdp: "V9938", buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    bd = _backdrop(vdp)

    for row in range(24):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            bx = col * 8
            for py in range(8):
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x3FFF]
                lc = _color((pat >> 4) & 0x0F, bd)
                rc = _color(pat & 0x0F, bd)
                y = row * 8 + py
                buf[y * _W + bx:y * _W + bx + 4] = bytes([lc] * 4)
                buf[y * _W + bx + 4:y * _W + bx + 8] = bytes([rc] * 4)


# ---------------------------------------------------------------------------
# Sprite mode 1 — V9938 allows 8 sprites per scanline (vs 4 on TMS9918A)
# ---------------------------------------------------------------------------

def _render_sprites(vdp: "V9938", buf: bytearray) -> None:
    r1 = vdp.regs[1]
    si  = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size   = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)

    sat_base = (vdp.regs[5] & 0x7F) << 7
    spt_base = (vdp.regs[6] & 0x07) << 11

    line_count = [0] * _TILE_H
    ninth_set  = False
    sprite_painted = bytearray(_W * _TILE_H)
    coincidence = False

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x3FFF]
        if y_byte == 0xD0:
            break

        x_byte  = vdp.vram[(sat_base + i * 4 + 1) & 0x3FFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x3FFF]
        attr    = vdp.vram[(sat_base + i * 4 + 3) & 0x3FFF]
        color   = attr & 0x0F
        if attr & 0x80:
            x_byte = (x_byte - 32) & 0xFF

        y_top = (y_byte + 1) & 0xFF

        for line in range(_TILE_H):
            sprite_row = (line - y_top) & 0xFF
            if sprite_row >= render_size:
                continue

            if line_count[line] >= 8:
                if not ninth_set:
                    vdp.status = (vdp.status & 0xA0) | 0x40 | (i & 0x1F)
                    ninth_set = True
                continue

            line_count[line] += 1

            if color == 0:
                continue

            src_row = sprite_row // 2 if mag else sprite_row
            pixels  = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row)
            scale   = 2 if mag else 1

            for bit_i, pixel in enumerate(pixels):
                if not pixel:
                    continue
                for s in range(scale):
                    px = (x_byte + bit_i * scale + s) & 0xFF
                    if px >= _W:
                        continue
                    coord = line * _W + px
                    if sprite_painted[coord]:
                        coincidence = True
                    else:
                        sprite_painted[coord] = 1
                        buf[coord] = color

    if coincidence:
        vdp.status |= 0x20


def _sprite_row_pixels(
    vdp: "V9938", spt_base: int, pat_idx: int, si: int, src_row: int
) -> bytes:
    if si == 0:
        b = vdp.vram[(spt_base + pat_idx * 8 + src_row) & 0x3FFF]
        return bytes((b >> (7 - bit)) & 1 for bit in range(8))

    base = pat_idx & 0xFC
    if src_row < 8:
        left  = vdp.vram[(spt_base + base * 8 + src_row) & 0x3FFF]
        right = vdp.vram[(spt_base + (base + 2) * 8 + src_row) & 0x3FFF]
    else:
        r = src_row - 8
        left  = vdp.vram[(spt_base + (base + 1) * 8 + r) & 0x3FFF]
        right = vdp.vram[(spt_base + (base + 3) * 8 + r) & 0x3FFF]

    return (
        bytes((left  >> (7 - b)) & 1 for b in range(8))
        + bytes((right >> (7 - b)) & 1 for b in range(8))
    )


# ---------------------------------------------------------------------------
# SCREEN 5 (Graphic 4) — 4-bpp bitmap, two palette indices per byte
# ---------------------------------------------------------------------------

def _render_g4(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 5: 4-bpp, palette index per half-byte (high nibble = left pixel)."""
    base = ((vdp.regs[2] & 0x7F) * 0x800) & 0x1FFFF
    for y in range(h):
        row_base = base + y * 128
        bx = y * _W
        for x in range(0, _W, 2):
            b = vdp.vram[(row_base + x // 2) & 0x1FFFF]
            buf[bx + x]     = (b >> 4) & 0x0F
            buf[bx + x + 1] = b & 0x0F


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5) — 2-bpp, 512 virtual width, rendered 256 wide
# ---------------------------------------------------------------------------

def _render_g5(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 6: 2-bpp, 4 virtual pixels per byte; sample even pixels → 256 wide."""
    base = ((vdp.regs[2] & 0x7F) * 0x800) & 0x1FFFF
    for y in range(h):
        row_base = base + y * 128
        bx = y * _W
        for ox in range(_W):
            b = vdp.vram[(row_base + ox // 2) & 0x1FFFF]
            shift = 6 if (ox % 2 == 0) else 2
            buf[bx + ox] = (b >> shift) & 0x03


# ---------------------------------------------------------------------------
# SCREEN 7 (Graphic 6) — 4-bpp, 512 virtual width, rendered 256 wide
# ---------------------------------------------------------------------------

def _render_g6(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 7: 4-bpp, 2 virtual pixels per byte; sample even pixels → 256 wide."""
    base = ((vdp.regs[2] & 0x7F) * 0x800) & 0x1FFFF
    for y in range(h):
        row_base = base + y * _W
        bx = y * _W
        for ox in range(_W):
            b = vdp.vram[(row_base + ox) & 0x1FFFF]
            buf[bx + ox] = (b >> 4) & 0x0F


# ---------------------------------------------------------------------------
# SCREEN 8 (Graphic 7) — 8-bpp GRB332, raw bytes, no palette
# ---------------------------------------------------------------------------

def grb332_to_rgb(byte: int) -> tuple[int, int, int]:
    """Convert a GRB332 pixel byte to (R, G, B) 8-bit channels.

    Bits 7:5 = G, bits 4:2 = R, bits 1:0 = B.
    3-bit channels scale × 36; 2-bit B channel scales × 85.
    """
    g = (byte >> 5) & 0x07
    r = (byte >> 2) & 0x07
    b = byte & 0x03
    return (r * 36, g * 36, b * 85)


def _render_g7(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 8: 8-bpp GRB332, one raw byte per pixel (palette not used)."""
    base = ((vdp.regs[2] & 0x7F) * 0x800) & 0x1FFFF
    for y in range(h):
        row_base = base + y * _W
        bx = y * _W
        for x in range(_W):
            buf[bx + x] = vdp.vram[(row_base + x) & 0x1FFFF]
