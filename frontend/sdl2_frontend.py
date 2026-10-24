from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

from frontend.audio_filter import BiquadLowPass
from frontend.frame_timer import FrameTimer
from frontend.sdl2_joystick import (
    _DEFAULT_GC_BUTTON_BIT,
    _DEFAULT_GC_TURBO_BUTTON_BIT,
    TURBO_PERIOD,
    JoystickManager,
)
from frontend.sdl2_keyboard import (
    SDLK_HOST_APOSTROPHE_KEY,
    SDLK_JIS_RO,
    SDLK_JIS_YEN,
    SdlKeyboard,
    resolve_hotkeys,
    resolve_keyboard_joy_map,
)
from host.app_config import AppConfig
from host.input_state import InputState
from host.media import swap_disk
from host.screenshot import save_screenshot
from host.state_files import load_state, resume_state, save_state
from msx.boundary import RgbFrame
from msx.input import KEY_NAME_TO_CELL
from msx.machine import Machine
from msx.mouse import MouseDevice
from msx.psg import SAMPLE_RATE, JoystickPort
from msx.vdp._geometry import OUTPUT_H

if TYPE_CHECKING:
    from host.rpc_server import DebugServer

_APP_NAME = "py-msx-emulator"
_SCREEN_WIDTH = 256
# Every rendered frame is padded to a constant output height (see
# msx/vdp/_geometry.py), so the window/texture height is fixed regardless of R#9
# LN (192↔212). Only the width still varies (256, or 512 for SCREEN 6/7).
_SCREEN_HEIGHT = OUTPUT_H
_MAX_FRAME_SKIP: int = 4

# Audio output format: the core's mix rate (msx.psg.SAMPLE_RATE), mono S16LE.
# Machine.run_frame() mixes each frame; the frontend only queues it.
_AUDIO_CHANNELS: int = 1
_AUDIO_BUFFER_SAMPLES: int = 1024

# Auto frame-skip: bump the skip counter when a frame overruns its budget by
# more than this ratio (5% slack).
_FRAME_OVERRUN_RATIO: float = 1.05

def _target_fps_for(machine: Machine) -> float:
    """Real-time pacing target for this machine: 60 fps NTSC, 50 fps PAL
    (VideoStandard.target_fps)."""
    return machine.video_standard.target_fps


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
        SAMPLE_RATE, sdl2.AUDIO_S16LSB, _AUDIO_CHANNELS, _AUDIO_BUFFER_SAMPLES
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
class _HostMouse:
    """Host mouse input for the frame's InputState, in MSX counts.

    One host pixel of pointer motion is one MSX count at the displayed size,
    so motion is divided by the window's integer scale factor. The remainder
    carries to the next event, so slow motion is not dropped. Motion
    accumulates in `input` until the frame loop hands it to the machine.
    """

    input: InputState
    scale: int
    frac_x: int = 0
    frac_y: int = 0

    def motion(self, dx_px: int, dy_px: int) -> None:
        """Convert host pixels to MSX counts and add them to the frame's input."""
        # PORT-NOTE: divmod() floors -- the quotient rounds toward -inf and
        #   the remainder stays in [0, scale) whatever the motion's sign.
        # Rust equivalent: div_euclid/rem_euclid, not plain '/'/'%' (which
        #   truncate toward zero).
        # C++ equivalent: a hand-written floor-division helper -- plain
        #   '/'/'%' also truncate toward zero in C++.
        # Kept as-is here because: semantic necessity -- truncating division
        #   would flip the carry's sign for negative motion and corrupt it.
        dx, self.frac_x = divmod(dx_px + self.frac_x, self.scale)
        dy, self.frac_y = divmod(dy_px + self.frac_y, self.scale)
        self.input.mouse_dx += dx
        self.input.mouse_dy += dy


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


@dataclass
class _ShiftComboState:
    """Tracks a Shift+hotkey combo, the Shift counterpart of `_CtrlComboState`.

    Plain Shift keeps working as the MSX SHIFT key. While a Shift+hotkey is
    active (`active_key is not None`) the MSX SHIFT cell stays released, so
    neither the hotkey nor the Shift held for it reaches the MSX; releasing
    the hotkey re-asserts SHIFT for every Shift side still held.
    """
    lshift_held: bool = False
    rshift_held: bool = False
    active_key: int | None = None


@dataclass
class _DiskList:
    """Drive A images cycled by the disk hotkeys (`--fdd1` / config `fdd1`)."""
    paths: list[Path]
    index: int = 0


@dataclass
class _HotkeyState:
    """Bound frontend hotkeys and the state their actions act on.

    `bindings` maps (SDL keycode, Shift held) to an action name.
    """
    bindings: dict[tuple[int, bool], str]
    disks: _DiskList
    shift: _ShiftComboState = field(default_factory=_ShiftComboState)
    # Frontend pause, separate from the RPC pause_state: emulation runs only
    # when neither is set, and neither side clears the other.
    user_paused: bool = False
    # Fast-forward is on while the hold key is down or the latch is set.
    ff_hold_key: int | None = None
    ff_locked: bool = False
    muted: bool = False
    fullscreen: bool = False
    # Output low-pass applied to the machine's PCM; one instance so its state
    # carries across frames. The reset action clears it with the machine.
    audio_filter: BiquadLowPass = field(default_factory=BiquadLowPass)

    @property
    def fast_forward(self) -> bool:
        return self.ff_hold_key is not None or self.ff_locked


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
    SDLK_LSHIFT: int
    SDLK_RSHIFT: int
    SDLK_F8: int
    SDLK_F9: int
    SDLK_F10: int
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
    # Host apostrophe/quote key detection (see `_resolve_key_sym`): on a JIS
    # input source this key's sym collides with SHIFT+"7", so only scancode
    # tells the two apart.
    SDL_SCANCODE_APOSTROPHE: int
    # JIS "\"/"_" key detection (see `_resolve_key_sym`): its keysym.sym is
    # unreliable, same as SDL_SCANCODE_INTERNATIONAL3 above, so this is the
    # only stable signal.
    SDL_SCANCODE_INTERNATIONAL1: int

    def SDL_GetError(self) -> bytes: ...
    def SDL_PollEvent(self, event: object) -> int: ...
    def SDL_SetWindowFullscreen(self, window: object, flags: int) -> int: ...


def _handle_ctrl_combo_keydown(
    sdl2: _EventApi, sym: int, ctrl_combo: _CtrlComboState,
    ctrl_fkey_cells: dict[int, tuple[int, int]], keyboard: SdlKeyboard,
) -> bool:
    """Ctrl+F1..F5 combo KEYDOWN handling. Returns True if `sym` was consumed
    (Ctrl itself, or an F-key while at least one Ctrl side is held)."""
    if sym == sdl2.SDLK_LCTRL:
        ctrl_combo.lctrl_held = True
        if ctrl_combo.active is None:
            keyboard.key_down(sym)
        return True
    if sym == sdl2.SDLK_RCTRL:
        ctrl_combo.rctrl_held = True
        if ctrl_combo.active is None:
            keyboard.key_down(sym)
        return True
    if sym in ctrl_fkey_cells and (ctrl_combo.lctrl_held or ctrl_combo.rctrl_held):
        if ctrl_combo.active is None:
            if ctrl_combo.lctrl_held:
                keyboard.key_up(sdl2.SDLK_LCTRL)
            if ctrl_combo.rctrl_held:
                keyboard.key_up(sdl2.SDLK_RCTRL)
            row, bit = ctrl_fkey_cells[sym]
            keyboard.input_state.set_key_state(row, bit, True)
            ctrl_combo.active = _ActiveCombo(sym, (row, bit))
        # else: a second F-key pressed while a combo is already active is
        # ignored until the active one releases.
        return True
    return False


def _handle_ctrl_combo_keyup(
    sdl2: _EventApi, sym: int, ctrl_combo: _CtrlComboState, keyboard: SdlKeyboard,
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
            ctrl_combo.release_active(keyboard.input_state)
        keyboard.key_up(sym)
        return True
    if ctrl_combo.active is not None and sym == ctrl_combo.active.fkey:
        # The F-key side of the combo releasing: end it and re-assert plain
        # CTRL for whichever side(s) are still physically held, so a lingering
        # Ctrl press reads as ordinary CTRL again rather than nothing at all.
        ctrl_combo.release_active(keyboard.input_state)
        if ctrl_combo.lctrl_held:
            keyboard.key_down(sdl2.SDLK_LCTRL)
        if ctrl_combo.rctrl_held:
            keyboard.key_down(sdl2.SDLK_RCTRL)
        return True
    return False


def _mount_disk(machine: Machine, path: Path) -> bool:
    """Swap `path` into drive A via `host.media.swap_disk` (outgoing image
    written back, disk change, transfer abort). Returns False, leaving the
    drive as it was, if the image cannot be opened."""
    assert machine.fdc is not None
    try:
        swap_disk(machine, 0, path)
    except (ValueError, OSError) as exc:
        print(f"disk swap failed: {exc}", file=sys.stderr)
        return False
    return True


def _disk_next(machine: Machine, disks: _DiskList) -> None:
    """F6: mount the next drive A image, wrapping to the first."""
    if machine.fdc is None or not disks.paths:
        return
    next_index = (disks.index + 1) % len(disks.paths)
    if _mount_disk(machine, disks.paths[next_index]):
        disks.index = next_index


def _disk_toggle_eject(machine: Machine, disks: _DiskList) -> None:
    """Shift+F6: eject drive A, or re-insert the current list image when the
    drive is empty (whoever emptied it -- this, the RPC or the debugger)."""
    if machine.fdc is None or not disks.paths:
        return
    if not machine.disk_mounted(0):
        _mount_disk(machine, disks.paths[disks.index])
    else:
        swap_disk(machine, 0, None)


def _toggle_fullscreen(sdl2: _EventApi, window: object, hotkeys: _HotkeyState) -> None:
    hotkeys.fullscreen = not hotkeys.fullscreen
    flag = sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP if hotkeys.fullscreen else 0
    if sdl2.SDL_SetWindowFullscreen(window, flag) != 0:
        print(f"fullscreen toggle failed: {sdl2.SDL_GetError().decode()}", file=sys.stderr)


def _run_hotkey_action(
    sdl2: _EventApi, action: str, sym: int, machine: Machine, window: object,
    hotkeys: _HotkeyState,
) -> None:
    if action == "disk_next":
        _disk_next(machine, hotkeys.disks)
    elif action == "disk_eject":
        _disk_toggle_eject(machine, hotkeys.disks)
    elif action == "pause":
        hotkeys.user_paused = not hotkeys.user_paused
    elif action == "fullscreen":
        _toggle_fullscreen(sdl2, window, hotkeys)
    elif action == "fast_forward_hold":
        hotkeys.ff_hold_key = sym
    elif action == "fast_forward_lock":
        hotkeys.ff_locked = not hotkeys.ff_locked
    elif action == "mute":
        hotkeys.muted = not hotkeys.muted
    elif action == "reset":
        machine.reset()
        hotkeys.audio_filter.reset()


def _handle_bound_hotkey_keydown(
    sdl2: _EventApi, sym: int, repeat: int, machine: Machine, keyboard: SdlKeyboard,
    window: object, ctrl_combo: _CtrlComboState, hotkeys: _HotkeyState,
) -> bool:
    """Shift tracking and bound-hotkey KEYDOWN handling. Returns True if `sym`
    was consumed (Shift itself, or a bound key with no Ctrl side held)."""
    shift = hotkeys.shift
    if sym == sdl2.SDLK_LSHIFT or sym == sdl2.SDLK_RSHIFT:
        if sym == sdl2.SDLK_LSHIFT:
            shift.lshift_held = True
        else:
            shift.rshift_held = True
        if shift.active_key is None:
            keyboard.key_down(sym)
        return True
    if ctrl_combo.lctrl_held or ctrl_combo.rctrl_held:
        return False
    shift_held = shift.lshift_held or shift.rshift_held
    action = hotkeys.bindings.get((sym, shift_held))
    if action is None:
        return False
    if repeat:
        return True
    if shift_held and shift.active_key is None:
        if shift.lshift_held:
            keyboard.key_up(sdl2.SDLK_LSHIFT)
        if shift.rshift_held:
            keyboard.key_up(sdl2.SDLK_RSHIFT)
        shift.active_key = sym
    _run_hotkey_action(sdl2, action, sym, machine, window, hotkeys)
    return True


def _handle_bound_hotkey_keyup(
    sdl2: _EventApi, sym: int, keyboard: SdlKeyboard, hotkeys: _HotkeyState,
) -> bool:
    """Shift tracking, fast-forward hold release and Shift+hotkey KEYUP
    handling. Returns True if `sym` was consumed."""
    consumed = False
    if hotkeys.ff_hold_key is not None and sym == hotkeys.ff_hold_key:
        # Ends the hold whether or not Shift was pressed since.
        hotkeys.ff_hold_key = None
        consumed = True
    shift = hotkeys.shift
    if sym == sdl2.SDLK_LSHIFT or sym == sdl2.SDLK_RSHIFT:
        if sym == sdl2.SDLK_LSHIFT:
            shift.lshift_held = False
        else:
            shift.rshift_held = False
        if not (shift.lshift_held or shift.rshift_held):
            shift.active_key = None
        keyboard.key_up(sym)
        return True
    if shift.active_key is not None and sym == shift.active_key:
        shift.active_key = None
        if shift.lshift_held:
            keyboard.key_down(sdl2.SDLK_LSHIFT)
        if shift.rshift_held:
            keyboard.key_down(sdl2.SDLK_RSHIFT)
        return True
    return consumed


def _handle_hotkey_keydown(
    sdl2: _EventApi, sym: int, mod: int, machine: Machine,
    game_title: str, rgb_buf: bytes, tex_w: int, tex_h: int, running: bool,
) -> tuple[bool, bool]:
    """Fixed application meta-hotkey KEYDOWN handling: Ctrl-Q quit, F8 save
    state, F9 load state, F10 screenshot, Ctrl-C debugger break (the
    configurable ones are `_HotkeyState.bindings`, checked earlier).

    None of these produce an MSX matrix-key effect -- they are frontend
    commands that happen to be bound to keys, checked before the Ctrl+F-key
    combo and the plain key_down fallback (mirrors `_handle_ctrl_combo_keydown`'s
    consumed/not-consumed shape). `running` is returned unchanged unless this
    key quits. Returns (consumed, running).

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
        return True, False
    if sym == sdl2.SDLK_F8:
        save_state(machine, RgbFrame(tex_w, tex_h, rgb_buf), game_title)
        return True, running
    if sym == sdl2.SDLK_F9:
        try:
            load_state(machine)
        except Exception as exc:
            print(f"load failed: {exc}", file=sys.stderr)
        return True, running
    if sym == sdl2.SDLK_F10:
        save_screenshot(rgb_buf, tex_w, tex_h)
        return True, running
    if sym == sdl2.SDLK_c and (mod & sdl2.KMOD_CTRL) and machine.enter_debugger_if_attached():
        return True, running
    return False, running


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
    regardless of `sym`.

    The host's dedicated apostrophe/quote key has the opposite problem: its
    sym (SDLK_QUOTE) is perfectly consistent, but on a JIS input source it is
    reported to collide with the sym SHIFT+"7" produces -- two different
    physical actions on one sym (see msx/input.py's SDLK_HOST_APOSTROPHE_KEY).
    This substitutes that sentinel whenever SDL_SCANCODE_APOSTROPHE is seen,
    so _JP_SYMBOLS can bind the two separately regardless.

    The JIS "\"/"_" key has the same unreliable-sym problem as the ¥ key
    above (probe-confirmed: keysym.sym == 0, constant across SHIFT). This
    substitutes the SDLK_JIS_RO sentinel (see msx/input.py) whenever
    SDL_SCANCODE_INTERNATIONAL1 is seen.

    The host's dedicated "="/"+" key and a real JIS "^"/"~" key sit at the
    same physical position and were once suspected of being indistinguishable
    (both reporting scancode 46) -- probe-confirmed since to be false: their
    syms differ (SDLK_EQUALS vs SDLK_CARET) and neither changes under SHIFT,
    so no scancode interception is needed here at all; both `sym` values
    already resolve correctly through their own layout's plain matrix entry.

    Every other key passes through unchanged.
    """
    if keysym.scancode == sdl2.SDL_SCANCODE_INTERNATIONAL3:
        return SDLK_JIS_YEN
    if keysym.scancode == sdl2.SDL_SCANCODE_APOSTROPHE:
        return SDLK_HOST_APOSTROPHE_KEY
    if keysym.scancode == sdl2.SDL_SCANCODE_INTERNATIONAL1:
        return SDLK_JIS_RO
    return keysym.sym


def _handle_events(
    sdl2: _EventApi, event: _EventLike, machine: Machine, keyboard: SdlKeyboard,
    window: object, joy_manager: JoystickManager,
    ctrl_combo: _CtrlComboState, ctrl_fkey_cells: dict[int, tuple[int, int]],
    game_title: str, rgb_buf: bytes, tex_w: int, tex_h: int,
    mouse: _HostMouse | None = None, hotkeys: _HotkeyState | None = None,
) -> bool:
    """Drain the SDL event queue, applying input and hotkeys.

    `hotkeys` (None: no bound hotkeys, Shift goes through the plain path) is
    checked before every other key handler.

    Returns running: False once a quit is requested. rgb_buf/tex_w/tex_h are
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
            if hotkeys is not None and _handle_bound_hotkey_keydown(
                sdl2, sym, event.key.repeat, machine, keyboard, window, ctrl_combo, hotkeys
            ):
                continue
            consumed, running = _handle_hotkey_keydown(
                sdl2, sym, event.key.keysym.mod, machine,
                game_title, rgb_buf, tex_w, tex_h, running,
            )
            if not consumed and not _handle_ctrl_combo_keydown(
                sdl2, sym, ctrl_combo, ctrl_fkey_cells, keyboard
            ):
                keyboard.key_down(sym)
        elif event.type == sdl2.SDL_KEYUP:
            sym = _resolve_key_sym(sdl2, event.key.keysym)
            if hotkeys is not None and _handle_bound_hotkey_keyup(sdl2, sym, keyboard, hotkeys):
                continue
            if not _handle_ctrl_combo_keyup(sdl2, sym, ctrl_combo, keyboard):
                keyboard.key_up(sym)
        elif event.type in (getattr(sdl2, name) for name in _JOYSTICK_EVENT_TYPES_ATTRS):
            joy_manager.handle_event(event)
        elif mouse is not None and event.type == sdl2.SDL_MOUSEMOTION:
            # Host motion is negated on both axes when converting to MSX
            # relative counts (matches openMSX src/input/Mouse.cc).
            mouse.motion(-int(event.motion.xrel), -int(event.motion.yrel))
        elif mouse is not None and event.type in (
            sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_MOUSEBUTTONUP,
        ):
            button = int(event.button.button)
            pressed = event.type == sdl2.SDL_MOUSEBUTTONDOWN
            if button == sdl2.SDL_BUTTON_LEFT:
                mouse.input.mouse_left = pressed
            elif button == sdl2.SDL_BUTTON_RIGHT:
                mouse.input.mouse_right = pressed
    return running


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
    frame_w: int, tex_w: int, tex_h: int, scale: int,
) -> tuple[object, int, int, bool]:
    """Recreate the streaming texture and resize the window when the frame width
    changes (SCREEN 6/7 is 512-wide; the output height is constant). Returns the
    (possibly new) texture, its width/height, and False if recreation failed."""
    new_h = _SCREEN_HEIGHT
    new_w = frame_w
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
    repeat: int


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
    def SDL_ClearQueuedAudio(self, dev: int) -> None: ...
    def SDL_SetWindowTitle(self, window: object, title: bytes) -> None: ...
    def SDL_CloseAudioDevice(self, dev: int) -> None: ...


def _disk_status(machine: Machine, disks: _DiskList) -> str:
    """The drive A part of the window title ("" with no FDC or no list)."""
    if machine.fdc is None or not disks.paths:
        return ""
    count = len(disks.paths)
    if not machine.disk_mounted(0):
        return f"disk {disks.index + 1}/{count}: (ejected)"
    path = machine.disk_path(0)
    name = path.name if path is not None else "?"
    if path == disks.paths[disks.index]:
        return f"disk {disks.index + 1}/{count}: {name}"
    return f"disk -/{count}: {name}"


def _window_title(
    game_title: str, fps: float, machine: Machine, hotkeys: _HotkeyState, paused: bool
) -> str:
    """Window title: app/game name, measured fps, status flags, drive A status.
    `paused` covers both the frontend and the RPC pause."""
    parts = [_APP_NAME if game_title == _APP_NAME else f"{_APP_NAME} — {game_title}"]
    if fps > 0:
        parts.append(f"[{fps:.0f} fps]")
    if paused:
        parts.append("[PAUSED]")
    if hotkeys.fast_forward:
        parts.append("[FF]")
    if hotkeys.muted:
        parts.append("[MUTE]")
    disk = _disk_status(machine, hotkeys.disks)
    if disk:
        parts.append(disk)
    return " ".join(parts)


def _update_window_title(
    sdl2: SDL2Api, window: object, title: str, last_title: str | None
) -> str:
    """Set the window title only when it changed; returns the title now shown."""
    if title != last_title:
        sdl2.SDL_SetWindowTitle(window, title.encode("utf-8"))
    return title


@dataclass
class _LoopState:
    """`run()`'s per-iteration loop-local state, read and updated in place by
    `_run_frame` across calls to `run()`'s main loop. Bundles what used to be
    7 separate loop-local variables threaded through `_run_frame`'s parameter
    list and an 8-element return tuple repeated at every early return."""
    running: bool
    texture: object
    tex_w: int
    tex_h: int
    rgb_buf: bytes
    skip_counter: int
    # Last pause state sent to SDL_PauseAudioDevice.
    audio_paused: bool = False
    # Fast-forward state of the previous iteration, to act on transitions.
    fast_forward: bool = False
    # perf_counter() of the last present, for the fast-forward present limit.
    last_present: float = 0.0


def _is_paused(hotkeys: _HotkeyState, rpc_server: "DebugServer | None") -> bool:
    return hotkeys.user_paused or (rpc_server is not None and rpc_server.pause_state.paused)


def _present(sdl2: SDL2Api, renderer: object, texture: object) -> None:
    sdl2.SDL_RenderClear(renderer)
    sdl2.SDL_RenderCopy(renderer, texture, None, None)
    sdl2.SDL_RenderPresent(renderer)


def _run_frame(
    sdl2: SDL2Api, machine: Machine, keyboard: SdlKeyboard, window: object, renderer: object,
    state: _LoopState, joy_manager: JoystickManager, ctrl_combo: _CtrlComboState,
    ctrl_fkey_cells: dict[int, tuple[int, int]], game_title: str,
    mouse: _HostMouse | None, event: _EventLike,
    rpc_server: "DebugServer | None", frame_timer: FrameTimer,
    frame_skip: str, audio_dev: int, scale: int,
    hotkeys: _HotkeyState,
) -> None:
    """Run one iteration of `run()`'s main loop: poll/apply events, service the
    RPC pause gate, advance emulation one frame (or redisplay under
    frame-skip), mix/queue audio, and present. Updates `state` in place with
    whatever this iteration changed.
    """
    # Process events (input + hotkeys); updates running.
    state.running = _handle_events(
        sdl2, event, machine, keyboard, window, joy_manager, ctrl_combo, ctrl_fkey_cells,
        game_title, state.rgb_buf, state.tex_w, state.tex_h, mouse, hotkeys,
    )
    if not state.running:
        return

    joy_manager.tick()

    # Service RPC calls between frames (on this, the emulator thread).
    if rpc_server is not None:
        rpc_server.drain()

    # While paused (frontend or RPC), keep the window and socket alive but
    # freeze the CPU and the audio device: redisplay the last frame, pace,
    # and skip emulation.
    paused = _is_paused(hotkeys, rpc_server)
    if audio_dev > 0 and paused != state.audio_paused:
        sdl2.SDL_PauseAudioDevice(audio_dev, 1 if paused else 0)
        state.audio_paused = paused
    if paused:
        _present(sdl2, renderer, state.texture)
        frame_timer.tick()
        return

    fast_forward = hotkeys.fast_forward
    if fast_forward != state.fast_forward:
        state.fast_forward = fast_forward
        state.skip_counter = 0
        if audio_dev > 0:
            # Drop the backlog so no stale audio plays across the change.
            sdl2.SDL_ClearQueuedAudio(audio_dev)

    # Run one frame. Skip VDP pixel rendering when behind schedule, or under
    # fast-forward when less than one display interval passed since the last
    # present.
    if fast_forward:
        since_present = time.perf_counter() - state.last_present
        skip_this_frame = since_present < 1.0 / frame_timer.fps
    else:
        skip_this_frame = state.skip_counter > 0
    out = machine.step_frame(
        keyboard.input_state.take_frame(), render_video=not skip_this_frame
    )
    if out.video is not None:
        state.rgb_buf = out.video.pixels
        # The output height is constant (192-line frames are padded to
        # 212); only the width can change at runtime (SCREEN 6/7 is
        # 512-wide). Recreate the texture / resize the window on a width
        # change before uploading, or the texture copy would overflow.
        state.texture, state.tex_w, state.tex_h, state.running = _resize_texture_if_needed(
            sdl2, renderer, window, state.texture, out.video.width, state.tex_w, state.tex_h,
            scale,
        )
        if not state.running:
            return

    # step_frame() already mixed the frame's audio; filter it every
    # frame (also with no device, so the filter state stays continuous) and
    # queue it unless fast-forwarding, as silence when muted.
    audio_buf = hotkeys.audio_filter.filter(out.audio)
    if audio_dev > 0:
        if hotkeys.muted:
            audio_buf = bytes(len(audio_buf))
        if not fast_forward:
            sdl2.SDL_QueueAudio(audio_dev, audio_buf, len(audio_buf))

    # Upload to texture only when frame was rendered
    if not skip_this_frame:
        _upload_to_texture(sdl2, state.texture, state.rgb_buf, state.tex_w, state.tex_h)

    # Render (always when paced -- redisplays the previous texture on skipped
    # frames; only rendered frames under fast-forward)
    if not (fast_forward and skip_this_frame):
        _present(sdl2, renderer, state.texture)
        state.last_present = time.perf_counter()

    # Frame pacing + skip counter update
    if fast_forward:
        frame_timer.tick_unpaced()
        return
    elapsed = frame_timer.tick()
    if frame_skip == "auto":
        state.skip_counter = _update_skip_counter(
            state.skip_counter, elapsed, frame_timer.frame_interval
        )


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
    fdd1_images: list[Path] | None = None,
    hotkey_bindings: dict[tuple[int, bool], str] | None = None,
    keyboard_joystick: Mapping[str, str] | None = None,
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
        fdd1_images: drive A disk list stepped through by F6 / Shift+F6; the
            first entry is the one build_machine already mounted.
        hotkey_bindings: (SDL keycode, Shift held) -> action table resolved
            from py_emulator.yaml (`resolve_hotkeys(AppConfig.hotkey_bindings())`); None uses the
            built-in table.
        keyboard_joystick: keyboard_joystick.buttons (function -> key name)
            from py_emulator.yaml; None or empty uses the built-in key map.

    Runtime hotkeys (built-in table; configurable): F6 next disk, Shift+F6
    eject/re-insert disk, F7 fast-forward while held, Shift+F7 latch
    fast-forward, F11 pause, Shift+F11 fullscreen, F12 mute, Shift+F12 reset.
    Fixed: Ctrl-Q quit, F8 save state, F9 load state, F10 screenshot,
    Ctrl-C break into the debugger (if attached),
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

    # Falls back to the same default tables JoystickManager's own dataclass
    # fields would use, resolved here (not left to the field defaults)
    # because an explicit gamepad_map always needs `dict(...)`-copying into
    # the constructor kwargs below either way.
    gc_button_bit, gc_turbo_button_bit = (
        gamepad_map if gamepad_map is not None
        else (_DEFAULT_GC_BUTTON_BIT, _DEFAULT_GC_TURBO_BUTTON_BIT)
    )
    # Host input accumulates here and reaches the machine once per frame
    # through step_frame (allium/core-frontend.allium InputHeldForFrame).
    frame_input = InputState()
    if rpc_server is not None:
        rpc_server.input_target = frame_input
    joy_manager = JoystickManager(
        _input=frame_input,
        _sdl=sdl2,
        _gc_button_bit=dict(gc_button_bit),
        _gc_turbo_button_bit=dict(gc_turbo_button_bit),
        _turbo_period=turbo_period if turbo_period is not None else TURBO_PERIOD,
    )

    keyboard = SdlKeyboard(
        frame_input, resolve_keyboard_joy_map(keyboard_joystick or {}),
        keyboard_layout=machine.keyboard_layout,
    )

    mouse: _HostMouse | None = None
    if mouse_port is not None:
        mouse = _HostMouse(keyboard.input_state, scale)
        machine.attach_mouse(MouseDevice(), JoystickPort(mouse_port))
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

    hotkeys = _HotkeyState(
        bindings=dict(
            hotkey_bindings if hotkey_bindings is not None
            else resolve_hotkeys(AppConfig().hotkey_bindings())
        ),
        disks=_DiskList(list(fdd1_images) if fdd1_images else []),
    )

    if resume is not None:
        try:
            resume_state(machine, resume)
        except Exception as exc:
            print(f"resume failed: {exc}", file=sys.stderr)

    frame_timer = FrameTimer(fps=_target_fps_for(machine), speed=speed)
    event = sdl2.SDL_Event()
    state = _LoopState(
        running=True, texture=texture, tex_w=tex_w, tex_h=tex_h,
        rgb_buf=bytes(_SCREEN_WIDTH * screen_h * 3), skip_counter=0,
    )
    last_title: str | None = None

    try:
        while state.running:
            try:
                _run_frame(
                    sdl2, machine, keyboard, window, renderer, state, joy_manager, ctrl_combo,
                    ctrl_fkey_cells, game_title, mouse, event, rpc_server,
                    frame_timer, frame_skip, audio_dev, scale, hotkeys,
                )
                last_title = _update_window_title(
                    sdl2, window,
                    _window_title(
                        game_title, frame_timer.fps_measured, machine, hotkeys,
                        _is_paused(hotkeys, rpc_server),
                    ),
                    last_title,
                )

            except KeyboardInterrupt:
                if not machine.enter_debugger_if_attached():
                    state.running = False

    finally:
        joy_manager.close_all()
        if mouse_port is not None:
            sdl2.SDL_SetRelativeMouseMode(sdl2.SDL_FALSE)
        if audio_dev > 0:
            sdl2.SDL_CloseAudioDevice(audio_dev)
        if state.texture:
            sdl2.SDL_DestroyTexture(state.texture)
        if renderer:
            sdl2.SDL_DestroyRenderer(renderer)
        if window:
            sdl2.SDL_DestroyWindow(window)
        sdl2.SDL_Quit()
