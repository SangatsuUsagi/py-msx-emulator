from __future__ import annotations

import ctypes
import sys
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

from msx.audio_filter import BiquadLowPass
from msx.frame_timer import FrameTimer
from msx.input import KEY_NAME_TO_CELL, SDLK_JIS_YEN, InputState
from msx.joystick import (
    _DEFAULT_GC_BUTTON_BIT,
    _DEFAULT_GC_TURBO_BUTTON_BIT,
    TURBO_PERIOD,
    JoystickManager,
)
from msx.machine import Machine
from msx.mouse import TRIGGER_A_BIT, TRIGGER_B_BIT, MouseDevice
from msx.psg import SAMPLES_PER_FRAME, JoystickPort
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

_TARGET_FPS: float = 60.0


class _InitSdlApi(Protocol):
    """The `sdl2.*` surface `_fail`/`_init_sdl` use: window/renderer/texture/
    audio-device creation and the cascading-failure teardown. Narrower than
    the full SDL2 API -- declares only what these two functions call.
    Part of the staged SDL2 FFI Protocol typing
    (openspec/changes/sdl2-ffi-protocol-typing); composed into `SDL2Api`
    once all stages land.
    """
    SDL_INIT_VIDEO: int
    SDL_INIT_AUDIO: int
    SDL_INIT_JOYSTICK: int
    SDL_INIT_GAMECONTROLLER: int
    SDL_WINDOWPOS_CENTERED: int
    SDL_WINDOW_SHOWN: int
    SDL_RENDERER_ACCELERATED: int
    SDL_RENDERER_SOFTWARE: int
    SDL_PIXELFORMAT_RGB24: int
    SDL_TEXTUREACCESS_STREAMING: int
    AUDIO_S16LSB: int

    def SDL_Init(self, flags: int) -> int: ...
    def SDL_GetError(self) -> bytes: ...
    def SDL_Quit(self) -> None: ...
    def SDL_CreateWindow(
        self, title: bytes, x: int, y: int, w: int, h: int, flags: int
    ) -> object: ...
    def SDL_DestroyWindow(self, window: object) -> None: ...
    def SDL_CreateRenderer(self, window: object, index: int, flags: int) -> object: ...
    def SDL_DestroyRenderer(self, renderer: object) -> None: ...
    def SDL_SetHint(self, name: bytes, value: bytes) -> int: ...
    def SDL_CreateTexture(
        self, renderer: object, format: int, access: int, w: int, h: int
    ) -> object: ...
    def SDL_AudioSpec(self, freq: int, format: int, channels: int, samples: int) -> object: ...
    def SDL_OpenAudioDevice(
        self, device: None, iscapture: int, desired: object, obtained: None,
        allowed_changes: int,
    ) -> int: ...
    def SDL_PauseAudioDevice(self, dev: int, pause_on: int) -> None: ...


def _fail(sdl2: _InitSdlApi, message: str, *destroy_calls: Callable[[], None]) -> NoReturn:
    """Print an SDL error, tear down whatever partial state was created so far
    (in order, earliest-created last), quit SDL and exit.

    Used by `_init_sdl`'s cascading failure paths once `SDL_Init` has already
    succeeded (an `SDL_Init` failure itself has nothing to tear down and skips
    `SDL_Quit`, so it does not go through this helper).
    """
    print(f"{message}: {sdl2.SDL_GetError().decode()}", file=sys.stderr)
    for destroy in destroy_calls:
        destroy()
    sdl2.SDL_Quit()
    sys.exit(1)


def _init_sdl(
    sdl2: _InitSdlApi, game_title: str, win_w: int, win_h: int, tex_w: int, tex_h: int
) -> tuple[object, object, object, int]:
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
        print(f"SDL_Init error: {sdl2.SDL_GetError().decode()}", file=sys.stderr)
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
        _fail(sdl2, "SDL_CreateWindow error")

    renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_ACCELERATED)
    if not renderer:
        renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_SOFTWARE)
    if not renderer:
        _fail(sdl2, "SDL_CreateRenderer error", lambda: sdl2.SDL_DestroyWindow(window))

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
        _fail(
            sdl2, "SDL_CreateTexture error",
            lambda: sdl2.SDL_DestroyRenderer(renderer),
            lambda: sdl2.SDL_DestroyWindow(window),
        )

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


class _EventApi(Protocol):
    """The `sdl2.*` surface event polling and dispatch use: `_handle_events`,
    `_handle_hotkey_keydown`, `_handle_ctrl_combo_keydown`,
    `_handle_ctrl_combo_keyup`. Part of the staged SDL2 FFI Protocol typing
    (openspec/changes/sdl2-ffi-protocol-typing)."""
    SDL_QUIT: int
    SDL_KEYDOWN: int
    SDL_KEYUP: int
    SDLK_LCTRL: int
    SDLK_RCTRL: int
    SDLK_F8: int
    SDLK_F9: int
    SDLK_F10: int
    SDLK_F11: int
    SDLK_c: int
    SDLK_q: int
    KMOD_CTRL: int
    SDL_WINDOW_FULLSCREEN_DESKTOP: int
    SDL_MOUSEMOTION: int
    SDL_MOUSEBUTTONDOWN: int
    SDL_MOUSEBUTTONUP: int
    SDL_BUTTON_LEFT: int
    SDL_BUTTON_RIGHT: int
    # Joystick/GameController device and input event types (see
    # _JOYSTICK_EVENT_TYPES_ATTRS below, resolved dynamically via getattr).
    SDL_CONTROLLERDEVICEADDED: int
    SDL_CONTROLLERDEVICEREMOVED: int
    SDL_JOYDEVICEADDED: int
    SDL_JOYDEVICEREMOVED: int
    SDL_CONTROLLERBUTTONDOWN: int
    SDL_CONTROLLERBUTTONUP: int
    SDL_CONTROLLERAXISMOTION: int
    SDL_JOYBUTTONDOWN: int
    SDL_JOYBUTTONUP: int
    SDL_JOYAXISMOTION: int
    SDL_JOYHATMOTION: int
    # JIS ¥-key detection (see `_resolve_key_sym`): the scancode macOS/SDL2
    # report for that key -- its keysym.sym is unreliable, so this is the
    # only stable signal.
    SDL_SCANCODE_INTERNATIONAL3: int

    def SDL_GetError(self) -> bytes: ...
    def SDL_PollEvent(self, event: object) -> int: ...
    def SDL_SetWindowFullscreen(self, window: object, flags: int) -> int: ...


def _handle_ctrl_combo_keydown(
    sdl2: _EventApi, sym: int, ctrl_combo: _CtrlComboState,
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
    sdl2: _EventApi, sym: int, ctrl_combo: _CtrlComboState, machine: Machine,
) -> bool:
    """Ctrl+F1..F5 combo KEYUP handling. Returns True if `sym` was consumed."""
    if sym in (sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL):
        if sym == sdl2.SDLK_LCTRL:
            ctrl_combo.lctrl_held = False
        else:
            ctrl_combo.rctrl_held = False
        if not (ctrl_combo.lctrl_held or ctrl_combo.rctrl_held):
            # Releasing the last-held Ctrl side ends the combo unconditionally,
            # even if the F-key is still physically held down: the F-key's own
            # later KEYUP will then find `active` already None and fall through
            # to the caller's plain key_up path (harmless — that key's KEYDOWN
            # was never forwarded there either, since the combo intercepted it).
            ctrl_combo.release_active(machine.input)
        machine.input.key_up(sym)
        return True
    if ctrl_combo.active is not None and sym == ctrl_combo.active.fkey:
        # The F-key side of the combo releasing: end it and re-assert plain
        # CTRL for whichever side(s) are still physically held, so a lingering
        # Ctrl press reads as ordinary CTRL again rather than nothing at all.
        ctrl_combo.release_active(machine.input)
        if ctrl_combo.lctrl_held:
            machine.input.key_down(sdl2.SDLK_LCTRL)
        if ctrl_combo.rctrl_held:
            machine.input.key_down(sdl2.SDLK_RCTRL)
        return True
    return False


def _handle_hotkey_keydown(
    sdl2: _EventApi, sym: int, mod: int, machine: Machine, window: object,
    game_title: str, rgb_buf: bytes, tex_w: int, tex_h: int,
    running: bool, fullscreen: bool,
) -> tuple[bool, bool, bool]:
    """Application meta-hotkey KEYDOWN handling: Ctrl-Q quit, F11 fullscreen,
    F8 save state, F9 load state, F10 screenshot, Ctrl-C debugger break.

    None of these produce an MSX matrix-key effect -- they are frontend
    commands that happen to be bound to keys, checked before the Ctrl+F-key
    combo and the plain key_down fallback (mirrors `_handle_ctrl_combo_keydown`'s
    consumed/not-consumed shape). `running`/`fullscreen` are returned unchanged
    unless this key actually changes them. Returns (consumed, running, fullscreen).

    ESC is deliberately not claimed here: it has its own MSX matrix cell
    (row 7, column 2) and reaches it through the plain key_down fallback,
    like any other ordinary key.
    """
    # PORT-NOTE: `mod & sdl2.KMOD_CTRL` relies on Python's implicit
    #   int-to-bool truthiness (0 is falsy, any nonzero bit pattern truthy).
    #   Same pattern below at the Ctrl-C debugger-break check.
    # Rust equivalent: spell this explicitly as `(mod & KMOD_CTRL) != 0` --
    #   Rust has no implicit int-to-bool conversion.
    # C++ equivalent: `mod & KMOD_CTRL` already works via C++'s implicit
    #   int-to-bool conversion in a boolean context, same as Python here.
    # Kept as-is here because: semantic necessity, not performance -- this is
    #   a translation contract for Rust specifically (C++ doesn't need the
    #   explicit `!= 0`), not a Python optimization to preserve.
    if sym == sdl2.SDLK_q and (mod & sdl2.KMOD_CTRL):
        return True, False, fullscreen
    if sym == sdl2.SDLK_F11:
        fullscreen = not fullscreen
        flag = sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP if fullscreen else 0
        if sdl2.SDL_SetWindowFullscreen(window, flag) != 0:
            print(f"fullscreen toggle failed: {sdl2.SDL_GetError().decode()}", file=sys.stderr)
        return True, running, fullscreen
    if sym == sdl2.SDLK_F8:
        save_state(machine, rgb_buf, game_title)
        return True, running, fullscreen
    if sym == sdl2.SDLK_F9:
        try:
            load_state(machine)
        except Exception as exc:
            print(f"load failed: {exc}", file=sys.stderr)
        return True, running, fullscreen
    if sym == sdl2.SDLK_F10:
        save_screenshot(rgb_buf, tex_w, tex_h)
        return True, running, fullscreen
    if sym == sdl2.SDLK_c and (mod & sdl2.KMOD_CTRL) and machine.enter_debugger_if_attached():
        return True, running, fullscreen
    return False, running, fullscreen


# SDL joystick/GameController device and input event types, all routed to
# JoystickManager unchanged. Held as attribute *names*, not resolved ints:
# `sdl2` is only ever imported lazily (see run()), so these can't be resolved
# into a real tuple of constants at module load time. `_handle_events` below
# resolves this once per call (not once per polled event) via getattr.
# PORT-NOTE: held as attribute name strings, resolved via getattr(sdl2, name)
#   once per _handle_events() call -- purely a Python workaround for `sdl2`
#   being an optionally, lazily imported module (see run()) whose constants
#   don't exist at module-import time.
# Rust equivalent: none needed -- the `sdl2` crate is a statically-linked,
#   always-present dependency, so a port just references the real constant
#   names (e.g. sdl2::event::Event::ControllerDeviceAdded) directly.
# C++ equivalent: same -- SDL2 headers are always available at compile time
#   in a C++ port, so this name-string indirection has no reason to exist.
# Kept as-is here because: this whole indirection layer is a Python-only
#   workaround for the optional-dependency import pattern; it disappears
#   entirely at port time rather than needing a translated equivalent.
_JOYSTICK_EVENT_TYPES_ATTRS = (
    "SDL_CONTROLLERDEVICEADDED", "SDL_CONTROLLERDEVICEREMOVED",
    "SDL_JOYDEVICEADDED", "SDL_JOYDEVICEREMOVED",
    "SDL_CONTROLLERBUTTONDOWN", "SDL_CONTROLLERBUTTONUP", "SDL_CONTROLLERAXISMOTION",
    "SDL_JOYBUTTONDOWN", "SDL_JOYBUTTONUP", "SDL_JOYAXISMOTION", "SDL_JOYHATMOTION",
)


def _resolve_key_sym(sdl2: _EventApi, keysym: _KeysymLike) -> int:
    """Return the key identifier `_handle_events` should use for this keysym.

    On macOS, SDL2 reports the JIS ¥ key with a consistent scancode
    (SDL_SCANCODE_INTERNATIONAL3) but an inconsistent keysym.sym --
    observed as both SDLK_UNKNOWN (0, host layout misdetected as US) and the
    Unicode YEN SIGN codepoint 165 (U+00A5, host layout correctly resolved).
    Neither matches KEY_MATRIX_JP's key, so `sym` can't identify this key
    reliably; only the scancode can. This substitutes the stable
    SDLK_JIS_YEN sentinel (see msx/input.py) whenever that scancode is seen,
    regardless of `sym`; every other key passes through unchanged.
    """
    if keysym.scancode == sdl2.SDL_SCANCODE_INTERNATIONAL3:
        return SDLK_JIS_YEN
    return keysym.sym


def _handle_events(
    sdl2: _EventApi, event: _EventLike, machine: Machine, window: object,
    joy_manager: JoystickManager,
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
    # `event` is typed `_EventLike` for the attribute reads below, but
    # `ctypes.byref` needs a real ctypes type -- both the real `SDL_Event`
    # and the fake test doubles are `ctypes.Structure` instances at runtime,
    # this cast only tells the type checker what is already true.
    while sdl2.SDL_PollEvent(ctypes.byref(cast(Any, event))) != 0:
        if event.type == sdl2.SDL_QUIT:
            running = False
        elif event.type == sdl2.SDL_KEYDOWN:
            sym = _resolve_key_sym(sdl2, event.key.keysym)
            consumed, running, fullscreen = _handle_hotkey_keydown(
                sdl2, sym, event.key.keysym.mod, machine, window,
                game_title, rgb_buf, tex_w, tex_h, running, fullscreen,
            )
            if not consumed and not _handle_ctrl_combo_keydown(
                sdl2, sym, ctrl_combo, ctrl_fkey_cells, machine
            ):
                machine.input.key_down(sym)
        elif event.type == sdl2.SDL_KEYUP:
            sym = _resolve_key_sym(sdl2, event.key.keysym)
            if not _handle_ctrl_combo_keyup(sdl2, sym, ctrl_combo, machine):
                machine.input.key_up(sym)
        elif event.type in (getattr(sdl2, name) for name in _JOYSTICK_EVENT_TYPES_ATTRS):
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
    #
    # PORT-NOTE: benchmarked the per-sample clamp loop below (timeit, 735
    #   samples/frame, 1-2 extra channels) against min()/max() clamping and a
    #   zip()+sum() rewrite -- the if/elif clamp here is already at or near
    #   the fastest of the three, and the whole loop costs ~40-55us/frame
    #   against a 16.6ms (60fps) budget either way. Not worth restructuring
    #   pre-port; a native port's per-sample loop is trivially fast regardless
    #   of which of these shapes it's translated from.
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


class _UploadTextureApi(Protocol):
    """The `sdl2.*` surface `_upload_to_texture` uses: locking/unlocking a
    streaming texture for a raw pixel-buffer copy. `SDL_LockTexture`'s
    `rect`/`pixels`/`pitch` parameters are typed `object` rather than exact
    ctypes pointer types: this file always calls it with `None` and
    `ctypes.byref(...)`-wrapped instances, and `ctypes.byref`'s return value
    (a `CArgObject`) has no clean public type name to declare here -- `object`
    is honest about that rather than asserting a precision this file's own
    call site doesn't rely on. Part of the staged SDL2 FFI Protocol typing
    (openspec/changes/sdl2-ffi-protocol-typing)."""
    def SDL_GetError(self) -> bytes: ...
    def SDL_LockTexture(
        self, texture: object, rect: object, pixels: object, pitch: object
    ) -> int: ...
    def SDL_UnlockTexture(self, texture: object) -> None: ...


def _upload_to_texture(
    sdl2: _UploadTextureApi, texture: object, rgb_buf: bytes, tex_w: int, tex_h: int
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
        print(f"SDL_LockTexture failed: {sdl2.SDL_GetError().decode()}", file=sys.stderr)
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


class _ResizeTextureApi(Protocol):
    """The `sdl2.*` surface `_resize_texture_if_needed` uses: texture
    recreation and window resize. Part of the staged SDL2 FFI Protocol
    typing (openspec/changes/sdl2-ffi-protocol-typing)."""
    SDL_PIXELFORMAT_RGB24: int
    SDL_TEXTUREACCESS_STREAMING: int

    def SDL_GetError(self) -> bytes: ...
    def SDL_DestroyTexture(self, texture: object) -> None: ...
    def SDL_CreateTexture(
        self, renderer: object, format: int, access: int, w: int, h: int
    ) -> object: ...
    def SDL_SetWindowSize(self, window: object, w: int, h: int) -> None: ...


def _resize_texture_if_needed(
    sdl2: _ResizeTextureApi, renderer: object, window: object, texture: object,
    index_buf: bytearray, tex_w: int, tex_h: int, scale: int,
) -> tuple[object, int, int, bool]:
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
        print(f"SDL_CreateTexture error: {sdl2.SDL_GetError().decode()}", file=sys.stderr)
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


class _KeysymLike(Protocol):
    sym: int
    mod: int
    scancode: int


class _KeyEventLike(Protocol):
    keysym: _KeysymLike


class _MotionEventLike(Protocol):
    xrel: int
    yrel: int


class _ButtonEventLike(Protocol):
    button: int


class _EventLike(Protocol):
    """The `event.*` attribute shape `_handle_events`/`_run_frame` read:
    `event.type`, `event.key.keysym.sym`/`.mod`, `event.motion.xrel`/`.yrel`,
    `event.button.button`. Matches both the real `sdl2.SDL_Event` ctypes
    Structure and `tests/test_sdl2_frontend_hotkeys.py`'s hand-rolled fake
    (a `ctypes.Structure` subclass with fewer fields, satisfying this
    narrower shape). Part of the staged SDL2 FFI Protocol typing
    (openspec/changes/sdl2-ffi-protocol-typing)."""
    type: int
    key: _KeyEventLike
    motion: _MotionEventLike
    button: _ButtonEventLike


class SDL2Api(_InitSdlApi, _ResizeTextureApi, _UploadTextureApi, _EventApi, Protocol):
    """Full SDL2 surface `frontend/sdl2_frontend.py` uses, composed from the
    per-function-group Protocols above (stages 1-4), plus the handful of
    members only `run()`/`_run_frame` call directly. Used where a function
    calls into more than one of those groups; each leaf function keeps its
    own narrower Protocol. Part of the staged SDL2 FFI Protocol typing
    (openspec/changes/sdl2-ffi-protocol-typing)."""
    SDL_TRUE: int
    SDL_FALSE: int

    def SDL_Event(self) -> _EventLike: ...
    def SDL_SetRelativeMouseMode(self, enabled: int) -> int: ...
    def SDL_RenderClear(self, renderer: object) -> int: ...
    def SDL_RenderCopy(
        self, renderer: object, texture: object, srcrect: None, dstrect: None
    ) -> int: ...
    def SDL_RenderPresent(self, renderer: object) -> None: ...
    def SDL_QueueAudio(self, dev: int, data: bytes, len_: int) -> int: ...
    def SDL_SetWindowTitle(self, window: object, title: bytes) -> None: ...
    def SDL_CloseAudioDevice(self, dev: int) -> None: ...


def _run_frame(
    sdl2: SDL2Api, machine: Machine, window: object, renderer: object, texture: object,
    joy_manager: JoystickManager, ctrl_combo: _CtrlComboState,
    ctrl_fkey_cells: dict[int, tuple[int, int]], game_title: str,
    rgb_buf: bytes, tex_w: int, tex_h: int, fullscreen: bool,
    mouse_device: MouseDevice | None, event: _EventLike,
    rpc_server: "DebugServer | None", frame_timer: FrameTimer,
    skip_counter: int, frame_skip: str, audio_dev: int,
    audio_filter: BiquadLowPass, scale: int,
) -> tuple[bool, bool, object, int, int, bytes, int, bool]:
    """Run one iteration of `run()`'s main loop: poll/apply events, service the
    RPC pause gate, advance emulation one frame (or redisplay under
    frame-skip), mix/queue audio, and present.

    Returns (running, fullscreen, texture, tex_w, tex_h, rgb_buf, skip_counter,
    frame_completed) -- the subset of `run()`'s loop-local state this
    iteration may have changed. `frame_completed` is False for every early
    return (quit requested, RPC-paused, texture resize failed) and True only
    when this iteration ran a full frame through to frame pacing -- `run()`
    uses it to skip the window-title update on an iteration that never
    actually advanced emulation, matching the original inline loop's
    `continue`/`break` short-circuits exactly.
    """
    # Process events (input + hotkeys); updates running/fullscreen.
    running, fullscreen = _handle_events(
        sdl2, event, machine, window, joy_manager, ctrl_combo, ctrl_fkey_cells,
        game_title, rgb_buf, tex_w, tex_h, fullscreen, mouse_device,
    )
    if not running:
        return running, fullscreen, texture, tex_w, tex_h, rgb_buf, skip_counter, False

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
            return running, fullscreen, texture, tex_w, tex_h, rgb_buf, skip_counter, False

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
            return running, fullscreen, texture, tex_w, tex_h, rgb_buf, skip_counter, False

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

    return running, fullscreen, texture, tex_w, tex_h, rgb_buf, skip_counter, True


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
        rpc_server: optional debug/automation RPC server; when given, its
            pause state gates the emulation loop (see the per-frame RPC
            servicing in `_run_frame`).
        gamepad_map: optional (direct, turbo) GameController button→bit maps from
            py_emulator.yaml; None uses the built-in defaults.
        turbo_period: optional turbo-fire per-frame period; None uses the default.
        mouse_port: 0 (Joy1) or 1 (Joy2) to attach an MSX mouse driven by the
            host mouse (relative-mouse mode); None disables mouse emulation.

    Runtime hotkeys: Ctrl-Q quit, F8 save state, F9 load state, F10 screenshot,
    F11 toggle fullscreen, Ctrl-C break into the debugger (if attached),
    Ctrl+F1..F5 -> MSX HOME/INS/DEL/STOP/SELECT (see `_CtrlComboState`).
    ESC now reaches the MSX matrix (row 7, column 2) like any other key.
    """
    try:
        import sdl2
        import sdl2.ext
    except ImportError:
        print("error: pysdl2 is not installed — run 'pip install pysdl2'", file=sys.stderr)
        sys.exit(1)

    # pysdl2 ships no type stubs, so the module import above is untyped; this
    # re-annotation gives the rest of run() a real, checkable SDL2 surface
    # (see SDL2Api's docstring) instead of Any for every call from here on.
    # The ignore is deliberate: there is no other way to attach a type to a
    # name a lazy `import` statement already bound.
    sdl2: SDL2Api = sdl2  # type: ignore[no-redef]

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

    gc_button_bit, gc_turbo_button_bit = (
        gamepad_map if gamepad_map is not None
        else (_DEFAULT_GC_BUTTON_BIT, _DEFAULT_GC_TURBO_BUTTON_BIT)
    )
    joy_manager = JoystickManager(
        _input=machine.input,
        _sdl=sdl2,
        _gc_button_bit=dict(gc_button_bit),
        _gc_turbo_button_bit=dict(gc_turbo_button_bit),
        _turbo_period=turbo_period if turbo_period is not None else TURBO_PERIOD,
    )

    mouse_device: MouseDevice | None = None
    if mouse_port is not None:
        mouse_device = MouseDevice(_scale=scale)
        machine.attach_mouse(mouse_device, JoystickPort(mouse_port))
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

    frame_timer = FrameTimer(fps=_TARGET_FPS, speed=speed)
    event = sdl2.SDL_Event()
    running = True
    fullscreen = False
    rgb_buf: bytes = bytes(_SCREEN_WIDTH * screen_h * 3)
    skip_counter: int = 0
    last_title: str | None = None

    try:
        while running:
            try:
                frame_result = _run_frame(
                    sdl2, machine, window, renderer, texture, joy_manager, ctrl_combo,
                    ctrl_fkey_cells, game_title, rgb_buf, tex_w, tex_h, fullscreen,
                    mouse_device, event, rpc_server, frame_timer, skip_counter,
                    frame_skip, audio_dev, audio_filter, scale,
                )
                (
                    running, fullscreen, texture, tex_w, tex_h, rgb_buf,
                    skip_counter, frame_completed,
                ) = frame_result

                if frame_completed and frame_timer.fps_measured > 0:
                    new_title = f"{game_title}  [{frame_timer.fps_measured:.0f} fps]"
                    if new_title != last_title:
                        sdl2.SDL_SetWindowTitle(window, new_title.encode("utf-8"))
                        last_title = new_title

            except KeyboardInterrupt:
                if not machine.enter_debugger_if_attached():
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
