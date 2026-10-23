"""Ctrl+F1..F5 and Right Alt hotkey combo tests for `_handle_events`.

Exercises the real `InputState` matrix through a minimal fake `sdl2` module:
events are scripted as (type, sym, mod) tuples and copied into a real ctypes
`_Event` structure by a fake `SDL_PollEvent`, so `ctypes.byref(event)` inside
`_handle_events` works exactly as it does against the real SDL2 library.
"""
import ctypes
from pathlib import Path

import pytest

from frontend.sdl2_frontend import (
    _CtrlComboState,
    _DiskList,
    _handle_events,
    _HotkeyState,
    _LoopState,
    _run_frame,
    _update_window_title,
    _window_title,
)
from frontend.sdl2_keyboard import KEY_MATRIX_INT, SdlKeyboard
from msx.boundary import FrameOutput, RgbFrame
from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793
from msx.input import KEY_NAME_TO_CELL, InputState


class _Keysym(ctypes.Structure):
    _fields_ = [
        ("sym", ctypes.c_int), ("mod", ctypes.c_uint16), ("scancode", ctypes.c_int),
    ]


class _KeyEvent(ctypes.Structure):
    _fields_ = [("repeat", ctypes.c_uint8), ("keysym", _Keysym)]


class _Event(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("key", _KeyEvent)]


class _FakeSDL:
    """Just enough SDL2 constant/API surface for `_handle_events`.

    Every constant here is actually read by `_handle_events` while processing
    a KEYDOWN/KEYUP event (the sequential if/elif chain evaluates each
    comparison in turn, even when it doesn't match) — this file's tests only
    ever send KEYDOWN/KEYUP events, so joystick/controller `SDL_*` event-type
    constants are intentionally omitted: that branch is never reached for
    these event types, and `window`/`joy_manager` are passed as `None` by
    `_Harness` on the same basis (only exercised by the fullscreen and
    joystick branches; the fullscreen tests use `_FullscreenSDL`).
    """

    SDL_QUIT = 1
    SDL_KEYDOWN = 2
    SDL_KEYUP = 3
    SDLK_ESCAPE = 27
    SDLK_F1 = 1073741882
    SDLK_F2 = 1073741883
    SDLK_F3 = 1073741884
    SDLK_F4 = 1073741885
    SDLK_F5 = 1073741886
    SDLK_F6 = 1073741887
    SDLK_F7 = 1073741888
    SDLK_F12 = 1073741893
    SDLK_F8 = 1073741889
    SDLK_F9 = 1073741890
    SDLK_F10 = 1073741891
    SDLK_F11 = 1073741892
    SDLK_c = ord("c")
    SDLK_q = ord("q")
    SDLK_LCTRL = 1073742048
    SDLK_RCTRL = 1073742052
    SDLK_LSHIFT = 1073742049
    SDLK_RSHIFT = 1073742053
    SDLK_LALT = 1073742050
    SDLK_RALT = 1073742054
    KMOD_CTRL = 0x0040
    # JIS ¥-key scancode (see `_resolve_key_sym` in frontend/sdl2_frontend.py):
    # the real SDL2 value, like the F-key keycodes above,
    # since a dedicated test pins it against the real pysdl2 library.
    SDL_SCANCODE_INTERNATIONAL3 = 137
    # Host apostrophe/quote key scancode (see `_resolve_key_sym`), same
    # real-value-pinning rationale as SDL_SCANCODE_INTERNATIONAL3 above.
    SDL_SCANCODE_APOSTROPHE = 52
    # Host "="/"+" key scancode (see `_resolve_key_sym`), same
    # real-value-pinning rationale as SDL_SCANCODE_INTERNATIONAL3 above.
    SDL_SCANCODE_EQUALS = 46
    # JIS "\"/"_" key scancode (see `_resolve_key_sym`), same
    # real-value-pinning rationale as SDL_SCANCODE_INTERNATIONAL3 above.
    SDL_SCANCODE_INTERNATIONAL1 = 135

    def __init__(self, script: list[tuple[int, ...]], event: _Event) -> None:
        self._script = list(script)
        self._event = event

    def SDL_PollEvent(self, _event_ptr: object) -> int:
        if not self._script:
            return 0
        ev_type, sym, mod, scancode, *rest = self._script.pop(0)
        self._event.type = ev_type
        self._event.key.repeat = rest[0] if rest else 0
        self._event.key.keysym.sym = sym
        self._event.key.keysym.mod = mod
        self._event.key.keysym.scancode = scancode
        return 1


class _FakeMachine:
    def __init__(self) -> None:
        self.input = InputState()
        self._debugger = None
        self.fdc: SonyPhilipsInterface | None = None
        self.reset_calls = 0
        self.shift_at_reset: bool | None = None

    def reset(self) -> None:
        self.reset_calls += 1
        self.shift_at_reset = _asserted(self.input.matrix, KEY_MATRIX_INT[_FakeSDL.SDLK_LSHIFT])


def _asserted(matrix: list[int], cell: tuple[int, int]) -> bool:
    row, bit = cell
    return matrix[row] & (1 << bit) == 0  # active-low


_CTRL_FKEY_CELLS = {
    _FakeSDL.SDLK_F1: KEY_NAME_TO_CELL["HOME"],
    _FakeSDL.SDLK_F2: KEY_NAME_TO_CELL["INS"],
    _FakeSDL.SDLK_F3: KEY_NAME_TO_CELL["DEL"],
    _FakeSDL.SDLK_F4: KEY_NAME_TO_CELL["STOP"],
    _FakeSDL.SDLK_F5: KEY_NAME_TO_CELL["SELECT"],
}


class _Harness:
    """Drives `_handle_events` across multiple scripted event batches while
    keeping the same machine/ctrl-combo/event state, like consecutive frames."""

    def __init__(
        self, hotkeys: _HotkeyState | None = None, sdl_cls: type[_FakeSDL] = _FakeSDL
    ) -> None:
        self.sdl_cls = sdl_cls
        self.machine = _FakeMachine()
        self.keyboard = SdlKeyboard(self.machine.input)
        self.ctrl_combo = _CtrlComboState()
        self.hotkeys = hotkeys
        self._event = _Event()
        self.running = True

    def send(self, script: list[tuple[int, ...]]) -> None:
        sdl = self.sdl_cls(script, self._event)
        self.running = _handle_events(
            sdl, self._event, self.machine, self.keyboard, None, None, self.ctrl_combo,
            _CTRL_FKEY_CELLS, "", b"", 0, 0, None, self.hotkeys,
        )

    def keydown(self, sym: int, mod: int = 0, scancode: int = 0, repeat: int = 0) -> None:
        self.send([(_FakeSDL.SDL_KEYDOWN, sym, mod, scancode, repeat)])

    def keyup(self, sym: int, mod: int = 0, scancode: int = 0) -> None:
        self.send([(_FakeSDL.SDL_KEYUP, sym, mod, scancode)])

    @property
    def matrix(self) -> list[int]:
        return self.machine.input.matrix


_HOME = KEY_NAME_TO_CELL["HOME"]
_INS = KEY_NAME_TO_CELL["INS"]
_DEL = KEY_NAME_TO_CELL["DEL"]
_STOP = KEY_NAME_TO_CELL["STOP"]
_SELECT = KEY_NAME_TO_CELL["SELECT"]
_CTRL = KEY_MATRIX_INT[_FakeSDL.SDLK_LCTRL]
_GRAPH = KEY_MATRIX_INT[_FakeSDL.SDLK_LALT]
_CODE_KANA = KEY_MATRIX_INT[_FakeSDL.SDLK_RALT]
_F1_CELL = KEY_MATRIX_INT[_FakeSDL.SDLK_F1]


def test_ctrl_f1_asserts_home_not_f1_or_ctrl() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _F1_CELL)
    assert not _asserted(h.matrix, _CTRL)


def test_ctrl_f4_asserts_stop_and_f5_asserts_select() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F4)
    assert _asserted(h.matrix, _STOP)
    h.keyup(_FakeSDL.SDLK_F4)
    assert not _asserted(h.matrix, _STOP)
    h.keydown(_FakeSDL.SDLK_F5)
    assert _asserted(h.matrix, _SELECT)


def test_releasing_fkey_first_restores_ctrl() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F2)
    assert _asserted(h.matrix, _INS)
    h.keyup(_FakeSDL.SDLK_F2)
    assert not _asserted(h.matrix, _INS)
    assert _asserted(h.matrix, _CTRL)  # Ctrl is still held


def test_releasing_ctrl_first_ends_combo_without_ctrl_bit() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F3)
    assert _asserted(h.matrix, _DEL)
    h.keyup(_FakeSDL.SDLK_LCTRL)
    assert not _asserted(h.matrix, _DEL)
    assert not _asserted(h.matrix, _CTRL)


def test_plain_ctrl_still_sends_ctrl() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    assert _asserted(h.matrix, _CTRL)
    h.keyup(_FakeSDL.SDLK_LCTRL)
    assert not _asserted(h.matrix, _CTRL)


def test_second_fkey_during_active_combo_is_ignored() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    h.keydown(_FakeSDL.SDLK_F4)
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _STOP)
    assert h.ctrl_combo.active is not None
    assert h.ctrl_combo.active.fkey == _FakeSDL.SDLK_F1


def test_fkey_without_ctrl_behaves_as_before() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _F1_CELL)
    assert not _asserted(h.matrix, _HOME)


def test_left_alt_still_sends_graph() -> None:
    # Unaffected by the Ctrl+F-key combo: Left Alt keeps its baseline mapping.
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LALT)
    assert _asserted(h.matrix, _GRAPH)
    h.keyup(_FakeSDL.SDLK_LALT)
    assert not _asserted(h.matrix, _GRAPH)


def test_right_alt_sends_code_kana_through_normal_path() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_RALT)
    assert _asserted(h.matrix, _CODE_KANA)
    h.keyup(_FakeSDL.SDLK_RALT)
    assert not _asserted(h.matrix, _CODE_KANA)


def test_right_ctrl_also_triggers_combo() -> None:
    # Left and Right Ctrl share one matrix cell; either side triggers the combo.
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_RCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _CTRL)


def test_both_ctrl_sides_held_keeps_combo_until_both_release() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_RCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _HOME)
    h.keyup(_FakeSDL.SDLK_LCTRL)
    assert _asserted(h.matrix, _HOME)  # RCTRL still held: combo stays active
    h.keyup(_FakeSDL.SDLK_RCTRL)
    assert not _asserted(h.matrix, _HOME)


def test_second_ctrl_side_during_active_combo_does_not_reassert_ctrl() -> None:
    # Regression: pressing the other Ctrl side while a combo is already
    # active must not leak the literal CTRL bit back into the matrix.
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _CTRL)
    h.keydown(_FakeSDL.SDLK_RCTRL)
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _CTRL)


# --- allium/frontend.allium: PlainKeyPressed (reserved-hotkey precedence) --
# config.reserved_hotkey_keys claims F8/F9/F10 for non-MSX app commands
# before PlainKeyPressed's matrix routing ever sees them. ESC is not in that
# set -- it is an ordinary MSX key (row 7, col 2) -- and quit is bound to
# Ctrl-Q instead (modifier-gated, same treatment as Ctrl-C).

_ESC_CELL = KEY_MATRIX_INT[_FakeSDL.SDLK_ESCAPE]


def test_escape_reaches_the_matrix_and_does_not_quit() -> None:
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_ESCAPE)
    assert _asserted(h.matrix, _ESC_CELL)
    assert h.running is True


def test_ctrl_q_quits_without_touching_the_matrix() -> None:
    q_cell = KEY_MATRIX_INT[_FakeSDL.SDLK_q]
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_q, _FakeSDL.KMOD_CTRL)
    assert h.running is False
    assert not _asserted(h.matrix, q_cell)


def test_plain_q_without_ctrl_reaches_the_matrix() -> None:
    # PlainKeyPressed's requires clause excludes Ctrl+Q specifically; without
    # Ctrl held, 'q' must go through the normal path like any other key.
    q_cell = KEY_MATRIX_INT[_FakeSDL.SDLK_q]
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_q)
    assert h.running is True
    assert _asserted(h.matrix, q_cell)


def test_releasing_fkey_with_no_active_combo_is_plain_release() -> None:
    # CtrlComboFKeyReleased requires combo.active != null; F1 released with
    # no Ctrl ever held must go through PlainKeyReleased instead.
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _F1_CELL)
    h.keyup(_FakeSDL.SDLK_F1)
    assert not _asserted(h.matrix, _F1_CELL)
    assert h.ctrl_combo.active is None


def test_releasing_unrelated_key_during_active_combo_is_plain_release() -> None:
    # CtrlComboFKeyReleased requires sdl_keycode = combo.active.fkey; releasing
    # some other already-held key mid-combo must not touch the combo at all.
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LALT)          # GRAPH, unrelated to the combo
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F1)
    assert _asserted(h.matrix, _HOME)
    h.keyup(_FakeSDL.SDLK_LALT)
    assert not _asserted(h.matrix, _GRAPH)  # plain release went through normally
    assert _asserted(h.matrix, _HOME)       # combo untouched
    assert h.ctrl_combo.active is not None
    assert h.ctrl_combo.active.fkey == _FakeSDL.SDLK_F1


def test_ctrl_plus_non_fkey_goes_through_plain_path_not_combo() -> None:
    # allium/frontend.allium's CtrlComboFKeyPressed only claims F1-F5
    # (ctrl_combo_cell returns null for anything else); Ctrl+A must behave
    # as an ordinary Ctrl-held plain keypress, not a swallowed/combo event.
    c_cell = KEY_MATRIX_INT[_FakeSDL.SDLK_c]
    h = _Harness()
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_c)
    assert _asserted(h.matrix, c_cell)            # 'c' reached the matrix normally
    assert _asserted(h.matrix, _CTRL)             # literal CTRL stays asserted
    assert h.ctrl_combo.active is None


def test_reserved_hotkey_keycodes_match_real_sdl2_constants() -> None:
    # Pins allium/frontend.allium's config.reserved_hotkey_keys literal
    # values against the real pysdl2 library and this file's fake. Also
    # guards against the reserved set drifting out of sync with the real
    # SDL2 F8-F10 keycodes (SDL_SCANCODE_F8..F10 = 65..67, so SDLK =
    # 0x40000000 | scancode = 1073741889..91).
    # ESC/Ctrl-Q are deliberately excluded: ESC has its own MSX matrix cell
    # and is not reserved, and Ctrl-Q is modifier-gated rather than a bare
    # keycode (see reserved_hotkey_keys's own comment).
    import sdl2

    assert (sdl2.SDLK_F8, sdl2.SDLK_F9, sdl2.SDLK_F10) == (1073741889, 1073741890, 1073741891)
    assert (_FakeSDL.SDLK_F8, _FakeSDL.SDLK_F9, _FakeSDL.SDLK_F10) == (
        1073741889, 1073741890, 1073741891,
    )


def test_ctrl_keycodes_match_real_sdl2_constants() -> None:
    # Pins allium/frontend.allium's config.lctrl_keycode/rctrl_keycode
    # against both the real pysdl2 library and msx.input's own constants.
    import sdl2

    from frontend.sdl2_keyboard import _K_LCTRL, _K_RCTRL

    assert sdl2.SDLK_LCTRL == _K_LCTRL == 1073742048
    assert sdl2.SDLK_RCTRL == _K_RCTRL == 1073742052


def test_jis_yen_scancode_matches_real_sdl2_constant() -> None:
    # On macOS, SDL2 reports the JIS ¥ key with scancode
    # SDL_SCANCODE_INTERNATIONAL3, but keysym.sym varies with host layout
    # detection state -- observed as both SDLK_UNKNOWN (0) and the Unicode
    # YEN SIGN codepoint (165, U+00A5) on the same physical key across runs.
    # Only the scancode is reliable. Pins it against the real pysdl2 library
    # so `_FakeSDL.SDL_SCANCODE_INTERNATIONAL3` above can't silently drift.
    import sdl2

    assert sdl2.SDL_SCANCODE_INTERNATIONAL3 == _FakeSDL.SDL_SCANCODE_INTERNATIONAL3 == 137


def test_jis_yen_key_reaches_matrix_via_scancode_fallback() -> None:
    # `_handle_events` must key off scancode alone to identify the ¥ key and
    # reach MSX matrix cell (1, 4) (the same cell International \ uses) --
    # regardless of whatever `sym` the host layout happened to produce.
    from frontend.sdl2_keyboard import KEY_MATRIX_JP, SDLK_JIS_YEN

    yen_cell = KEY_MATRIX_JP[SDLK_JIS_YEN]
    for observed_sym in (0, 165):  # SDLK_UNKNOWN, and Unicode YEN SIGN (U+00A5)
        h = _Harness()
        h.machine.input.keyboard_type = "jp"
        h.keyboard._matrix_map = KEY_MATRIX_JP
        h.keydown(observed_sym, scancode=_FakeSDL.SDL_SCANCODE_INTERNATIONAL3)
        assert _asserted(h.matrix, yen_cell)
        h.keyup(observed_sym, scancode=_FakeSDL.SDL_SCANCODE_INTERNATIONAL3)
        assert not _asserted(h.matrix, yen_cell)


def test_apostrophe_scancode_matches_real_sdl2_constant() -> None:
    # Same rationale as test_jis_yen_scancode_matches_real_sdl2_constant:
    # pins _FakeSDL.SDL_SCANCODE_APOSTROPHE against the real pysdl2 library
    # so it can't silently drift.
    import sdl2

    assert sdl2.SDL_SCANCODE_APOSTROPHE == _FakeSDL.SDL_SCANCODE_APOSTROPHE == 52


def test_host_apostrophe_key_reaches_colon_cell_via_scancode() -> None:
    # On a JIS input source the host's dedicated apostrophe/quote key and
    # SHIFT+"7" both report keysym.sym == SDLK_QUOTE (39) -- `_handle_events`
    # must use scancode to route the former to SDLK_HOST_APOSTROPHE_KEY's
    # cell (":"/"*", row 2 bit 0) and leave the latter (scancode for "7",
    # left as plain sym 39) reaching _K_QUOTE's cell (row 0 bit 7) instead.
    from frontend.sdl2_keyboard import _K_QUOTE, KEY_MATRIX_JP, SDLK_HOST_APOSTROPHE_KEY

    colon_cell = KEY_MATRIX_JP[SDLK_HOST_APOSTROPHE_KEY]
    apostrophe_cell = KEY_MATRIX_JP[_K_QUOTE]
    assert colon_cell != apostrophe_cell

    h = _Harness()
    h.machine.input.keyboard_type = "jp"
    h.keyboard._matrix_map = KEY_MATRIX_JP

    h.keydown(_K_QUOTE, scancode=_FakeSDL.SDL_SCANCODE_APOSTROPHE)
    assert _asserted(h.matrix, colon_cell)
    assert not _asserted(h.matrix, apostrophe_cell)
    h.keyup(_K_QUOTE, scancode=_FakeSDL.SDL_SCANCODE_APOSTROPHE)
    assert not _asserted(h.matrix, colon_cell)

    # scancode 30 == SDL_SCANCODE_7, the "7" key SHIFT+7 collapses from.
    h.keydown(_K_QUOTE, scancode=30)
    assert _asserted(h.matrix, apostrophe_cell)
    assert not _asserted(h.matrix, colon_cell)
    h.keyup(_K_QUOTE, scancode=30)
    assert not _asserted(h.matrix, apostrophe_cell)


def test_equals_scancode_matches_real_sdl2_constant() -> None:
    # Same rationale as test_jis_yen_scancode_matches_real_sdl2_constant:
    # pins _FakeSDL.SDL_SCANCODE_EQUALS against the real pysdl2 library so it
    # can't silently drift. This scancode is no longer given any special
    # handling by `_resolve_key_sym` -- it's the shared host physical
    # position of the International "="/"+" key and the real JIS "^"/"~"
    # key, told apart by plain sym alone (see the two tests below).
    import sdl2

    assert sdl2.SDL_SCANCODE_EQUALS == _FakeSDL.SDL_SCANCODE_EQUALS == 46


def test_jis_caret_key_reaches_caret_cell_via_plain_sym() -> None:
    # The real JIS "^"/"~" key reports scancode 46 (same host physical
    # position as the International "="/"+" key) but its own sym
    # (SDLK_CARET, 94, probe-confirmed constant across SHIFT) -- no scancode
    # interception needed, `_handle_events` passes the sym straight through
    # to _K_CARET's cell.
    from frontend.sdl2_keyboard import _K_CARET, KEY_MATRIX_JP

    caret_cell = KEY_MATRIX_JP[_K_CARET]

    h = _Harness()
    h.machine.input.keyboard_type = "jp"
    h.keyboard._matrix_map = KEY_MATRIX_JP

    h.keydown(_K_CARET, scancode=_FakeSDL.SDL_SCANCODE_EQUALS)
    assert _asserted(h.matrix, caret_cell)
    h.keyup(_K_CARET, scancode=_FakeSDL.SDL_SCANCODE_EQUALS)
    assert not _asserted(h.matrix, caret_cell)


def test_international1_scancode_matches_real_sdl2_constant() -> None:
    # Same rationale as test_jis_yen_scancode_matches_real_sdl2_constant:
    # pins _FakeSDL.SDL_SCANCODE_INTERNATIONAL1 against the real pysdl2
    # library so it can't silently drift.
    import sdl2

    assert sdl2.SDL_SCANCODE_INTERNATIONAL1 == _FakeSDL.SDL_SCANCODE_INTERNATIONAL1 == 135


def test_host_ro_key_reaches_underscore_cell_via_scancode() -> None:
    # The real JIS "\"/"_" key has an unreliable sym (observed as 0,
    # constant across SHIFT -- same class of problem as the JIS ¥ key), so
    # `_handle_events` must identify it by scancode and route it to
    # SDLK_JIS_RO's cell ("_", row 2 bit 5).
    from frontend.sdl2_keyboard import KEY_MATRIX_JP, SDLK_JIS_RO

    underscore_cell = KEY_MATRIX_JP[SDLK_JIS_RO]

    h = _Harness()
    h.machine.input.keyboard_type = "jp"
    h.keyboard._matrix_map = KEY_MATRIX_JP

    h.keydown(0, scancode=_FakeSDL.SDL_SCANCODE_INTERNATIONAL1)
    assert _asserted(h.matrix, underscore_cell)
    h.keyup(0, scancode=_FakeSDL.SDL_SCANCODE_INTERNATIONAL1)
    assert not _asserted(h.matrix, underscore_cell)


def test_international_equals_key_reaches_equals_cell_via_plain_sym() -> None:
    # On International layout, the same host physical position (scancode
    # 46) reports SDLK_EQUALS instead -- reaches the plain "=" cell with no
    # scancode assist needed, regression check against the removed
    # SDLK_HOST_EQUALS_KEY scancode interception.
    from frontend.sdl2_keyboard import _K_EQUALS, KEY_MATRIX_INT

    equals_cell = KEY_MATRIX_INT[_K_EQUALS]

    h = _Harness()
    h.keydown(_K_EQUALS, scancode=_FakeSDL.SDL_SCANCODE_EQUALS)
    assert _asserted(h.matrix, equals_cell)
    h.keyup(_K_EQUALS, scancode=_FakeSDL.SDL_SCANCODE_EQUALS)
    assert not _asserted(h.matrix, equals_cell)


# ---------------------------------------------------------------------------
# Bound hotkeys: disk swap (F6 / Shift+F6) and Shift-combo isolation
# ---------------------------------------------------------------------------

_SHIFT = KEY_MATRIX_INT[_FakeSDL.SDLK_LSHIFT]
_DISK_BINDINGS = {
    (_FakeSDL.SDLK_F6, False): "disk_next",
    (_FakeSDL.SDLK_F6, True): "disk_eject",
}


def _dsk(path: Path) -> Path:
    path.write_bytes(bytes(737280))
    return path


def _disk_harness(tmp_path: Path, names: list[str], fdc: bool = True) -> _Harness:
    paths = [_dsk(tmp_path / n) for n in names]
    h = _Harness(_HotkeyState(bindings=dict(_DISK_BINDINGS), disks=_DiskList(paths)))
    if fdc:
        h.machine.fdc = SonyPhilipsInterface(WD2793(), [DiskDrive()], disk_rom=bytes(16384))
        if paths:
            h.machine.fdc.mount(DskDiskImage(paths[0]), 0)
    return h


def _drive_a(h: _Harness) -> str | None:
    assert h.machine.fdc is not None
    image = h.machine.fdc.drives[0].image
    return None if image is None else image.path.name


def _no_cell_asserted(h: _Harness) -> bool:
    return all(row == 0xFF for row in h.matrix)


def test_f6_mounts_the_next_image(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk", "c.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "b.dsk"
    assert h.hotkeys is not None and h.hotkeys.disks.index == 1


def test_f6_goes_through_floppy_disk_swap(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    assert h.machine.fdc is not None
    h.machine.fdc.read_mem(0x7FFD)  # clear the power-on state
    h.keydown(_FakeSDL.SDLK_F6)
    assert h.machine.fdc.read_mem(0x7FFD) & 0x04 == 0  # disk-change reported


def test_f6_wraps_to_the_first_image(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    h.keyup(_FakeSDL.SDLK_F6)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "a.dsk"
    assert h.hotkeys is not None and h.hotkeys.disks.index == 0


def test_unreadable_image_leaves_the_drive_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    (tmp_path / "b.dsk").unlink()
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "a.dsk"
    assert h.hotkeys is not None and h.hotkeys.disks.index == 0
    assert "disk swap failed" in capsys.readouterr().err


def test_shift_f6_ejects(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) is None


def test_shift_f6_reinserts_the_current_image(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)            # index 1, b.dsk
    h.keyup(_FakeSDL.SDLK_F6)
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)            # eject
    h.keyup(_FakeSDL.SDLK_F6)
    h.keydown(_FakeSDL.SDLK_F6)            # re-insert
    assert _drive_a(h) == "b.dsk"
    assert h.hotkeys is not None and h.hotkeys.disks.index == 1


def test_shift_f6_reinserts_after_an_external_eject(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    assert h.machine.fdc is not None
    h.machine.fdc.swap(0, None)            # as the RPC or the debugger would
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "a.dsk"


@pytest.mark.parametrize("shift", [False, True])
def test_disk_hotkeys_do_nothing_without_an_fdc(tmp_path: Path, shift: bool) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"], fdc=False)
    if shift:
        h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    h.keyup(_FakeSDL.SDLK_F6)
    if shift:
        h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert h.hotkeys is not None and h.hotkeys.disks.index == 0
    assert _no_cell_asserted(h)


@pytest.mark.parametrize("shift", [False, True])
def test_disk_hotkeys_do_nothing_with_an_empty_list(tmp_path: Path, shift: bool) -> None:
    h = _disk_harness(tmp_path, [])
    assert h.machine.fdc is not None
    h.machine.fdc.mount(DskDiskImage(_dsk(tmp_path / "x.dsk")), 0)
    if shift:
        h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "x.dsk"


def test_hotkey_fkey_does_not_reach_the_matrix(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    assert _no_cell_asserted(h)
    h.keyup(_FakeSDL.SDLK_F6)
    assert _no_cell_asserted(h)


def test_key_repeat_does_not_repeat_the_action(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk", "c.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    h.keydown(_FakeSDL.SDLK_F6, repeat=1)
    h.keydown(_FakeSDL.SDLK_F6, repeat=1)
    assert _drive_a(h) == "b.dsk"


def test_ctrl_blocks_the_hotkey(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    h.keydown(_FakeSDL.SDLK_LCTRL)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) == "a.dsk"


def test_shift_hotkey_does_not_leave_shift_asserted(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    assert _asserted(h.matrix, _SHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    assert _drive_a(h) is None
    assert _no_cell_asserted(h)


def test_releasing_the_fkey_with_shift_held_restores_shift(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    h.keyup(_FakeSDL.SDLK_F6)
    assert _asserted(h.matrix, _SHIFT)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _no_cell_asserted(h)


def test_releasing_shift_first_ends_the_combo(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    h.keyup(_FakeSDL.SDLK_F6)
    assert _no_cell_asserted(h)
    assert h.hotkeys is not None and h.hotkeys.shift.active_key is None


def test_other_shift_side_during_the_combo_is_not_forwarded(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F6)
    h.keydown(_FakeSDL.SDLK_RSHIFT)
    h.keydown(_FakeSDL.SDLK_LSHIFT, repeat=1)  # SDL repeats a held modifier too
    assert not _asserted(h.matrix, _SHIFT)
    h.keyup(_FakeSDL.SDLK_F6)
    assert _asserted(h.matrix, _SHIFT)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _asserted(h.matrix, _SHIFT)          # Right Shift still held
    h.keyup(_FakeSDL.SDLK_RSHIFT)
    assert _no_cell_asserted(h)


@pytest.mark.parametrize("fkey", [
    _FakeSDL.SDLK_F1, _FakeSDL.SDLK_F2, _FakeSDL.SDLK_F3, _FakeSDL.SDLK_F4, _FakeSDL.SDLK_F5,
])
def test_shift_f1_to_f5_still_reach_the_msx(tmp_path: Path, fkey: int) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(fkey)
    assert _asserted(h.matrix, _SHIFT)
    assert _asserted(h.matrix, KEY_MATRIX_INT[fkey])
    h.keyup(fkey)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _no_cell_asserted(h)


def test_plain_shift_still_sends_shift(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk"])
    h.keydown(_FakeSDL.SDLK_RSHIFT)
    assert _asserted(h.matrix, _SHIFT)
    h.keyup(_FakeSDL.SDLK_RSHIFT)
    assert _no_cell_asserted(h)


def test_hotkey_keycodes_match_real_sdl2_constants() -> None:
    import sdl2

    from frontend.sdl2_keyboard import _K_LSHIFT, _K_RSHIFT, FKEY_NAME_TO_SDLKEY

    assert FKEY_NAME_TO_SDLKEY == {
        "f1": sdl2.SDLK_F1, "f2": sdl2.SDLK_F2, "f3": sdl2.SDLK_F3, "f4": sdl2.SDLK_F4,
        "f5": sdl2.SDLK_F5, "f6": sdl2.SDLK_F6, "f7": sdl2.SDLK_F7, "f8": sdl2.SDLK_F8,
        "f9": sdl2.SDLK_F9, "f10": sdl2.SDLK_F10, "f11": sdl2.SDLK_F11, "f12": sdl2.SDLK_F12,
    }
    assert sdl2.SDLK_F6 == _FakeSDL.SDLK_F6
    assert (sdl2.SDLK_LSHIFT, sdl2.SDLK_RSHIFT) == (_FakeSDL.SDLK_LSHIFT, _FakeSDL.SDLK_RSHIFT)
    assert (_K_LSHIFT, _K_RSHIFT) == (_FakeSDL.SDLK_LSHIFT, _FakeSDL.SDLK_RSHIFT)


# ---------------------------------------------------------------------------
# Window title
# ---------------------------------------------------------------------------

def test_title_shows_the_disk_position(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk", "c.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    assert h.hotkeys is not None
    title = _window_title("Game", 59.6, h.machine, h.hotkeys, False)
    assert title == "py-msx-emulator — Game [60 fps] disk 2/3: b.dsk"


def test_title_shows_an_ejected_drive(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    assert h.machine.fdc is not None and h.hotkeys is not None
    h.machine.fdc.swap(0, None)
    title = _window_title("Game", 60.0, h.machine, h.hotkeys, False)
    assert title.endswith("disk 1/2: (ejected)")


def test_title_shows_an_image_from_outside_the_list(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk"])
    assert h.machine.fdc is not None and h.hotkeys is not None
    h.machine.fdc.swap(0, DskDiskImage(_dsk(tmp_path / "other.dsk")))
    title = _window_title("Game", 60.0, h.machine, h.hotkeys, False)
    assert title.endswith("disk -/2: other.dsk")


def test_title_without_a_rom_database_title() -> None:
    machine = _FakeMachine()
    hotkeys = _HotkeyState(bindings={}, disks=_DiskList([]))
    assert _window_title("py-msx-emulator", 0.0, machine, hotkeys, False) == "py-msx-emulator"


def test_title_set_only_on_change() -> None:
    calls: list[bytes] = []

    class _TitleSDL:
        def SDL_SetWindowTitle(self, _window: object, title: bytes) -> None:
            calls.append(title)

    sdl = _TitleSDL()
    last = _update_window_title(sdl, None, "a", None)
    last = _update_window_title(sdl, None, "a", last)
    assert calls == [b"a"]
    _update_window_title(sdl, None, "b", last)
    assert calls == [b"a", b"b"]


def test_title_shows_the_status_flags(tmp_path: Path) -> None:
    h = _disk_harness(tmp_path, ["a.dsk", "b.dsk", "c.dsk"])
    h.keydown(_FakeSDL.SDLK_F6)
    assert h.hotkeys is not None
    h.hotkeys.ff_locked = True
    h.hotkeys.muted = True
    title = _window_title("Game", 60.0, h.machine, h.hotkeys, True)
    assert title == "py-msx-emulator — Game [60 fps] [PAUSED] [FF] [MUTE] disk 2/3: b.dsk"


# ---------------------------------------------------------------------------
# Bound hotkeys: fast-forward, mute, reset
# ---------------------------------------------------------------------------

_CONTROL_BINDINGS = {
    (_FakeSDL.SDLK_F7, False): "fast_forward_hold",
    (_FakeSDL.SDLK_F7, True): "fast_forward_lock",
    (_FakeSDL.SDLK_F12, False): "mute",
    (_FakeSDL.SDLK_F12, True): "reset",
}


def _control_harness() -> _Harness:
    return _Harness(_HotkeyState(bindings=dict(_CONTROL_BINDINGS), disks=_DiskList([])))


def test_f7_hold() -> None:
    h = _control_harness()
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_F7)
    assert h.hotkeys.fast_forward
    h.keydown(_FakeSDL.SDLK_F7, repeat=1)
    assert h.hotkeys.fast_forward
    h.keyup(_FakeSDL.SDLK_F7)
    assert not h.hotkeys.fast_forward
    assert _no_cell_asserted(h)


def test_f7_hold_ends_even_if_shift_was_pressed_meanwhile() -> None:
    h = _control_harness()
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_F7)
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keyup(_FakeSDL.SDLK_F7)
    assert not h.hotkeys.fast_forward
    assert _asserted(h.matrix, _SHIFT)  # plain Shift still reaches the MSX


def test_shift_f7_toggles_the_latch() -> None:
    h = _control_harness()
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F7)
    h.keyup(_FakeSDL.SDLK_F7)
    assert h.hotkeys.fast_forward
    h.keydown(_FakeSDL.SDLK_F7)
    h.keyup(_FakeSDL.SDLK_F7)
    assert not h.hotkeys.fast_forward
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _no_cell_asserted(h)


def test_f12_toggles_mute() -> None:
    h = _control_harness()
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_F12)
    h.keyup(_FakeSDL.SDLK_F12)
    assert h.hotkeys.muted
    h.keydown(_FakeSDL.SDLK_F12)
    h.keyup(_FakeSDL.SDLK_F12)
    assert not h.hotkeys.muted
    assert _no_cell_asserted(h)


def test_shift_f12_resets_without_shift_held() -> None:
    h = _control_harness()
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F12)
    h.keydown(_FakeSDL.SDLK_F12, repeat=1)
    assert h.machine.reset_calls == 1
    assert h.machine.shift_at_reset is False
    h.keyup(_FakeSDL.SDLK_F12)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _no_cell_asserted(h)


def test_control_keycodes_match_real_sdl2_constants() -> None:
    import sdl2

    assert (sdl2.SDLK_F7, sdl2.SDLK_F12) == (_FakeSDL.SDLK_F7, _FakeSDL.SDLK_F12)


# ---------------------------------------------------------------------------
# _run_frame: pause, fast-forward, mute
# ---------------------------------------------------------------------------

_AUDIO_DEV = 7
_SAMPLES = 735


class _LoopSDL(_FakeSDL):
    """`_FakeSDL` with no queued events plus the render/audio calls `_run_frame` makes."""

    def __init__(self) -> None:
        super().__init__([], _Event())
        self.presents = 0
        self.queued: list[bytes] = []
        self.pause_calls: list[int] = []
        self.clear_calls = 0

    def SDL_RenderClear(self, _renderer: object) -> int:
        return 0

    def SDL_RenderCopy(self, *_args: object) -> int:
        return 0

    def SDL_RenderPresent(self, _renderer: object) -> None:
        self.presents += 1

    def SDL_QueueAudio(self, _dev: int, data: bytes, _len: int) -> int:
        self.queued.append(data)
        return 0

    def SDL_PauseAudioDevice(self, _dev: int, pause_on: int) -> None:
        self.pause_calls.append(pause_on)

    def SDL_ClearQueuedAudio(self, _dev: int) -> None:
        self.clear_calls += 1


class _LoopVDP:
    def to_rgb24(self, index_buf: bytearray) -> bytes:
        return bytes(len(index_buf) * 3)


class _LoopStandard:
    target_fps = 60.0


class _LoopMachine(_FakeMachine):
    def __init__(self) -> None:
        super().__init__()
        self.cycle_count = 0
        self.frames = 0
        self.takes = 0
        self.vdp = _LoopVDP()
        self.video_standard = _LoopStandard()

    def step_frame(self, frame_input: InputState, render_video: bool) -> FrameOutput:
        self.frames += 1
        self.takes += 1
        self.cycle_count += 59736
        video = RgbFrame(256, 212, bytes(256 * 212 * 3)) if render_video else None
        return FrameOutput(video, b"\x10\x00" * _SAMPLES, self.frames)


class _LoopTimer:
    fps = 60.0
    frame_interval = 1.0 / 60.0

    def __init__(self) -> None:
        self.ticks = 0
        self.unpaced = 0

    def tick(self) -> float:
        self.ticks += 1
        return self.frame_interval

    def tick_unpaced(self) -> float:
        self.unpaced += 1
        return 0.0


class _LoopJoy:
    def tick(self) -> None:
        pass


class _PauseState:
    def __init__(self, paused: bool) -> None:
        self.paused = paused


class _LoopRpc:
    def __init__(self, paused: bool) -> None:
        self.pause_state = _PauseState(paused)

    def drain(self) -> None:
        pass


class _Loop:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, rpc_paused: bool | None = None) -> None:
        import frontend.sdl2_frontend as fe

        monkeypatch.setattr(fe, "_upload_to_texture", lambda *_a: None)
        self.sdl = _LoopSDL()
        self.machine = _LoopMachine()
        self.timer = _LoopTimer()
        self.hotkeys = _HotkeyState(bindings={}, disks=_DiskList([]))
        self.rpc = None if rpc_paused is None else _LoopRpc(rpc_paused)
        self.audio_dev = _AUDIO_DEV
        self.state = _LoopState(
            running=True, texture=object(), tex_w=256, tex_h=212,
            rgb_buf=b"", skip_counter=0,
        )

    def step(self) -> None:
        _run_frame(
            self.sdl, self.machine, SdlKeyboard(self.machine.input), None, None, self.state,
            _LoopJoy(), _CtrlComboState(),
            _CTRL_FKEY_CELLS, "", None, _Event(), self.rpc, self.timer, "auto",
            self.audio_dev, 3, self.hotkeys,
        )


def test_pause_stops_emulation_and_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.user_paused = True
    loop.step()
    loop.step()
    assert loop.machine.frames == 0
    assert loop.machine.takes == 0
    assert loop.sdl.queued == []
    assert loop.sdl.pause_calls == [1]        # paused once, not per iteration
    assert loop.sdl.presents == 2              # last frame still shown
    assert loop.timer.ticks == 2


def test_resume_restarts_emulation_and_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.user_paused = True
    loop.step()
    loop.hotkeys.user_paused = False
    loop.step()
    loop.step()
    assert loop.machine.frames == 2
    assert loop.sdl.pause_calls == [1, 0]
    assert len(loop.sdl.queued) == 2


def test_rpc_pause_also_pauses_the_audio_device(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch, rpc_paused=True)
    loop.step()
    assert loop.machine.frames == 0
    assert loop.sdl.pause_calls == [1]


def test_pause_action_does_not_resume_an_rpc_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch, rpc_paused=True)
    h = _Harness(_HotkeyState(bindings={(_FakeSDL.SDLK_F6, False): "pause"},
                              disks=_DiskList([])))
    loop.hotkeys = h.hotkeys
    h.keydown(_FakeSDL.SDLK_F6)
    h.keyup(_FakeSDL.SDLK_F6)
    assert loop.hotkeys.user_paused
    h.keydown(_FakeSDL.SDLK_F6)
    assert not loop.hotkeys.user_paused
    loop.step()
    assert loop.rpc is not None and loop.rpc.pause_state.paused
    assert loop.machine.frames == 0


def test_fast_forward_frame_is_unpaced_and_not_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.ff_locked = True
    loop.step()
    assert loop.machine.frames == 1
    assert loop.timer.unpaced == 1 and loop.timer.ticks == 0
    assert loop.machine.takes == 1              # still taken: the core mixed it
    assert loop.sdl.queued == []


def test_fast_forward_limits_presents(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.ff_locked = True
    loop.step()
    loop.step()                                 # well inside 1/60 s of the first
    assert loop.machine.frames == 2
    assert loop.sdl.presents == 1


def test_fast_forward_transitions_clear_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.step()
    assert loop.sdl.clear_calls == 0
    loop.hotkeys.ff_locked = True
    loop.state.skip_counter = 3
    loop.step()
    assert loop.sdl.clear_calls == 1
    assert loop.state.skip_counter == 0
    loop.step()
    assert loop.sdl.clear_calls == 1
    loop.hotkeys.ff_locked = False
    loop.step()
    assert loop.sdl.clear_calls == 2


def test_leaving_fast_forward_restores_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.ff_locked = True
    loop.step()
    loop.hotkeys.ff_locked = False
    loop.step()
    assert loop.timer.ticks == 1 and loop.timer.unpaced == 1
    assert len(loop.sdl.queued) == 1


def test_muted_frame_queues_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _Loop(monkeypatch)
    loop.hotkeys.muted = True
    loop.step()
    assert loop.machine.takes == 1
    assert loop.sdl.pause_calls == []           # device keeps running
    assert loop.sdl.queued == [bytes(2 * _SAMPLES)]
    loop.hotkeys.muted = False
    loop.step()
    assert loop.sdl.queued[1] == b"\x10\x00" * _SAMPLES


def test_queued_buffer_is_the_machines_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    # app-sdl2-audio "Queued buffer is the machine's PCM" and "Queued audio is
    # the filtered machine PCM": the frontend filters the machine's PCM.
    from frontend.audio_filter import BiquadLowPass

    loop = _Loop(monkeypatch)
    loop.step()
    assert loop.machine.takes == 1
    assert loop.sdl.queued == [BiquadLowPass().filter(b"\x10\x00" * _SAMPLES)]


def test_reset_hotkey_clears_the_filter_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # app-sdl2-audio "Machine reset clears the filter state".
    from frontend.audio_filter import BiquadLowPass
    from frontend.sdl2_frontend import _run_hotkey_action

    loop = _Loop(monkeypatch)
    loop.step()
    loop.step()
    _run_hotkey_action(loop.sdl, "reset", 0, loop.machine, None, loop.hotkeys)
    loop.step()
    assert loop.sdl.queued[2] == BiquadLowPass().filter(b"\x10\x00" * _SAMPLES)
    assert loop.sdl.queued[1] != loop.sdl.queued[2]


def test_no_audio_device_still_takes_the_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    # app-sdl2-audio "No audio device still takes the PCM".
    loop = _Loop(monkeypatch)
    loop.audio_dev = 0
    loop.step()
    assert loop.machine.takes == 1
    assert loop.sdl.queued == []


# ---------------------------------------------------------------------------
# F11 pause, Shift+F11 fullscreen, bindings from the config
# ---------------------------------------------------------------------------

class _FullscreenSDL(_FakeSDL):
    SDL_WINDOW_FULLSCREEN_DESKTOP = 0x1001
    calls: list[int] = []

    def SDL_SetWindowFullscreen(self, _window: object, flags: int) -> int:
        _FullscreenSDL.calls.append(flags)
        return 0


def _default_harness(monkeypatch: pytest.MonkeyPatch, hotkeys: dict[str, str] | None = None
                     ) -> _Harness:
    import frontend.sdl2_frontend as fe
    from msx.app_config import AppConfig

    _FullscreenSDL.calls = []
    monkeypatch.setattr(fe, "save_state", lambda *_a: pytest.fail("F8 save ran"))
    from frontend.sdl2_keyboard import resolve_hotkeys

    bindings = resolve_hotkeys(AppConfig(hotkeys=dict(hotkeys or {})).hotkey_bindings())
    return _Harness(_HotkeyState(bindings=bindings, disks=_DiskList([])), _FullscreenSDL)


def test_f11_pauses_and_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _default_harness(monkeypatch)
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_F11)
    h.keyup(_FakeSDL.SDLK_F11)
    assert h.hotkeys.user_paused
    h.keydown(_FakeSDL.SDLK_F11)
    h.keyup(_FakeSDL.SDLK_F11)
    assert not h.hotkeys.user_paused
    assert _no_cell_asserted(h)
    assert _FullscreenSDL.calls == []


def test_shift_f11_enters_and_leaves_fullscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _default_harness(monkeypatch)
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F11)
    h.keyup(_FakeSDL.SDLK_F11)
    h.keydown(_FakeSDL.SDLK_F11)
    h.keyup(_FakeSDL.SDLK_F11)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    assert _FullscreenSDL.calls == [_FullscreenSDL.SDL_WINDOW_FULLSCREEN_DESKTOP, 0]
    assert h.hotkeys is not None and not h.hotkeys.user_paused
    assert _no_cell_asserted(h)


def test_plain_f11_does_not_toggle_fullscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _default_harness(monkeypatch)
    h.keydown(_FakeSDL.SDLK_F11)
    assert _FullscreenSDL.calls == []


def test_remapped_action_fires_on_its_new_key(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _default_harness(monkeypatch, {"mute": "shift+f8"})
    assert h.hotkeys is not None
    h.keydown(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F8)             # save_state would fail the test
    assert h.hotkeys.muted
    h.keyup(_FakeSDL.SDLK_F8)
    h.keyup(_FakeSDL.SDLK_LSHIFT)
    h.keydown(_FakeSDL.SDLK_F12)            # F12 is no longer bound
    assert h.hotkeys.muted


def test_built_in_table_with_no_config() -> None:
    from msx.app_config import AppConfig

    f6, f7, f11, f12 = (_FakeSDL.SDLK_F6, _FakeSDL.SDLK_F7, _FakeSDL.SDLK_F11,
                        _FakeSDL.SDLK_F12)
    from frontend.sdl2_keyboard import resolve_hotkeys

    assert resolve_hotkeys(AppConfig().hotkey_bindings()) == {
        (f6, False): "disk_next", (f6, True): "disk_eject",
        (f7, False): "fast_forward_hold", (f7, True): "fast_forward_lock",
        (f11, False): "pause", (f11, True): "fullscreen",
        (f12, False): "mute", (f12, True): "reset",
    }
