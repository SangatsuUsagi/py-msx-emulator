"""Tests for the SDL2 frontend audio mixer (_mix_audio)."""
from array import array

from frontend.sdl2_frontend import _init_sdl, _mix_audio
from msx.audio_filter import BiquadLowPass
from msx.psg import SAMPLES_PER_FRAME


def _pcm(value: int) -> bytes:
    """SAMPLES_PER_FRAME signed-16 samples of `value`, as native-order bytes."""
    return array("h", [value] * SAMPLES_PER_FRAME).tobytes()


class _Gen:
    def __init__(self, buf: bytes) -> None:
        self._buf = buf

    def generate_samples(self, _n: int, *_args: int) -> bytes:
        return self._buf


class _FmPac:
    def __init__(self, opll_buf: bytes) -> None:
        self.opll = _Gen(opll_buf)


class _Machine:
    def __init__(
        self,
        psg: bytes,
        scc: bytes | None = None,
        dac: bytes | None = None,
        fmpac_opll: bytes | None = None,
    ) -> None:
        self.psg = _Gen(psg)
        self.scc = _Gen(scc) if scc is not None else None
        self.dac = _Gen(dac) if dac is not None else None
        self.fmpac = _FmPac(fmpac_opll) if fmpac_opll is not None else None


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


def test_no_fmpac_leaves_audio_unchanged() -> None:
    """No FM-PAC → identical to the pre-FM-PAC PSG+SCC+DAC mix."""
    machine = _Machine(_pcm(100), scc=_pcm(50), dac=_pcm(25))
    assert machine.fmpac is None
    out = _mix_audio(machine, 0, 100)
    assert array("h", out).tolist() == [175] * SAMPLES_PER_FRAME


def test_opll_contributes_to_mix() -> None:
    """FM-PAC present → its OPLL buffer is summed into the mix."""
    out = _mix_audio(_Machine(_pcm(100), fmpac_opll=_pcm(50)), 0, 100)
    assert array("h", out).tolist() == [150] * SAMPLES_PER_FRAME


# --- allium/frontend.allium: CombineAndFilter (mix + output low-pass) ------
# The clamp/sum tests above never pass an `audio_filter`; the filter tests in
# test_audio_filter.py never go through `_mix_audio`. Neither exercises the
# two wired together, which is what CombineAndFilter's rule actually claims:
# the *combined* signal is filtered, and the filter's state is carried by the
# caller across successive `_mix_audio` calls (not reset per frame).

def test_mix_audio_passes_combined_signal_through_provided_filter() -> None:
    psg = _pcm(20000)
    mix_filter = BiquadLowPass()
    reference_filter = BiquadLowPass()

    out = _mix_audio(_Machine(psg), 0, 100, audio_filter=mix_filter)

    # An independently-constructed filter, fed the same one-shot mixed
    # signal, must reach the same output -- confirms _mix_audio calls
    # `audio_filter.filter(...)` on the already-summed/clamped buffer.
    assert out == reference_filter.filter(psg)
    # And without a filter, the signal is materially different (this cutoff
    # is well below the tone's own frequency content for this raw square/DC
    # level, so filtering must change something).
    assert out != _mix_audio(_Machine(psg), 0, 100)


def test_mix_audio_filter_state_persists_across_successive_frames() -> None:
    psg = _pcm(20000)
    shared_filter = BiquadLowPass()

    first = _mix_audio(_Machine(psg), 0, 100, audio_filter=shared_filter)
    second = _mix_audio(_Machine(psg), 100, 200, audio_filter=shared_filter)

    # A filter driven by the same two calls in isolation reaches the same
    # two outputs (continuity is deterministic)...
    isolated_pair = BiquadLowPass()
    assert first == isolated_pair.filter(psg)
    assert second == isolated_pair.filter(psg)
    # ...but a *fresh* filter given only the second frame's samples, with no
    # carried history, must not match -- proving the mixer's filter state is
    # a continuous analog-style stage across frames, not reset per frame.
    assert second != BiquadLowPass().filter(psg)


def test_mix_audio_filter_applies_after_scc_dac_opll_summed() -> None:
    # Filtering happens on the already-combined signal, not per-source: a
    # filtered multi-source mix must equal filtering the plain unfiltered sum.
    machine = _Machine(_pcm(100), scc=_pcm(50), dac=_pcm(25), fmpac_opll=_pcm(25))
    unfiltered = _mix_audio(machine, 0, 100)
    filtered = _mix_audio(machine, 0, 100, audio_filter=BiquadLowPass())
    assert filtered == BiquadLowPass().filter(unfiltered)


# --- allium/frontend.allium: OpenAudioDevice --------------------------------
# `_init_sdl` implements both CreateHostDisplay (texture/window creation) and
# OpenAudioDevice (device open + no-device fallback); the display half is
# covered by test_sdl2_frontend_display.py. This covers the audio half using
# the same fake-sdl2-module technique as test_sdl2_frontend_hotkeys.py.

class _FakeInitSDL:
    SDL_INIT_VIDEO = 1
    SDL_INIT_AUDIO = 2
    SDL_INIT_JOYSTICK = 4
    SDL_INIT_GAMECONTROLLER = 8
    SDL_WINDOWPOS_CENTERED = 0
    SDL_WINDOW_SHOWN = 0
    SDL_RENDERER_ACCELERATED = 1
    SDL_RENDERER_SOFTWARE = 2
    SDL_PIXELFORMAT_RGB24 = 1
    SDL_TEXTUREACCESS_STREAMING = 1
    AUDIO_S16LSB = 1

    def __init__(self, audio_opens: bool) -> None:
        self._audio_opens = audio_opens
        self.paused_devices: list[tuple[object, int]] = []
        self.audio_spec_args: tuple[object, ...] | None = None
        self.created_texture_size: tuple[int, int] | None = None

    def SDL_Init(self, _flags: int) -> int:
        return 0

    def SDL_CreateWindow(self, *_a: object) -> object:
        return 1

    def SDL_CreateRenderer(self, *_a: object) -> object:
        return 1

    def SDL_SetHint(self, *_a: object) -> None:
        pass

    def SDL_CreateTexture(
        self, _renderer: object, _fmt: int, _access: int, w: int, h: int
    ) -> object:
        self.created_texture_size = (w, h)
        return 1

    def SDL_AudioSpec(self, *args: object) -> tuple[object, ...]:
        self.audio_spec_args = args
        return args

    def SDL_OpenAudioDevice(self, *_a: object) -> int:
        return 1 if self._audio_opens else 0

    def SDL_PauseAudioDevice(self, device: object, pause: int) -> None:
        self.paused_devices.append((device, pause))

    def SDL_GetError(self) -> bytes:
        return b"fake sdl error"


def test_open_audio_device_failure_falls_back_without_audio() -> None:
    sdl = _FakeInitSDL(audio_opens=False)
    window, renderer, texture, audio_dev = _init_sdl(sdl, "title", 768, 636, 256, 212)
    assert audio_dev == 0                 # AudioOutputDevice.available = False
    assert window and renderer and texture  # video/input setup still succeeds
    assert sdl.paused_devices == []        # never unmuted a device that doesn't exist


def test_open_audio_device_success_unmutes_device() -> None:
    sdl = _FakeInitSDL(audio_opens=True)
    _window, _renderer, _texture, audio_dev = _init_sdl(sdl, "title", 768, 636, 256, 212)
    assert audio_dev == 1
    assert sdl.paused_devices == [(1, 0)]


def test_init_sdl_creates_texture_at_requested_size() -> None:
    sdl = _FakeInitSDL(audio_opens=True)
    _init_sdl(sdl, "title", 768, 636, 256, 212)
    assert sdl.created_texture_size == (256, 212)
