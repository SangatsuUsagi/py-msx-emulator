from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.vdp.vdp import VDP

_W = 256
_H = 192

# Precomputed tile-row lookup: _ROW_BYTES[pat][fg][bg] → 8-byte slice.
# Eliminates the 8-iteration per-pixel Python loop in G1/G2 renderers.
# Memory cost: 256 × 16 × 16 × 8 bytes ≈ 512 KB, built once at import time.
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


def render_frame(vdp: VDP, skip_render: bool = False) -> bytearray:
    if skip_render:
        _finalize(vdp)
        return bytearray(0)

    r0 = vdp.regs[0]
    r1 = vdp.regs[1]
    border = vdp.regs[7] & 0x0F

    if not (r1 & 0x40):  # BL (Blank) bit clear → blank display
        buf = bytearray([border] * (_W * _H))
        _finalize(vdp)
        return buf

    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1

    h = vdp.display_height
    buf = bytearray(_W * h)

    if m1:
        _render_text(vdp, buf)
    elif m3 and m4:
        _render_g4(vdp, buf)
    elif m3:
        _render_g2(vdp, buf)
        if not vdp.debug_disable_sprites:
            _render_sprites(vdp, buf)
    elif m2:
        _render_mc(vdp, buf)
        if not vdp.debug_disable_sprites:
            _render_sprites(vdp, buf)
    else:
        _render_g1(vdp, buf)
        if not vdp.debug_disable_sprites:
            _render_sprites(vdp, buf)

    _finalize(vdp)
    return buf


def _finalize(vdp: VDP) -> None:
    vdp.status |= 0x80
    if (vdp.regs[1] & 0x20) and vdp.on_interrupt is not None:
        vdp.on_interrupt()


def _backdrop(vdp: VDP) -> int:
    return vdp.regs[7] & 0x0F


def _color(c: int, backdrop: int) -> int:
    return backdrop if c == 0 else c


# ---------------------------------------------------------------------------
# Graphic 1 (SCREEN 1) — 32×24 tiles, colour per 8-tile group
# ---------------------------------------------------------------------------

def _render_g1(vdp: VDP, buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base = (vdp.regs[4] & 0x07) << 11
    col_base = vdp.regs[3] << 6
    bd = _backdrop(vdp)

    for row in range(24):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            cb = vdp.vram[(col_base + tile // 8) & 0x3FFF]
            fg = _color((cb >> 4) & 0x0F, bd)
            bg = _color(cb & 0x0F, bd)
            pat_base_tile = pat_base + tile * 8
            bx = col * 8
            for py in range(8):
                pat = vdp.vram[(pat_base_tile + py) & 0x3FFF]
                row_start = (row * 8 + py) * _W + bx
                buf[row_start:row_start + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Graphic 2 (SCREEN 2) — 32×24 tiles, per-row per-tile colour
# ---------------------------------------------------------------------------

def _render_g2(vdp: VDP, buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base = (vdp.regs[4] & 0x04) << 11
    col_base = (vdp.regs[3] & 0x80) << 6
    # TMS9918A G2 band masking: R3[6:0] / R4[1:0] control which sector-select
    # address bits vary. 0 bits collapse band1/2 tables onto band0.
    col_mask = ((vdp.regs[3] & 0x7F) << 6) | 0x3F
    pat_mask = ((vdp.regs[4] & 0x03) << 11) | 0x7FF
    bd = _backdrop(vdp)

    for row in range(24):
        band = row // 8
        band_offset = band * 0x800
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            tile_offset = band_offset + tile * 8
            bx = col * 8
            for py in range(8):
                offset = tile_offset + py
                pat = vdp.vram[(pat_base + (offset & pat_mask)) & 0x3FFF]
                cb = vdp.vram[(col_base + (offset & col_mask)) & 0x3FFF]
                fg = _color((cb >> 4) & 0x0F, bd)
                bg = _color(cb & 0x0F, bd)
                row_start = (row * 8 + py) * _W + bx
                buf[row_start:row_start + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text (SCREEN 0) — 40×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------

def _render_text(vdp: VDP, buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base = (vdp.regs[4] & 0x07) << 11
    fg = _color((vdp.regs[7] >> 4) & 0x0F, 1)
    bg = vdp.regs[7] & 0x0F
    border = bg

    buf[:] = bytes([border]) * (_W * _H)

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

def _render_mc(vdp: VDP, buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base = (vdp.regs[4] & 0x07) << 11
    bd = _backdrop(vdp)

    for row in range(24):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            for py in range(8):
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x3FFF]
                lc = _color((pat >> 4) & 0x0F, bd)
                rc = _color(pat & 0x0F, bd)
                y = row * 8 + py
                bx = col * 8
                for px in range(4):
                    buf[y * _W + bx + px] = lc
                for px in range(4, 8):
                    buf[y * _W + bx + px] = rc


# ---------------------------------------------------------------------------
# Graphic 4 / SCREEN 5 — 256×(192 or 212) bitmap, 4-bit packed pixels
# ---------------------------------------------------------------------------

def _render_g4(vdp: VDP, buf: bytearray) -> None:
    display_base = (vdp.regs[2] >> 4 & 3) << 15
    h = vdp.display_height
    for y in range(h):
        row_start = y * _W
        vram_row = display_base + y * 128
        for x in range(0, _W, 2):
            byte = vdp.vram[(vram_row + x // 2) & 0x1FFFF]
            buf[row_start + x] = (byte >> 4) & 0xF
            buf[row_start + x + 1] = byte & 0xF


# ---------------------------------------------------------------------------
# Sprite rendering — shared across G1, G2, MC
# ---------------------------------------------------------------------------

def _render_sprites(vdp: VDP, buf: bytearray) -> None:
    r1 = vdp.regs[1]
    si = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)

    sat_base = (vdp.regs[5] & 0x7F) << 7
    spt_base = (vdp.regs[6] & 0x07) << 11

    line_count = [0] * _H
    fifth_set = False
    sprite_painted = bytearray(_W * _H)
    coincidence = False

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x3FFF]
        if y_byte == 0xD0:
            break

        x_byte = vdp.vram[(sat_base + i * 4 + 1) & 0x3FFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x3FFF]
        attr = vdp.vram[(sat_base + i * 4 + 3) & 0x3FFF]
        color = attr & 0x0F
        if attr & 0x80:
            x_byte -= 32  # EC: shift 32px left; may go negative → clipped below

        y_top = (y_byte + 1) & 0xFF

        # Scan only the sprite's visible band [y_top, y_top+render_size); take the
        # wrapped band (increasing screen line) only when the & 0xFF row test
        # crosses 255. Per-sprite line order does not affect line_count / 5S /
        # coincidence, so this is equivalent to the old full [0, _H) scan.
        end = y_top + render_size
        if end <= 256:
            lines = range(y_top, min(_H, end))
        else:
            lines = chain(range(0, min(_H, end - 256)), range(y_top, _H))

        for line in lines:
            sprite_row = (line - y_top) & 0xFF  # guaranteed < render_size

            if line_count[line] >= 4:
                if not fifth_set:
                    vdp.status = (vdp.status & 0xA0) | 0x40 | (i & 0x1F)
                    fifth_set = True
                continue

            line_count[line] += 1

            if color == 0:
                continue

            src_row = sprite_row // 2 if mag else sprite_row
            pixels = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row)
            scale = 2 if mag else 1
            row = line * _W

            if scale == 1:  # MAG=0 fast path: skip the range(1) magnification loop
                for bit_i, pixel in enumerate(pixels):
                    if not pixel:
                        continue
                    px = x_byte + bit_i
                    if px < 0 or px >= _W:
                        continue
                    coord = row + px
                    if sprite_painted[coord]:
                        coincidence = True
                    else:
                        sprite_painted[coord] = 1
                        buf[coord] = color
            else:
                for bit_i, pixel in enumerate(pixels):
                    if not pixel:
                        continue
                    for s in range(scale):
                        px = x_byte + bit_i * scale + s
                        if px < 0 or px >= _W:
                            continue
                        coord = row + px
                        if sprite_painted[coord]:
                            coincidence = True
                        else:
                            sprite_painted[coord] = 1
                            buf[coord] = color

    if coincidence:
        vdp.status |= 0x20


def _sprite_row_pixels(
    vdp: VDP, spt_base: int, pat_idx: int, si: int, src_row: int
) -> bytes:
    if si == 0:
        b = vdp.vram[(spt_base + pat_idx * 8 + src_row) & 0x3FFF]
        return bytes((b >> (7 - bit)) & 1 for bit in range(8))

    # TMS9918A 16x16 layout: base+0=upper-left, base+1=lower-left,
    #                          base+2=upper-right, base+3=lower-right
    base = pat_idx & 0xFC
    if src_row < 8:
        left = vdp.vram[(spt_base + base * 8 + src_row) & 0x3FFF]
        right = vdp.vram[(spt_base + (base + 2) * 8 + src_row) & 0x3FFF]
    else:
        r = src_row - 8
        left = vdp.vram[(spt_base + (base + 1) * 8 + r) & 0x3FFF]
        right = vdp.vram[(spt_base + (base + 3) * 8 + r) & 0x3FFF]

    left_bits = bytes((left >> (7 - b)) & 1 for b in range(8))
    right_bits = bytes((right >> (7 - b)) & 1 for b in range(8))
    return left_bits + right_bits
