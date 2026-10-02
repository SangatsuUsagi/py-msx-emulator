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
from msx.vdp._v9938_defs import (
    _S2_EO,
    _PaletteChange,
    _RegChange,
    display_height_of,
    display_width_of,
    even_odd_enabled_of,
)
from msx.vdp.vdp import FramebufferFormat, _channel_tables_indexed, _translate_rgb24

if TYPE_CHECKING:
    from msx.vdp.v9938 import V9938

_W = 256
_TILE_H = 192  # TMS9918A-compatible modes always render 24 tile rows (192 px)

# Sprite-mode-2 pixel-claim marker: bit 4 of the sprite compositing buffer, so a
# displayed colour-0 pixel (only possible with TP set) is distinguishable from an
# unclaimed pixel. Masked off with 0x0F when the buffer is composited.
_SPRITE_CLAIM = 0x10
# Collision-coordinate bias (S#3-S#6): the V9938 reports the collision position
# offset by +12 dots / +8 lines, which software subtracts again (Handbook
# Expression 4.5; openMSX SpriteChecker "x-coord should be increased by 12,
# y-coord 8").
_COLLISION_X_OFFSET = 12
_COLLISION_Y_OFFSET = 8

# Cache border-fill buffers per border pattern (see _border_pattern) so a band
# prefill can slice-assign a memoryview instead of rebuilding the fill every
# band/frame.
#
# PORT-NOTE: module-level mutable memoization cache keyed on the border
#   pattern (a 1-byte colour, or a 2-byte GRAPHIC5 even/odd pair), grown
#   lazily by _border_fill().
# Rust equivalent: precompute all 16 constant border-fill buffers once at
#   VDP/renderer construction (or as const data), not as a runtime-grown
#   global — Rust has no ergonomic mutable static without OnceLock/interior
#   mutability.
# C++ equivalent: a fixed std::array<std::vector<uint8_t>, 16> (or similar)
#   built once at construction, not a lazily-grown global map.
# Kept as-is here because: port target/shape not decided yet, and a growable
#   global cache shared across VDP instances is also a latent
#   multi-instance-safety issue this single-machine-process design doesn't
#   currently need to think about; revisit once Rust vs. C++ is chosen.
_BORDER_CACHE: dict[bytes, bytes] = {}


def _border_fill(pattern: bytes, n: int) -> memoryview:
    """Return an n-byte view of a cached buffer repeating `pattern` (n is a
    whole number of lines, so a 2-byte pattern starts every line on dot 0)."""
    buf = _BORDER_CACHE.get(pattern)
    if buf is None or len(buf) < n:
        buf = pattern * (n // len(pattern))
        _BORDER_CACHE[pattern] = buf
    return memoryview(buf)[:n]


# byte → 8 pixel bits (MSB first), precomputed to avoid per-call generators.
_UNPACK8: tuple[bytes, ...] = tuple(
    bytes((b >> (7 - i)) & 1 for i in range(8)) for b in range(256)
)

# screen_scale is always 1 (256-wide modes) or 2 (512-wide G5/G6); precomputed
# so the sprite-mode-2 per-pixel loop iterates a cached tuple instead of
# constructing a fresh range(screen_scale) object on every displayed dot.
_SS_OFFSETS: dict[int, tuple[int, ...]] = {1: (0,), 2: (0, 1)}

# GRAPHIC7 (SCREEN 8) sprites use a FIXED 16-colour palette, NOT the
# programmable one (V9938 data book, "Setting the Sprite Colors", whose table
# these values match). Source values from openMSX
# Renderer::GRAPHIC7_SPRITE_PALETTE as 0xGRB nibbles (each channel 0-7);
# stored here pre-converted to the GRB332 byte the SCREEN 8 frame buffer
# holds: (G<<5) | (R<<2) | (B>>1).
_G7_SPRITE_GRB_SRC: tuple[int, ...] = (
    0x000,
    0x002,
    0x030,
    0x032,
    0x300,
    0x302,
    0x330,
    0x332,
    0x472,
    0x007,
    0x070,
    0x077,
    0x700,
    0x707,
    0x770,
    0x777,
)
_G7_SPRITE_PALETTE: tuple[int, ...] = tuple(
    (((v >> 8) & 7) << 5) | (((v >> 4) & 7) << 2) | ((v & 7) >> 1) for v in _G7_SPRITE_GRB_SRC
)

# Solid-colour fill tables — avoids bytes([c]*N) allocation in hot tile loops.
_COLOR4: tuple[bytes, ...] = tuple(bytes([c] * 4) for c in range(16))

# LUT caches for G4/G6 and G5 pixel expansion via bytes.translate().
# Keys: (tp: bool, border: int).  At most 32 entries each (2 × 16).
#
# PORT-NOTE: dict-memoized bytes.translate() approach, keyed and grown lazily
#   on demand.
# Rust equivalent: fixed [u8; 256] LUT arrays, precomputing every (tp, border)
#   pattern at init (only 32 combinations), indexed directly instead of
#   hashing a tuple key on demand.
# C++ equivalent: a fixed std::array<std::array<uint8_t,256>, 32> (or similar)
#   built once at init, indexed by a small integer instead of a hashed pair.
# Kept as-is here because: bytes.translate() itself is the perf-critical part
#   (see the PORT-NOTE on _translate_rgb24 in msx/vdp/vdp.py); the dict cache
#   around it only pays a hash lookup once per (tp, border) combination, not
#   per pixel, so it isn't worth restructuring in Python.
_G46_LUT_CACHE: dict[tuple[bool, int], tuple[bytes, bytes]] = {}
_G5_LUT_CACHE: dict[tuple[bool, int], tuple[bytes, bytes, bytes, bytes]] = {}


def _g46_luts(tp: bool, border: int) -> tuple[bytes, bytes]:
    """Return (lut_hi, lut_lo) translate tables for 4-bpp G4/G6 expansion."""
    key = (tp, border)
    if key not in _G46_LUT_CACHE:
        lut_hi = bytes(
            ((b >> 4) & 0x0F) if (tp or (b >> 4) & 0x0F) else border for b in range(256)
        )
        lut_lo = bytes((b & 0x0F) if (tp or b & 0x0F) else border for b in range(256))
        _G46_LUT_CACHE[key] = (lut_hi, lut_lo)
    return _G46_LUT_CACHE[key]


def _g5_luts(tp: bool, border: int) -> tuple[bytes, bytes, bytes, bytes]:
    """Return (p0,p1,p2,p3) translate tables for 2-bpp G5 expansion.

    With TP clear, colour 0 takes R#7 bits 3:2 at even dots (p0, p2) and bits
    1:0 at odd dots (p1, p3), matching openMSX's precalcColorIndex0 and the
    V9938 data book's GRAPHIC 5 "Hardware tiling function" (background colour
    split into even and odd dots)."""
    key = (tp, border)
    if key not in _G5_LUT_CACHE:
        even_zero = (border >> 2) & 0x03
        odd_zero = border & 0x03

        def _ch(b: int, shift: int, zero: int) -> int:
            c = (b >> shift) & 0x03
            return c if (tp or c) else zero

        p0 = bytes(_ch(b, 6, even_zero) for b in range(256))
        p1 = bytes(_ch(b, 4, odd_zero) for b in range(256))
        p2 = bytes(_ch(b, 2, even_zero) for b in range(256))
        p3 = bytes(_ch(b, 0, odd_zero) for b in range(256))
        _G5_LUT_CACHE[key] = (p0, p1, p2, p3)
    return _G5_LUT_CACHE[key]


# Precomputed tile-row lookup: _ROW_BYTES[pat][fg][bg] → 8-byte slice.
#
# PORT-NOTE: three-level nested list of bytes objects, each level a separate
#   heap-allocated PyObject with its own indirection/refcount, built once at
#   import via triple-nested comprehension and indexed _ROW_BYTES[pat][fg][bg]
#   in the hottest tile-rendering loops (_render_g1/_render_g2/_render_text/
#   _render_mc in this file; the MSX1 equivalent is renderer.py's own
#   _ROW_BYTES).
# Rust equivalent: flatten to a single [u8; 256*16*16*8] (or
#   [[u8;8]; 256*16*16]) const array indexed by pat*256 + fg*16 + bg (built at
#   compile time via a const fn or build script) — a single contiguous
#   cache-friendly lookup instead of three pointer chases.
# C++ equivalent: a single flat std::array<uint8_t, 256*16*16*8> (or
#   std::array<std::array<uint8_t,8>, 256*16*16>) computed once via a
#   constexpr function or static initializer.
# Kept as-is here because: eliminates the 8-iteration per-pixel Python loop
#   in the tile renderers; Python has no cheaper way to express a flat
#   compile-time-computed array of this size than nested lists.
_ROW_BYTES: list[list[list[bytes]]] = [
    [
        [bytes((fg if (pat >> (7 - px)) & 1 else bg) for px in range(8)) for bg in range(16)]
        for fg in range(16)
    ]
    for pat in range(256)
]

# Precomputed text-row lookup: _TEXT6_BYTES[pat][fg][bg] → 6-byte slice.
# Same nested-list-LUT shape and PORT-NOTE as _ROW_BYTES above (6-byte rows
# instead of 8-byte).
_TEXT6_BYTES: list[list[list[bytes]]] = [
    [
        [bytes((fg if (pat >> (7 - px)) & 1 else bg) for px in range(6)) for bg in range(16)]
        for fg in range(16)
    ]
    for pat in range(256)
]


@dataclass(slots=True)
class _RenderStatus:
    """The chip status a render produces: S#0 (5S/9S, sprite index, C), the
    collision coordinates (S#3-S#6) and S#2 (EO). Seeded from the chip,
    updated by the frame's sprite passes (S#0, collision) and by
    _update_field_parity (S#2 EO), and written back to the chip only by
    render_frame -- render_preview discards it."""

    status: int
    collision_x: int
    collision_y: int
    status2: int


def render_frame(vdp: "V9938", skip_render: bool = False) -> bytearray:
    """Render one frame; return bytearray of palette indices.

    The returned buffer always has the constant output height (length
    width × OUTPUT_H: 256×212 normally, 512×212 for SCREEN 6/7 and TEXT2, and
    512×212 for a frame that mixes a 512-dot band with a 256-dot one). A
    192-line frame (R#9 LN=0) is centred with border rows above
    and below so the displayed image keeps a stable geometry regardless of LN.

    Commits the render's _RenderStatus (S#0 sprite bits, S#3-S#6, S#2 EO) to
    the chip; render_preview discards it. Frame counting is owned by the
    caller (Machine.run_frame), not the renderer.
    """
    buf, st = _render(vdp, skip_render)
    vdp.commit_render_status(st.status, st.collision_x, st.collision_y, st.status2)
    return buf


def render_preview(vdp: "V9938") -> bytearray:
    """Render the current frame like render_frame, leaving the chip unchanged."""
    return _render(vdp, False)[0]


def _render(vdp: "V9938", skip_render: bool) -> tuple[bytearray, _RenderStatus]:
    """Render a frame; return it with the status the render produces."""
    st = _RenderStatus(vdp.status, vdp.collision_x, vdp.collision_y, vdp.status2)
    if skip_render:
        _update_field_parity(vdp, st)
        return bytearray(0), st

    log = vdp._reg_write_log
    buf = _render_banded(vdp, st) if log else _render_pass(vdp, st)
    buf = _apply_display_adjust(vdp, buf)
    h = vdp.display_height
    # The frame's width comes from the buffer: a mid-frame width change makes it
    # the widest band's width, not the live registers' (see _render_banded).
    out = pad_to_output_height(buf, h, len(buf) // h, _border_pattern(vdp.regs))
    return out, st


def _r18_offset(reg18: int) -> tuple[int, int]:
    """Decode R#18 into (horizontal, vertical) pixel offsets (openMSX): each
    nibble adjust = (nibble ^ 7), offset = adjust - 7, range -7..+8; the
    BIOS-neutral nibble 0 → 0. Positive offset moves the picture right/down.
    Matches the V9938 data book's R#18 figure: H = 7 leftmost, 0 centre,
    8 rightmost; V = 7 top, 0 centre, 8 bottom."""
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
        (
            (e.line + 1, e.value)
            for e in vdp._reg_write_log
            if isinstance(e, _RegChange) and e.reg == 18
        ),
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


def _border_pattern(regs: list[int]) -> bytes:
    """Border fill pattern for the mode in `regs`, repeated from dot 0.

    SCREEN 8 (G7) uses the raw R#7 byte as a direct GRB332 colour. SCREEN 6
    (G5) alternates two palette indices, R#7 bits 3:2 at even dots and bits
    1:0 at odd dots (openMSX getBorderColors; V9938 data book GRAPHIC 5
    "Hardware tiling function"). Every other mode uses the low
    nibble of R#7. Shared by the band prefill, the R#18 display-adjust fill
    and the output-height padding so they cannot drift.
    """
    r0 = regs[0]
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1
    if m5 and m4:
        return bytes((regs[7],))
    if m5 and not m3:
        return bytes(((regs[7] >> 2) & 0x03, regs[7] & 0x03))
    return bytes((regs[7] & 0x0F,))


def _apply_display_adjust(vdp: "V9938", buf: bytearray) -> bytearray:
    """Apply R#18 display adjust: shift the picture horizontally/vertically and
    fill the exposed edges with the border colour.

    The horizontal adjust is applied *per scanline* from R#18's value at that
    line, so a split screen that adjusts only its lower half shifts just that
    region. The whole VDP output (background and sprites) shifts together,
    matching openMSX where both derive from getLeftSprites.
    """
    h = vdp.display_height
    w = len(buf) // h  # the rendered frame's width (see _render_banded)

    row_h, v_off = _r18_row_offsets(vdp, h)
    # Nothing to do when there is no vertical shift and every scanline offset is
    # zero — the common BIOS-neutral R#18 = 0 case.
    if v_off == 0 and not any(row_h):
        return buf

    border = _border_pattern(vdp.regs)
    scale = w // 256  # 512-wide modes shift 2 buffer px per dot (keeps G5's dot parity)

    out = bytearray(border * (w * h // len(border)))
    for sy in range(h):
        dy = sy + v_off
        if dy < 0 or dy >= h:
            continue
        srow = sy * w
        drow = dy * w
        h_shift = row_h[sy] * scale
        if h_shift == 0:
            out[drow : drow + w] = buf[srow : srow + w]
        elif h_shift > 0:
            if h_shift < w:
                out[drow + h_shift : drow + w] = buf[srow : srow + w - h_shift]
        else:
            k = -h_shift
            if k < w:
                out[drow : drow + w - k] = buf[srow + k : srow + w]
    return out


class _Band(NamedTuple):
    """Scanlines [y0, y1) rendered with one register and palette snapshot."""

    y0: int
    y1: int
    regs: list[int]
    palette: list[int]


def _build_bands(vdp: "V9938") -> list[_Band]:
    """Build the frame's bands from the register change log."""
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

    bands: list[_Band] = []
    cur_regs = list(vdp._frame_start_regs)
    cur_palette = list(vdp._frame_start_palette)
    prev_y = 0

    for line in sorted(change_lines):
        if line > prev_y:
            bands.append(_Band(prev_y, line, list(cur_regs), list(cur_palette)))
        for entry in change_lines[line]:
            if isinstance(entry, _PaletteChange):
                cur_palette[entry.idx] = entry.rgb
            else:
                cur_regs[entry.reg] = entry.value
        prev_y = line

    if prev_y < display_height:
        bands.append(_Band(prev_y, display_height, list(cur_regs), list(cur_palette)))

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


def _render_banded(vdp: "V9938", st: _RenderStatus) -> bytearray:
    """Render with band-split: each band uses its own register snapshot.

    The frame is as wide as its widest band. Consecutive bands of one width form
    a run; with a mid-frame width change each run renders at its own width and a
    narrower run's rows are widened by doubling every pixel (openMSX likewise
    keeps a width per line and scales each line to the output width).
    """
    bands = _build_bands(vdp)
    if not bands:
        return _render_pass(vdp, st)

    full_h = vdp.display_height
    runs = _width_runs(bands)
    frame_w = 0
    for run in runs:
        frame_w = max(frame_w, display_width_of(run[0].regs))

    # Sprites: vertical scroll (R#23) is applied per scanline within a pass.
    # openMSX re-reads R#23 on every line (VDP.cc case 23 syncs then stores,
    # with NO syncAtNextLine) and tests each sprite's Y against that line's
    # vscroll, so a split screen that changes R#23 mid-frame positions each
    # region's sprites with its own vscroll without redrawing them. The band
    # snapshots already carry each region's R#23; row_vscroll[y] flattens that
    # into a per-line value that the sprite pass reads instead of its registers' R#23.
    # SPD sprite-disable (R#8 bit 1) is per-line too: a split screen may blank
    # sprites over just its status band (SPD=1 there, 0 in the play area). Build
    # it alongside row_vscroll so the sprite pass reads both per line.
    row_vscroll = [0] * full_h
    row_spd = [False] * full_h
    for band in bands:
        v = band.regs[23]
        spd = bool(band.regs[8] & 0x02)
        for y in range(band.y0, min(band.y1, full_h)):
            row_vscroll[y] = v
            row_spd[y] = spd

    out = bytearray(frame_w * full_h)
    if len(runs) == 1:
        _render_band_run(vdp, st, out, runs[0], full_h, row_vscroll, row_spd, True)
    else:
        for run in runs:
            run_w = display_width_of(run[0].regs)
            if run_w == frame_w:
                _render_band_run(vdp, st, out, run, full_h, row_vscroll, row_spd, False)
            else:
                narrow = bytearray(run_w * full_h)
                _render_band_run(vdp, st, narrow, run, full_h, row_vscroll, row_spd, False)
                _widen_rows(out, frame_w, narrow, run_w, run[0].y0, min(run[-1].y1, full_h))
    _update_field_parity(vdp, st)
    return out


def _width_runs(bands: list[_Band]) -> list[list[_Band]]:
    """Split the bands into maximal runs of consecutive bands of one display width."""
    runs: list[list[_Band]] = []
    run_w = -1
    for band in bands:
        w = display_width_of(band.regs)
        if runs and w == run_w:
            runs[-1].append(band)
        else:
            runs.append([band])
            run_w = w
    return runs


def _widen_rows(
    out: bytearray, out_w: int, src: bytearray, src_w: int, y0: int, y1: int
) -> None:
    """Copy rows [y0, y1) of `src` into `out`, doubling every pixel (256 → 512)."""
    for y in range(y0, y1):
        row = src[y * src_w:(y + 1) * src_w]
        d = y * out_w
        out[d:d + out_w:2] = row
        out[d + 1:d + out_w:2] = row


def _render_band_run(
    vdp: "V9938",
    st: _RenderStatus,
    buf: bytearray,
    run: list[_Band],
    full_h: int,
    row_vscroll: list[int],
    row_spd: list[bool],
    whole_frame: bool,
) -> None:
    """Render one width run's bands into `buf` (the run's own width): the
    background per band, then its sprite passes. `whole_frame` means the run is
    the whole frame, whose single sprite pass then covers every scanline."""
    # Each pass is handed its band's (or SAT segment's) registers; the chip's own
    # registers and palette are never swapped. The palette needs no per-band
    # handling here at all: the renderers emit palette indices, and to_rgb24
    # applies each band's palette from the register-write log.
    for band in run:
        _render_pass_range(
            vdp, band.regs, st, buf, band.y0, min(band.y1, full_h), draw_sprites=False
        )

    # Sprites are evaluated in as few passes as possible: one per distinct sprite
    # attribute table (R#5/R#11), using the registers of the largest band sharing
    # that SAT — the main play area. Vertical scroll (R#23) is *not* a reason to
    # split; it is applied per scanline within a pass (row_vscroll).
    #
    # A mid-screen sprite attribute table switch (R#5/R#11) is different: the
    # regions read *different* SAT buffers, used for sprite multiplexing /
    # "sprite doubler" (a second set of 32 sprites in the lower half). There
    # one sprite pass per SAT region — each clipped to its scanline span — is
    # required. Consecutive bands sharing a SAT base are merged so an ordinary
    # game (single SAT, whatever its vscroll splits) stays at one pass. R#5 is
    # only changed mid-active-display for this purpose, so keying on it does
    # not affect non-multiplexed games. The merge runs within one width run, so a
    # pass never spans a width change.
    sat_segments: list[_SatSegment] = []
    for band in run:
        bh = band.y1 - band.y0
        if sat_segments and _sat_key(sat_segments[-1].regs) == _sat_key(band.regs):
            seg = sat_segments[-1]
            seg.y1 = band.y1
            if bh > seg.height:
                seg.regs = band.regs
                seg.height = bh
        else:
            sat_segments.append(_SatSegment(band.y0, band.y1, band.regs, bh))

    if len(sat_segments) == 1 and whole_frame:
        # One SAT for the whole frame → single pass (main band regs); per-line
        # row_vscroll / row_spd handle any R#23 / SPD split within it.
        _render_sprites_for_mode(
            vdp, sat_segments[0].regs, st, buf, 0, full_h, row_vscroll, row_spd
        )
    else:
        for seg in sat_segments:
            _render_sprites_for_mode(
                vdp, seg.regs, st, buf, seg.y0, min(seg.y1, full_h), row_vscroll, row_spd
            )


def _sat_key(regs: list[int]) -> tuple[int, int]:
    return (regs[5], regs[11] & 0x03)


def _render_pass(vdp: "V9938", st: _RenderStatus) -> bytearray:
    """Single-pass render with the live registers."""
    regs = vdp.regs
    h = display_height_of(regs)
    w = display_width_of(regs)
    buf = bytearray(w * h)
    _render_pass_range(vdp, regs, st, buf, 0, h)
    _update_field_parity(vdp, st)
    return buf


def _render_pass_range(
    vdp: "V9938",
    regs: list[int],
    st: _RenderStatus,
    buf: bytearray,
    y_start: int,
    y_end: int,
    draw_sprites: bool = True,
) -> None:
    """Render scanlines [y_start, y_end) into buf with the registers `regs`.

    Pre-fills the scanline range with border colour, then writes content pixels.
    draw_sprites=False renders the background only (banded mode draws sprites once
    per frame afterwards, so a sprite is not re-evaluated in every band).
    """
    w = display_width_of(regs)
    h = display_height_of(regs)
    r0 = regs[0]
    r1 = regs[1]
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1

    n = (y_end - y_start) * w
    buf[y_start * w : y_end * w] = _border_fill(_border_pattern(regs), n)

    if not (r1 & 0x40):  # BL clear → blank band already filled above
        return

    m1 = (r1 >> 4) & 1
    m2 = (r1 >> 3) & 1

    # Background plane only — sprites are dispatched separately (see _render_sprites_for_mode).
    if m5:
        if m4:
            _render_g7(vdp, regs, buf, h, y_start, y_end)
        elif m3:
            _render_g6(vdp, regs, buf, h, y_start, y_end)
        else:
            _render_g5(vdp, regs, buf, h, y_start, y_end)
    elif m4:
        if m3:
            _render_g4(vdp, regs, buf, h, y_start, y_end)
        elif m1:
            _render_text2(vdp, regs, buf, y_start, y_end)
        else:
            _render_g2(vdp, regs, buf, y_start, y_end)
    elif m1:
        _render_text(vdp, regs, buf, y_start, y_end)
    elif m3:
        _render_g2(vdp, regs, buf, y_start, y_end)
    elif m2:
        _render_mc(vdp, regs, buf, y_start, y_end)
    else:
        _render_g1(vdp, regs, buf, y_start, y_end)

    if draw_sprites:
        _render_sprites_for_mode(vdp, regs, st, buf, y_start, y_end)


def _plot_mode1_row(
    buf: bytearray,
    painted: bytearray,
    touched: bytearray,
    pixels: bytes,
    x_pos: int,
    scale: int,
    line_off: int,
    *,
    color: int,
) -> int:
    """Plot one sprite-mode-1 pattern row onto the line starting at `line_off`.

    `x_pos` is the row's first dot and `scale` the MAG factor; off-screen dots
    are clipped (no wrap). Colour 0 only reaches here when TP makes it opaque,
    and is displayed as palette entry 0. Returns the x of the first colliding
    dot, or -1 for none (an Option<u16> in a port). The TMS9918A counterpart
    is renderer._plot_sprite_pixel (per pixel), which skips colour 0 itself
    since that chip has no TP.
    """
    hit_x = -1
    for bit_i, pixel in enumerate(pixels):
        if not pixel:
            continue
        px = x_pos + bit_i * scale
        last = px + scale
        while px < last:
            if 0 <= px < _W:
                coord = line_off + px
                if touched[coord]:
                    if hit_x < 0:
                        hit_x = px
                else:
                    touched[coord] = 1
                if not painted[coord]:
                    painted[coord] = 1
                    buf[coord] = color
            px += 1
    return hit_x


def _plot_mode2_row(
    sprite_buf: bytearray,
    touched: bytearray,
    drawn: list[int],
    pixels: bytes,
    x_pos: int,
    scale: int,
    line_off: int,
    screen_scale: int,
    *,
    collides: bool,
    or_mode: bool,
    color: int,
) -> int:
    """Plot one sprite-mode-2 pattern row into the sprite plane.

    `x_pos` is the row's first dot in the 256-dot sprite plane, `scale` the
    MAG factor; dots off-screen are clipped (no wrap), and each dot covers
    `screen_scale` output pixels (2 in 512-wide modes). A CC (OR) sprite ORs
    its colour into an already-claimed pixel; otherwise the first claim wins.
    Returns the sprite-plane x of the first colliding dot, or -1 for none (an
    Option<u16> in a port).
    """
    claim = color | _SPRITE_CLAIM  # marks the pixel even for colour 0
    ss_offsets = _SS_OFFSETS[screen_scale]
    hit_x = -1
    for bit_i, pixel in enumerate(pixels):
        if not pixel:
            continue
        sx = x_pos + bit_i * scale
        last = sx + scale
        while sx < last:
            if 0 <= sx < _W:
                base = line_off + sx * screen_scale
                for ss in ss_offsets:
                    coord = base + ss
                    if collides:
                        if touched[coord]:
                            if hit_x < 0:
                                hit_x = sx
                        else:
                            touched[coord] = 1
                    if sprite_buf[coord]:
                        if or_mode:
                            sprite_buf[coord] |= color
                    else:
                        sprite_buf[coord] = claim
                        drawn.append(coord)
            sx += 1
    return hit_x



def _render_sprites_for_mode(
    vdp: "V9938",
    regs: list[int],
    st: _RenderStatus,
    buf: bytearray,
    y_start: int,
    y_end: int,
    row_vscroll: list[int] | None = None,
    row_spd: list[bool] | None = None,
) -> None:
    """Dispatch to the sprite renderer for the current screen mode over [y_start, y_end).

    Sprite Y uses R#23 (vscroll) as on real hardware, in both sprite modes.
    row_vscroll, when given, supplies the per-scanline vscroll (banded mode,
    where R#23 may change mid-frame); otherwise the scalar regs[23] is
    used. row_spd likewise carries the per-scanline SPD (R#8 sprite-disable)
    so a split screen can blank sprites over just one band. TEXT1 (M1) and
    TEXT2 (M1+M4) draw no sprites, nor does a blanked display (BL=0).
    """
    r0 = regs[0]
    r1 = regs[1]
    if not (r1 & 0x40):  # BL clear → display blanked
        return
    m1 = (r1 >> 4) & 1
    m3 = (r0 >> 1) & 1
    m4 = (r0 >> 2) & 1
    m5 = (r0 >> 3) & 1
    h = display_height_of(regs)

    if m5:
        if m4:
            _render_sprites_mode2(
                vdp,
                regs,
                st,
                buf,
                h,
                y_start,
                y_end,
                grb_mode=True,
                row_vscroll=row_vscroll,
                row_spd=row_spd,
            )
        else:  # G5 and G6 both render sprites onto a 512-wide buffer
            _render_sprites_mode2(
                vdp,
                regs,
                st,
                buf,
                h,
                y_start,
                y_end,
                width=512,
                split_colour=not m3,  # G5 only (M3 clear)
                row_vscroll=row_vscroll,
                row_spd=row_spd,
            )
    elif m4 and m1:
        pass  # TEXT2: no sprites
    elif m4:
        _render_sprites_mode2(
            vdp, regs, st, buf, h, y_start, y_end, row_vscroll=row_vscroll, row_spd=row_spd
        )
    elif m1:
        pass  # TEXT1: no sprites
    else:  # G1 / G2(M3) / MULTICOLOR(M2): sprite mode 1
        _render_sprites(
            vdp, regs, st, buf, y_start, y_end, row_vscroll=row_vscroll, row_spd=row_spd
        )


def _update_field_parity(vdp: "V9938", st: _RenderStatus) -> None:
    # S#2 bit 1 (current field) toggles once per frame, in step with the EO
    # page-alternation parity (frame_count is stable for the whole render, and
    # this runs exactly once per render on every path).
    if vdp.frame_count & 1:
        st.status2 |= _S2_EO
    else:
        st.status2 &= ~_S2_EO


def _colour0(regs: list[int]) -> int:
    """Palette index drawn for colour code 0 in the pattern and text modes.

    TP=0 (R#8 bit 5): colour 0 is invisible, so the backdrop shows; TP=1: it
    is palette entry 0 (V9938 data book, R#8 TP).
    """
    return 0 if regs[8] & 0x20 else regs[7] & 0x0F


# ---------------------------------------------------------------------------
# Graphic 1 (SCREEN 1) — 32×24 tiles, colour per 8-tile group
# ---------------------------------------------------------------------------


def _render_g1(
    vdp: "V9938", regs: list[int], buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    # V9938 pattern-mode table bases span the full 128 KB: name = R#2 bits 6:0,
    # pattern = R#4 bits 5:0, colour = R#10 + R#3. In GRAPHIC1 the table
    # indices fit inside the base's own low bits, so (unlike GRAPHIC2/3) no
    # per-third gating applies. TEXT1 and MULTICOLOR use the same bases;
    # GRAPHIC2/3 and TEXT2 narrow them (see those functions).
    name_base = (regs[2] & 0x7F) << 10
    pat_base = (regs[4] & 0x3F) << 11
    col_base = ((regs[10] & 0x07) << 14) | (regs[3] << 6)
    # R#23 vertical scroll: openMSX applies it to every non-text mode alike
    # (PixelRenderer::draw: "if (!isTextMode()) displayY += getVerticalScroll()",
    # with no GRAPHIC1-vs-GRAPHIC2 distinction), as _render_g2 does.
    vscroll = regs[23]
    colour0 = _colour0(regs)
    ye = y_end if y_end is not None else _TILE_H

    # `tile`/`cb` (hence fg/bg) only depend on `row`, constant across the 8
    # scanlines sharing a tile row -- cached here and re-fetched from VRAM
    # only when `row` changes, so the per-scanline vscroll loop below reads
    # the name/colour tables once per tile row, not once per scanline. `pat`
    # still depends on `py` too, so it's re-fetched every scanline.
    row_tiles = [0] * 32
    row_fg = [0] * 32
    row_bg = [0] * 32
    cached_row = -1

    for scan in range(y_start, ye):
        vline = (scan + vscroll) & 0xFF
        row = vline >> 3
        py = vline & 0x07
        if row != cached_row:
            name_row = name_base + row * 32
            for col in range(32):
                tile = vdp.vram[(name_row + col) & 0x1FFFF]
                cb = vdp.vram[(col_base + tile // 8) & 0x1FFFF]
                row_tiles[col] = tile
                hi_nib = (cb >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
                row_fg[col] = hi_nib if hi_nib else colour0
                lo_nib = cb & 0x0F
                row_bg[col] = lo_nib if lo_nib else colour0
            cached_row = row
        scan_w = scan * _W
        for col in range(32):
            tile = row_tiles[col]
            pat = vdp.vram[(pat_base + tile * 8 + py) & 0x1FFFF]
            bx = col * 8
            buf[scan_w + bx : scan_w + bx + 8] = _ROW_BYTES[pat][row_fg[col]][row_bg[col]]


# ---------------------------------------------------------------------------
# Graphic 2 (SCREEN 2) — 32×24 tiles, per-row per-tile colour
# ---------------------------------------------------------------------------


def _render_g2(
    vdp: "V9938", regs: list[int], buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    # GRAPHIC 2 (SCREEN 2) and GRAPHIC 3 (SCREEN 4) share this tile plane. Table
    # bases follow the V9938 (per openMSX VDP::update*Base): the pattern and
    # colour tables are 8 KB-aligned (the low 13 index bits come from the
    # band/tile/line offset), so
    #   pattern generator = (R#4 << 11) & ~0x1FFF = (R#4 & 0x3C) << 11
    #   colour table      = ((R#10 << 14) | (R#3 << 6)) & ~0x1FFF
    #                     = (R#10 & 0x07) << 14 | (R#3 & 0x80) << 6
    #   name table        = R#2 << 10 (A16-A10)
    # R#4 bits 5:2 are the A16-A13 pattern base bits (R#4 bit 2 alone is only
    # the TMS9918A form; an MSX2 title with R#4 = 0x13 puts its generator at
    # 0x8000).
    #
    # R#4 bits 1:0 (colour: R#3 bits 6:5) gate the 2nd/3rd-third offset bits
    # (0x800/0x1000) before they are added to the base -- documented VDP
    # behaviour (msx.org/wiki/SCREEN_2), as in the TMS9918A renderer; a clear
    # bit collapses that third's reads onto the 1st third's table.
    name_base = (regs[2] & 0x7F) << 10
    pat_base = (regs[4] & 0x3C) << 11
    col_base = ((regs[10] & 0x07) << 14) | ((regs[3] & 0x80) << 6)
    pat_mask = ((regs[4] & 0x03) << 11) | 0x7FF
    col_mask = ((regs[3] & 0x7F) << 6) | 0x3F
    vscroll = regs[23]
    colour0 = _colour0(regs)
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
            pat = vdp.vram[(pat_base + (off & pat_mask)) & 0x1FFFF]
            cb = vdp.vram[(col_base + (off & col_mask)) & 0x1FFFF]
            hi_nib = (cb >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
            fg = hi_nib if hi_nib else colour0
            lo_nib = cb & 0x0F
            bg = lo_nib if lo_nib else colour0
            bx = col * 8
            buf[scan_w + bx : scan_w + bx + 8] = _ROW_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text (SCREEN 0) — 40×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------


def _render_text(
    vdp: "V9938", regs: list[int], buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    # TEXT1 uses the same full-width R#2/R#4 bases (see _render_g1).
    name_base = (regs[2] & 0x7F) << 10
    pat_base = (regs[4] & 0x3F) << 11
    # TEXT1: R#7 high nibble = text colour, low nibble = background. A text
    # colour code 0 follows TP like every other colour code 0 (see _colour0).
    fg = (regs[7] >> 4) & 0x0F
    if fg == 0:
        fg = _colour0(regs)
    bg = regs[7] & 0x0F
    # No mode-specific 192-line cap: VDP::getNumberOfLines() (real hardware)
    # is mode-independent, so TEXT1's active display period at R#9 LN=1
    # extends to 212 lines exactly like every other mode -- see this file's
    # Open Questions.
    ye = y_end if y_end is not None else _TILE_H
    row_start = y_start // 8
    row_end = (ye + 7) // 8

    for row in range(row_start, row_end):
        for col in range(40):
            tile = vdp.vram[(name_base + row * 40 + col) & 0x1FFFF]
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x1FFFF]
                off = scan * _W + 8 + col * 6
                buf[off : off + 6] = _TEXT6_BYTES[pat][fg][bg]


# ---------------------------------------------------------------------------
# Text 2 (SCREEN 0, WIDTH 80) — 80×24 chars, 6 pixels wide, no sprites
# ---------------------------------------------------------------------------


def _render_text2(
    vdp: "V9938", regs: list[int], buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    """TEXT2: same pattern generator table and R#7 colours as TEXT1 (MSX2
    Technical Handbook 3.2.2), but 80 columns into a 512-wide buffer and a
    4 KB-aligned name table selected by R#2 bits 6-2 (A16-A12) instead of
    TEXT1's 1 KB-aligned, 4-bit addressing -- see openspec design.md Decision
    1.

    Blink: a character whose bit in the blink table (R#10/R#3, A16-A9; one
    bit per character, MSB first, 10 bytes per row) is set uses R#12's
    colours during the R#13 blink ON period and R#7's otherwise (V9938 data
    book, TEXT 2 mode sections 4-6).
    """
    name_base = (regs[2] & 0x7C) << 10
    # Pattern generator: full-width R#4 base (see _render_g1); the name table
    # is 4 KB-aligned in TEXT2.
    pat_base = (regs[4] & 0x3F) << 11
    blink_base = ((regs[10] & 0x07) << 14) | ((regs[3] & 0xF8) << 6)
    fg = (regs[7] >> 4) & 0x0F
    if fg == 0:  # text colour 0 follows TP, as in TEXT1 (see _colour0)
        fg = _colour0(regs)
    bg = regs[7] & 0x0F
    blink_on = vdp.blink_state
    blink_bg = regs[12] & 0x0F
    if blink_bg == 0:
        blink_bg = _colour0(regs)
    blink_fg = (regs[12] >> 4) & 0x0F
    if blink_fg == 0:  # matches openMSX: blink text colour 0 shows the blink background
        blink_fg = blink_bg
    ye = y_end if y_end is not None else _TILE_H
    row_start = y_start // 8
    row_end = (ye + 7) // 8
    # 80 cols * 6 px = 480 of the 512-wide buffer; the remaining 32 px split
    # into a 16 px margin each side, doubling TEXT1's 8 px margin at half the
    # width (240 of 256) -- the wide text/bitmap modes double the pixel clock
    # while the active-display time stays the same, so the border doubles in
    # pixel count right along with the active area.
    for row in range(row_start, row_end):
        for col in range(80):
            tile = vdp.vram[(name_base + row * 80 + col) & 0x1FFFF]
            cfg = fg
            cbg = bg
            if blink_on:
                attr = vdp.vram[(blink_base + row * 10 + (col >> 3)) & 0x1FFFF]
                if attr & (0x80 >> (col & 7)):
                    cfg = blink_fg
                    cbg = blink_bg
            for py in range(8):
                scan = row * 8 + py
                if scan < y_start or scan >= ye:
                    continue
                pat = vdp.vram[(pat_base + tile * 8 + py) & 0x1FFFF]
                off = scan * 512 + 16 + col * 6
                buf[off : off + 6] = _TEXT6_BYTES[pat][cfg][cbg]


# ---------------------------------------------------------------------------
# Multicolor (SCREEN 3) — 64×48 blocks of 4×4 pixels
# ---------------------------------------------------------------------------


def _render_mc(
    vdp: "V9938", regs: list[int], buf: bytearray, y_start: int = 0, y_end: int | None = None
) -> None:
    # Full V9938 128 KB range (R#2 7 bits, R#4 6 bits) -- see _render_g1's
    # comment.
    name_base = (regs[2] & 0x7F) << 10
    pat_base = (regs[4] & 0x3F) << 11
    # R#23 vertical scroll: applies here exactly as it does to G1/G2 -- see
    # _render_g1's comment (openMSX PixelRenderer::draw treats every
    # non-text mode alike).
    vscroll = regs[23]
    colour0 = _colour0(regs)
    ye = y_end if y_end is not None else _TILE_H

    # `tile` only depends on `row`, constant across the 8 scanlines sharing a
    # tile row -- cached here and re-fetched from the name table only when
    # `row` changes, so the per-scanline vscroll loop below doesn't pay for
    # 8x as many name-table reads as the pre-vscroll per-row loop did.
    row_tiles = [0] * 32
    cached_row = -1

    for scan in range(y_start, ye):
        vline = (scan + vscroll) & 0xFF
        row = vline >> 3
        py = vline & 0x07
        if row != cached_row:
            name_row = name_base + row * 32
            for col in range(32):
                row_tiles[col] = vdp.vram[(name_row + col) & 0x1FFFF]
            cached_row = row
        # MULTICOLOR uses only 2 of the 8 pattern bytes per cell: the byte
        # pair is selected by the character row (row & 3)*2, and the top vs
        # bottom 4 scanlines pick within the pair (py >> 2). Each nibble is a
        # solid 4x4 block, so the colour is constant across its 4 scanlines.
        seg = (row & 3) * 2
        scan_w = scan * _W
        for col in range(32):
            tile = row_tiles[col]
            bx = col * 8
            pat = vdp.vram[(pat_base + tile * 8 + seg + (py >> 2)) & 0x1FFFF]
            hi_nib = (pat >> 4) & 0x0F  # inline _color() to avoid a per-pixel call
            lc = hi_nib if hi_nib else colour0
            lo_nib = pat & 0x0F
            rc = lo_nib if lo_nib else colour0
            buf[scan_w + bx : scan_w + bx + 4] = _COLOR4[lc]
            buf[scan_w + bx + 4 : scan_w + bx + 8] = _COLOR4[rc]


# ---------------------------------------------------------------------------
# Sprite mode 1 — keeps the TMS9918A limit of 4 sprites per scanline.
# (Sprite mode 2, below, raises this to 8 per scanline.)
# ---------------------------------------------------------------------------


def _render_sprites(
    vdp: "V9938",
    regs: list[int],
    st: _RenderStatus,
    buf: bytearray,
    y_start: int = 0,
    y_end: int | None = None,
    row_vscroll: list[int] | None = None,
    row_spd: list[bool] | None = None,
) -> None:
    """Sprite mode 1 for TEXT1/GRAPHIC1/GRAPHIC2/MULTICOLOR.

    row_vscroll/row_spd carry the same per-scanline R#23/R#8-SPD schedule
    sprite mode 2 already honours (see _render_sprites_mode2 and
    _sprite_runs) -- openMSX applies R#23 to sprite mode 1 identically to
    mode 2 (SpriteChecker::checkSprites1's displayDelta = getVerticalScroll()
    - getLineZero(), same construction as checkSprites2's); there is no
    hardware basis for sprite mode 1 ignoring vscroll or a banded pass's
    per-line SPD schedule.
    """
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    spd_scalar = bool(regs[8] & 0x02)  # SPD: sprite disable (R#8 bit 1)
    if row_spd is None and spd_scalar:
        return  # sprites disabled for the whole pass (no per-line schedule)
    r1 = regs[1]
    si = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)

    # R#5/R#11 -> SAT base, matching sprite mode 2's addressing exactly (no
    # further alignment rounding needed here: unlike mode 2, which shares one
    # VRAM window with a colour table 0x200 bytes below the SAT and so needs
    # the coarser 512-byte rounding _render_sprites_mode2 applies, sprite
    # mode 1 has no such shared window -- openMSX's VDP::updateSpriteAttributeBase
    # computes baseMask = (R#11<<15)|(R#5<<7)|0x7F for BOTH sprite modes
    # unconditionally (only indexMask/alignment differs by mode: 7-bit for
    # mode 1, 10-bit for mode 2), so V9938 extends sprite mode 1's SAT into
    # the full 128 KB VRAM via R#11 exactly as it does for mode 2.
    sat_base = (((regs[11] & 3) << 15) | (regs[5] << 7)) & 0x1FFFF
    spt_base = (regs[6] & 0x3F) << 11

    # TP (R#8 bit 5): colour 0 is not transparent, so a colour-0 sprite can
    # collide (openMSX VDP.hh canSpriteColor0Collide() for a non-MSX1 VDP:
    # !getTransparency(); V9938 data book "TP and Sprites"). At the default
    # TP=0, colour-0 sprites are fully transparent and take no part in
    # coincidence, same as before.
    can0collide = bool(regs[8] & 0x20)

    # No mode-specific 192-line cap: sprite mode 1's active display period is
    # exactly as long as any other mode's (VDP::getNumberOfLines() is mode-
    # independent on real hardware -- see the GRAPHIC1/MULTICOLOUR fix above
    # and this file's Open Questions). h mirrors sprite mode 2's own `h`
    # parameter (_render_sprites_mode2, which _render_sprites_for_mode
    # computes once as display_height_of(regs) and passes down) -- both sprite
    # renderers size their per-line/per-pixel state to the display's actual
    # height, not a fixed constant, so a 192-line frame does not allocate
    # (or bound-check against) 212 lines' worth of unused state.
    h = display_height_of(regs)
    # scan_hi caps how far this pass's line index ever reaches (< scan_hi
    # always), so sizing the per-line/per-pixel state below to scan_hi rather
    # than the full h avoids over-allocating for a banded pass covering only
    # the leading part of the frame (a mid-frame SAT switch's earlier
    # segments; see _render_banded's multi-segment branch) -- a no-op size-
    # wise for the common single-segment case, where scan_hi already equals h.
    scan_hi = min(y_end if y_end is not None else h, h)
    line_count = [0] * scan_hi
    fifth_set = False  # mode 1: the 5th sprite on a line sets the 5S flag
    # Two separate claim arrays (see allium/tms9918a.allium's RenderFrame
    # for the base-class version of this split): sprite_painted tracks
    # z-order (only a pixel the VDP actually displays claims a pixel, so a
    # transparent higher-priority sprite does not block a lower-priority one
    # showing through); sprite_touched tracks coincidence.
    sprite_painted = bytearray(_W * scan_hi)
    sprite_touched = bytearray(_W * scan_hi)
    coincidence = False
    # Collision coordinates (S#3-S#6): the earliest colliding line, and the
    # leftmost collision on it (openMSX SpriteChecker takes minXCollision on
    # the first colliding line and stops there).
    col_line = 999
    col_x = 999
    had_collision = bool(st.status & 0x20)  # C set since last cleared by an S#0 read
    # Vertical scroll and SPD are both per scanline: split the range into runs
    # of constant vscroll AND constant sprite-enable, exactly as sprite mode 2
    # does (see _sprite_runs). With no per-line schedule this is one run over
    # the whole range using the scalar regs[23], so the common non-banded
    # case is unaffected.
    sprite_runs = _sprite_runs(row_vscroll, regs[23], y_start, scan_hi, row_spd)

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x1FFFF]
        if y_byte == 0xD0:
            break

        x_byte = vdp.vram[(sat_base + i * 4 + 1) & 0x1FFFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x1FFFF]
        attr = vdp.vram[(sat_base + i * 4 + 3) & 0x1FFFF]
        color = attr & 0x0F
        if attr & 0x80:
            x_byte -= 32  # EC: shift 32px left; may go negative → clipped below

        y_top = (y_byte + 1) & 0xFF

        # Scan only the sprite's visible band per constant-vscroll run, as
        # sprite mode 2 does -- see _sprite_scanlines. Per-sprite line order
        # does not affect line_count / 5S / coincidence, and the runs
        # partition the scanlines, so this matches a single-vscroll full
        # scan when there is no per-line schedule.
        for line, sprite_row in _sprite_scanlines(sprite_runs, y_top, render_size):
            if line_count[line] >= 4:  # V9938 sprite mode 1: 4 sprites per line
                if not fifth_set:
                    st.status = (st.status & 0xA0) | 0x40 | (i & 0x1F)
                    fifth_set = True
                continue

            line_count[line] += 1

            if color == 0 and not can0collide:
                continue

            src_row = sprite_row // 2 if mag else sprite_row
            pixels = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row, mask=0x1FFFF)
            hit_x = _plot_mode1_row(
                buf, sprite_painted, sprite_touched, pixels, x_byte, 2 if mag else 1,
                line * _W, color=color,
            )
            if hit_x >= 0:
                coincidence = True
                # First collision in raster order wins (openMSX).
                if line < col_line or (line == col_line and hit_x < col_x):
                    col_line = line
                    col_x = hit_x

    if coincidence:
        st.status |= 0x20
        if not had_collision:  # first collision since C was last cleared wins (openMSX)
            st.collision_x = col_x + _COLLISION_X_OFFSET
            st.collision_y = col_line + _COLLISION_Y_OFFSET


class _SpriteRun(NamedTuple):
    """A run of consecutive scanlines [lo, hi) with constant vertical scroll
    (R#23) and constant sprite-enable. `enabled` is False where R#8 SPD blanks
    sprites, so the sprite pass skips the run entirely."""

    lo: int
    hi: int
    vscroll: int
    enabled: bool


def _sprite_runs(
    row_vscroll: list[int] | None,
    default_vscroll: int,
    y_start: int,
    scan_hi: int,
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


def _sprite_scanlines(
    sprite_runs: list[_SpriteRun], y_top: int, render_size: int
) -> Iterable[tuple[int, int]]:
    """Yield (line, sprite_row) for every visible scanline of a sprite whose
    pattern starts at VRAM row y_top and spans render_size rows, across all
    per-scanline vscroll/SPD runs.

    Shared by _render_sprites and _render_sprites_mode2 so a scan fix lands
    in both sprite modes at once.
    """
    for run in sprite_runs:
        if not run.enabled:  # SPD set over this run: sprites blanked here
            continue
        vsc = run.vscroll
        base_line = (y_top - vsc) & 0xFF
        end = base_line + render_size
        lines: Iterable[int]
        if end <= 256:
            lines = range(max(run.lo, base_line), min(run.hi, end))
        else:
            lines = chain(
                range(run.lo, min(run.hi, end - 256)), range(max(run.lo, base_line), run.hi)
            )

        for line in lines:
            # Sprite Y is in VRAM coordinate space; account for vertical scroll.
            vram_line = (line + vsc) & 0xFF
            sprite_row = (vram_line - y_top) & 0xFF  # guaranteed < render_size
            yield line, sprite_row


def _composite_sprite_buf(
    buf: bytearray,
    sprite_buf: bytearray,
    drawn: list[int],
    grb_mode: bool,
    split_colour: bool,
) -> None:
    """Composite only the pixels a sprite actually touched (avoids scanning the
    whole h*width buffer every frame when sprites are sparse or absent)."""
    if grb_mode:
        # SCREEN 8 sprites use the fixed GRAPHIC7 sprite palette, not vdp.palette.
        for coord in drawn:
            buf[coord] = _G7_SPRITE_PALETTE[sprite_buf[coord] & 0x0F]
    elif split_colour:
        # GRAPHIC5: bits 3:2 of the colour go to the even (left) screen pixel of
        # the dot, bits 1:0 to the odd (right) one. Splitting here rather than at
        # store time keeps the CC OR-merge correct, since (a | b) >> 2 equals
        # (a >> 2) | (b >> 2) and likewise for the low half.
        for coord in drawn:
            v = sprite_buf[coord] & 0x0F
            buf[coord] = (v >> 2) if (coord & 1) == 0 else (v & 3)
    else:
        for coord in drawn:
            buf[coord] = sprite_buf[coord] & 0x0F


def _render_sprites_mode2(
    vdp: "V9938",
    regs: list[int],
    st: _RenderStatus,
    buf: bytearray,
    h: int,
    y_start: int = 0,
    y_end: int | None = None,
    grb_mode: bool = False,
    width: int = _W,
    split_colour: bool = False,
    row_vscroll: list[int] | None = None,
    row_spd: list[bool] | None = None,
) -> None:
    """Sprite mode 2 for SCREEN 4–8.

    R#5/R#11 → SAT base (512-byte aligned). Colour table at sat_base-0x200.
    Per-line colour byte: EC(7) | CC(6, OR-combine) | IC(5, ignore collision) | colour(3:0).
    grb_mode=True (SCREEN 8): sprite palette colours are converted to GRB332 before writing.
    width: buffer line width; 512 for the wide modes (G5/G6), where the 256-dot
    sprite plane is doubled horizontally so sprites span the full screen.
    split_colour=True (SCREEN 6 / GRAPHIC5): the plane holds 2 bits per screen dot,
    so each sprite dot's 4-bit colour splits across its two screen pixels — bits
    3:2 on the left, bits 1:0 on the right (V9938 data book GRAPHIC 5
    "Hardware tiling function": high two bits for even dots, low two for odd;
    openMSX SpriteConverter::drawMode2's
    GRAPHIC5 branch; confirmed on openMSX with a solid sprite of colour code 6,
    which renders as palette 1 / palette 2 columns, not as a palette-6 block).

    Sprite Y is evaluated against R#23 vertical scroll per scanline (as on real
    hardware, which re-reads R#23 each line): row_vscroll gives the per-line value
    when R#23 changes mid-frame, else the scalar regs[23] is used. Each sprite
    is positioned in a single pass regardless.

    SPD (R#8 bit 1) disables sprites, also per scanline: row_spd, when given,
    supplies the per-line sprite-disable flag (a split screen may blank sprites
    over just its status band, as real hardware does), else the scalar
    regs[8] is used. Disabled scanline runs are skipped entirely.
    """
    if vdp.debug_disable_sprites:  # debug: render background only
        return
    spd_scalar = bool(regs[8] & 0x02)  # SPD: sprite disable (R#8 bit 1)
    if row_spd is None and spd_scalar:
        return  # sprites disabled for the whole pass (no per-line schedule)
    r1 = regs[1]
    si = (r1 >> 1) & 1
    mag = r1 & 1
    pat_size = 16 if si else 8
    render_size = pat_size * (2 if mag else 1)
    screen_scale = width // _W  # 1 (256-wide modes) or 2 (512-wide G5/G6)

    # SAT base = the R#11/R#5 attribute address with bits 8:0 cleared
    # (512-byte aligned). The per-sprite colour table uses the same base with
    # bits 9:0 cleared; there R#5 bits 1:0 gate offset bits 8:7 and bit 9
    # reads as 0, so the sprite*16 + line offset is ANDed, not added
    # (matches openMSX's sprite-mode-2 colour addressing). With R#5 bits 2:0
    # all set -- the usual BIOS value -- this equals "SAT - 0x200 + offset",
    # the V9938 data book's rule (Sprite Color Table base = SAT base - 512);
    # the AND behaviour for other R#5 values is openMSX-only.
    attr_reg = ((regs[11] & 3) << 15) | (regs[5] << 7)
    sat_base = attr_reg & ~0x1FF & 0x1FFFF
    col_base_high = attr_reg & ~0x3FF & 0x1FFFF
    col_offset_mask = 0x7F | ((regs[5] & 3) << 7)
    spt_base = (regs[6] & 0x3F) << 11

    # TP (R#8 bit 5): colour 0 is not transparent, so a colour-0 sprite can
    # collide (openMSX VDP.hh canSpriteColor0Collide() for a non-MSX1 VDP:
    # !getTransparency(); V9938 data book "TP and Sprites"). At the default
    # TP=0, colour-0 sprites are fully transparent and take no part in
    # coincidence, same as sprite mode 1.
    can0collide = bool(regs[8] & 0x20)

    # scan_hi caps how far this pass's line index ever reaches (< scan_hi
    # always), so sizing the per-line/per-pixel state below to scan_hi rather
    # than the full h avoids over-allocating for a banded pass covering only
    # the leading part of the frame -- a no-op size-wise for the common
    # single-segment case, where scan_hi already equals h.
    scan_hi = min(y_end if y_end is not None else h, h)
    line_count = [0] * scan_hi
    ninth_set = False
    # 0 = unclaimed. A displayed pixel stores colour | _SPRITE_CLAIM, so an
    # opaque colour-0 pixel (TP=1) claims its pixel too; the claim bit is
    # masked off when compositing.
    sprite_buf = bytearray(scan_hi * width)
    # Coincidence is tracked separately from sprite_buf (same split as
    # _render_sprites above): sprite_buf holds the composited colour and the
    # z-order winner, which a CC/IC sprite claims like any other, while
    # sprite_touched records only pixels a collision-eligible sprite covered.
    sprite_touched = bytearray(scan_hi * width)
    # Per line: set once a CC=0 sprite has appeared. A CC=1 sprite is only
    # visible if a higher-priority CC=0 sprite precedes it on the same line
    # (V9938 rule); leading CC=1 sprites are invisible.
    cc0_seen = bytearray(scan_hi)
    drawn: list[int] = []  # coords touched, to composite only those (vs full scan)
    coincidence = False
    # Collision coordinates (S#3-S#6), as in _render_sprites above.
    col_line = 999
    col_x = 999
    had_collision = bool(st.status & 0x20)
    # Vertical scroll and SPD (sprite disable) are both per scanline: split the
    # range into runs of constant vscroll AND constant sprite-enable (one run for
    # the usual single R#23/R#8; more only when a split changes either). Runs
    # whose SPD is set are skipped, so a band that blanks its sprites draws none.
    sprite_runs = _sprite_runs(row_vscroll, regs[23], y_start, scan_hi, row_spd)

    for i in range(32):
        y_byte = vdp.vram[(sat_base + i * 4) & 0x1FFFF]
        if y_byte == 0xD8:  # sprite mode 2 list terminator is 216 (0xD8), not 208
            break

        x_byte = vdp.vram[(sat_base + i * 4 + 1) & 0x1FFFF]
        pat_idx = vdp.vram[(sat_base + i * 4 + 2) & 0x1FFFF]
        # SAT 4th byte is unused in sprite mode 2; colour/EC/CC/IC come from the
        # per-line colour table byte instead.

        y_top = (y_byte + 1) & 0xFF
        # A sprite spans render_size rows in VRAM-Y space, wrapping row 255→0, and
        # is positioned on screen at (y_top - vsc); openMSX applies no VRAM-row clip
        # (it tests (line - y) & 0xFF < magSize per line). The screen-line wrap is
        # handled by _sprite_scanlines's end > 256 branch. An earlier build clipped
        # to 256 - y_top, but that is in VRAM-Y space, so with vertical scroll it
        # wrongly truncated a sprite whose screen band is nowhere near the bottom
        # (a high-Y sprite pulled up by R#23) — e.g. y_top=251, vsc=62 → screen
        # line 189, clipped to 5 rows. The terminator (y==0xD8) already stops the
        # scan, so no clip is needed.

        # Scan only the sprite's visible band, per constant-vscroll run -- see
        # _sprite_scanlines. Per-sprite line order is immaterial to the
        # accumulated line_count / 9S / cc0 / coincidence state, and the runs
        # partition the scanlines, so this equals a full scan at one vscroll.
        for line, sprite_row in _sprite_scanlines(sprite_runs, y_top, render_size):
            if line_count[line] >= 8:
                if not ninth_set:
                    st.status = (st.status & 0xA0) | 0x40 | (i & 0x1F)
                    ninth_set = True
                continue

            line_count[line] += 1

            src_row = sprite_row // 2 if mag else sprite_row
            # Per-line colour byte: EC(7) | CC(6) | IC(5) | 0 | colour(3:0).
            col_addr = col_base_high | ((i * 16 + src_row) & col_offset_mask)
            col_entry = vdp.vram[col_addr & 0x1FFFF]
            color = col_entry & 0x0F
            or_mode = bool(col_entry & 0x40)  # CC: OR-combine with same-priority sprite
            x_pos = x_byte - 32 if (col_entry & 0x80) else x_byte  # EC: per-line shift
            # Collision eligibility, per sprite line: neither CC nor IC set,
            # and a displayed colour (colour 0 only counts once TP makes it
            # opaque). openMSX skips a sprite whose colourAttrib & 0x60 is
            # set on both sides of every pair it tests, and the Handbook
            # (Sec. 5.3.3) words the rule as "a conflict occurs when the
            # display colour of a sprite is not transparent and '1' bits on
            # the line whose CC bit is 0 overlap each other".
            collides = (col_entry & 0x60) == 0 and (color != 0 or can0collide)

            # CC=1 sprites are only visible once a higher-priority CC=0 sprite
            # has appeared on this line; a CC=0 sprite enables them (counted even
            # when its own colour is transparent).
            if or_mode:
                if not cc0_seen[line]:
                    continue
            else:
                cc0_seen[line] = 1

            if color == 0 and not can0collide:
                continue  # transparent regardless of OR mode

            pixels = _sprite_row_pixels(vdp, spt_base, pat_idx, si, src_row, mask=0x1FFFF)
            hit_x = _plot_mode2_row(
                sprite_buf, sprite_touched, drawn, pixels, x_pos, 2 if mag else 1,
                line * width, screen_scale, collides=collides, or_mode=or_mode, color=color,
            )
            if hit_x >= 0:
                coincidence = True
                # First collision in raster order wins (openMSX).
                if line < col_line or (line == col_line and hit_x < col_x):
                    col_line = line
                    col_x = hit_x

    _composite_sprite_buf(buf, sprite_buf, drawn, grb_mode, split_colour)

    if coincidence:
        st.status |= 0x20
        if not had_collision:  # first collision since C was last cleared wins (openMSX)
            st.collision_x = col_x + _COLLISION_X_OFFSET
            st.collision_y = col_line + _COLLISION_Y_OFFSET


def _sprite_row_pixels(
    vdp: "V9938",
    spt_base: int,
    pat_idx: int,
    si: int,
    src_row: int,
    mask: int = 0x3FFF,
) -> bytes:
    if si == 0:
        b = vdp.vram[(spt_base + pat_idx * 8 + src_row) & mask]
        return _UNPACK8[b]

    base = pat_idx & 0xFC
    if src_row < 8:
        left = vdp.vram[(spt_base + base * 8 + src_row) & mask]
        right = vdp.vram[(spt_base + (base + 2) * 8 + src_row) & mask]
    else:
        r = src_row - 8
        left = vdp.vram[(spt_base + (base + 1) * 8 + r) & mask]
        right = vdp.vram[(spt_base + (base + 3) * 8 + r) & mask]

    return _UNPACK8[left] + _UNPACK8[right]


def _display_page_bits(vdp: "V9938", regs: list[int], mask: int) -> int:
    """R#2 display-page bits for GRAPHIC4-7. `mask` is the page-select bits
    within R#2: 0x60 (A16:A15) for the 32 KB-page modes SCREEN 5/6, 0x20 (A16)
    for the 64 KB-page modes SCREEN 7/8. Either way the lowest page bit is
    R#2 bit 5.

    Bit 5 is forced to 0 (even page) during the R#13 blink ON period, and
    with EO (R#9 bit 2) set on the first field (S#2 EO = 0, even frame): the
    data book shows the even page for the ON time and, with EO, in the first
    field (V9938 data book, R#13 and Special Functions 1.1/2.2).
    """
    bits = regs[2] & mask
    first_field = even_odd_enabled_of(regs) and not (vdp.frame_count & 1)
    if vdp.blink_state or first_field:
        bits &= ~0x20 & 0xFF
    return bits


# ---------------------------------------------------------------------------
# SCREEN 5 (Graphic 4) — 4-bpp bitmap, two palette indices per byte
# ---------------------------------------------------------------------------


def _render_g4(
    vdp: "V9938",
    regs: list[int],
    buf: bytearray,
    h: int,
    y_start: int = 0,
    y_end: int | None = None,
) -> None:
    """SCREEN 5: 4-bpp, palette index per half-byte (high nibble = left pixel)."""
    base = _display_page_bits(vdp, regs, 0x60) << 10
    tp = bool(regs[8] & 0x20)  # R#8 bit5: 1=col0 solid, 0=col0 transparent→backdrop
    border = regs[7] & 0x0F
    vscroll = regs[23]
    vram = vdp.vram
    # C-level expansion: two translate tables (hi/lo nibble) + strided slice assignment.
    lut_hi, lut_lo = _g46_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * 128) & 0x1FFFF
        bx = y * _W
        row = bytes(vram[row_base : row_base + 128])
        buf[bx : bx + _W : 2] = row.translate(lut_hi)
        buf[bx + 1 : bx + _W : 2] = row.translate(lut_lo)


# ---------------------------------------------------------------------------
# SCREEN 6 (Graphic 5) — 2-bpp, 512 virtual width, rendered 512 wide
# ---------------------------------------------------------------------------


def _render_g5(
    vdp: "V9938",
    regs: list[int],
    buf: bytearray,
    h: int,
    y_start: int = 0,
    y_end: int | None = None,
) -> None:
    """SCREEN 6: 2-bpp, 4 pixels per byte, full 512-pixel width."""
    base = _display_page_bits(vdp, regs, 0x60) << 10
    tp = bool(regs[8] & 0x20)
    border = regs[7] & 0x0F
    vscroll = regs[23]
    vram = vdp.vram
    w = 512
    # C-level expansion: four translate tables (bit-pairs 7:6, 5:4, 3:2, 1:0) + stride-4.
    p0, p1, p2, p3 = _g5_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * 128) & 0x1FFFF
        bx = y * w
        row = bytes(vram[row_base : row_base + 128])
        buf[bx : bx + w : 4] = row.translate(p0)
        buf[bx + 1 : bx + w : 4] = row.translate(p1)
        buf[bx + 2 : bx + w : 4] = row.translate(p2)
        buf[bx + 3 : bx + w : 4] = row.translate(p3)


# ---------------------------------------------------------------------------
# SCREEN 7 (Graphic 6) — 4-bpp, 512 virtual width, rendered 512 wide
# ---------------------------------------------------------------------------


def _render_g6(
    vdp: "V9938",
    regs: list[int],
    buf: bytearray,
    h: int,
    y_start: int = 0,
    y_end: int | None = None,
) -> None:
    """SCREEN 7: 4-bpp, 2 pixels per byte, full 512-pixel width."""
    base = _display_page_bits(vdp, regs, 0x20) << 11  # G6: R#2 bit 5 = A16 (64 KB pages)
    tp = bool(regs[8] & 0x20)
    border = regs[7] & 0x0F
    vscroll = regs[23]
    vram = vdp.vram
    w = 512
    # Same hi/lo translate approach as G4 but 256 VRAM bytes → 512 output pixels.
    lut_hi, lut_lo = _g46_luts(tp, border)
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * 256) & 0x1FFFF  # 256 bytes/line
        bx = y * w
        row = bytes(vram[row_base : row_base + 256])
        buf[bx : bx + w : 2] = row.translate(lut_hi)
        buf[bx + 1 : bx + w : 2] = row.translate(lut_lo)


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
    vdp: "V9938",
    regs: list[int],
    buf: bytearray,
    h: int,
    y_start: int = 0,
    y_end: int | None = None,
) -> None:
    """SCREEN 8: 8-bpp GRB332, one raw byte per pixel (palette not used)."""
    base = _display_page_bits(vdp, regs, 0x20) << 11  # G7: R#2 bit 5 = A16 (64 KB pages)
    vscroll = regs[23]
    vram = vdp.vram
    ye = y_end if y_end is not None else h
    for y in range(y_start, ye):
        row_base = (base + ((y + vscroll) & 0xFF) * _W) & 0x1FFFF
        bx = y * _W
        buf[bx : bx + _W] = vram[row_base : row_base + _W]  # whole row, one C-level copy


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
    r18_changed = any(isinstance(e, _RegChange) and e.reg == 18 for e in vdp._reg_write_log)
    base18 = vdp._frame_start_regs[18] if r18_changed else vdp.regs[18]
    _, v_off = _r18_offset(base18)  # signed line shift from R#18

    bands = _build_bands(vdp)

    # Channel tables are built once per band (not per row/pixel); lines outside
    # any band fall back to the frame-start palette (e.g. the border after the
    # v_off shift, and the top/bottom output-height pad rows).
    default_channels = _channel_tables_indexed(_make_lut16(vdp._frame_start_palette))
    line_channels: list[tuple[bytes, bytes, bytes]] = [default_channels] * native_h
    for band in bands:
        channels = _channel_tables_indexed(_make_lut16(band.palette))
        for sy in range(max(0, band.y0), min(native_h, band.y1)):
            line_channels[sy] = channels

    out = bytearray(w * OUTPUT_H * 3)
    for dy in range(OUTPUT_H):
        ny = dy - pad  # native index-buffer row this output row maps to
        if 0 <= ny < native_h:
            sy = ny - v_off
            rtab, gtab, btab = line_channels[sy] if 0 <= sy < native_h else default_channels
        else:
            rtab, gtab, btab = default_channels
        row = src[dy * w : dy * w + w]
        o = dy * w * 3
        end = o + w * 3
        out[o:end:3] = row.translate(rtab)
        out[o + 1 : end : 3] = row.translate(gtab)
        out[o + 2 : end : 3] = row.translate(btab)
    return bytes(out)


def to_rgb24(vdp: "V9938", src: bytearray) -> bytes:
    """Convert a V9938 palette-index (or SCREEN 8 GRB332) framebuffer to RGB24.

    SCREEN 8 (G7) maps each byte through the fixed GRB332 table; other modes use
    the programmable 16-colour palette. When the register-write log records a
    mid-frame palette change, conversion is done per band. The 16-colour channel
    tables are cached on the VDP instance, keyed on the palette snapshot.

    PORT-NOTE: the channel-table cache (`_rgb_lut_key` / `_rgb_channels`) is
      the only chip field this conversion writes, and it is not emulated state.
    Rust equivalent: a `RefCell` (or `Cell` of an owned table) inside the chip,
      so `to_rgb24(&self)` stays a shared borrow.
    C++ equivalent: a `mutable` member.
    Kept as-is here because: Python has no borrow distinction to keep.
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
