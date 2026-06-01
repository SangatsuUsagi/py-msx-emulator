from __future__ import annotations

import ctypes
import sys

from msx.frame_timer import FrameTimer
from msx.machine import Machine
from msx.psg import SAMPLES_PER_FRAME

# Standard TMS9918A hardware palette — 16 (R, G, B) triples.
# Index 0 = transparent (rendered as black).
TMS9918A_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0,   0,   0),    # 0  transparent / black
    (0,   0,   0),    # 1  black
    (33,  200, 66),   # 2  medium green
    (94,  220, 120),  # 3  light green
    (84,  85,  237),  # 4  dark blue
    (125, 118, 252),  # 5  light blue
    (212, 82,  77),   # 6  dark red
    (66,  235, 245),  # 7  cyan
    (252, 85,  84),   # 8  medium red
    (255, 121, 120),  # 9  light red
    (212, 193, 84),   # 10 dark yellow
    (230, 206, 128),  # 11 light yellow
    (33,  176, 59),   # 12 dark green
    (201, 91,  186),  # 13 magenta
    (204, 204, 204),  # 14 grey
    (255, 255, 255),  # 15 white
)

_W = 256
_H = 192


def _index_to_rgb24(src: bytearray) -> bytearray:
    dst = bytearray(len(src) * 3)
    for i in range(len(src)):
        r, g, b = TMS9918A_PALETTE[src[i] & 0x0F]
        dst[i * 3]     = r
        dst[i * 3 + 1] = g
        dst[i * 3 + 2] = b
    return dst


def run(machine: Machine, scale: int = 3, speed: float = 1.0) -> None:
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    win_w = _W * scale
    win_h = _H * scale

    if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_AUDIO) != 0:
        print(f"SDL_Init error: {sdl2.SDL_GetError()}", file=sys.stderr)
        sys.exit(1)

    window = sdl2.SDL_CreateWindow(
        b"py-msx-emulator",
        sdl2.SDL_WINDOWPOS_CENTERED,
        sdl2.SDL_WINDOWPOS_CENTERED,
        win_w,
        win_h,
        sdl2.SDL_WINDOW_SHOWN,
    )
    if not window:
        print(f"SDL_CreateWindow error: {sdl2.SDL_GetError()}", file=sys.stderr)
        sdl2.SDL_Quit()
        sys.exit(1)

    renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_ACCELERATED)
    if not renderer:
        renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_SOFTWARE)

    texture = sdl2.SDL_CreateTexture(
        renderer,
        sdl2.SDL_PIXELFORMAT_RGB24,
        sdl2.SDL_TEXTUREACCESS_STREAMING,
        _W,
        _H,
    )

    # Open SDL2 audio device (mono, 44100 Hz, signed 16-bit LE).
    # Fall back gracefully if unavailable — video and input remain functional.
    audio_dev = 0
    desired = sdl2.SDL_AudioSpec()
    desired.freq = 44100
    desired.format = sdl2.AUDIO_S16LSB
    desired.channels = 1
    desired.samples = 1024
    desired.callback = None
    desired.userdata = None
    audio_dev = sdl2.SDL_OpenAudioDevice(None, 0, desired, None, 0)
    if audio_dev == 0:
        print(f"SDL audio warning: {sdl2.SDL_GetError().decode()} — continuing without audio",
              file=sys.stderr)
    else:
        sdl2.SDL_PauseAudioDevice(audio_dev, 0)

    frame_timer = FrameTimer(fps=60.0, speed=speed)
    event = sdl2.SDL_Event()
    running = True

    while running:
        # Process events
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl2.SDL_QUIT:
                running = False
            elif event.type == sdl2.SDL_KEYDOWN:
                if event.key.keysym.sym == sdl2.SDLK_ESCAPE:
                    running = False
                machine.input.key_down(event.key.keysym.sym)
            elif event.type == sdl2.SDL_KEYUP:
                machine.input.key_up(event.key.keysym.sym)

        if not running:
            break

        # Run one frame
        index_buf = machine.run_frame()
        rgb_buf = _index_to_rgb24(index_buf)

        # Generate and queue audio
        if audio_dev > 0:
            audio_buf = machine.psg.generate_samples(SAMPLES_PER_FRAME)
            sdl2.SDL_QueueAudio(audio_dev, bytes(audio_buf), len(audio_buf))

        # Upload to texture
        pixels_ptr = ctypes.c_void_p()
        pitch = ctypes.c_int()
        sdl2.SDL_LockTexture(texture, None, ctypes.byref(pixels_ptr), ctypes.byref(pitch))
        ctypes.memmove(pixels_ptr, bytes(rgb_buf), len(rgb_buf))
        sdl2.SDL_UnlockTexture(texture)

        # Render
        sdl2.SDL_RenderClear(renderer)
        sdl2.SDL_RenderCopy(renderer, texture, None, None)
        sdl2.SDL_RenderPresent(renderer)

        # Frame pacing
        frame_timer.tick()
        if frame_timer.fps_measured > 0:
            title = f"py-msx-emulator  [{frame_timer.fps_measured:.0f} fps]".encode()
            sdl2.SDL_SetWindowTitle(window, title)

    if audio_dev > 0:
        sdl2.SDL_CloseAudioDevice(audio_dev)
    sdl2.SDL_DestroyTexture(texture)
    sdl2.SDL_DestroyRenderer(renderer)
    sdl2.SDL_DestroyWindow(window)
    sdl2.SDL_Quit()
