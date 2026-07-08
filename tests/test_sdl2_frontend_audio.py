"""Tests for the SDL2 frontend audio mixer (_mix_audio)."""
from array import array

from frontend.sdl2_frontend import _mix_audio
from msx.psg import SAMPLES_PER_FRAME


def _pcm(value: int) -> bytes:
    """SAMPLES_PER_FRAME signed-16 samples of `value`, as native-order bytes."""
    return array("h", [value] * SAMPLES_PER_FRAME).tobytes()


class _Gen:
    def __init__(self, buf: bytes) -> None:
        self._buf = buf

    def generate_samples(self, _n: int, *_args: int) -> bytes:
        return self._buf


class _Machine:
    def __init__(self, psg: bytes, scc: bytes | None = None, dac: bytes | None = None) -> None:
        self.psg = _Gen(psg)
        self.scc = _Gen(scc) if scc is not None else None
        self.dac = _Gen(dac) if dac is not None else None


def test_psg_only_passthrough() -> None:
    """No SCC/DAC → the PSG buffer is returned unchanged."""
    psg = _pcm(1234)
    assert _mix_audio(_Machine(psg), 0, 100) == psg


def test_mix_sums_channels() -> None:
    """PSG + SCC + DAC are summed sample-wise."""
    out = _mix_audio(_Machine(_pcm(100), scc=_pcm(50), dac=_pcm(25)), 0, 100)
    assert array("h", out).tolist() == [175] * SAMPLES_PER_FRAME


def test_mix_clamps_to_s16_range() -> None:
    """The summed mix is clamped to the signed-16 range, not wrapped."""
    out = array("h", _mix_audio(_Machine(_pcm(30000), scc=_pcm(10000)), 0, 100)).tolist()
    assert out == [32767] * SAMPLES_PER_FRAME
    out_neg = array("h", _mix_audio(_Machine(_pcm(-30000), scc=_pcm(-10000)), 0, 100)).tolist()
    assert out_neg == [-32768] * SAMPLES_PER_FRAME
