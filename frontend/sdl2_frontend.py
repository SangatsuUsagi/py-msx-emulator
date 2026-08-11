from __future__ import annotations

import ctypes
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from msx.audio_filter import BiquadLowPass
from msx.frame_timer import FrameTimer
from msx.input import KEY_NAME_TO_CELL, InputState
from msx.joystick import JoystickManager
from msx.machine import Machine
from msx.mouse import TRIGGER_A_BIT, TRIGGER_B_BIT, MouseDevice
from msx.psg import SAMPLES_PER_FRAME, JoystickPort, MouseSlot
from msx.screenshot import save_screenshot
from msx.state import load_state, save_state
from msx.vdp._geometry import OUTPUT_H

if TYPE_CHECKING:
    from msx.rpc_server import DebugServer

_SCREEN_WIDTH = 256
# Every rendered frame is padded to a constant output height (see
# msx/vdp/_geometry.py), so the window/texture height is fixed regardless of R#9
# LN (192↔212). Only the width still varies (256, or 512 for SCREEN 6/7).
_SCREEN_HEIGHT = OUTPUT_H
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


@dataclass
class _ActiveCombo:
    """A Ctrl+F-key combo currently asserting a synthetic MSX key."""
    fkey: int
    cell: tuple[int, int]


@dataclass
class _CtrlComboState:
    """Tracks the Ctrl+F1..F5 hotkey combo across independent SDL key events
    (Ctrl and the F-key arrive as separate KEYDOWN/KEYUP events, and either
    may release first; Left and Right Ctrl also share one MSX matrix cell).

    Plain Ctrl (no F-key held) is unaffected and keeps working as the MSX
    CTRL key via the normal key_down/key_up path. While a combo is active
    (`active is not None`): a second F-key press is ignored until the active
    one releases; the literal CTRL bit stays suppressed even if the other
    Ctrl side is pressed mid-combo; and releasing the F-key re-asserts CTRL
    for every Ctrl side still held.
    """
    lctrl_held: bool = False
    rctrl_held: bool = False
    active: _ActiveCombo | None = None

    def release_active(self, input_state: InputState) -> None:
        """Release the currently-asserted combo cell, if any."""
        if self.active is not None:
            row, bit = self.active.cell
            input_state.set_key_state(row, bit, False)
            self.active = None


def _handle_ctrl_combo_keydown(
    sdl2: Any, sym: int, ctrl_combo: _CtrlComboState,
    ctrl_fkey_cells: dict[int, tuple[int, int]], machine: Machine,
) -> bool:
    """Ctrl+F1..F5 combo KEYDOWN handling. Returns True if `sym` was consumed
    (Ctrl itself, or an F-key while at least one Ctrl side is held)."""
    if sym == sdl2.SDLK_LCTRL:
        ctrl_combo.lctrl_held = True
        if ctrl_combo.active is None:
            machine.input.key_down(sym)
        return True
    if sym == sdl2.SDLK_RCTRL:
        ctrl_combo.rctrl_held = True
        if ctrl_combo.active is None:
            machine.input.key_down(sym)
        return True
    if sym in ctrl_fkey_cells and (ctrl_combo.lctrl_held or ctrl_combo.rctrl_held):
        if ctrl_combo.active is None:
            if ctrl_combo.lctrl_held:
                machine.input.key_up(sdl2.SDLK_LCTRL)
            if ctrl_combo.rctrl_held:
                machine.input.key_up(sdl2.SDLK_RCTRL)
            row, bit = ctrl_fkey_cells[sym]
            machine.input.set_key_state(row, bit, True)
            ctrl_combo.active = _ActiveCombo(sym, (row, bit))
        # else: a second F-key pressed while a combo is already active is
        # ignored until the active one releases.
        return True
    return False


def _handle_ctrl_combo_keyup(
    sdl2: Any, sym: int, ctrl_combo: _CtrlComboState, machine: Machine,
) -> bool:
    """Ctrl+F1..F5 combo KEYUP handling. Returns True if `sym` was consumed."""
    if sym in (sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL):
        if sym == sdl2.SDLK_LCTRL:
            ctrl_combo.lctrl_held = False
        else:
            ctrl_combo.rctrl_held = False
        if not (ctrl_combo.lctrl_held or ctrl_combo.rctrl_held):
            ctrl_combo.release_active(machine.input)
        machine.input.key_up(sym)
        return True
    if ctrl_combo.active is not None and sym == ctrl_combo.active.fkey:
        ctrl_combo.release_active(machine.input)
        if ctrl_combo.lctrl_held:
            machine.input.key_down(sdl2.SDLK_LCTRL)
        if ctrl_combo.rctrl_held:
            machine.input.key_down(sdl2.SDLK_RCTRL)
        return True
    return False


def _handle_events(
    sdl2: Any, event: Any, machine: Machine, window: Any, joy_manager: JoystickManager,
    ctrl_combo: _CtrlComboState, ctrl_fkey_cells: dict[int, tuple[int, int]],
    game_title: str, rgb_buf: bytes, tex_w: int, tex_h: int, fullscreen: bool,
    mouse_device: MouseDevice | None = None,
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
            elif _handle_ctrl_combo_keydown(
                sdl2, event.key.keysym.sym, ctrl_combo, ctrl_fkey_cells, machine
            ):
                pass
            else:
                machine.input.key_down(event.key.keysym.sym)
        elif event.type == sdl2.SDL_KEYUP:
            if not _handle_ctrl_combo_keyup(sdl2, event.key.keysym.sym, ctrl_combo, machine):
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
        elif mouse_device is not None and event.type == sdl2.SDL_MOUSEMOTION:
            # Host motion is negated on both axes when converting to MSX
            # relative counts (matches openMSX src/input/Mouse.cc).
            mouse_device.add_motion(-int(event.motion.xrel), -int(event.motion.yrel))
        elif mouse_device is not None and event.type in (
            sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_MOUSEBUTTONUP,
        ):
            button = int(event.button.button)
            pressed = event.type == sdl2.SDL_MOUSEBUTTONDOWN
            if button == sdl2.SDL_BUTTON_LEFT:
                mouse_device.set_button(TRIGGER_A_BIT, pressed)
            elif button == sdl2.SDL_BUTTON_RIGHT:
                mouse_device.set_button(TRIGGER_B_BIT, pressed)
    return running, fullscreen


def _mix_audio(
    machine: Machine,
    frame_start_cycle: int,
    frame_end_cycle: int,
    audio_filter: BiquadLowPass | None = None,
) -> bytes:
    """Generate one frame of audio: PSG plus SCC/DAC/OPLL (FM-PAC) when
    present, mixed and clamped to signed 16-bit. When `audio_filter` is given,
    the final mixed buffer is passed through it (modelling the analog output
    low-pass) before being returned. Returns the PCM bytes to queue."""
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
    if machine.fmpac is not None:
        extra_bufs.append(machine.fmpac.opll.generate_samples(SAMPLES_PER_FRAME))
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


def _resize_texture_if_needed(
    sdl2: Any, renderer: Any, window: Any, texture: Any,
    index_buf: Any, tex_w: int, tex_h: int, scale: int,
) -> tuple[Any, int, int, bool]:
    """Recreate the streaming texture and resize the window when the frame width
    changes (SCREEN 6/7 is 512-wide; the output height is constant). Returns the
    (possibly new) texture, its width/height, and False if recreation failed."""
    new_h = _SCREEN_HEIGHT
    new_w = (len(index_buf) // new_h) if index_buf else tex_w
    if (new_w, new_h) == (tex_w, tex_h):
        return texture, tex_w, tex_h, True
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
        print(f"SDL_CreateTexture error: {sdl2.SDL_GetError()}", file=sys.stderr)
        return texture, tex_w, tex_h, False
    # 512-wide modes (SCREEN 6/7) display at 256*scale to keep aspect ratio; SDL
    # scales the texture down via bilinear filter.
    win_display_w = _SCREEN_WIDTH if tex_w > _SCREEN_WIDTH else tex_w
    sdl2.SDL_SetWindowSize(window, win_display_w * scale, tex_h * scale)
    return texture, tex_w, tex_h, True


def _update_skip_counter(skip_counter: int, elapsed: float, frame_interval: float) -> int:
    """Adapt the auto frame-skip counter to how far the last frame overran."""
    if elapsed > frame_interval * _FRAME_OVERRUN_RATIO:
        return min(skip_counter + 1, _MAX_FRAME_SKIP)
    return max(skip_counter - 1, 0)


def run(
    machine: Machine,
    scale: int = 3,
    speed: float = 1.0,
    game_title: str = "py-msx-emulator",
    resume: str | None = None,
    frame_skip: str = "auto",
    rpc_server: "DebugServer | None" = None,
    gamepad_map: "tuple[dict[int, int], dict[int, int]] | None" = None,
    turbo_period: int | None = None,
    mouse_port: int | None = None,
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
        gamepad_map: optional (direct, turbo) GameController button→bit maps from
            py_emulator.yaml; None uses the built-in defaults.
        turbo_period: optional turbo-fire per-frame period; None uses the default.
        mouse_port: 0 (Joy1) or 1 (Joy2) to attach an MSX mouse driven by the
            host mouse (relative-mouse mode); None disables mouse emulation.

    Runtime hotkeys: ESC quit, F8 save state, F9 load state, F10 screenshot,
    F11 toggle fullscreen, Ctrl-C break into the debugger (if attached).
    """
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    screen_h = _SCREEN_HEIGHT  # constant output height; render_frame pads 192→212
    win_w = _SCREEN_WIDTH * scale
    win_h = screen_h * scale

    tex_w, tex_h = _SCREEN_WIDTH, screen_h
    window, renderer, texture, audio_dev = _init_sdl(
        sdl2, game_title, win_w, win_h, tex_w, tex_h
    )

    # Analog-style output low-pass; one persistent instance so IIR state carries
    # across frames (it starts from the clean state __init__ already zeroes).
    audio_filter = BiquadLowPass()

    joy_kwargs: dict[str, Any] = {}
    if gamepad_map is not None:
        joy_kwargs["_gc_button_bit"], joy_kwargs["_gc_turbo_button_bit"] = gamepad_map
    if turbo_period is not None:
        joy_kwargs["_turbo_period"] = turbo_period
    joy_manager = JoystickManager(_input=machine.input, _sdl=sdl2, **joy_kwargs)

    mouse_device: MouseDevice | None = None
    if mouse_port is not None:
        mouse_device = MouseDevice(_scale=scale)
        mouse_device._get_cycle = lambda: machine.cycle_count
        machine.psg._mouse = MouseSlot(mouse_device, JoystickPort(mouse_port))
        sdl2.SDL_SetRelativeMouseMode(sdl2.SDL_TRUE)
    ctrl_combo = _CtrlComboState()
    # Ctrl+F1..F5 -> MSX HOME/INS/DEL/STOP/SELECT (alternate access on host
    # keyboards without dedicated Home/Insert/Delete keys). Left Alt (GRAPH)
    # and Right Alt (CODE/KANA) need no entries here: they're plain
    # physical-key mappings in msx/input.py. Built once (not per-frame): the
    # mapping never changes for the lifetime of this run.
    ctrl_fkey_cells = {
        sdl2.SDLK_F1: KEY_NAME_TO_CELL["HOME"],
        sdl2.SDLK_F2: KEY_NAME_TO_CELL["INS"],
        sdl2.SDLK_F3: KEY_NAME_TO_CELL["DEL"],
        sdl2.SDLK_F4: KEY_NAME_TO_CELL["STOP"],
        sdl2.SDLK_F5: KEY_NAME_TO_CELL["SELECT"],
    }

    if resume is not None:
        try:
            load_state(machine, path=Path(resume) if resume else None)
        except Exception as exc:
            print(f"resume failed: {exc}", file=sys.stderr)

    frame_timer = FrameTimer(fps=60.0, speed=speed)
    event = sdl2.SDL_Event()
    running = True
    fullscreen = False
    rgb_buf: bytes = bytes(_SCREEN_WIDTH * screen_h * 3)
    skip_counter: int = 0

    try:
        while running:
            try:
                # Process events (input + hotkeys); updates running/fullscreen.
                running, fullscreen = _handle_events(
                    sdl2, event, machine, window, joy_manager, ctrl_combo, ctrl_fkey_cells,
                    game_title, rgb_buf, tex_w, tex_h, fullscreen, mouse_device,
                )

                if not running:
                    break

                joy_manager.tick()

                # Service RPC calls between frames (on this, the emulator thread).
                # While paused, keep the window and socket alive but freeze the
                # CPU: redisplay the last frame, pace, and skip emulation.
                if rpc_server is not None:
                    rpc_server.drain()
                    if rpc_server.pause_state.paused:
                        sdl2.SDL_RenderClear(renderer)
                        sdl2.SDL_RenderCopy(renderer, texture, None, None)
                        sdl2.SDL_RenderPresent(renderer)
                        frame_timer.tick()
                        continue

                # Run one frame (skip VDP pixel rendering when behind schedule)
                skip_this_frame = skip_counter > 0
                frame_start_cycle = machine.cycle_count
                index_buf = machine.run_frame(skip_render=skip_this_frame)
                frame_end_cycle = machine.cycle_count
                if not skip_this_frame:
                    rgb_buf = machine.vdp.to_rgb24(index_buf)
                    # The output height is constant (192-line frames are padded to
                    # 212); only the width can change at runtime (SCREEN 6/7 is
                    # 512-wide). Recreate the texture / resize the window on a width
                    # change before uploading, or the texture copy would overflow.
                    texture, tex_w, tex_h, running = _resize_texture_if_needed(
                        sdl2, renderer, window, texture, index_buf, tex_w, tex_h, scale
                    )
                    if not running:
                        break

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
                    skip_counter = _update_skip_counter(
                        skip_counter, elapsed, frame_timer.frame_interval
                    )

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
        if mouse_port is not None:
            sdl2.SDL_SetRelativeMouseMode(sdl2.SDL_FALSE)
        if audio_dev > 0:
            sdl2.SDL_CloseAudioDevice(audio_dev)
        if texture:
            sdl2.SDL_DestroyTexture(texture)
        if renderer:
            sdl2.SDL_DestroyRenderer(renderer)
        if window:
            sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
