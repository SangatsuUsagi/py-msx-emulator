"""A skipped frame sets the same VDP status as a full render.

Covers machine-core "Skipping video never changes emulated state" at the
renderer level: vdp-renderer "skip_render sets the same sprite status as a
full render", "TMS9918A skip sets the same sprite status as a full render",
and the vdp-v9938 render_frame skip scenarios.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938, _RegChange
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
from msx.vdp.vdp import VDP


def _v9938_status(vdp: V9938) -> tuple[int, int, int, int]:
    return (vdp.status, vdp.collision_x, vdp.collision_y, vdp.status2)


def _v9938_with_9s_and_collision() -> V9938:
    vdp = V9938()
    vdp.regs[0] = 0x06               # GRAPHIC4, sprite mode 2
    vdp.regs[1] = 0x60               # BL=1, IE0=1
    vdp.regs[5] = 0x77
    sat = 0x77 << 7
    for i in range(9):               # nine overlapping sprites on line 1
        vdp.vram[sat + i * 4] = 0
        vdp.vram[sat + i * 4 + 1] = i
    vdp.vram[sat + 9 * 4] = 216
    col = (sat & ~0x3FF) & 0x1FFFF
    for i in range(9):
        vdp.vram[col + i * 16] = 0x0F
    vdp.vram[0] = 0xFF
    return vdp


def test_v9938_skip_sets_the_same_sprite_status_as_a_full_render() -> None:
    full, skipped = _v9938_with_9s_and_collision(), _v9938_with_9s_and_collision()
    for vdp in (full, skipped):
        vdp.frame_count = 1
    render_frame_v9938(full)
    render_frame_v9938(skipped, skip_render=True)
    assert full.status & 0x60 == 0x60          # 9S and C set: not vacuous
    assert _v9938_status(skipped) == _v9938_status(full)


def test_v9938_banded_skip_matches_a_full_render() -> None:
    # A mid-frame SPD change (R#8 bit 1 from line 3) makes the frame banded.
    full, skipped = _v9938_with_9s_and_collision(), _v9938_with_9s_and_collision()
    for vdp in (full, skipped):
        vdp._frame_start_regs = vdp.regs[:]
        vdp._frame_start_palette = vdp.palette[:]
        vdp.regs[8] |= 0x02
        vdp._reg_write_log = [_RegChange(2, 8, vdp.regs[8])]
    render_frame_v9938(full)
    render_frame_v9938(skipped, skip_render=True)
    assert _v9938_status(skipped) == _v9938_status(full)


_SPRITE = st.tuples(
    st.integers(0, 0xCF), st.integers(0, 0xFF), st.integers(0, 0xFF), st.integers(0, 0xFF)
)


@settings(max_examples=60, deadline=None)
@given(sprites=st.lists(_SPRITE, min_size=1, max_size=32),
       pattern=st.binary(min_size=32, max_size=32), r1_size=st.sampled_from([0, 1, 2, 3]))
def test_tms_skip_status_matches_full_render(
    sprites: list[tuple[int, int, int, int]], pattern: bytes, r1_size: int
) -> None:
    def build() -> VDP:
        vdp = VDP()
        vdp.regs[1] = 0x40 | r1_size     # BL=1, SI/MAG from r1_size
        vdp.regs[5] = 0x20               # SAT at 0x1000
        for i, (y, x, p, a) in enumerate(sprites):
            vdp.vram[0x1000 + i * 4: 0x1000 + i * 4 + 4] = bytes((y, x, p, a))
        if len(sprites) < 32:
            vdp.vram[0x1000 + len(sprites) * 4] = 0xD0
        vdp.vram[0:32] = pattern
        return vdp

    full, skipped = build(), build()
    render_frame(full)
    render_frame(skipped, skip_render=True)
    assert skipped.status == full.status


@settings(max_examples=40, deadline=None)
@given(sprites=st.lists(_SPRITE, min_size=1, max_size=32),
       pattern=st.binary(min_size=32, max_size=32),
       colours=st.binary(min_size=16, max_size=16),
       mode=st.sampled_from([0x00, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0E]),
       r1_size=st.sampled_from([0, 1, 2, 3]))
def test_v9938_skip_status_matches_full_render(
    sprites: list[tuple[int, int, int, int]], pattern: bytes, colours: bytes,
    mode: int, r1_size: int,
) -> None:
    # mode = R#0 (M3-M5): G1, G2, G3, G4, G5, G6, G7 (sprite modes 1 and 2).
    def build() -> V9938:
        vdp = V9938()
        vdp.regs[0] = mode
        vdp.regs[1] = 0x40 | r1_size
        vdp.regs[5] = 0x77
        sat = 0x77 << 7
        for i, (y, x, p, a) in enumerate(sprites):
            vdp.vram[sat + i * 4: sat + i * 4 + 4] = bytes((y, x, p, a))
            col = ((sat & ~0x3FF) & 0x1FFFF) + i * 16
            vdp.vram[col: col + 16] = colours
        if len(sprites) < 32:
            vdp.vram[sat + len(sprites) * 4] = 216
        vdp.vram[0:32] = pattern
        vdp.frame_count = 3
        return vdp

    full, skipped = build(), build()
    render_frame_v9938(full)
    render_frame_v9938(skipped, skip_render=True)
    assert _v9938_status(skipped) == _v9938_status(full)


def _sat2(r5: int) -> int:
    return (r5 << 7) & ~0x1FF            # sprite mode 2 SAT: bits 8:0 cleared


def _g4_sprite(vdp: V9938, sat: int, idx: int, y: int) -> None:
    vdp.vram[sat + idx * 4] = y
    vdp.vram[sat + idx * 4 + 1] = (idx % 9) * 16
    vdp.vram[(sat & ~0x3FF) + idx * 16] = 0x0F


def _g4_sprite_vdp() -> V9938:
    vdp = V9938()
    vdp.regs[0] = 0x06               # GRAPHIC4, sprite mode 2
    vdp.regs[1] = 0x40               # BL=1
    vdp.regs[5] = 0x77
    vdp.vram[0] = 0xFF
    return vdp


def test_v9938_topmost_overflowing_line_gives_ninth_sprite_index() -> None:
    # Sprites 0-8 on line 100, sprites 9-17 on line 10: S#0 bits 4:0 hold the
    # 9th sprite on line 10 (17), not the first overflow in SAT order (8).
    vdp = _g4_sprite_vdp()
    sat = _sat2(0x77)
    for i in range(9):
        _g4_sprite(vdp, sat, i, 99)
        _g4_sprite(vdp, sat, 9 + i, 9)
    vdp.vram[sat + 18 * 4] = 216

    render_frame_v9938(vdp)

    assert vdp.status & 0x40
    assert vdp.status & 0x1F == 17


def test_v9938_overflow_index_takes_the_topmost_line_across_sat_segments() -> None:
    # SAT A (lines 0-99) overflows on line 50 with sprite 8; SAT B (from line
    # 100) overflows on line 150 with sprite 11. The topmost line wins: 8.
    vdp = _g4_sprite_vdp()
    vdp.regs[5] = 0x77
    vdp.begin_scanline(0)
    sat_a = _sat2(0x77)
    sat_b = _sat2(0x6F)
    for i in range(9):
        _g4_sprite(vdp, sat_a, i, 49)
    vdp.vram[sat_a + 9 * 4] = 216
    for i in range(3):
        _g4_sprite(vdp, sat_b, i, 200)   # off the active area
    for i in range(3, 12):
        _g4_sprite(vdp, sat_b, i, 149)
    vdp.vram[sat_b + 12 * 4] = 216
    vdp.regs[5] = 0x6F
    vdp._reg_write_log.append(_RegChange(99, 5, 0x6F))

    render_frame_v9938(vdp)

    assert vdp.status & 0x40
    assert vdp.status & 0x1F == 8
