"""Tests for the SDL2 frontend audio device setup.

The per-frame mix moved to the core (tests/test_audio_mixer.py,
tests/test_machine_audio.py); queueing, mute and fast-forward are driven
through _run_frame in tests/test_sdl2_frontend_hotkeys.py.
"""
from frontend.sdl2_frontend import _init_sdl
from msx.psg import SAMPLE_RATE

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


def test_audio_device_opens_at_the_core_mix_rate() -> None:
    sdl = _FakeInitSDL(audio_opens=True)
    _init_sdl(sdl, "title", 768, 636, 256, 212)
    assert sdl.audio_spec_args is not None
    assert sdl.audio_spec_args[0] == SAMPLE_RATE
