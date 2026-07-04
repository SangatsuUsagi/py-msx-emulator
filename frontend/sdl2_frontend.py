from __future__ import annotations

import ctypes
import datetime
import sys
from array import array
from pathlib import Path

from PIL import Image as _PIL_Image

from msx.frame_timer import FrameTimer
from msx.joystick import JoystickManager
from msx.machine import Machine
from msx.psg import SAMPLES_PER_FRAME
from msx.state import load_state, save_state
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import _INTENSITY3, _build_bands, grb332_to_rgb

# Precomputed SCREEN 8 GRB332 → 3-byte RGB table (256 entries), for fast join.
_GRB332_BYTES: tuple[bytes, ...] = tuple(bytes(grb332_to_rgb(b)) for b in range(256))

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
_MAX_FRAME_SKIP: int = 4

# TMS9918A palette as 3-byte RGB entries (for the MSX1 join path).
_TMS9918A_BYTES: tuple[bytes, ...] = tuple(bytes(c) for c in TMS9918A_PALETTE)


def _make_lut16(palette: list) -> list:
    """Build a 16-entry RGB24 bytes LUT from a 9-bit RGB333 palette."""
    return [
        bytes((_INTENSITY3[(p >> 6) & 7], _INTENSITY3[(p >> 3) & 7], _INTENSITY3[p & 7]))
        for p in palette[:16]
    ]


# Cache the 16-entry RGB24 LUT keyed on a snapshot of the palette; the palette
# only changes when the guest reprograms it, so this avoids rebuilding the LUT
# on every frame.
_LUT16_CACHE_KEY: tuple | None = None
_LUT16_CACHE: list | None = None


def _cached_lut16(palette: list) -> list:
    """Return _make_lut16(palette), rebuilding only when the palette changed."""
    global _LUT16_CACHE_KEY, _LUT16_CACHE
    key = tuple(palette[:16])
    if key != _LUT16_CACHE_KEY:
        _LUT16_CACHE_KEY = key
        _LUT16_CACHE = _make_lut16(palette)
    return _LUT16_CACHE


def _index_to_rgb24(src: bytearray, vdp: object) -> bytes:
    """Map a palette-index (or SCREEN 8 GRB332) buffer to packed RGB24.

    For V9938 indexed modes with mid-frame palette changes, applies each
    band's palette snapshot to the correct scanlines so that colours written
    via H-sync interrupt are rendered at the right lines rather than using the
    end-of-frame palette for the whole frame.
    """
    if isinstance(vdp, V9938):
        r0 = vdp.regs[0]
        is_g7 = bool((r0 >> 2) & 1) and bool((r0 >> 3) & 1)  # M4+M5 = SCREEN 8
        if is_g7:
            lut = _GRB332_BYTES
            return b"".join([lut[x] for x in src])

        # Check for mid-frame palette changes in the log.  _reg_write_log is
        # still valid here: it is cleared by begin_scanline(0) at the *next*
        # frame, not at the end of this one.
        has_palette_change = any(entry[1] == -1 for entry in vdp._reg_write_log)
        if has_palette_change:
            return _v9938_banded_to_rgb24(src, vdp)

        lut16 = _cached_lut16(vdp.palette)
        return b"".join([lut16[x & 0x0F] for x in src])
    lut16 = _TMS9918A_BYTES
    return b"".join([lut16[x & 0x0F] for x in src])


def _v9938_banded_to_rgb24(src: bytearray, vdp: "V9938") -> bytes:
    """Per-band palette→RGB24 conversion for mid-frame palette changes.

    Reuses _build_bands() (which reads _reg_write_log / _frame_start_palette)
    to determine which palette was active on each scanline, then applies the
    correct 16-entry LUT per output line.  Display-adjust vertical offset
    (R#18 high nibble) is accounted for: output line dy came from source line
    dy - v_off, whose band determines the palette.
    """
    h = vdp.display_height
    w = len(src) // h if h else _W

    reg18 = vdp.regs[18]
    v_off = ((reg18 >> 4) ^ 0x07) - 7   # signed line shift from R#18

    bands = _build_bands(vdp)

    # Build a per-source-line LUT table (fall back to frame-start palette for
    # source lines outside any band, e.g. the border area after v_off shift).
    default_lut = _make_lut16(vdp._frame_start_palette)
    line_lut: list = [default_lut] * h
    for _y0, _y1, _band_regs, band_palette in bands:
        lut16 = _make_lut16(band_palette)
        for sy in range(max(0, _y0), min(h, _y1)):
            line_lut[sy] = lut16

    parts: list[bytes] = []
    for dy in range(h):
        sy = dy - v_off
        lut16 = line_lut[sy] if 0 <= sy < h else default_lut
        row_start = dy * w
        parts.append(b"".join(lut16[b & 0x0F] for b in src[row_start:row_start + w]))
    return b"".join(parts)


def _save_screenshot(rgb_buf: bytearray, w: int, h: int) -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"screenshot_{stamp}.png"
    img = _PIL_Image.frombytes("RGB", (w, h), bytes(rgb_buf))
    img.save(path)
    print(f"screenshot saved: {path}")


def run(
    machine: Machine,
    scale: int = 3,
    speed: float = 1.0,
    game_title: str = "py-msx-emulator",
    resume: str | None = None,
    frame_skip: str = "auto",
) -> None:
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    h = machine.vdp.display_height
    win_w = _W * scale
    win_h = h * scale

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

    # Linear filtering so 512-wide SCREEN 6/7 textures downscale smoothly to 256*scale window.
    sdl2.SDL_SetHint(b"SDL_RENDER_SCALE_QUALITY", b"1")

    tex_w, tex_h = _W, h
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
    desired = sdl2.SDL_AudioSpec(44100, sdl2.AUDIO_S16LSB, 1, 1024)
    audio_dev = sdl2.SDL_OpenAudioDevice(None, 0, desired, None, 0)
    if audio_dev == 0:
        print(f"SDL audio warning: {sdl2.SDL_GetError().decode()} — continuing without audio",
              file=sys.stderr)
    else:
        sdl2.SDL_PauseAudioDevice(audio_dev, 0)

    joy_manager = JoystickManager(_input=machine.input, _sdl=sdl2)

    if resume is not None:
        try:
            load_state(machine, path=Path(resume) if resume else None)
        except Exception as exc:
            print(f"resume failed: {exc}", file=sys.stderr)

    frame_timer = FrameTimer(fps=60.0, speed=speed)
    event = sdl2.SDL_Event()
    running = True
    _fullscreen = False
    rgb_buf: bytearray = bytearray(_W * h * 3)
    _skip_counter: int = 0

    try:
        while running:
            try:
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
                            _save_screenshot(rgb_buf, tex_w, tex_h)
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

                if not running:
                    break

                joy_manager.tick()

                # Run one frame (skip VDP pixel rendering when behind schedule)
                skip_this_frame = _skip_counter > 0
                frame_start_cycle = machine.cycle_count
                index_buf = machine.run_frame(skip_render=skip_this_frame)
                frame_end_cycle = machine.cycle_count
                if not skip_this_frame:
                    rgb_buf = _index_to_rgb24(index_buf, machine.vdp)
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
                        win_display_w = _W if tex_w > _W else tex_w
                        sdl2.SDL_SetWindowSize(window, win_display_w * scale, tex_h * scale)

                # Generate and queue audio (PSG + SCC + DAC mixed as present) — always runs
                if audio_dev > 0:
                    psg_buf = machine.psg.generate_samples(SAMPLES_PER_FRAME)
                    extra_bufs = []
                    if machine.scc is not None:
                        extra_bufs.append(machine.scc.generate_samples(SAMPLES_PER_FRAME))
                    if machine.dac is not None:
                        extra_bufs.append(
                            machine.dac.generate_samples(
                                SAMPLES_PER_FRAME, frame_start_cycle, frame_end_cycle
                            )
                        )
                    if extra_bufs:
                        # Batch-decode each PCM buffer to signed 16-bit samples
                        # via array("h") (one C call per buffer) instead of a
                        # per-sample struct.unpack_from/pack_into. Sums are taken
                        # in Python ints so the clamp applies to the full mix,
                        # then re-encoded once. (array("h") is native byte order,
                        # which matches the little-endian PCM on LE hosts.)
                        psg_arr = array("h")
                        psg_arr.frombytes(bytes(psg_buf))
                        extra_arrs = []
                        for buf in extra_bufs:
                            a = array("h")
                            a.frombytes(bytes(buf))
                            extra_arrs.append(a)
                        out_arr = array("h", bytes(2 * SAMPLES_PER_FRAME))
                        for i in range(SAMPLES_PER_FRAME):
                            total = psg_arr[i]
                            for a in extra_arrs:
                                total += a[i]
                            if total > 32767:
                                total = 32767
                            elif total < -32768:
                                total = -32768
                            out_arr[i] = total
                        audio_buf = out_arr.tobytes()
                    else:
                        audio_buf = psg_buf
                    sdl2.SDL_QueueAudio(audio_dev, bytes(audio_buf), len(audio_buf))

                # Upload to texture only when frame was rendered
                if not skip_this_frame:
                    pixels_ptr = ctypes.c_void_p()
                    pitch = ctypes.c_int()
                    if sdl2.SDL_LockTexture(
                        texture, None, ctypes.byref(pixels_ptr), ctypes.byref(pitch)
                    ) != 0:
                        # Transient failure: skip this frame's pixel upload rather
                        # than writing through an invalid pointer.
                        print(f"SDL_LockTexture failed: {sdl2.SDL_GetError()}", file=sys.stderr)
                    else:
                        # Honour the destination row stride SDL returns. When the
                        # texture rows are tightly packed (pitch == width*3, RGB24),
                        # a single contiguous memmove is correct and fastest.
                        # Otherwise (driver row padding, or a future width) copy
                        # row-by-row: width*3 source bytes into each pitch-strided
                        # destination row, so no source overrun and no padding is
                        # overwritten with pixel data.
                        row_bytes = tex_w * 3
                        dst_pitch = pitch.value
                        if dst_pitch == row_bytes:
                            # rgb_buf is already an immutable bytes from
                            # _index_to_rgb24; memmove accepts it directly.
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

                # Render (always — redisplays previous texture on skipped frames)
                sdl2.SDL_RenderClear(renderer)
                sdl2.SDL_RenderCopy(renderer, texture, None, None)
                sdl2.SDL_RenderPresent(renderer)

                # Frame pacing + skip counter update
                elapsed = frame_timer.tick()
                if frame_skip == "auto":
                    if elapsed > frame_timer._frame_interval * 1.05:
                        _skip_counter = min(_skip_counter + 1, _MAX_FRAME_SKIP)
                    else:
                        _skip_counter = max(_skip_counter - 1, 0)

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
