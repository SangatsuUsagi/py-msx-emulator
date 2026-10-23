"""Tests for the core per-frame audio mixer (msx/audio_mixer.py, spec audio-mixer).

The mixer hands off the unfiltered mix; the output filter is the frontend's
(tests/test_sdl2_frontend_audio.py).
"""
from array import array

from msx.audio_mixer import AudioMixer
from msx.psg import SAMPLES_PER_FRAME

N = SAMPLES_PER_FRAME


def _pcm(value: int, n: int = N) -> bytes:
    """n signed-16 samples of `value`, as native-order bytes."""
    return array("h", [value] * n).tobytes()


class _Gen:
    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self.calls: list[tuple[int, ...]] = []

    def generate_samples(self, n: int, *args: int) -> bytes:
        self.calls.append((n, *args))
        return self._buf


class _SilentGen:
    """A DAC-style source that is silent for the frame."""

    def generate_samples(self, _n: int, *_args: int) -> None:
        return None


def _mix(
    psg: bytes,
    scc: bytes | None = None,
    dac: "bytes | _SilentGen | None" = None,
    click: "bytes | _SilentGen | None" = None,
    opll: bytes | None = None,
) -> bytes:
    def source(buf: "bytes | _SilentGen | None") -> "_Gen | _SilentGen | None":
        if buf is None or isinstance(buf, _SilentGen):
            return buf
        return _Gen(buf)

    mixer = AudioMixer()
    mixer.mix_frame(
        N, 0, 100, _Gen(psg), source(scc), source(dac), source(click), source(opll),
    )
    return mixer.take()


def test_psg_only_passthrough() -> None:
    # audio-mixer "Silent optional sources leave the PSG-only path".
    psg = _pcm(1234)
    assert _mix(psg) == psg


def test_silent_dac_and_key_click_leave_psg_only_path() -> None:
    psg = _pcm(1234)
    assert _mix(psg, dac=_SilentGen(), click=_SilentGen()) == psg


def test_dac_is_mixed_when_it_sounds() -> None:
    # audio-mixer "DAC is mixed when it sounds".
    assert _mix(_pcm(100), dac=_pcm(25)) == _pcm(125)


def test_mix_sums_psg_scc_dac() -> None:
    assert _mix(_pcm(100), scc=_pcm(50), dac=_pcm(25)) == _pcm(175)


def test_opll_contributes_to_mix() -> None:
    # audio-mixer "OPLL contributes to the mix".
    assert _mix(_pcm(100), opll=_pcm(50)) == _pcm(150)


def test_key_click_is_mixed() -> None:
    # audio-mixer "Key click is mixed when PPI Port C bit 7 changes".
    assert _mix(_pcm(100), click=_pcm(9288)) == _pcm(9388)


def test_sum_is_clamped() -> None:
    # audio-mixer "Sum is clamped".
    assert _mix(_pcm(30000), scc=_pcm(10000)) == _pcm(32767)
    assert _mix(_pcm(-30000), scc=_pcm(-10000)) == _pcm(-32768)
    assert _mix(_pcm(30000), click=_pcm(9288)) == _pcm(32767)


def test_cycle_stamped_sources_get_the_frame_window() -> None:
    # audio-mixer "PSG receives the frame boundaries" (and the DAC/key click).
    psg, scc, dac, click, opll = (_Gen(_pcm(0)) for _ in range(5))
    AudioMixer().mix_frame(N, 1000, 60736, psg, scc, dac, click, opll)
    assert psg.calls == [(N, 1000, 60736)]
    assert dac.calls == [(N, 1000, 60736)]
    assert click.calls == [(N, 1000, 60736)]
    assert scc.calls == [(N,)]
    assert opll.calls == [(N,)]


def test_filter_applies_after_the_sum() -> None:
    mixed = _mix(_pcm(100), scc=_pcm(50), dac=_pcm(25), opll=_pcm(25))
    assert mixed == _pcm(200)


def test_take_returns_the_frame_once() -> None:
    # audio-mixer "take_audio returns the last frame once" (mixer level).
    mixer = AudioMixer()
    mixer.mix_frame(N, 0, 100, _Gen(_pcm(7)), None, None, None, None)
    assert len(mixer.take()) == 2 * N
    assert mixer.take() == b""


def test_untaken_frame_is_replaced() -> None:
    mixer = AudioMixer()
    for value in (1000, 2000, 3000):
        mixer.mix_frame(N, 0, 100, _Gen(_pcm(value)), None, None, None, None)
    assert mixer.take() == _pcm(3000)


def test_reset_clears_filter_history_and_pending_frame() -> None:
    psg = _pcm(20000)
    mixer = AudioMixer()
    mixer.mix_frame(N, 0, 100, _Gen(psg), None, None, None, None)
    mixer.reset()
    assert mixer.take() == b""
    mixer.mix_frame(N, 0, 100, _Gen(psg), None, None, None, None)
    assert mixer.take() == psg
