"""Ctrl+F1..F5 and Right Alt hotkey combo tests for `_handle_events`.

Exercises the real `InputState` matrix through a minimal fake `sdl2` module:
events are scripted as (type, sym, mod) tuples and copied into a real ctypes
`_Event` structure by a fake `SDL_PollEvent`, so `ctypes.byref(event)` inside
`_handle_events` works exactly as it does against the real SDL2 library.
"""
import ctypes

from frontend.sdl2_frontend import _CtrlComboState, _handle_events
from msx.input import KEY_MATRIX_INT, KEY_NAME_TO_CELL, InputState


class _Keysym(ctypes.Structure):
    _fields_ = [("sym", ctypes.c_int), ("mod", ctypes.c_uint16)]


class _KeyEvent(ctypes.Structure):
    _fields_ = [("keysym", _Keysym)]


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
    `_Harness` on the same basis (only exercised by the F11 and joystick
    branches, neither of which any test here triggers).
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
    SDLK_F8 = -8
    SDLK_F9 = -9
    SDLK_F10 = -10
    SDLK_F11 = -11
    SDLK_c = ord("c")
    SDLK_q = ord("q")
    SDLK_LCTRL = 1073742048
    SDLK_RCTRL = 1073742052
    SDLK_LALT = 1073742050
    SDLK_RALT = 1073742054
    KMOD_CTRL = 0x0040

    def __init__(self, script: list[tuple[int, int, int]], event: _Event) -> None:
        self._script = list(script)
        self._event = event

    def SDL_PollEvent(self, _event_ptr: object) -> int:
        if not self._script:
            return 0
        ev_type, sym, mod = self._script.pop(0)
        self._event.type = ev_type
        self._event.key.keysym.sym = sym
        self._event.key.keysym.mod = mod
        return 1


class _FakeMachine:
    def __init__(self) -> None:
        self.input = InputState()
        self._debugger = None


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

    def __init__(self) -> None:
        self.machine = _FakeMachine()
        self.ctrl_combo = _CtrlComboState()
        self._event = _Event()
        self.running = True

    def send(self, script: list[tuple[int, int, int]]) -> None:
        sdl = _FakeSDL(script, self._event)
        self.running, _ = _handle_events(
            sdl, self._event, self.machine, None, None, self.ctrl_combo,
            _CTRL_FKEY_CELLS, "", b"", 0, 0, False,
        )

    def keydown(self, sym: int, mod: int = 0) -> None:
        self.send([(_FakeSDL.SDL_KEYDOWN, sym, mod)])

    def keyup(self, sym: int, mod: int = 0) -> None:
        self.send([(_FakeSDL.SDL_KEYUP, sym, mod)])

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
# config.reserved_hotkey_keys claims F8/F9/F10/F11 for non-MSX app commands
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
    # values against the real pysdl2 library, not just this file's fake
    # (whose own SDLK_F8..F11 sentinels are deliberately unrelated integers
    # -- see _FakeSDL's docstring). Also guards against the reserved set
    # drifting out of sync with the real SDL2 F8-F11 keycodes (SDL_SCANCODE_
    # F8..F11 = 65..68, so SDLK = 0x40000000 | scancode = 1073741889..92).
    # ESC/Ctrl-Q are deliberately excluded: ESC has its own MSX matrix cell
    # and is not reserved, and Ctrl-Q is modifier-gated rather than a bare
    # keycode (see reserved_hotkey_keys's own comment).
    import sdl2

    assert {
        sdl2.SDLK_F8, sdl2.SDLK_F9, sdl2.SDLK_F10, sdl2.SDLK_F11,
    } == {1073741889, 1073741890, 1073741891, 1073741892}


def test_ctrl_keycodes_match_real_sdl2_constants() -> None:
    # Pins allium/frontend.allium's config.lctrl_keycode/rctrl_keycode
    # against both the real pysdl2 library and msx.input's own constants.
    import sdl2

    from msx.input import _K_LCTRL, _K_RCTRL

    assert sdl2.SDLK_LCTRL == _K_LCTRL == 1073742048
    assert sdl2.SDLK_RCTRL == _K_RCTRL == 1073742052
