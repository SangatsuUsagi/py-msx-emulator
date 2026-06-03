from __future__ import annotations

import ctypes
import datetime
import struct
import sys

from PIL import Image as _PIL_Image

from msx.frame_timer import FrameTimer
from msx.joystick import JoystickManager
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


def _save_screenshot(rgb_buf: bytearray) -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"screenshot_{stamp}.png"
    img = _PIL_Image.frombytes("RGB", (256, 192), bytes(rgb_buf))
    img.save(path)
    print(f"screenshot saved: {path}")


def run(machine: Machine, scale: int = 3, speed: float = 1.0, game_title: str = "py-msx-emulator") -> None:
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    win_w = _W * scale
    win_h = _H * scale

    if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_AUDIO | sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_GAMECONTROLLER) != 0:
        print(f"SDL_Init error: {sdl2.SDL_GetError()}", file=sys.stderr)
        sys.exit(1)

    window = sdl2.SDL_CreateWindow(
        game_title.encode("utf-8"),
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
    desired = sdl2.SDL_AudioSpec(44100, sdl2.AUDIO_S16LSB, 1, 1024)
    audio_dev = sdl2.SDL_OpenAudioDevice(None, 0, desired, None, 0)
    if audio_dev == 0:
        print(f"SDL audio warning: {sdl2.SDL_GetError().decode()} — continuing without audio",
              file=sys.stderr)
    else:
        sdl2.SDL_PauseAudioDevice(audio_dev, 0)

    joy_manager = JoystickManager(_input=machine.input, _sdl=sdl2)

    frame_timer = FrameTimer(fps=60.0, speed=speed)
    event = sdl2.SDL_Event()
    running = True
    _fullscreen = False
    rgb_buf: bytearray = bytearray(_W * _H * 3)

    while running:
        # Process events
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl2.SDL_QUIT:
                running = False
            elif event.type == sdl2.SDL_KEYDOWN:
                if event.key.keysym.sym == sdl2.SDLK_ESCAPE:
                    running = False
                elif event.key.keysym.sym == sdl2.SDLK_F11:
                    _fullscreen = not _fullscreen
                    flag = sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP if _fullscreen else 0
                    if sdl2.SDL_SetWindowFullscreen(window, flag) != 0:
                        print(f"fullscreen toggle failed: {sdl2.SDL_GetError()}", file=sys.stderr)
                elif event.key.keysym.sym == sdl2.SDLK_F10:
                    _save_screenshot(rgb_buf)
                else:
                    machine.input.key_down(event.key.keysym.sym)
            elif event.type == sdl2.SDL_KEYUP:
                machine.input.key_up(event.key.keysym.sym)
            elif event.type in (
                sdl2.SDL_CONTROLLERDEVICEADDED,
                sdl2.SDL_CONTROLLERDEVICEREMOVED,
                sdl2.SDL_JOYDEVICEADDED,
                sdl2.SDL_JOYDEVICEREMOVED,
                sdl2.SDL_CONTROLLERBUTTONDOWN,
                sdl2.SDL_CONTROLLERBUTTONUP,
                sdl2.SDL_CONTROLLERAXISMOTION,
                sdl2.SDL_JOYBUTTONDOWN,
                sdl2.SDL_JOYBUTTONUP,
                sdl2.SDL_JOYAXISMOTION,
                sdl2.SDL_JOYHATMOTION,
            ):
                joy_manager.handle_event(event)

        if not running:
            break

        # Run one frame
        index_buf = machine.run_frame()
        rgb_buf = _index_to_rgb24(index_buf)

        # Generate and queue audio (PSG + SCC mixed if SCC present)
        if audio_dev > 0:
            psg_buf = machine.psg.generate_samples(SAMPLES_PER_FRAME)
            if machine.scc is not None:
                scc_buf = machine.scc.generate_samples(SAMPLES_PER_FRAME)
                mixed = bytearray(len(psg_buf))
                for i in range(SAMPLES_PER_FRAME):
                    offset = i * 2
                    psg_s = struct.unpack_from("<h", psg_buf, offset)[0]
                    scc_s = struct.unpack_from("<h", scc_buf, offset)[0]
                    total = psg_s + scc_s
                    if total > 32767:
                        total = 32767
                    elif total < -32768:
                        total = -32768
                    struct.pack_into("<h", mixed, offset, total)
                audio_buf = mixed
            else:
                audio_buf = psg_buf
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
            title = f"{game_title}  [{frame_timer.fps_measured:.0f} fps]".encode("utf-8")
            sdl2.SDL_SetWindowTitle(window, title)

    joy_manager.close_all()
    if audio_dev > 0:
        sdl2.SDL_CloseAudioDevice(audio_dev)
    sdl2.SDL_DestroyTexture(texture)
    sdl2.SDL_DestroyRenderer(renderer)
    sdl2.SDL_DestroyWindow(window)
    sdl2.SDL_Quit()
