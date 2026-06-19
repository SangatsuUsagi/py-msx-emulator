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

# byte → 8 pixel bits (MSB first), precomputed to avoid per-call generators.
_UNPACK8: tuple[bytes, ...] = tuple(
    bytes((b >> (7 - i)) & 1 for i in range(8)) for b in range(256)
)

# GRAPHIC7 (SCREEN 8) sprites use a FIXED 16-colour palette, NOT the
# programmable one. Source values from openMSX Renderer::GRAPHIC7_SPRITE_PALETTE
# as 0xGRB nibbles (each channel 0-7); stored here pre-converted to the GRB332
# byte the SCREEN 8 frame buffer holds: (G<<5) | (R<<2) | (B>>1).
_G7_SPRITE_GRB_SRC: tuple[int, ...] = (
    0x000, 0x002, 0x030, 0x032, 0x300, 0x302, 0x330, 0x332,
    0x472, 0x007, 0x070, 0x077, 0x700, 0x707, 0x770, 0x777,
)
_G7_SPRITE_PALETTE: tuple[int, ...] = tuple(
    (((v >> 8) & 7) << 5) | (((v >> 4) & 7) << 2) | ((v & 7) >> 1)
    for v in _G7_SPRITE_GRB_SRC
)

# Solid-colour fill tables — avoids bytes([c]*N) allocation in hot tile loops.
_COLOR4: tuple[bytes, ...] = tuple(bytes([c] * 4) for c in range(16))
_COLOR8: tuple[bytes, ...] = tuple(bytes([c] * 8) for c in range(16))

# LUT caches for G4/G6 and G5 pixel expansion via bytes.translate().
# Keys: (tp: bool, border: int).  At most 32 entries each (2 × 16).
_G46_LUT_CACHE: dict[tuple[bool, int], tuple[bytes, bytes]] = {}
_G5_LUT_CACHE: dict[tuple[bool, int], tuple[bytes, bytes, bytes, bytes]] = {}


def _g46_luts(tp: bool, border: int) -> tuple[bytes, bytes]:
    """Return (lut_hi, lut_lo) translate tables for 4-bpp G4/G6 expansion."""
    key = (tp, border)
    if key not in _G46_LUT_CACHE:
        lut_hi = bytes(((b >> 4) & 0x0F) if (tp or (b >> 4) & 0x0F) else border
                       for b in range(256))
        lut_lo = bytes((b & 0x0F) if (tp or b & 0x0F) else border
                       for b in range(256))
        _G46_LUT_CACHE[key] = (lut_hi, lut_lo)
    return _G46_LUT_CACHE[key]


def _g5_luts(tp: bool, border: int) -> tuple[bytes, bytes, bytes, bytes]:
    """Return (p0,p1,p2,p3) translate tables for 2-bpp G5 expansion."""
    key = (tp, border)
    if key not in _G5_LUT_CACHE:
        def _ch(b: int, shift: int) -> int:
            c = (b >> shift) & 0x03
            return c if (tp or c) else border
        p0 = bytes(_ch(b, 6) for b in range(256))
        p1 = bytes(_ch(b, 4) for b in range(256))
        p2 = bytes(_ch(b, 2) for b in range(256))
        p3 = bytes(_ch(b, 0) for b in range(256))
        _G5_LUT_CACHE[key] = (p0, p1, p2, p3)
    return _G5_LUT_CACHE[key]


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

    log = vdp._reg_write_log
    buf = _render_banded(vdp) if log else _render_pass(vdp)
    return _apply_display_adjust(vdp, buf)


def _apply_display_adjust(vdp: "V9938", buf: bytearray) -> bytearray:
    """Apply R#18 display adjust: shift the whole picture horizontally/vertically
    and fill the exposed edges with the border colour.

    Each nibble of R#18 encodes an offset (openMSX): adjust = (nibble ^ 7),
    pixel/line offset = adjust - 7, range -7..+8; the BIOS-neutral nibble 0 → 0.
    Positive offset moves the picture right/down.
    """
    reg18 = vdp.regs[18]
    h_off = ((reg18 & 0x0F) ^ 0x07) - 7        # dots, +ve = right
    v_off = ((reg18 >> 4) ^ 0x07) - 7          # lines, +ve = down
    if h_off == 0 and v_off == 0:
        return buf

    h = vdp.display_height
    w = vdp.display_width
    r0 = vdp.regs[0]
    is_g7 = bool(((r0 >> 2) & 1) and ((r0 >> 3) & 1))
    border = vdp.regs[7] if is_g7 else (vdp.regs[7] & 0x0F)
    h_shift = h_off * (w // 256)               # 512-wide modes shift 2 buffer px per dot

    out = bytearray([border]) * (w * h)
    for sy in range(h):
        dy = sy + v_off
        if dy < 0 or dy >= h:
            continue
        srow = sy * w
        drow = dy * w
        if h_shift == 0:
            out[drow:drow + w] = buf[srow:srow + w]
        elif h_shift > 0:
            if h_shift < w:
                out[drow + h_shift:drow + w] = buf[srow:srow + w - h_shift]
        else:
            k = -h_shift
            if k < w:
                out[drow:drow + w - k] = buf[srow + k:srow + w]
    return out


def _build_bands(vdp: "V9938") -> list[tuple[int, int, list, list]]:
    """Build (y0, y1, regs_snapshot, palette_snapshot) bands from register change log."""
    log = vdp._reg_write_log
    display_height = vdp.display_height

    change_lines: dict[int, list[tuple[int, int]]] = {}
    for dl, reg, value in log:
        if 0 <= dl < display_height:
            change_lines.setdefault(dl, []).append((reg, value))

    if not change_lines:
        return []

    bands = []
    cur_regs = list(vdp._frame_start_regs)
    cur_palette = list(vdp._frame_start_palette)
    prev_y = 0

    for line in sorted(change_lines):
        if line > prev_y:
            bands.append((prev_y, line, list(cur_regs), list(cur_palette)))
        for reg, value in change_lines[line]:
            if reg == -1:
                idx, rgb = value
                cur_palette[idx] = rgb
            else:
                cur_regs[reg] = value
        prev_y = line

    if prev_y < display_height:
        bands.append((prev_y, display_height, list(cur_regs), list(cur_palette)))

    return bands


def _render_banded(vdp: "V9938") -> bytearray:
    """Render with band-split: each band uses its own register/palette snapshot."""
    bands = _build_bands(vdp)
    if not bands:
        return _render_pass(vdp)

    full_h = vdp.display_height
    full_w = vdp.display_width
    out = bytearray(full_w * full_h)

    saved_regs = vdp.regs[:]
    saved_palette = vdp.palette[:]

    for y0, y1, band_regs, band_palette in bands:
        vdp.regs = band_regs
        vdp.palette = band_palette
        band_h = vdp.display_height  # R#9 bit7 from band snapshot
        band_w = vdp.display_width   # R#0 bit3 from band snapshot
        temp = _render_pass(vdp)
        # Copy rows [y0, y1) into output; clamp y1 to band_h
        y_end = min(y1, band_h)
        out[y0 * band_w:y_end * band_w] = temp[y0 * band_w:y_end * band_w]

    vdp.regs = saved_regs
    vdp.palette = saved_palette
    return out


def _render_pass(vdp: "V9938") -> bytearray:
    """Single-pass render using current vdp register/palette state."""
    h = vdp.display_height
    w = vdp.display_width
    r0 = vdp.regs[0]
    r1 = vdp.regs[1]
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1

    is_g7 = bool(m5 and m4)
    border = vdp.regs[7] if is_g7 else (vdp.regs[7] & 0x0F)

    if not (r1 & 0x40):  # BL clear → blank display
        _finalize(vdp)
        return bytearray([border] * (w * h))

    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1

    buf = bytearray([border] * (w * h))

    if m5:
        if m4:
            _render_g7(vdp, buf, h)
            _render_sprites_mode2(vdp, buf, h, grb_mode=True)
        elif m3:
            _render_g6(vdp, buf, h)
            _render_sprites_mode2(vdp, buf, h, width=512)
        else:
            _render_g5(vdp, buf, h)
            _render_sprites_mode2(vdp, buf, h, width=512)
    elif m4:
        if m3:
            _render_g4(vdp, buf, h)
            _render_sprites_mode2(vdp, buf, h)
        else:
            _render_g2(vdp, buf)
            _render_sprites_mode2(vdp, buf, h)
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
    # VBlank flag is now set by begin_scanline() in the scanline loop (machine.py).
    # _finalize only handles frame-buffer finalisation; no interrupt generation here.
    pass


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
    col_base  = ((vdp.regs[10] & 0x07) << 14) | (vdp.regs[3] << 6)
    bd = _backdrop(vdp)

    for row in range(24):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            cb = vdp.vram[(col_base + tile // 8) & 0x1FFFF]
            _hi = (cb >> 4) & 0x0F; fg = _hi if _hi else bd  # inline _color
            _lo = cb & 0x0F;         bg = _lo if _lo else bd
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
    col_base  = ((vdp.regs[10] & 0x07) << 14) | ((vdp.regs[3] & 0x80) << 6)
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
                cb  = vdp.vram[(col_base + off) & 0x1FFFF]
                _hi = (cb >> 4) & 0x0F; fg = _hi if _hi else bd  # inline _color
                _lo = cb & 0x0F;         bg = _lo if _lo else bd
                rs = (row * 8 + py) * _W + bx
                buf[rs:rs + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text (SCREEN 0) — 40×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------

def _render_text(vdp: "V9938", buf: bytearray) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    # TEXT1: R#7 high nibble = text colour, low nibble = background; both are
    # palette indices used directly (the V9938 maps them through the programmable
    # palette — no colour-0 substitution).
    fg = (vdp.regs[7] >> 4) & 0x0F
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
                _hi = (pat >> 4) & 0x0F; lc = _hi if _hi else bd  # inline _color
                _lo = pat & 0x0F;         rc = _lo if _lo else bd
                y = row * 8 + py
                buf[y * _W + bx:y * _W + bx + 4] = _COLOR4[lc]
                buf[y * _W + bx + 4:y * _W + bx + 8] = _COLOR4[rc]


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
    spt_base = (vdp.regs[6] & 0x3F) << 11

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
            x_byte -= 32  # EC: shift 32px left; may go negative → clipped below

        y_top = (y_byte + 1) & 0xFF

        for line in range(_TILE_H):
            sprite_row = (line - y_top) & 0xFF
            if sprite_row >= render_size:
                continue

            if line_count[line] >= 4:  # V9938 sprite mode 1: 4 sprites per line
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
                    px = x_byte + bit_i * scale + s
                    if px < 0 or px >= _W:  # clip off-screen, no wrap
                        continue
                    coord = line * _W + px
                    if sprite_painted[coord]:
                        coincidence = True
                    else:
                        sprite_painted[coord] = 1
                        buf[coord] = color

    if coincidence:
        vdp.status |= 0x20


def _render_sprites_mode2(
    vdp: "V9938", buf: bytearray, h: int, grb_mode: bool = False, width: int = _W,
) -> None:
    """Sprite mode 2 for SCREEN 4–8.

    R#5/R#11 → SAT base (512-byte aligned). Colour table at sat_base-0x200.
    Per-line colour byte: EC(7) | CC(6, OR-combine) | IC(5, ignore collision) | colour(3:0).
    grb_mode=True (SCREEN 8): sprite palette colours are converted to GRB332 before writing.
    width: buffer line width; 512 for the wide modes (G5/G6), where the 256-dot
    sprite plane is doubled horizontally so sprites span the full screen.
    """
    r1 = vdp.regs[1]
    si  = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size    = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)
    screen_scale = width // _W  # 1 (256-wide modes) or 2 (512-wide G5/G6)

    # R#5/R#11 specify the SAT base (512-byte aligned). Color table is 0x200 before SAT.
    attr_reg = (((vdp.regs[11] & 3) << 15) | (vdp.regs[5] << 7)) & 0x1FFFF
    sat_base = attr_reg & ~0x1FF & 0x1FFFF
    col_base = (sat_base - 0x200) & 0x1FFFF
    spt_base = (vdp.regs[6] & 0x3F) << 11

    vscroll = vdp.regs[23]
    line_count = [0] * h
    ninth_set  = False
    sprite_buf = bytearray(h * width)  # 0 = transparent
    # Per line: set once a CC=0 sprite has appeared. A CC=1 sprite is only
    # visible if a higher-priority CC=0 sprite precedes it on the same line
    # (V9938 rule); leading CC=1 sprites are invisible.
    cc0_seen = bytearray(h)
    drawn: list[int] = []  # coords touched, to composite only those (vs full scan)
    coincidence = False

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x1FFFF]
        if y_byte == 0xD0:
            break

        x_byte  = vdp.vram[(sat_base + i * 4 + 1) & 0x1FFFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x1FFFF]
        # SAT 4th byte is unused in sprite mode 2; colour/EC/CC/IC come from the
        # per-line colour table byte instead.

        y_top = (y_byte + 1) & 0xFF

        for line in range(h):
            # Sprite Y is in VRAM coordinate space; account for vertical scroll.
            vram_line = (line + vscroll) & 0xFF
            sprite_row = (vram_line - y_top) & 0xFF
            if sprite_row >= render_size:
                continue

            if line_count[line] >= 8:
                if not ninth_set:
                    vdp.status = (vdp.status & 0xA0) | 0x40 | (i & 0x1F)
                    ninth_set = True
                continue

            line_count[line] += 1

            src_row = sprite_row // 2 if mag else sprite_row
            # Per-line colour byte: EC(7) | CC(6) | IC(5) | 0 | colour(3:0).
            col_entry = vdp.vram[(col_base + i * 16 + src_row) & 0x1FFFF]
            color   = col_entry & 0x0F
            or_mode = bool(col_entry & 0x40)  # CC: OR-combine with same-priority sprite
            ignore_collision = bool(col_entry & 0x20)  # IC: don't flag coincidence
            x_pos = x_byte - 32 if (col_entry & 0x80) else x_byte  # EC: per-line shift

            # CC=1 sprites are only visible once a higher-priority CC=0 sprite
            # has appeared on this line; a CC=0 sprite enables them (counted even
            # when its own colour is transparent).
            if or_mode:
                if not cc0_seen[line]:
                    continue
            else:
                cc0_seen[line] = 1

            if color == 0:
                continue  # transparent regardless of OR mode

            pixels = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row, mask=0x1FFFF)
            scale  = 2 if mag else 1

            for bit_i, pixel in enumerate(pixels):
                if not pixel:
                    continue
                for s in range(scale):
                    sx = x_pos + bit_i * scale + s  # position in the 256-dot sprite plane
                    if sx < 0 or sx >= _W:  # clip off-screen, no wrap
                        continue
                    for ss in range(screen_scale):  # horizontal doubling in 512-wide modes
                        px = sx * screen_scale + ss
                        coord = line * width + px
                        if sprite_buf[coord]:
                            if not ignore_collision:
                                coincidence = True
                            if or_mode:
                                sprite_buf[coord] |= color
                        else:
                            sprite_buf[coord] = color
                            drawn.append(coord)

    # Composite only the pixels a sprite actually touched (avoids scanning the
    # whole h*width buffer every frame when sprites are sparse or absent).
    if grb_mode:
        # SCREEN 8 sprites use the fixed GRAPHIC7 sprite palette, not vdp.palette.
        for coord in drawn:
            buf[coord] = _G7_SPRITE_PALETTE[sprite_buf[coord] & 0x0F]
    else:
        for coord in drawn:
            buf[coord] = sprite_buf[coord]

    if coincidence:
        vdp.status |= 0x20


def _sprite_row_pixels(
    vdp: "V9938", spt_base: int, pat_idx: int, si: int, src_row: int,
    mask: int = 0x3FFF,
) -> bytes:
    if si == 0:
        b = vdp.vram[(spt_base + pat_idx * 8 + src_row) & mask]
        return _UNPACK8[b]

    base = pat_idx & 0xFC
    if src_row < 8:
        left  = vdp.vram[(spt_base + base * 8 + src_row) & mask]
        right = vdp.vram[(spt_base + (base + 2) * 8 + src_row) & mask]
    else:
        r = src_row - 8
        left  = vdp.vram[(spt_base + (base + 1) * 8 + r) & mask]
        right = vdp.vram[(spt_base + (base + 3) * 8 + r) & mask]

    return _UNPACK8[left] + _UNPACK8[right]


# ---------------------------------------------------------------------------
# SCREEN 5 (Graphic 4) — 4-bpp bitmap, two palette indices per byte
# ---------------------------------------------------------------------------

def _render_g4(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 5: 4-bpp, palette index per half-byte (high nibble = left pixel)."""
    base = (vdp.regs[2] & 0x60) << 10
    tp = bool(vdp.regs[8] & 0x20)  # R#8 bit5: 1=col0 solid, 0=col0 transparent→backdrop
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    # C-level expansion: two translate tables (hi/lo nibble) + strided slice assignment.
    lut_hi, lut_lo = _g46_luts(tp, border)
    for y in range(h):
        row_base = (base + ((y + vscroll) & 0xFF) * 128) & 0x1FFFF
        bx = y * _W
        row = bytes(vram[row_base:row_base + 128])
        buf[bx:bx + _W:2]      = row.translate(lut_hi)
        buf[bx + 1:bx + _W:2]  = row.translate(lut_lo)


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5) — 2-bpp, 512 virtual width, rendered 256 wide
# ---------------------------------------------------------------------------

def _render_g5(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 6: 2-bpp, 4 pixels per byte, full 512-pixel width."""
    base = (vdp.regs[2] & 0x60) << 10
    tp = bool(vdp.regs[8] & 0x20)
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    w = 512
    # C-level expansion: four translate tables (bit-pairs 7:6, 5:4, 3:2, 1:0) + stride-4.
    p0, p1, p2, p3 = _g5_luts(tp, border)
    for y in range(h):
        row_base = (base + ((y + vscroll) & 0xFF) * 128) & 0x1FFFF
        bx = y * w
        row = bytes(vram[row_base:row_base + 128])
        buf[bx:bx + w:4]     = row.translate(p0)
        buf[bx + 1:bx + w:4] = row.translate(p1)
        buf[bx + 2:bx + w:4] = row.translate(p2)
        buf[bx + 3:bx + w:4] = row.translate(p3)


# ---------------------------------------------------------------------------
# SCREEN 7 (Graphic 6) — 4-bpp, 512 virtual width, rendered 256 wide
# ---------------------------------------------------------------------------

def _render_g6(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 7: 4-bpp, 2 pixels per byte, full 512-pixel width."""
    base = (vdp.regs[2] & 0x40) << 10  # G6: 64KB pages, bit6 only
    tp = bool(vdp.regs[8] & 0x20)
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    w = 512
    # Same hi/lo translate approach as G4 but 256 VRAM bytes → 512 output pixels.
    lut_hi, lut_lo = _g46_luts(tp, border)
    for y in range(h):
        row_base = (base + ((y + vscroll) & 0xFF) * 256) & 0x1FFFF  # 256 bytes/line
        bx = y * w
        row = bytes(vram[row_base:row_base + 256])
        buf[bx:bx + w:2]     = row.translate(lut_hi)
        buf[bx + 1:bx + w:2] = row.translate(lut_lo)


# ---------------------------------------------------------------------------
# SCREEN 8 (Graphic 7) — 8-bpp GRB332, raw bytes, no palette
# ---------------------------------------------------------------------------

# 3-bit channel → 8-bit intensity, matching openMSX (255 * i / 7).
_INTENSITY3: tuple[int, ...] = tuple(255 * i // 7 for i in range(8))
# 2-bit blue → 8-bit, via openMSX's intensity indices {0, 2, 4, 7}.
_BLUE2: tuple[int, ...] = (_INTENSITY3[0], _INTENSITY3[2], _INTENSITY3[4], _INTENSITY3[7])


def grb332_to_rgb(byte: int) -> tuple[int, int, int]:
    """Convert a GRB332 pixel byte to (R, G, B) 8-bit channels.

    Bits 7:5 = G, bits 4:2 = R, bits 1:0 = B. Matches openMSX: the 3-bit R/G
    channels use the 255*i//7 intensity table; the 2-bit B channel maps through
    intensity indices {0, 2, 4, 7} → (0, 72, 145, 255).
    """
    g = (byte >> 5) & 0x07
    r = (byte >> 2) & 0x07
    b = byte & 0x03
    return (_INTENSITY3[r], _INTENSITY3[g], _BLUE2[b])


def _render_g7(vdp: "V9938", buf: bytearray, h: int) -> None:
    """SCREEN 8: 8-bpp GRB332, one raw byte per pixel (palette not used)."""
    base = (vdp.regs[2] & 0x40) << 10  # G7: 64KB pages, bit6 only
    vscroll = vdp.regs[23]
    vram = vdp.vram
    for y in range(h):
        row_base = (base + ((y + vscroll) & 0xFF) * _W) & 0x1FFFF
        bx = y * _W
        buf[bx:bx + _W] = vram[row_base:row_base + _W]  # whole row, one C-level copy
