from __future__ import annotations

import math
import struct
from functools import lru_cache

from msx.psg import SAMPLE_RATE


@lru_cache(maxsize=8)
def _unpacker(n: int) -> struct.Struct:
    """Cached little-endian signed-16 unpacker for an n-sample buffer.

    filter() is called with a constant buffer length per session, so caching the
    compiled Struct avoids re-parsing the format string on every frame.
    """
    return struct.Struct("<%dh" % n)

# Default output filter: 2-pole Butterworth low-pass. The cutoff tames the
# out-of-band imaging/ripple that point-sampling synthesis leaves near Nyquist
# (software PCM upsampled from ~11 kHz, the ultrasonic tone carrier) while leaving
# the audible band essentially flat, mirroring the analog RC filter on real MSX
# audio output.
_DEFAULT_CUTOFF_HZ: float = 8_000.0
_BUTTERWORTH_Q: float = 0.7071067811865476   # 1/sqrt(2)


class BiquadLowPass:
    """Stateful second-order (biquad) low-pass filter for 16-bit mono PCM.

    Coefficients follow the RBJ audio-EQ cookbook low-pass formulas and are
    computed once at construction. Buffers are filtered sample-by-sample with
    Direct Form I, carrying the two previous inputs/outputs across calls so
    successive buffers are continuous.

    The coefficients and recurrence use f64 (host libm); output is not
    bit-reproducible across platforms/ports. This only affects the audio output
    path — it never feeds emulation state or save-states — so it is not a
    determinism concern for the core.
    """

    def __init__(
        self,
        cutoff: float = _DEFAULT_CUTOFF_HZ,
        q: float = _BUTTERWORTH_Q,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        w0 = 2.0 * math.pi * cutoff / sample_rate
        cos_w0 = math.cos(w0)
        alpha = math.sin(w0) / (2.0 * q)

        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        # Normalise so a0 == 1.
        self.b0 = b0 / a0
        self.b1 = b1 / a0
        self.b2 = b2 / a0
        self.a1 = a1 / a0
        self.a2 = a2 / a0

        # Direct Form I state: previous two inputs and outputs.
        self._x1 = 0.0
        self._x2 = 0.0
        self._y1 = 0.0
        self._y2 = 0.0

    def reset(self) -> None:
        """Clear the carried input/output history (e.g. when audio restarts)."""
        self._x1 = 0.0
        self._x2 = 0.0
        self._y1 = 0.0
        self._y2 = 0.0

    def filter(self, buf: bytes) -> bytes:
        """Return `buf` low-pass filtered; input/output are signed-16 LE mono PCM.

        The output has the same number of samples as the input. Each sample is
        rounded to the nearest integer and clipped to the signed 16-bit range.
        """
        n = len(buf) // 2
        samples = _unpacker(n).unpack(buf)
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self._x1, self._x2, self._y1, self._y2

        out = bytearray(n * 2)
        for i in range(n):
            x0 = samples[i]
            y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x0
            y2, y1 = y1, y0

            s = int(y0 + 0.5) if y0 >= 0 else int(y0 - 0.5)
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            out[i * 2] = s & 0xFF
            out[i * 2 + 1] = (s >> 8) & 0xFF

        self._x1, self._x2, self._y1, self._y2 = x1, x2, y1, y2
        return bytes(out)
