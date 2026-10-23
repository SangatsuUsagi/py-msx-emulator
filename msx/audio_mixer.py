"""Per-frame audio mix: PSG plus the optional sources, summed and clamped.

Machine.run_frame() calls mix_frame() once per frame, with or without a
frontend, so every sound chip advances the same way headless and under SDL2.
A frontend reads the result with take(). The output low-pass filter is the
frontend's (frontend/audio_filter.py): the core hands off the unfiltered mix.
"""
from __future__ import annotations

from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.dac import EventDac
    from msx.mapper import MajutsushiMapper
    from msx.opll import Opll
    from msx.psg import PSG
    from msx.scc import SCC

_S16_MAX: int = 32767
_S16_MIN: int = -32768


class AudioMixer:
    """Mixes one frame of every present source and holds it until taken.

    Only the latest frame is held: a frame that nobody took is replaced by the
    next one, so memory stays bounded on a headless run.
    """

    def __init__(self) -> None:
        self._pending = b""

    def mix_frame(
        self,
        n: int,
        frame_start: int,
        frame_end: int,
        psg: PSG,
        scc: SCC | None,
        dac: MajutsushiMapper | None,
        click: EventDac | None,
        opll: Opll | None,
    ) -> None:
        """Mix n samples of every present source and hold the result.

        frame_start/frame_end are the frame's cycle window for the
        cycle-stamped sources (PSG, DAC, key click). A DAC or key click that
        is silent for the frame returns None and adds nothing, so an idle
        machine keeps the PSG-only path.
        """
        psg_buf = psg.generate_samples(n, frame_start, frame_end)
        extra_bufs = []
        if scc is not None:
            extra_bufs.append(scc.generate_samples(n))
        if dac is not None:
            dac_buf = dac.generate_samples(n, frame_start, frame_end)
            if dac_buf is not None:
                extra_bufs.append(dac_buf)
        if click is not None:
            click_buf = click.generate_samples(n, frame_start, frame_end)
            if click_buf is not None:
                extra_bufs.append(click_buf)
        if opll is not None:
            extra_bufs.append(opll.generate_samples(n))
        if not extra_bufs:
            self._pending = bytes(psg_buf)
            return
        self._pending = _sum_and_clamp(n, psg_buf, extra_bufs)

    def take(self) -> bytes:
        """Return the held frame and clear it; b"" when nothing is held."""
        pcm = self._pending
        self._pending = b""
        return pcm

    def reset(self) -> None:
        """Drop any untaken frame."""
        self._pending = b""


def _sum_and_clamp(n: int, psg_buf: bytes | bytearray, extra_bufs: list[bytearray]) -> bytes:
    """Sum the buffers sample by sample and clamp to signed 16-bit.

    Each buffer is batch-decoded with array("h") (one C call per buffer)
    instead of a per-sample struct.unpack_from. Sums are taken in Python ints
    so the clamp applies to the full mix. array("h") is native byte order,
    which matches the LE PCM on LE hosts.
    """
    # PORT-NOTE: benchmarked the per-sample clamp loop below (timeit, 735
    #   samples/frame, 1-2 extra channels) against min()/max() clamping and a
    #   zip()+sum() rewrite -- the if/elif clamp here is already at or near
    #   the fastest of the three, and the whole loop costs ~40-55us/frame
    #   against a 16.6ms (60fps) budget either way. Not worth restructuring
    #   pre-port; a native port's per-sample loop is trivially fast regardless
    #   of which of these shapes it's translated from.
    psg_arr = array("h")
    psg_arr.frombytes(psg_buf)
    extra_arrs = []
    for buf in extra_bufs:
        chan_arr = array("h")
        chan_arr.frombytes(buf)
        extra_arrs.append(chan_arr)
    out_arr = array("h", bytes(2 * n))
    for i in range(n):
        total = psg_arr[i]
        for chan_arr in extra_arrs:
            total += chan_arr[i]
        if total > _S16_MAX:
            total = _S16_MAX
        elif total < _S16_MIN:
            total = _S16_MIN
        out_arr[i] = total
    return out_arr.tobytes()
