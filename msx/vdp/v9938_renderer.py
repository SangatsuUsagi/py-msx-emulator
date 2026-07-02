"""render_frame() for V9938 VDP — SCREEN 0–3 (TMS9918A-compatible modes).

SCREEN 4–8 dispatch stubs are included but return blank frames; those modes
are implemented in later phases.
"""
from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.vdp.v9938 import V9938

_W = 256
_TILE_H = 192  # TMS9918A-compatible modes always render 24 tile rows (192 px)

# Cache constant border-fill buffers per colour so a band prefill can slice-assign
# a memoryview instead of rebuilding bytes([border]) * n every band/frame.
_BORDER_CACHE: dict[int, bytes] = {}


def _border_fill(border: int, n: int) -> memoryview:
    """Return an n-byte view of a cached constant-`border` buffer."""
    buf = _BORDER_CACHE.get(border)
    if buf is None or len(buf) < n:
        buf = bytes((border,)) * n
        _BORDER_CACHE[border] = buf
    return memoryview(buf)[:n]

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

# Precomputed text-row lookup: _TEXT6_BYTES[pat][fg][bg] → 6-byte slice.
_TEXT6_BYTES: list[list[list[bytes]]] = [
    [
        [
            bytes((fg if (pat >> (7 - px)) & 1 else bg) for px in range(6))
            for bg in range(16)
        ]
        for fg in range(16)
    ]
    for pat in range(256)
]


def render_frame(vdp: "V9938", skip_render: bool = False) -> bytearray:
    """Render one frame; return bytearray of palette indices (length 256×display_height).

    Frame counting is owned by the caller (Machine.run_frame), not the renderer.
    """
    # Reset 5S (bit6) and C (bit5) at frame start (F/bit7 is owned by
    # begin_scanline). The sprite scan below re-sets them if a 5th sprite /
    # collision occurs this frame; without this reset they would persist across
    # frames whenever no S#0 read cleared them (V9938 clears them on S#0 read).
    vdp.status &= ~0x60

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
        # Writes logged at display_line=N happen during line N+1's CPU window
        # (begin_scanline(N) fires after N's CPU budget; ISR runs in N+1's window).
        # The register change therefore takes effect from line N+1 onwards.
        effective = dl + 1
        if 0 < effective < display_height:
            change_lines.setdefault(effective, []).append((reg, value))

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

    # Sprites are evaluated once per frame (like real hardware / openMSX positions
    # each sprite once), using the registers of the largest band — the main play
    # area.  Drawing sprites per band would re-evaluate every sprite in every band
    # and, when bands carry different vscroll, draw the same sprite at two screen
    # positions (the duplicate/ghost bug).
    main_regs = max(bands, key=lambda b: b[1] - b[0])[2]

    out = bytearray(full_w * full_h)

    saved_regs = vdp.regs[:]
    saved_palette = vdp.palette[:]

    # Background: one pass per band with that band's registers/palette (no sprites).
    for y0, y1, band_regs, band_palette in bands:
        vdp.regs = band_regs
        vdp.palette = band_palette
        _render_pass_range(vdp, out, y0, min(y1, full_h), draw_sprites=False)

    # Sprites: normally a single full-frame pass with the main band's registers
    # (drawing sprites per band would redraw the same SAT sprite at each band's
    # vscroll → the duplicate/ghost bug). The exception is a mid-screen sprite
    # attribute table switch (R#5/R#11), used for sprite multiplexing / "sprite
    # doubler" (e.g. Space Manbow flips R#5 near screen centre to show a second
    # set of 32 sprites in the lower half). There the regions read *different*
    # SAT buffers, so one sprite pass per SAT region — each clipped to its
    # scanline span — reproduces the doubler without the same-sprite ghost.
    # Consecutive bands sharing a SAT base are merged so the pass count stays
    # at the number of distinct SAT regions (one for ordinary games), keeping
    # the cost ~unchanged. R#5 is only changed mid-active-display for this
    # purpose, so gating on it does not affect non-multiplexed games.
    def _sat_key(regs: list[int]) -> tuple[int, int]:
        return (regs[5], regs[11] & 0x03)

    sat_segments: list[list] = []  # [y0, y1, regs_of_largest_band, largest_band_h]
    for y0, y1, band_regs, _ in bands:
        bh = y1 - y0
        if sat_segments and _sat_key(sat_segments[-1][2]) == _sat_key(band_regs):
            seg = sat_segments[-1]
            seg[1] = y1
            if bh > seg[3]:
                seg[2] = band_regs
                seg[3] = bh
        else:
            sat_segments.append([y0, y1, band_regs, bh])

    if len(sat_segments) <= 1:
        # One SAT for the whole frame → original single pass (main band regs).
        vdp.regs = main_regs
        _render_sprites_for_mode(vdp, out, 0, full_h)
    else:
        for y0, y1, seg_regs, _ in sat_segments:
            vdp.regs = seg_regs
            _render_sprites_for_mode(vdp, out, y0, min(y1, full_h))

    _finalize(vdp)
    vdp.regs = saved_regs
    vdp.palette = saved_palette
    return out


def _render_pass(vdp: "V9938") -> bytearray:
    """Single-pass render using current vdp register/palette state."""
    h = vdp.display_height
    w = vdp.display_width
    buf = bytearray(w * h)
    _render_pass_range(vdp, buf, 0, h)
    _finalize(vdp)
    return buf


def _render_pass_range(
    vdp: "V9938", buf: bytearray, y_start: int, y_end: int,
    draw_sprites: bool = True,
) -> None:
    """Render scanlines [y_start, y_end) into buf using current vdp register/palette state.

    Pre-fills the scanline range with border colour, then writes content pixels.
    draw_sprites=False renders the background only (banded mode draws sprites once
    per frame afterwards, so a sprite is not re-evaluated in every band).
    """
    w = vdp.display_width
    h = vdp.display_height
    r0 = vdp.regs[0]
    r1 = vdp.regs[1]
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1

    is_g7 = bool(m5 and m4)
    border = vdp.regs[7] if is_g7 else (vdp.regs[7] & 0x0F)

    n = (y_end - y_start) * w
    buf[y_start * w:y_end * w] = _border_fill(border, n)

    if not (r1 & 0x40):  # BL clear → blank band already filled above
        return

    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1

    # Background plane only — sprites are dispatched separately (see _render_sprites_for_mode).
    if m5:
        if m4:
            _render_g7(vdp, buf, h, y_start, y_end)
        elif m3:
            _render_g6(vdp, buf, h, y_start, y_end)
        else:
            _render_g5(vdp, buf, h, y_start, y_end)
    elif m4:
        if m3:
            _render_g4(vdp, buf, h, y_start, y_end)
        else:
            _render_g2(vdp, buf, y_start, y_end)
    elif m1:
        _render_text(vdp, buf, y_start, y_end)
    elif m3:
        _render_g2(vdp, buf, y_start, y_end)
    elif m2:
        _render_mc(vdp, buf, y_start, y_end)
    else:
        _render_g1(vdp, buf, y_start, y_end)

    if draw_sprites:
        _render_sprites_for_mode(vdp, buf, y_start, y_end)


def _render_sprites_for_mode(vdp: "V9938", buf: bytearray, y_start: int, y_end: int) -> None:
    """Dispatch to the sprite renderer for the current screen mode over [y_start, y_end).

    Sprite Y uses R#23 (vscroll) as on real hardware; each sprite is positioned
    once. TEXT1 (M1) and blanked display (BL=0) draw no sprites.
    """
    r0 = vdp.regs[0]
    r1 = vdp.regs[1]
    if not (r1 & 0x40):  # BL clear → display blanked
        return
    m1 = (r1 >> 4) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1
    h = vdp.display_height

    if m5:
        if m4:
            _render_sprites_mode2(vdp, buf, h, y_start, y_end, grb_mode=True)
        else:  # G5 and G6 both render sprites onto a 512-wide buffer
            _render_sprites_mode2(vdp, buf, h, y_start, y_end, width=512)
    elif m4:
        _render_sprites_mode2(vdp, buf, h, y_start, y_end)
    elif m1:
        pass  # TEXT1: no sprites
    else:  # G1 / G2(M3) / MULTICOLOR(M2): sprite mode 1
        _render_sprites(vdp, buf, y_start, y_end)


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

def _render_g1(vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    col_base  = ((vdp.regs[10] & 0x07) << 14) | (vdp.regs[3] << 6)
    bd = _backdrop(vdp)
    ye = y_end if y_end is not None else _TILE_H
    row_start = y_start // 8
    row_end = min(24, (ye + 7) // 8)

    for row in range(row_start, row_end):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            cb = vdp.vram[(col_base + tile // 8) & 0x1FFFF]
            _hi = (cb >> 4) & 0x0F  # inline _color
            fg = _hi if _hi else bd
            _lo = cb & 0x0F
            bg = _lo if _lo else bd
            pat_tile = pat_base + tile * 8
            bx = col * 8
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_tile + py) & 0x3FFF]
                buf[scan * _W + bx:scan * _W + bx + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Graphic 2 (SCREEN 2) — 32×24 tiles, per-row per-tile colour
# ---------------------------------------------------------------------------

def _render_g2(vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None) -> None:
    # Name table base: V9938 GRAPHIC2/3 use the full 7-bit R#2 (A16-A10), so the
    # name table can sit anywhere in 128 KB VRAM — not just the low 16 KB an
    # MSX1 4-bit mask would allow. The pattern/colour bases keep the TMS9918
    # GRAPHIC2 quirk (R#4 bit2 / R#3 bit7 select 0 or 0x2000; the low bits act as
    # tile-index masks, here covered by the per-third band offset).
    name_base = (vdp.regs[2] & 0x7F) << 10
    pat_base  = (vdp.regs[4] & 0x04) << 11
    col_base  = ((vdp.regs[10] & 0x07) << 14) | ((vdp.regs[3] & 0x80) << 6)
    # R#23 vertical scroll applies to GRAPHIC2/3 on the V9938 (it wraps within the
    # 256-line VRAM field). With R#23 = 0 this loop is identical to the previous
    # row-stepped renderer. The per-third pattern/colour bank still follows the
    # (scrolled) VRAM tile row, as on the TMS9918A.
    vscroll = vdp.regs[23]
    bd = _backdrop(vdp)
    ye = y_end if y_end is not None else _TILE_H

    for scan in range(y_start, ye):
        vline = (scan + vscroll) & 0xFF
        row = vline >> 3
        py = vline & 0x07
        band_offset = (row >> 3) * 0x800
        name_row = name_base + row * 32
        scan_w = scan * _W
        for col in range(32):
            tile = vdp.vram[(name_row + col) & 0x1FFFF]
            off = band_offset + tile * 8 + py
            pat = vdp.vram[(pat_base + off) & 0x1FFFF]
            cb  = vdp.vram[(col_base + off) & 0x1FFFF]
            _hi = (cb >> 4) & 0x0F  # inline _color
            fg = _hi if _hi else bd
            _lo = cb & 0x0F
            bg = _lo if _lo else bd
            bx = col * 8
            buf[scan_w + bx:scan_w + bx + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text (SCREEN 0) — 40×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------

def _render_text(vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    # TEXT1: R#7 high nibble = text colour, low nibble = background; both are
    # palette indices used directly (the V9938 maps them through the programmable
    # palette — no colour-0 substitution).
    fg = (vdp.regs[7] >> 4) & 0x0F
    bg = vdp.regs[7] & 0x0F
    ye = y_end if y_end is not None else _TILE_H
    row_start = y_start // 8
    row_end = min(24, (ye + 7) // 8)

    for row in range(row_start, row_end):
        for col in range(40):
            tile = vdp.vram[(name_base + row * 40 + col) & 0x3FFF]
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x3FFF]
                off = scan * _W + 8 + col * 6
                buf[off:off + 6] = _TEXT6_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Multicolor (SCREEN 3) — 64×48 blocks of 4×4 pixels
# ---------------------------------------------------------------------------

def _render_mc(vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None) -> None:
    name_base = (vdp.regs[2] & 0x0F) << 10
    pat_base  = (vdp.regs[4] & 0x07) << 11
    bd = _backdrop(vdp)
    ye = y_end if y_end is not None else _TILE_H
    row_start = y_start // 8
    row_end = min(24, (ye + 7) // 8)

    for row in range(row_start, row_end):
        for col in range(32):
            tile = vdp.vram[(name_base + row * 32 + col) & 0x3FFF]
            bx = col * 8
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x3FFF]
                _hi = (pat >> 4) & 0x0F  # inline _color
                lc = _hi if _hi else bd
                _lo = pat & 0x0F
                rc = _lo if _lo else bd
                buf[scan * _W + bx:scan * _W + bx + 4] = _COLOR4[lc]
                buf[scan * _W + bx + 4:scan * _W + bx + 8] = _COLOR4[rc]


# ---------------------------------------------------------------------------
# Sprite mode 1 — V9938 allows 8 sprites per scanline (vs 4 on TMS9918A)
# ---------------------------------------------------------------------------

def _render_sprites(
    vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    if vdp.regs[8] & 0x04:  # SPD: sprite disable (R#8 bit 2)
        return
    r1 = vdp.regs[1]
    si  = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size   = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)

    sat_base = (vdp.regs[5] & 0x7F) << 7
    spt_base = (vdp.regs[6] & 0x3F) << 11

    line_count = [0] * _TILE_H
    fifth_set = False  # mode 1: the 5th sprite on a line sets the 5S flag
    sprite_painted = bytearray(_W * _TILE_H)
    coincidence = False
    scan_hi = min(y_end if y_end is not None else _TILE_H, _TILE_H)

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

        # Scan only the sprite's visible band [y_top, y_top+render_size) instead
        # of every scanline; the second (wrapped) band is taken only when the
        # & 0xFF row test actually wraps past 255. Per-sprite line order does not
        # affect line_count / 5S / coincidence, so iterating the wrapped band
        # first (increasing screen line) is equivalent to the old full scan.
        end = y_top + render_size
        if end <= 256:
            lines = range(max(y_start, y_top), min(scan_hi, end))
        else:
            lines = chain(range(y_start, min(scan_hi, end - 256)),
                          range(max(y_start, y_top), scan_hi))

        for line in lines:
            sprite_row = (line - y_top) & 0xFF  # guaranteed < render_size

            if line_count[line] >= 4:  # V9938 sprite mode 1: 4 sprites per line
                if not fifth_set:
                    vdp.status = (vdp.status & 0xA0) | 0x40 | (i & 0x1F)
                    fifth_set = True
                continue

            line_count[line] += 1

            if color == 0:
                continue

            src_row = sprite_row // 2 if mag else sprite_row
            pixels  = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row)
            scale   = 2 if mag else 1
            row = line * _W

            if scale == 1:  # MAG=0 fast path: skip the range(1) magnification loop
                for bit_i, pixel in enumerate(pixels):
                    if not pixel:
                        continue
                    px = x_byte + bit_i
                    if px < 0 or px >= _W:  # clip off-screen, no wrap
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
                        if px < 0 or px >= _W:  # clip off-screen, no wrap
                            continue
                        coord = row + px
                        if sprite_painted[coord]:
                            coincidence = True
                        else:
                            sprite_painted[coord] = 1
                            buf[coord] = color

    if coincidence:
        vdp.status |= 0x20


def _render_sprites_mode2(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None,
    grb_mode: bool = False, width: int = _W,
) -> None:
    """Sprite mode 2 for SCREEN 4–8.

    R#5/R#11 → SAT base (512-byte aligned). Colour table at sat_base-0x200.
    Per-line colour byte: EC(7) | CC(6, OR-combine) | IC(5, ignore collision) | colour(3:0).
    grb_mode=True (SCREEN 8): sprite palette colours are converted to GRB332 before writing.
    width: buffer line width; 512 for the wide modes (G5/G6), where the 256-dot
    sprite plane is doubled horizontally so sprites span the full screen.

    Sprite Y is evaluated against R#23 vertical scroll (as on real hardware), and
    this is called once per frame so each sprite is positioned a single time.
    """
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    if vdp.regs[8] & 0x04:  # SPD: sprite disable (R#8 bit 2)
        return
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
    scan_hi = min(y_end if y_end is not None else h, h)

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x1FFFF]
        if y_byte == 0xD8:  # sprite mode 2 list terminator is 216 (0xD8), not 208
            break

        x_byte  = vdp.vram[(sat_base + i * 4 + 1) & 0x1FFFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x1FFFF]
        # SAT 4th byte is unused in sprite mode 2; colour/EC/CC/IC come from the
        # per-line colour table byte instead.

        y_top = (y_byte + 1) & 0xFF
        # NOTE (speculative, from b24fb1d): clip the sprite to VRAM rows < 256
        # rather than letting the pattern wrap row 255→0. openMSX does NOT clip
        # (it uses a plain (line - y) & 0xFF < magSize test, so a sprite near
        # Y=255 wraps onto the top of the screen). This clip therefore diverges
        # from openMSX and suppresses partially-above-top sprites; it is kept for
        # now only because it is currently harmless (post-terminator garbage is
        # gone) — revisit if top-edge sprites look wrong.
        max_sprite_rows = min(render_size, 256 - y_top)

        # Scan only the sprite's visible band. Sprite Y is in VRAM space, so the
        # screen-line band starts at (y_top - vscroll) & 0xFF and spans
        # max_sprite_rows lines; the wrapped band is taken only when it crosses
        # line 255. Per-sprite line order is immaterial to the accumulated
        # line_count / 9S / cc0 / coincidence state, so this matches the old scan.
        base_line = (y_top - vscroll) & 0xFF
        end = base_line + max_sprite_rows
        if end <= 256:
            lines = range(max(y_start, base_line), min(scan_hi, end))
        else:
            lines = chain(range(y_start, min(scan_hi, end - 256)),
                          range(max(y_start, base_line), scan_hi))

        for line in lines:
            # Sprite Y is in VRAM coordinate space; account for vertical scroll.
            vram_line = (line + vscroll) & 0xFF
            sprite_row = (vram_line - y_top) & 0xFF  # guaranteed < max_sprite_rows

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
            line_off = line * width

            if scale == 1:  # MAG=0 fast path: skip the range(1) magnification loop
                for bit_i, pixel in enumerate(pixels):
                    if not pixel:
                        continue
                    sx = x_pos + bit_i  # position in the 256-dot sprite plane
                    if sx < 0 or sx >= _W:  # clip off-screen, no wrap
                        continue
                    for ss in range(screen_scale):  # horizontal doubling in 512-wide modes
                        coord = line_off + sx * screen_scale + ss
                        if sprite_buf[coord]:
                            if not ignore_collision:
                                coincidence = True
                            if or_mode:
                                sprite_buf[coord] |= color
                        else:
                            sprite_buf[coord] = color
                            drawn.append(coord)
            else:
                for bit_i, pixel in enumerate(pixels):
                    if not pixel:
                        continue
                    for s in range(scale):
                        sx = x_pos + bit_i * scale + s  # position in the 256-dot sprite plane
                        if sx < 0 or sx >= _W:  # clip off-screen, no wrap
                            continue
                        for ss in range(screen_scale):  # horizontal doubling in 512-wide modes
                            coord = line_off + sx * screen_scale + ss
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

def _render_g4(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None
) -> None:
    """SCREEN 5: 4-bpp, palette index per half-byte (high nibble = left pixel)."""
    base = (vdp.regs[2] & 0x60) << 10
    tp = bool(vdp.regs[8] & 0x20)  # R#8 bit5: 1=col0 solid, 0=col0 transparent→backdrop
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    # C-level expansion: two translate tables (hi/lo nibble) + strided slice assignment.
    lut_hi, lut_lo = _g46_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * 128) & 0x1FFFF
        bx = y * _W
        row = bytes(vram[row_base:row_base + 128])
        buf[bx:bx + _W:2]      = row.translate(lut_hi)
        buf[bx + 1:bx + _W:2]  = row.translate(lut_lo)


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5) — 2-bpp, 512 virtual width, rendered 256 wide
# ---------------------------------------------------------------------------

def _render_g5(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None
) -> None:
    """SCREEN 6: 2-bpp, 4 pixels per byte, full 512-pixel width."""
    base = (vdp.regs[2] & 0x60) << 10
    tp = bool(vdp.regs[8] & 0x20)
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    w = 512
    # C-level expansion: four translate tables (bit-pairs 7:6, 5:4, 3:2, 1:0) + stride-4.
    p0, p1, p2, p3 = _g5_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
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

def _render_g6(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None
) -> None:
    """SCREEN 7: 4-bpp, 2 pixels per byte, full 512-pixel width."""
    base = (vdp.regs[2] & 0x40) << 10  # G6: 64KB pages, bit6 only
    tp = bool(vdp.regs[8] & 0x20)
    border = vdp.regs[7] & 0x0F
    vscroll = vdp.regs[23]
    vram = vdp.vram
    w = 512
    # Same hi/lo translate approach as G4 but 256 VRAM bytes → 512 output pixels.
    lut_hi, lut_lo = _g46_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
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


def _render_g7(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None
) -> None:
    """SCREEN 8: 8-bpp GRB332, one raw byte per pixel (palette not used)."""
    base = (vdp.regs[2] & 0x40) << 10  # G7: 64KB pages, bit6 only
    vscroll = vdp.regs[23]
    vram = vdp.vram
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * _W) & 0x1FFFF
        bx = y * _W
        buf[bx:bx + _W] = vram[row_base:row_base + _W]  # whole row, one C-level copy
