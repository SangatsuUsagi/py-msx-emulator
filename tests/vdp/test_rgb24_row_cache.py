"""Rgb24RowCache: memoised index-to-RGB24 rows (vdp-renderer
"RGB24 conversion may memoise rows")."""
from __future__ import annotations

import random

import pytest

from msx.vdp._geometry import OUTPUT_H
from msx.vdp.vdp import _TMS_CHANNELS, Rgb24RowCache, _channel_tables_indexed, _translate_rgb24

_OTHER_CHANNELS = _channel_tables_indexed([bytes((i * 16, 255 - i * 16, i)) for i in range(16)])


def _frame(width: int, seed: int, repeat_rows: bool) -> bytearray:
    rng = random.Random(seed)
    if repeat_rows:
        rows = [bytes(rng.randrange(16) for _ in range(width)) for _ in range(8)]
        return bytearray(b"".join(rows[rng.randrange(8)] for _ in range(OUTPUT_H)))
    return bytearray(rng.randrange(256) for _ in range(width * OUTPUT_H))


@pytest.mark.parametrize("width", [256, 512])
@pytest.mark.parametrize("repeat_rows", [False, True])
def test_frame_equals_the_uncached_conversion(width: int, repeat_rows: bool) -> None:
    cache = Rgb24RowCache()
    for seed in range(4):
        for channels in (_TMS_CHANNELS, _OTHER_CHANNELS):
            src = _frame(width, seed, repeat_rows)
            assert cache.frame(src, channels) == _translate_rgb24(src, channels)


def test_a_buffer_that_is_not_whole_rows_converts_without_the_memo() -> None:
    cache = Rgb24RowCache()
    for src in (bytearray(b"\x01\x08\x0f"), bytearray(256 * OUTPUT_H + 1)):
        assert cache.frame(src, _TMS_CHANNELS) == _translate_rgb24(src, _TMS_CHANNELS)
    assert len(cache) == 0


def test_row_equals_a_row_sized_conversion() -> None:
    cache = Rgb24RowCache()
    row = bytes(range(256))
    assert cache.row(row, _TMS_CHANNELS) == _translate_rgb24(bytearray(row), _TMS_CHANNELS)
    assert cache.row(row, _TMS_CHANNELS) == _translate_rgb24(bytearray(row), _TMS_CHANNELS)


def test_the_same_row_under_other_tables_converts_with_those_tables() -> None:
    cache = Rgb24RowCache()
    row = bytes(range(16)) * 16
    cache.row(row, _TMS_CHANNELS)
    assert cache.row(row, _OTHER_CHANNELS) == _translate_rgb24(bytearray(row), _OTHER_CHANNELS)


def test_equal_tables_built_again_still_hit() -> None:
    cache = Rgb24RowCache()
    row = bytes(256)
    cache.row(row, _TMS_CHANNELS)
    rebuilt = tuple(bytes(t) for t in _TMS_CHANNELS)
    assert rebuilt is not _TMS_CHANNELS
    cache.row(row, rebuilt)  # type: ignore[arg-type]
    assert len(cache) == 1


def test_mutating_the_source_after_a_call_does_not_change_later_results() -> None:
    cache = Rgb24RowCache()
    src = _frame(256, 1, repeat_rows=True)
    first = cache.frame(src, _TMS_CHANNELS)
    src[0] = (src[0] + 1) & 0x0F
    assert cache.frame(src, _TMS_CHANNELS) == _translate_rgb24(src, _TMS_CHANNELS)
    assert first != cache.frame(src, _TMS_CHANNELS)


def test_the_memo_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # vdp-renderer "The memo stays bounded".
    monkeypatch.setattr(Rgb24RowCache, "LIMIT", 100)
    cache = Rgb24RowCache()
    for n in range(350):
        row = n.to_bytes(2, "little") * 128
        assert cache.row(row, _TMS_CHANNELS) == _translate_rgb24(bytearray(row), _TMS_CHANNELS)
        assert len(cache) <= 100


@pytest.mark.parametrize("msx2", [False, True])
def test_the_memo_is_not_part_of_the_snapshot(msx2: bool) -> None:
    # vdp-renderer "The memo is not part of the snapshot".
    from msx import state
    from msx.input import InputFrame
    from tests.factories import make_machine, make_machine_msx2

    def build():  # type: ignore[no-untyped-def]
        if msx2:
            return make_machine_msx2(bytes(32768), bytes(16384))
        return make_machine(rom=bytes(32768))

    plain, converted = build(), build()
    for machine in (plain, converted):
        for _ in range(3):
            machine.step_frame(InputFrame(), render_video=False)
    converted.vdp._rgb_rows.frame(_frame(256, 7, repeat_rows=True), _TMS_CHANNELS)
    assert len(converted.vdp._rgb_rows) > 0
    saved = state.save_state(converted)
    assert saved == state.save_state(plain)

    loaded = build()
    state.load_state(loaded, saved, "test")
    expected = converted.step_frame(InputFrame(), render_video=True).video
    assert loaded.step_frame(InputFrame(), render_video=True).video == expected


class _Uncached:
    """Stand-in for Rgb24RowCache that converts every row afresh."""

    def frame(self, src: bytearray, channels):  # type: ignore[no-untyped-def]
        return _translate_rgb24(src, channels)

    def row(self, row, channels):  # type: ignore[no-untyped-def]
        return _translate_rgb24(bytearray(row), channels)


def _v9938(mode_r0: int):  # type: ignore[no-untyped-def]
    from msx.vdp.v9938 import V9938

    vdp = V9938()
    vdp.regs[0] = mode_r0
    vdp.regs[1] = 0x40
    for i in range(16):
        vdp.palette[i] = (i * 37) & 0x1FF
    return vdp


def _banded_v9938():  # type: ignore[no-untyped-def]
    from msx.vdp._v9938_defs import _PaletteChange

    vdp = _v9938(0x06)  # SCREEN 5
    vdp.begin_scanline(0)
    vdp.palette[1] = 0b111_111_111
    vdp._reg_write_log.append(_PaletteChange(64, 1, 0b111_111_111))
    vdp.palette[2] = 0b000_111_000
    vdp._reg_write_log.append(_PaletteChange(150, 2, 0b000_111_000))
    return vdp


@pytest.mark.parametrize(("label", "make", "width"), [
    ("TMS9918A", lambda: __import__("msx.vdp.vdp", fromlist=["VDP"]).VDP(), 256),
    ("V9938 SCREEN 5", lambda: _v9938(0x06), 256),
    ("V9938 SCREEN 7", lambda: _v9938(0x0A), 512),
    ("V9938 SCREEN 8", lambda: _v9938(0x0E), 256),
    ("V9938 banded", _banded_v9938, 256),
])
def test_memoised_output_equals_the_uncached_conversion(label, make, width) -> None:  # type: ignore[no-untyped-def]
    # vdp-renderer "Memoised output equals the uncached conversion".
    memo, plain = make(), make()
    plain._rgb_rows = _Uncached()
    frames = [_frame(width, seed, repeat_rows=True) for seed in range(3)]
    frames += [_frame(width, 9, repeat_rows=False)]
    for src in frames + frames:  # the second pass hits the memo
        assert memo.to_rgb24(bytearray(src)) == plain.to_rgb24(bytearray(src)), label
    assert len(memo._rgb_rows) > 0


def test_a_palette_change_is_not_hidden_by_the_memo() -> None:
    # vdp-renderer "A palette change is not hidden by the memo".
    vdp = _v9938(0x06)
    src = bytearray([3]) * (256 * OUTPUT_H)
    before = vdp.to_rgb24(src)
    vdp.palette[3] = 0b111_111_111  # index 3 -> white
    after = vdp.to_rgb24(bytearray(src))
    assert tuple(before[:3]) != (255, 255, 255)
    assert tuple(after[:3]) == (255, 255, 255)
    assert tuple(after[-3:]) == (255, 255, 255)
