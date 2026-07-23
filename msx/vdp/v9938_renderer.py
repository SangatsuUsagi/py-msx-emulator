"""render_frame() for the V9938 VDP.

Implements SCREEN 0–3 (TMS9918A-compatible modes) plus the V9938 bitmap modes
SCREEN 4–8 (GRAPHIC 3–7) and sprite mode 2, with per-scanline banding for
mid-frame register/palette changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Iterable, NamedTuple

from msx.vdp._geometry import OUTPUT_H, pad_rows, pad_to_output_height
from msx.vdp.v9938 import _PaletteChange, _RegChange
from msx.vdp.vdp import FramebufferFormat, _channel_tables_indexed, _translate_rgb24

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

# LUT caches for G4/G6 and G5 pixel expansion via bytes.translate().
# Keys: (tp: bool, border: int).  At most 32 entries each (2 × 16).
# Portability note: the dict-memoized bytes.translate() approach is Python-
# specific. A Rust/C++ port keeps fixed [u8; 256] LUT arrays and precomputes
# every (tp, border) pattern at init (only 32 combinations), indexing directly
# instead of hashing a tuple key on demand.
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
    """Render one frame; return bytearray of palette indices.

    The returned buffer always has the constant output height (length
    display_width × OUTPUT_H, i.e. 256×212 or 512×212 for SCREEN 6/7). A
    192-line frame (R#9 LN=0) is centred with border rows above and below so the
    displayed image keeps a stable geometry regardless of LN.

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
    buf = _apply_display_adjust(vdp, buf)
    return pad_to_output_height(
        buf, vdp.display_height, vdp.display_width, _border_color(vdp)
    )


def _r18_offset(reg18: int) -> tuple[int, int]:
    """Decode R#18 into (horizontal, vertical) pixel offsets (openMSX): each
    nibble adjust = (nibble ^ 7), offset = adjust - 7, range -7..+8; the
    BIOS-neutral nibble 0 → 0. Positive offset moves the picture right/down."""
    h_off = ((reg18 & 0x0F) ^ 0x07) - 7
    v_off = ((reg18 >> 4) ^ 0x07) - 7
    return h_off, v_off


def _r18_row_offsets(vdp: "V9938", h: int) -> tuple[list[int], int]:
    """Per-scanline horizontal offset (dots) and the frame-level vertical offset,
    derived from R#18 at frame start plus mid-frame R#18 changes.

    A change logged at line N takes effect from line N+1 (openMSX applies R#18 at
    the next line, VDP.cc case 18 → syncAtNextLine), so each change overrides the
    horizontal offset for the lines below it. Sorting by effective line (a stable
    sort keeps later same-line writes last) lets the fill walk consecutive change
    points in a single pass. The vertical offset is a frame-level display
    position, taken from R#18 at frame start.
    """
    r18_changes = sorted(
        ((e.line + 1, e.value)
         for e in vdp._reg_write_log
         if isinstance(e, _RegChange) and e.reg == 18),
        key=lambda c: c[0],
    )
    # Base is R#18 at the top of the frame. When R#18 changed mid-frame the live
    # regs hold the final post-change value, so read the frame-start snapshot;
    # with no mid-frame change the live regs already hold the frame-start value.
    base18 = vdp._frame_start_regs[18] if r18_changes else vdp.regs[18]

    base_h, v_off = _r18_offset(base18)
    row_h = [base_h] * h
    # Fill each row with the offset in force there: for change i, that is its own
    # offset from its effective line up to the next change point (single pass).
    for idx, (eff_line, reg18_val) in enumerate(r18_changes):
        if eff_line >= h:
            break
        next_eff = r18_changes[idx + 1][0] if idx + 1 < len(r18_changes) else h
        h_off, _ = _r18_offset(reg18_val)
        for y in range(eff_line, min(next_eff, h)):
            row_h[y] = h_off
    return row_h, v_off


def _border_color(vdp: "V9938") -> int:
    """Border-colour byte for the current mode.

    SCREEN 8 (G7) uses the raw R#7 byte as a direct GRB332 colour; every other
    mode uses the low nibble of R#7 as a palette index. Shared by the R#18
    display-adjust fill and the output-height padding so the two cannot drift.
    """
    r0 = vdp.regs[0]
    is_g7 = bool(((r0 >> 2) & 1) and ((r0 >> 3) & 1))
    return vdp.regs[7] if is_g7 else (vdp.regs[7] & 0x0F)


def _apply_display_adjust(vdp: "V9938", buf: bytearray) -> bytearray:
    """Apply R#18 display adjust: shift the picture horizontally/vertically and
    fill the exposed edges with the border colour.

    The horizontal adjust is applied *per scanline* from R#18's value at that
    line, so a split screen that adjusts only its lower half shifts just that
    region. The whole VDP output (background and sprites) shifts together,
    matching openMSX where both derive from getLeftSprites.
    """
    h = vdp.display_height
    w = vdp.display_width

    row_h, v_off = _r18_row_offsets(vdp, h)
    # Nothing to do when there is no vertical shift and every scanline offset is
    # zero — the common BIOS-neutral R#18 = 0 case.
    if v_off == 0 and not any(row_h):
        return buf

    border = _border_color(vdp)
    scale = w // 256                           # 512-wide modes shift 2 buffer px per dot

    out = bytearray([border]) * (w * h)
    for sy in range(h):
        dy = sy + v_off
        if dy < 0 or dy >= h:
            continue
        srow = sy * w
        drow = dy * w
        h_shift = row_h[sy] * scale
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


def _build_bands(vdp: "V9938") -> list[tuple[int, int, list[int], list[int]]]:
    """Build (y0, y1, regs_snapshot, palette_snapshot) bands from register change log."""
    log = vdp._reg_write_log
    display_height = vdp.display_height

    change_lines: dict[int, list[_RegChange | _PaletteChange]] = {}
    for entry in log:
        # R#18 (display adjust) is logged for _apply_display_adjust but no band
        # renderer reads regs[18], so splitting a band on it only produces a
        # background re-render identical to the band above — skip it here.
        if isinstance(entry, _RegChange) and entry.reg == 18:
            continue
        # Writes logged at display_line=N happen during line N+1's CPU window
        # (begin_scanline(N) fires after N's CPU budget; ISR runs in N+1's window).
        # The register change therefore takes effect from line N+1 onwards.
        effective = entry.line + 1
        if 0 < effective < display_height:
            change_lines.setdefault(effective, []).append(entry)

    if not change_lines:
        return []

    bands = []
    cur_regs = list(vdp._frame_start_regs)
    cur_palette = list(vdp._frame_start_palette)
    prev_y = 0

    for line in sorted(change_lines):
        if line > prev_y:
            bands.append((prev_y, line, list(cur_regs), list(cur_palette)))
        for entry in change_lines[line]:
            if isinstance(entry, _PaletteChange):
                cur_palette[entry.idx] = entry.rgb
            else:
                cur_regs[entry.reg] = entry.value
        prev_y = line

    if prev_y < display_height:
        bands.append((prev_y, display_height, list(cur_regs), list(cur_palette)))

    return bands



@dataclass(slots=True)
class _SatSegment:
    """A run of consecutive bands sharing a sprite-attribute-table key.

    Built in _render_banded to coalesce sprite passes: y0..y1 is the merged
    scanline span, `regs` are the registers of the tallest band in the run
    (used for that region's sprite pass), and `height` tracks that band's height.
    """

    y0: int
    y1: int
    regs: list[int]
    height: int


def _render_banded(vdp: "V9938") -> bytearray:
    """Render with band-split: each band uses its own register/palette snapshot."""
    bands = _build_bands(vdp)
    if not bands:
        return _render_pass(vdp)

    full_h = vdp.display_height
    full_w = vdp.display_width

    # Sprites are evaluated in as few passes as possible: one per distinct sprite
    # attribute table (R#5/R#11), using the registers of the largest band sharing
    # that SAT — the main play area. Vertical scroll (R#23) is *not* a reason to
    # split; it is applied per scanline within a pass (see row_vscroll below).
    main_regs = max(bands, key=lambda b: b[1] - b[0])[2]

    out = bytearray(full_w * full_h)

    saved_regs = vdp.regs[:]
    saved_palette = vdp.palette[:]
    try:
        # Background: one pass per band with that band's registers/palette (no sprites).
        for y0, y1, band_regs, band_palette in bands:
            vdp.regs = band_regs
            vdp.palette = band_palette
            _render_pass_range(vdp, out, y0, min(y1, full_h), draw_sprites=False)

        # Sprites: vertical scroll (R#23) is applied per scanline within a pass.
        # openMSX re-reads R#23 on every line (VDP.cc case 23 syncs then stores,
        # with NO syncAtNextLine) and tests each sprite's Y against that line's
        # vscroll, so a split screen that changes R#23 mid-frame positions each
        # region's sprites with its own vscroll without redrawing them. The band
        # snapshots already carry each region's R#23; row_vscroll[y] flattens that
        # into a per-line value that the sprite pass reads instead of vdp.regs[23].
        #
        # A mid-screen sprite attribute table switch (R#5/R#11) is different: the
        # regions read *different* SAT buffers, used for sprite multiplexing /
        # "sprite doubler" (a second set of 32 sprites in the lower half). There
        # one sprite pass per SAT region — each clipped to its scanline span — is
        # required. Consecutive bands sharing a SAT base are merged so an ordinary
        # game (single SAT, whatever its vscroll splits) stays at one pass. R#5 is
        # only changed mid-active-display for this purpose, so keying on it does
        # not affect non-multiplexed games.
        # SPD sprite-disable (R#8 bit 1) is per-line too: a split screen may blank
        # sprites over just its status band (SPD=1 there, 0 in the play area). Build
        # it alongside row_vscroll so the sprite pass reads both per line.
        row_vscroll = [0] * full_h
        row_spd = [False] * full_h
        for y0, y1, band_regs, _ in bands:
            v = band_regs[23]
            spd = bool(band_regs[8] & 0x02)
            for y in range(y0, min(y1, full_h)):
                row_vscroll[y] = v
                row_spd[y] = spd

        def _sat_key(regs: list[int]) -> tuple[int, int]:
            return (regs[5], regs[11] & 0x03)

        sat_segments: list[_SatSegment] = []
        for y0, y1, band_regs, _ in bands:
            bh = y1 - y0
            if sat_segments and _sat_key(sat_segments[-1].regs) == _sat_key(band_regs):
                seg = sat_segments[-1]
                seg.y1 = y1
                if bh > seg.height:
                    seg.regs = band_regs
                    seg.height = bh
            else:
                sat_segments.append(_SatSegment(y0, y1, band_regs, bh))

        if len(sat_segments) <= 1:
            # One SAT for the whole frame → single pass (main band regs); per-line
            # row_vscroll / row_spd handle any R#23 / SPD split within it.
            vdp.regs = main_regs
            _render_sprites_for_mode(vdp, out, 0, full_h, row_vscroll, row_spd)
        else:
            for seg in sat_segments:
                vdp.regs = seg.regs
                _render_sprites_for_mode(vdp, out, seg.y0, min(seg.y1, full_h),
                                         row_vscroll, row_spd)

        _finalize(vdp)
    finally:
        # Portability note: the band passes above temporarily rebind
        # vdp.regs / vdp.palette to per-band snapshots (shared list objects held
        # in `bands`). That reference-swap is a Python convenience that does not
        # map to an ownership model — a port threads the band registers/palette
        # as explicit parameters to the per-band renderers instead. The
        # try/finally guarantees the live registers are restored even if a
        # renderer raises mid-frame (the invariant is otherwise implicit).
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


def _render_sprites_for_mode(
    vdp: "V9938", buf: bytearray, y_start: int, y_end: int,
    row_vscroll: list[int] | None = None,
    row_spd: list[bool] | None = None,
) -> None:
    """Dispatch to the sprite renderer for the current screen mode over [y_start, y_end).

    Sprite Y uses R#23 (vscroll) as on real hardware. row_vscroll, when given,
    supplies the per-scanline vscroll (banded mode, where R#23 may change
    mid-frame); otherwise the scalar vdp.regs[23] is used. row_spd likewise carries
    the per-scanline SPD (R#8 sprite-disable) so a split screen can blank sprites
    over just one band. Sprite mode 1 ignores vscroll. TEXT1 (M1) and blanked
    display (BL=0) draw no sprites.
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
            _render_sprites_mode2(vdp, buf, h, y_start, y_end, grb_mode=True,
                                  row_vscroll=row_vscroll, row_spd=row_spd)
        else:  # G5 and G6 both render sprites onto a 512-wide buffer
            _render_sprites_mode2(vdp, buf, h, y_start, y_end, width=512,
                                  row_vscroll=row_vscroll, row_spd=row_spd)
    elif m4:
        _render_sprites_mode2(vdp, buf, h, y_start, y_end, row_vscroll=row_vscroll,
                              row_spd=row_spd)
    elif m1:
        pass  # TEXT1: no sprites
    else:  # G1 / G2(M3) / MULTICOLOR(M2): sprite mode 1 (no vertical scroll)
        _render_sprites(vdp, buf, y_start, y_end)


def _finalize(vdp: "V9938") -> None:
    # VBlank flag is now set by begin_scanline() in the scanline loop (machine.py).
    # _finalize only handles frame-buffer finalisation; no interrupt generation here.
    pass


def _backdrop(vdp: "V9938") -> int:
    return vdp.regs[7] & 0x0F


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
            hi_nib = (cb >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
            fg = hi_nib if hi_nib else bd
            lo_nib = cb & 0x0F
            bg = lo_nib if lo_nib else bd
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
    # GRAPHIC 2 (SCREEN 2) and GRAPHIC 3 (SCREEN 4) share this tile plane. Table
    # bases follow the V9938 (per openMSX VDP::update*Base): the pattern and
    # colour tables are 8 KB-aligned (the low 13 index bits come from the
    # band/tile/line offset), so
    #   pattern generator = (R#4 << 11) & ~0x1FFF = (R#4 & 0x3C) << 11
    #   colour table      = ((R#10 << 14) | (R#3 << 6)) & ~0x1FFF
    #                     = (R#10 & 0x07) << 14 | (R#3 & 0x80) << 6
    #   name table        = R#2 << 10 (A16-A10)
    # Using only R#4 bit2 for the pattern base (the TMS9918 form) put the
    # generator at 0x0000 for e.g. Ultima III (R#4=0x13 → 0x8000), garbling the
    # background; R#4 bits 5:2 are the real A16-A13 base bits.
    name_base = (vdp.regs[2] & 0x7F) << 10
    pat_base  = (vdp.regs[4] & 0x3C) << 11
    col_base  = ((vdp.regs[10] & 0x07) << 14) | ((vdp.regs[3] & 0x80) << 6)
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
            hi_nib = (cb >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
            fg = hi_nib if hi_nib else bd
            lo_nib = cb & 0x0F
            bg = lo_nib if lo_nib else bd
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
            # MULTICOLOR uses only 2 of the 8 pattern bytes per cell: the byte
            # pair is selected by the character row (row & 3)*2, and the top vs
            # bottom 4 scanlines pick within the pair (py >> 2). Each nibble is a
            # solid 4x4 block, so the colour is constant across its 4 scanlines.
            seg = (row & 3) * 2
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_base + tile * 8 + seg + (py >> 2)) & 0x3FFF]
                hi_nib = (pat >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
                lc = hi_nib if hi_nib else bd
                lo_nib = pat & 0x0F
                rc = lo_nib if lo_nib else bd
                buf[scan * _W + bx:scan * _W + bx + 4] = _COLOR4[lc]
                buf[scan * _W + bx + 4:scan * _W + bx + 8] = _COLOR4[rc]


# ---------------------------------------------------------------------------
# Sprite mode 1 — keeps the TMS9918A limit of 4 sprites per scanline.
# (Sprite mode 2, below, raises this to 8 per scanline.)
# ---------------------------------------------------------------------------

def _render_sprites(
    vdp: "V9938", buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    if vdp.regs[8] & 0x02:  # SPD: sprite disable (R#8 bit 1)
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
        lines: Iterable[int]
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


class _SpriteRun(NamedTuple):
    """A run of consecutive scanlines [lo, hi) with constant vertical scroll
    (R#23) and constant sprite-enable. `enabled` is False where R#8 SPD blanks
    sprites, so the sprite pass skips the run entirely."""
    lo: int
    hi: int
    vscroll: int
    enabled: bool


def _sprite_runs(
    row_vscroll: list[int] | None, default_vscroll: int, y_start: int, scan_hi: int,
    row_spd: list[bool] | None = None,
) -> list[_SpriteRun]:
    """Group scanlines [y_start, scan_hi) into runs of constant vertical scroll
    (R#23) AND constant sprite-enable (R#8 SPD). With no per-line schedule this is
    one run over the whole range, so the common non-banded case stays a single
    per-sprite scan. A None row_spd means sprites are enabled throughout — the
    scalar whole-pass-disable case is handled by the caller's early return."""
    if y_start >= scan_hi:
        return []
    if row_vscroll is None and row_spd is None:
        return [_SpriteRun(y_start, scan_hi, default_vscroll, True)]

    runs: list[_SpriteRun] = []
    lo = y_start
    cur_vsc = default_vscroll if row_vscroll is None else row_vscroll[y_start]
    cur_spd = False if row_spd is None else row_spd[y_start]
    for y in range(y_start + 1, scan_hi):
        vsc = default_vscroll if row_vscroll is None else row_vscroll[y]
        spd = False if row_spd is None else row_spd[y]
        if vsc != cur_vsc or spd != cur_spd:
            runs.append(_SpriteRun(lo, y, cur_vsc, not cur_spd))
            lo = y
            cur_vsc = vsc
            cur_spd = spd
    runs.append(_SpriteRun(lo, scan_hi, cur_vsc, not cur_spd))
    return runs


def _render_sprites_mode2(
    vdp: "V9938", buf: bytearray, h: int, y_start: int = 0, y_end: int | None = None,
    grb_mode: bool = False, width: int = _W, row_vscroll: list[int] | None = None,
    row_spd: list[bool] | None = None,
) -> None:
    """Sprite mode 2 for SCREEN 4–8.

    R#5/R#11 → SAT base (512-byte aligned). Colour table at sat_base-0x200.
    Per-line colour byte: EC(7) | CC(6, OR-combine) | IC(5, ignore collision) | colour(3:0).
    grb_mode=True (SCREEN 8): sprite palette colours are converted to GRB332 before writing.
    width: buffer line width; 512 for the wide modes (G5/G6), where the 256-dot
    sprite plane is doubled horizontally so sprites span the full screen.

    Sprite Y is evaluated against R#23 vertical scroll per scanline (as on real
    hardware, which re-reads R#23 each line): row_vscroll gives the per-line value
    when R#23 changes mid-frame, else the scalar vdp.regs[23] is used. Each sprite
    is positioned in a single pass regardless.

    SPD (R#8 bit 1) disables sprites, also per scanline: row_spd, when given,
    supplies the per-line sprite-disable flag (a split screen may blank sprites
    over just its status band, as real hardware does), else the scalar
    vdp.regs[8] is used. Disabled scanline runs are skipped entirely.
    """
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    spd_scalar = bool(vdp.regs[8] & 0x02)  # SPD: sprite disable (R#8 bit 1)
    if row_spd is None and spd_scalar:
        return  # sprites disabled for the whole pass (no per-line schedule)
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
    # Vertical scroll and SPD (sprite disable) are both per scanline: split the
    # range into runs of constant vscroll AND constant sprite-enable (one run for
    # the usual single R#23/R#8; more only when a split changes either). Runs
    # whose SPD is set are skipped, so a band that blanks its sprites draws none.
    sprite_runs = _sprite_runs(row_vscroll, vdp.regs[23], y_start, scan_hi, row_spd)

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x1FFFF]
        if y_byte == 0xD8:  # sprite mode 2 list terminator is 216 (0xD8), not 208
            break

        x_byte  = vdp.vram[(sat_base + i * 4 + 1) & 0x1FFFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x1FFFF]
        # SAT 4th byte is unused in sprite mode 2; colour/EC/CC/IC come from the
        # per-line colour table byte instead.

        y_top = (y_byte + 1) & 0xFF
        # A sprite spans render_size rows in VRAM-Y space, wrapping row 255→0, and
        # is positioned on screen at (y_top - vsc); openMSX applies no VRAM-row clip
        # (it tests (line - y) & 0xFF < magSize per line). The screen-line wrap is
        # handled below by the end > 256 branch. An earlier build clipped to
        # 256 - y_top, but that is in VRAM-Y space, so with vertical scroll it
        # wrongly truncated a sprite whose screen band is nowhere near the bottom
        # (a high-Y sprite pulled up by R#23) — e.g. y_top=251, vsc=62 → screen
        # line 189, clipped to 5 rows. The terminator (y==0xD8) already stops the
        # scan, so no clip is needed.
        max_sprite_rows = render_size

        # Scan only the sprite's visible band, per constant-vscroll run. Sprite Y
        # is in VRAM space, so within a run the screen-line band starts at
        # (y_top - vsc) & 0xFF and spans max_sprite_rows lines; the wrapped band is
        # taken only when it crosses line 255. Per-sprite line order is immaterial
        # to the accumulated line_count / 9S / cc0 / coincidence state, and the
        # runs partition the scanlines, so this matches the old single-vscroll scan.
        for run in sprite_runs:
            if not run.enabled:  # SPD set over this run: sprites blanked here
                continue
            vsc = run.vscroll  # bound locally: read per line in the inner loop
            base_line = (y_top - vsc) & 0xFF
            end = base_line + max_sprite_rows
            lines2: Iterable[int]
            if end <= 256:
                lines2 = range(max(run.lo, base_line), min(run.hi, end))
            else:
                lines2 = chain(range(run.lo, min(run.hi, end - 256)),
                               range(max(run.lo, base_line), run.hi))

            for line in lines2:
                # Sprite Y is in VRAM coordinate space; account for vertical scroll.
                vram_line = (line + vsc) & 0xFF
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


# ---------------------------------------------------------------------------
# Palette-index / SCREEN 8 → RGB24 conversion (V9938.to_rgb24 delegates here)
# ---------------------------------------------------------------------------

# SCREEN 8 GRB332 per-channel translate tables — constant (each byte is a full
# pixel; no index mask). Built once from grb332_to_rgb.
_GRB332_CHANNELS: tuple[bytes, bytes, bytes] = (
    bytes(grb332_to_rgb(b)[0] for b in range(256)),
    bytes(grb332_to_rgb(b)[1] for b in range(256)),
    bytes(grb332_to_rgb(b)[2] for b in range(256)),
)


def _make_lut16(palette: list[int]) -> list[bytes]:
    """Build a 16-entry RGB24 bytes LUT from a 9-bit RGB333 palette."""
    return [
        bytes((_INTENSITY3[(p >> 6) & 7], _INTENSITY3[(p >> 3) & 7], _INTENSITY3[p & 7]))
        for p in palette[:16]
    ]


def _banded_to_rgb24(vdp: "V9938", src: bytearray) -> bytes:
    """Per-band palette→RGB24 conversion for mid-frame palette changes.

    Reuses _build_bands() (which reads _reg_write_log / _frame_start_palette) to
    determine which palette was active on each scanline, then translates each
    output line with that band's channel tables. Display-adjust vertical offset
    (R#18 high nibble) is accounted for: output line dy came from source line
    dy - v_off, whose band determines the palette.

    src is the padded output-height buffer (OUTPUT_H rows). When the native
    active height is 192 the picture occupies rows [pad, pad + native_h); the
    top/bottom border pad rows use the frame-start palette.
    """
    native_h = vdp.display_height
    w = len(src) // OUTPUT_H
    pad = pad_rows(native_h)  # 10 when native_h == 192, 0 when 212

    # Vertical shift must match _apply_display_adjust: use the frame-start R#18
    # when it changed mid-frame (live regs hold the post-change value), else live.
    r18_changed = any(
        isinstance(e, _RegChange) and e.reg == 18 for e in vdp._reg_write_log
    )
    base18 = vdp._frame_start_regs[18] if r18_changed else vdp.regs[18]
    _, v_off = _r18_offset(base18)   # signed line shift from R#18

    bands = _build_bands(vdp)

    # Channel tables are built once per band (not per row/pixel); lines outside
    # any band fall back to the frame-start palette (e.g. the border after the
    # v_off shift, and the top/bottom output-height pad rows).
    default_channels = _channel_tables_indexed(_make_lut16(vdp._frame_start_palette))
    line_channels: list[tuple[bytes, bytes, bytes]] = [default_channels] * native_h
    for _y0, _y1, _band_regs, band_palette in bands:
        channels = _channel_tables_indexed(_make_lut16(band_palette))
        for sy in range(max(0, _y0), min(native_h, _y1)):
            line_channels[sy] = channels

    out = bytearray(w * OUTPUT_H * 3)
    for dy in range(OUTPUT_H):
        ny = dy - pad  # native index-buffer row this output row maps to
        if 0 <= ny < native_h:
            sy = ny - v_off
            rtab, gtab, btab = (
                line_channels[sy] if 0 <= sy < native_h else default_channels
            )
        else:
            rtab, gtab, btab = default_channels
        row = src[dy * w:dy * w + w]
        o = dy * w * 3
        end = o + w * 3
        out[o:end:3] = row.translate(rtab)
        out[o + 1:end:3] = row.translate(gtab)
        out[o + 2:end:3] = row.translate(btab)
    return bytes(out)


def to_rgb24(vdp: "V9938", src: bytearray) -> bytes:
    """Convert a V9938 palette-index (or SCREEN 8 GRB332) framebuffer to RGB24.

    SCREEN 8 (G7) maps each byte through the fixed GRB332 table; other modes use
    the programmable 16-colour palette. When the register-write log records a
    mid-frame palette change, conversion is done per band. The 16-colour channel
    tables are cached on the VDP instance, keyed on the palette snapshot.
    """
    if vdp.framebuffer_format is FramebufferFormat.GRB332:
        return _translate_rgb24(src, _GRB332_CHANNELS)

    # _reg_write_log is still valid here: it is cleared by begin_scanline(0) at
    # the *next* frame, not at the end of this one.
    if any(isinstance(e, _PaletteChange) for e in vdp._reg_write_log):
        return _banded_to_rgb24(vdp, src)

    key = tuple(vdp.palette[:16])
    if key != vdp._rgb_lut_key:
        vdp._rgb_lut_key = key
        vdp._rgb_channels = _channel_tables_indexed(_make_lut16(vdp.palette))
    return _translate_rgb24(src, vdp._rgb_channels)
