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
from msx.vdp.v9938 import V9938, _PaletteChange
from msx.vdp.v9938_renderer import _INTENSITY3, _build_bands, grb332_to_rgb


def _translate_rgb24(src: bytearray, channels: tuple[bytes, bytes, bytes]) -> bytes:
    """Map an 8-bit-index buffer to packed RGB24 via per-channel bytes.translate.

    Each of the three 256-byte tables maps a source byte to one output channel;
    the strided slice assignment interleaves them. This replaces a per-pixel
    Python loop (``b"".join([lut[x] for x in src])``) with three C-level
    translate calls, ~5-8x faster per frame with byte-identical output.
    """
    rtab, gtab, btab = channels
    out = bytearray(len(src) * 3)
    out[0::3] = src.translate(rtab)
    out[1::3] = src.translate(gtab)
    out[2::3] = src.translate(btab)
    return bytes(out)


def _channel_tables_indexed(lut16: list[bytes]) -> tuple[bytes, bytes, bytes]:
    """Three 256-byte (R, G, B) translate tables for a 16-entry RGB24 LUT.

    Table index i maps through (i & 0x0F), folding the 4-bit palette-index mask
    into the table so bytes.translate needs no separate masking step.
    """
    return (
        bytes(lut16[i & 0x0F][0] for i in range(256)),
        bytes(lut16[i & 0x0F][1] for i in range(256)),
        bytes(lut16[i & 0x0F][2] for i in range(256)),
    )


# Precomputed SCREEN 8 GRB332 → 3-byte RGB table (256 entries), for fast join.
_GRB332_BYTES: tuple[bytes, ...] = tuple(bytes(grb332_to_rgb(b)) for b in range(256))

# Per-channel translate tables for the SCREEN 8 (GRB332) path — constant, so
# they are built once here (each source byte is a full pixel, no index mask).
_GRB332_CHANNELS: tuple[bytes, bytes, bytes] = (
    bytes(t[0] for t in _GRB332_BYTES),
    bytes(t[1] for t in _GRB332_BYTES),
    bytes(t[2] for t in _GRB332_BYTES),
)

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

# TMS9918A palette as 3-byte RGB entries (for the MSX1 path).
_TMS9918A_BYTES: tuple[bytes, ...] = tuple(bytes(c) for c in TMS9918A_PALETTE)
# Per-channel translate tables for the MSX1 (TMS9918A) path — constant.
_TMS9918A_CHANNELS: tuple[bytes, bytes, bytes] = _channel_tables_indexed(list(_TMS9918A_BYTES))


def _make_lut16(palette: list[int]) -> list[bytes]:
    """Build a 16-entry RGB24 bytes LUT from a 9-bit RGB333 palette."""
    return [
        bytes((_INTENSITY3[(p >> 6) & 7], _INTENSITY3[(p >> 3) & 7], _INTENSITY3[p & 7]))
        for p in palette[:16]
    ]


# Cache the 16-entry LUT and its (R, G, B) translate tables keyed on a snapshot
# of the palette; the palette only changes when the guest reprograms it, so
# this avoids rebuilding them every frame.
_LUT16_CACHE_KEY: tuple[int, ...] = ()
_LUT16_CACHE: list[bytes] = _make_lut16([0] * 16)
_CHANNELS_CACHE: tuple[bytes, bytes, bytes] = _channel_tables_indexed(_LUT16_CACHE)


def _refresh_palette_cache(palette: list[int]) -> None:
    global _LUT16_CACHE_KEY, _LUT16_CACHE, _CHANNELS_CACHE
    key = tuple(palette[:16])
    if key != _LUT16_CACHE_KEY:
        _LUT16_CACHE_KEY = key
        _LUT16_CACHE = _make_lut16(palette)
        _CHANNELS_CACHE = _channel_tables_indexed(_LUT16_CACHE)


def _cached_lut16(palette: list[int]) -> list[bytes]:
    """Return _make_lut16(palette), rebuilding only when the palette changed."""
    _refresh_palette_cache(palette)
    return _LUT16_CACHE


def _cached_channels16(palette: list[int]) -> tuple[bytes, bytes, bytes]:
    """Return the (R, G, B) translate tables for the palette (cached with the LUT)."""
    _refresh_palette_cache(palette)
    return _CHANNELS_CACHE


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
            return _translate_rgb24(src, _GRB332_CHANNELS)

        # Check for mid-frame palette changes in the log.  _reg_write_log is
        # still valid here: it is cleared by begin_scanline(0) at the *next*
        # frame, not at the end of this one.
        has_palette_change = any(isinstance(e, _PaletteChange) for e in vdp._reg_write_log)
        if has_palette_change:
            return _v9938_banded_to_rgb24(src, vdp)

        return _translate_rgb24(src, _cached_channels16(vdp.palette))
    return _translate_rgb24(src, _TMS9918A_CHANNELS)


def _v9938_banded_to_rgb24(src: bytearray, vdp: "V9938") -> bytes:
    """Per-band palette→RGB24 conversion for mid-frame palette changes.

    Reuses _build_bands() (which reads _reg_write_log / _frame_start_palette)
    to determine which palette was active on each scanline, then applies the
    correct 16-entry LUT per output line.  Display-adjust vertical offset
    (R#18 high nibble) is accounted for: output line dy came from source line
    dy - v_off, whose band determines the palette.
    """
    h = vdp.display_height
    w = len(src) // h if h else _SCREEN_WIDTH

    reg18 = vdp.regs[18]
    v_off = ((reg18 >> 4) ^ 0x07) - 7   # signed line shift from R#18

    bands = _build_bands(vdp)

    # Build a per-source-line channel-table triple (fall back to the frame-start
    # palette for source lines outside any band, e.g. the border area after the
    # v_off shift). Tables are built once per band, not per row/pixel.
    default_channels = _channel_tables_indexed(_make_lut16(vdp._frame_start_palette))
    line_channels: list[tuple[bytes, bytes, bytes]] = [default_channels] * h
    for _y0, _y1, _band_regs, band_palette in bands:
        channels = _channel_tables_indexed(_make_lut16(band_palette))
        for sy in range(max(0, _y0), min(h, _y1)):
            line_channels[sy] = channels

    out = bytearray(w * h * 3)
    for dy in range(h):
        sy = dy - v_off
        rtab, gtab, btab = line_channels[sy] if 0 <= sy < h else default_channels
        row = src[dy * w:dy * w + w]
        o = dy * w * 3
        end = o + w * 3
        out[o:end:3] = row.translate(rtab)
        out[o + 1:end:3] = row.translate(gtab)
        out[o + 2:end:3] = row.translate(btab)
    return bytes(out)


def _save_screenshot(rgb_buf: bytearray, w: int, h: int) -> None:
    """Write the w×h RGB24 buffer to a timestamped PNG (screenshot_<ts>.png)
    in the current working directory."""
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

    tex_w, tex_h = _SCREEN_WIDTH, h
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
    rgb_buf: bytearray = bytearray(_SCREEN_WIDTH * h * 3)
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
                        win_display_w = _SCREEN_WIDTH if tex_w > _SCREEN_WIDTH else tex_w
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
                            if total > _S16_MAX:
                                total = _S16_MAX
                            elif total < _S16_MIN:
                                total = _S16_MIN
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
                    if elapsed > frame_timer._frame_interval * _FRAME_OVERRUN_RATIO:
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
