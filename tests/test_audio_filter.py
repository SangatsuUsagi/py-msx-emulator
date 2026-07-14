"""Tests for the analog-style output low-pass filter (BiquadLowPass)."""

import math
import struct

from msx.audio_filter import BiquadLowPass
from msx.psg import SAMPLE_RATE


def _sine(freq: float, n: int, amp: int = 30000) -> bytes:
    return struct.pack(
        "<%dh" % n,
        *(int(amp * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)),
    )


def _peak(buf: bytes) -> int:
    return max(abs(v) for v in struct.unpack("<%dh" % (len(buf) // 2), buf))


def test_default_coefficients_finite_and_unity_dc_gain() -> None:
    f = BiquadLowPass()
    for c in (f.b0, f.b1, f.b2, f.a1, f.a2):
        assert math.isfinite(c)
    dc_gain = (f.b0 + f.b1 + f.b2) / (1.0 + f.a1 + f.a2)
    assert abs(dc_gain - 1.0) < 1e-9


def test_buffer_length_preserved() -> None:
    f = BiquadLowPass()
    out = f.filter(_sine(1000, 735))
    assert len(out) == 735 * 2


def test_high_frequency_attenuated() -> None:
    f = BiquadLowPass()
    inp = _sine(20000, 2048)
    out = f.filter(inp)
    # 20 kHz is well above the 10 kHz cutoff: strongly rolled off.
    assert _peak(out) < 0.5 * _peak(inp)


def test_in_band_passed_through() -> None:
    f = BiquadLowPass()
    inp = _sine(1000, 4096)
    out = f.filter(inp)
    # Use the steady-state tail (skip the filter's startup transient).
    tail_in = _peak(inp[-2048 * 2:])
    tail_out = _peak(out[-2048 * 2:])
    assert 0.9 * tail_in <= tail_out <= 1.1 * tail_in


def test_state_carries_across_successive_buffers() -> None:
    inp = _sine(3000, 1470)
    whole = BiquadLowPass().filter(inp)

    split = BiquadLowPass()
    half = len(inp) // 2
    part1 = split.filter(inp[:half])
    part2 = split.filter(inp[half:])
    assert part1 + part2 == whole


def test_reset_restores_fresh_behaviour() -> None:
    inp = _sine(3000, 735)
    fresh = BiquadLowPass().filter(inp)

    used = BiquadLowPass()
    used.filter(_sine(5000, 735))   # dirty the state
    used.reset()
    assert used.filter(inp) == fresh
