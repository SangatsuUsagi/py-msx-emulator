from __future__ import annotations

import ctypes
import sys
from array import array
from pathlib import Path
from typing import Any

from msx.audio_filter import BiquadLowPass
from msx.frame_timer import FrameTimer
from msx.joystick import JoystickManager
from msx.machine import Machine
from msx.psg import SAMPLES_PER_FRAME
from msx.screenshot import save_screenshot
from msx.state import load_state, save_state

_SCREEN_WIDTH = 256
_MAX_FRAME_SKIP: int = 4

# Audio output format. The sample rate must match what msx.psg's
# SAMPLES_PER_FRAME assumes (samples generated per emulated frame).
_AUDIO_SAMPLE_RATE: int = 44100
_AUDIO_CHANNELS: int = 1
_AUDIO_BUFFER_SAMPLES: int = 1024
_S16_MAX: int = 32767   # signed 16-bit sample clamp range
_S16_MIN: int = -32768

# Auto frame-skip: bump the skip counter when a frame overruns its budget by
# more than this ratio (5% slack).
_FRAME_OVERRUN_RATIO: float = 1.05


def _init_sdl(
    sdl2: Any, game_title: str, win_w: int, win_h: int, tex_w: int, tex_h: int
) -> tuple[Any, Any, Any, int]:
    """Initialize SDL video/audio and create the window, renderer, streaming
    texture, and audio device. Exits the process on a fatal SDL error.

    Returns (window, renderer, texture, audio_dev); audio_dev is 0 when audio
    could not be opened (video and input still work).
    """
    init_flags = (
        sdl2.SDL_INIT_VIDEO
        | sdl2.SDL_INIT_AUDIO
        | sdl2.SDL_INIT_JOYSTICK
        | sdl2.SDL_INIT_GAMECONTROLLER
    )
    if sdl2.SDL_Init(init_flags) != 0:
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
    if not renderer:
        print(f"SDL_CreateRenderer error: {sdl2.SDL_GetError()}", file=sys.stderr)
        sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
        sys.exit(1)

    # Linear filtering so 512-wide SCREEN 6/7 textures downscale smoothly to the
    # 256*scale window.
    sdl2.SDL_SetHint(b"SDL_RENDER_SCALE_QUALITY", b"1")

    texture = sdl2.SDL_CreateTexture(
        renderer,
        sdl2.SDL_PIXELFORMAT_RGB24,
        sdl2.SDL_TEXTUREACCESS_STREAMING,
        tex_w,
        tex_h,
    )
    if not texture:
        print(f"SDL_CreateTexture error: {sdl2.SDL_GetError()}", file=sys.stderr)
        sdl2.SDL_DestroyRenderer(renderer)
        sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
        sys.exit(1)

    # Open SDL2 audio device (mono, 44100 Hz, signed 16-bit LE).
    # Fall back gracefully if unavailable — video and input remain functional.
    desired = sdl2.SDL_AudioSpec(
        _AUDIO_SAMPLE_RATE, sdl2.AUDIO_S16LSB, _AUDIO_CHANNELS, _AUDIO_BUFFER_SAMPLES
    )
    audio_dev = sdl2.SDL_OpenAudioDevice(None, 0, desired, None, 0)
    if audio_dev == 0:
        print(f"SDL audio warning: {sdl2.SDL_GetError().decode()} — continuing without audio",
              file=sys.stderr)
    else:
        sdl2.SDL_PauseAudioDevice(audio_dev, 0)

    return window, renderer, texture, audio_dev


def _handle_events(
    sdl2: Any, event: Any, machine: Machine, window: Any, joy_manager: JoystickManager,
    game_title: str, rgb_buf: bytes, tex_w: int, tex_h: int, fullscreen: bool,
) -> tuple[bool, bool]:
    """Drain the SDL event queue, applying input and hotkeys.

    Returns (running, fullscreen): running is False once a quit is requested;
    fullscreen is the (possibly toggled) window state. rgb_buf/tex_w/tex_h are
    the previous frame's, used by F8 (save state) and F10 (screenshot).
    """
    running = True
    while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
        if event.type == sdl2.SDL_QUIT:
            running = False
        elif event.type == sdl2.SDL_KEYDOWN:
            if event.key.keysym.sym == sdl2.SDLK_ESCAPE:
                running = False
            elif event.key.keysym.sym == sdl2.SDLK_F11:
                fullscreen = not fullscreen
                flag = sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP if fullscreen else 0
                if sdl2.SDL_SetWindowFullscreen(window, flag) != 0:
                    print(
                        f"fullscreen toggle failed: {sdl2.SDL_GetError()}",
                        file=sys.stderr,
                    )
            elif event.key.keysym.sym == sdl2.SDLK_F8:
                save_state(machine, rgb_buf, game_title)
            elif event.key.keysym.sym == sdl2.SDLK_F9:
                try:
                    load_state(machine)
                except Exception as exc:
                    print(f"load failed: {exc}", file=sys.stderr)
            elif event.key.keysym.sym == sdl2.SDLK_F10:
                save_screenshot(rgb_buf, tex_w, tex_h)
            elif (event.key.keysym.sym == sdl2.SDLK_c
                  and (event.key.keysym.mod & sdl2.KMOD_CTRL)
                  and machine._debugger is not None):
                machine._debugger.enter()
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
    return running, fullscreen


def _mix_audio(
    machine: Machine,
    frame_start_cycle: int,
    frame_end_cycle: int,
    audio_filter: BiquadLowPass | None = None,
) -> bytes:
    """Generate one frame of audio: PSG plus SCC/DAC when present, mixed and
    clamped to signed 16-bit. When `audio_filter` is given, the final mixed
    buffer is passed through it (modelling the analog output low-pass) before
    being returned. Returns the PCM bytes to queue."""
    psg_buf = machine.psg.generate_samples(
        SAMPLES_PER_FRAME, frame_start_cycle, frame_end_cycle
    )
    extra_bufs = []
    if machine.scc is not None:
        extra_bufs.append(machine.scc.generate_samples(SAMPLES_PER_FRAME))
    if machine.dac is not None:
        extra_bufs.append(
            machine.dac.generate_samples(SAMPLES_PER_FRAME, frame_start_cycle, frame_end_cycle)
        )
    if not extra_bufs:
        return audio_filter.filter(bytes(psg_buf)) if audio_filter else bytes(psg_buf)

    # Batch-decode each PCM buffer to signed 16-bit samples via array("h") (one
    # C call per buffer) instead of a per-sample struct.unpack_from/pack_into.
    # Sums are taken in Python ints so the clamp applies to the full mix, then
    # re-encoded once. (array("h") is native byte order, matching the LE PCM on
    # LE hosts.)
    psg_arr = array("h")
    psg_arr.frombytes(psg_buf)
    extra_arrs = []
    for buf in extra_bufs:
        chan_arr = array("h")
        chan_arr.frombytes(buf)
        extra_arrs.append(chan_arr)
    out_arr = array("h", bytes(2 * SAMPLES_PER_FRAME))
    for i in range(SAMPLES_PER_FRAME):
        total = psg_arr[i]
        for chan_arr in extra_arrs:
            total += chan_arr[i]
        if total > _S16_MAX:
            total = _S16_MAX
        elif total < _S16_MIN:
            total = _S16_MIN
        out_arr[i] = total
    mixed = out_arr.tobytes()
    return audio_filter.filter(mixed) if audio_filter else mixed


def _upload_to_texture(
    sdl2: Any, texture: Any, rgb_buf: bytes, tex_w: int, tex_h: int
) -> None:
    """Copy the RGB24 frame buffer into the streaming texture, honouring the
    destination row stride SDL reports."""
    pixels_ptr = ctypes.c_void_p()
    pitch = ctypes.c_int()
    if sdl2.SDL_LockTexture(
        texture, None, ctypes.byref(pixels_ptr), ctypes.byref(pitch)
    ) != 0:
        # Transient failure: skip this frame's pixel upload rather than writing
        # through an invalid pointer.
        print(f"SDL_LockTexture failed: {sdl2.SDL_GetError()}", file=sys.stderr)
        return
    # When the texture rows are tightly packed (pitch == width*3, RGB24) a single
    # contiguous memmove is correct and fastest. Otherwise (driver row padding)
    # copy row-by-row: width*3 source bytes into each pitch-strided destination
    # row, so no source overrun and no padding is overwritten with pixel data.
    row_bytes = tex_w * 3
    dst_pitch = pitch.value
    if dst_pitch == row_bytes:
        ctypes.memmove(pixels_ptr, rgb_buf, len(rgb_buf))
    else:
        dst_base = pixels_ptr.value
        assert dst_base is not None
        for row in range(tex_h):
            src_off = row * row_bytes
            ctypes.memmove(
                dst_base + row * dst_pitch,
                rgb_buf[src_off:src_off + row_bytes],
                row_bytes,
            )
    sdl2.SDL_UnlockTexture(texture)


def run(
    machine: Machine,
    scale: int = 3,
    speed: float = 1.0,
    game_title: str = "py-msx-emulator",
    resume: str | None = None,
    frame_skip: str = "auto",
) -> None:
    """Run the SDL2 window loop for `machine` until the user quits.

    Args:
        machine: the emulated MSX machine to drive.
        scale: integer window scale factor over the 256-wide base resolution.
        speed: emulation speed multiplier (1.0 = real time).
        game_title: window title.
        resume: save-state path to load at startup; "" loads the default slot;
            None starts fresh.
        frame_skip: "auto" adapts the skip counter to frame overruns; any other
            value disables frame skipping.

    Runtime hotkeys: ESC quit, F8 save state, F9 load state, F10 screenshot,
    F11 toggle fullscreen, Ctrl-C break into the debugger (if attached).
    """
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    h = machine.vdp.display_height
    win_w = _SCREEN_WIDTH * scale
    win_h = h * scale

    tex_w, tex_h = _SCREEN_WIDTH, h
    window, renderer, texture, audio_dev = _init_sdl(
        sdl2, game_title, win_w, win_h, tex_w, tex_h
    )

    # Analog-style output low-pass; one persistent instance so IIR state carries
    # across frames (it starts from the clean state __init__ already zeroes).
    audio_filter = BiquadLowPass()

    joy_manager = JoystickManager(_input=machine.input, _sdl=sdl2)

    if resume is not None:
        try:
            load_state(machine, path=Path(resume) if resume else None)
        except Exception as exc:
            print(f"resume failed: {exc}", file=sys.stderr)

    frame_timer = FrameTimer(fps=60.0, speed=speed)
    event = sdl2.SDL_Event()
    running = True
    fullscreen = False
    rgb_buf: bytes = bytes(_SCREEN_WIDTH * h * 3)
    skip_counter: int = 0

    try:
        while running:
            try:
                # Process events (input + hotkeys); updates running/fullscreen.
                running, fullscreen = _handle_events(
                    sdl2, event, machine, window, joy_manager,
                    game_title, rgb_buf, tex_w, tex_h, fullscreen,
                )

                if not running:
                    break

                joy_manager.tick()

                # Run one frame (skip VDP pixel rendering when behind schedule)
                skip_this_frame = skip_counter > 0
                frame_start_cycle = machine.cycle_count
                index_buf = machine.run_frame(skip_render=skip_this_frame)
                frame_end_cycle = machine.cycle_count
                if not skip_this_frame:
                    rgb_buf = machine.vdp.to_rgb24(index_buf)
                    # The VDP resolution can change at runtime (R#9 LN: 192↔212;
                    # SCREEN 6/7 width). Recreate the texture and resize the window
                    # to match before uploading, or the texture copy would overflow.
                    new_h = machine.vdp.display_height
                    new_w = (len(index_buf) // new_h) if new_h else tex_w
                    if (new_w, new_h) != (tex_w, tex_h):
                        tex_w, tex_h = new_w, new_h
                        sdl2.SDL_DestroyTexture(texture)
                        texture = sdl2.SDL_CreateTexture(
                            renderer,
                            sdl2.SDL_PIXELFORMAT_RGB24,
                            sdl2.SDL_TEXTUREACCESS_STREAMING,
                            tex_w,
                            tex_h,
                        )
                        if not texture:
                            print(f"SDL_CreateTexture error: {sdl2.SDL_GetError()}",
                                  file=sys.stderr)
                            running = False
                            break
                        # 512-wide modes (SCREEN 6/7) display at 256*scale to keep
                        # aspect ratio; SDL scales the texture down via bilinear filter.
                        win_display_w = _SCREEN_WIDTH if tex_w > _SCREEN_WIDTH else tex_w
                        sdl2.SDL_SetWindowSize(window, win_display_w * scale, tex_h * scale)

                # Generate and queue audio (PSG + SCC + DAC mixed as present) — always runs
                if audio_dev > 0:
                    audio_buf = _mix_audio(
                        machine, frame_start_cycle, frame_end_cycle, audio_filter
                    )
                    sdl2.SDL_QueueAudio(audio_dev, audio_buf, len(audio_buf))

                # Upload to texture only when frame was rendered
                if not skip_this_frame:
                    _upload_to_texture(sdl2, texture, rgb_buf, tex_w, tex_h)

                # Render (always — redisplays previous texture on skipped frames)
                sdl2.SDL_RenderClear(renderer)
                sdl2.SDL_RenderCopy(renderer, texture, None, None)
                sdl2.SDL_RenderPresent(renderer)

                # Frame pacing + skip counter update
                elapsed = frame_timer.tick()
                if frame_skip == "auto":
                    if elapsed > frame_timer._frame_interval * _FRAME_OVERRUN_RATIO:
                        skip_counter = min(skip_counter + 1, _MAX_FRAME_SKIP)
                    else:
                        skip_counter = max(skip_counter - 1, 0)

                if frame_timer.fps_measured > 0:
                    title = f"{game_title}  [{frame_timer.fps_measured:.0f} fps]".encode("utf-8")
                    sdl2.SDL_SetWindowTitle(window, title)

            except KeyboardInterrupt:
                if machine._debugger is not None:
                    machine._debugger.enter()
                else:
                    running = False

    finally:
        joy_manager.close_all()
        if audio_dev > 0:
            sdl2.SDL_CloseAudioDevice(audio_dev)
        if texture:
            sdl2.SDL_DestroyTexture(texture)
        if renderer:
            sdl2.SDL_DestroyRenderer(renderer)
        if window:
            sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
