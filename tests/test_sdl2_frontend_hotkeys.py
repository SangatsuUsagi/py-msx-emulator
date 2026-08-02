"""Left Alt+F1..F5 and Right Alt hotkey combo tests for `_handle_events`.

Exercises the real `InputState` matrix through a minimal fake `sdl2` module:
events are scripted as (type, sym, mod) tuples and copied into a real ctypes
`_Event` structure by a fake `SDL_PollEvent`, so `ctypes.byref(event)` inside
`_handle_events` works exactly as it does against the real SDL2 library.
"""
import ctypes

from frontend.sdl2_frontend import _AltComboState, _handle_events
from msx.input import KEY_MATRIX_INT, KEY_NAME_TO_CELL, InputState


class _Keysym(ctypes.Structure):
    _fields_ = [("sym", ctypes.c_int), ("mod", ctypes.c_uint16)]


class _KeyEvent(ctypes.Structure):
    _fields_ = [("keysym", _Keysym)]


class _Event(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("key", _KeyEvent)]


class _FakeSDL:
    """Just enough SDL2 constant/API surface for `_handle_events`."""

    SDL_QUIT = 1
    SDL_KEYDOWN = 2
    SDL_KEYUP = 3
    SDL_CONTROLLERDEVICEADDED = 100
    SDL_CONTROLLERDEVICEREMOVED = 101
    SDL_JOYDEVICEADDED = 102
    SDL_JOYDEVICEREMOVED = 103
    SDL_CONTROLLERBUTTONDOWN = 104
    SDL_CONTROLLERBUTTONUP = 105
    SDL_CONTROLLERAXISMOTION = 106
    SDL_JOYBUTTONDOWN = 107
    SDL_JOYBUTTONUP = 108
    SDL_JOYAXISMOTION = 109
    SDL_JOYHATMOTION = 110
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


class _Harness:
    """Drives `_handle_events` across multiple scripted event batches while
    keeping the same machine/alt-combo/event state, like consecutive frames."""

    def __init__(self) -> None:
        self.machine = _FakeMachine()
        self.alt_combo = _AltComboState()
        self._event = _Event()

    def send(self, script: list[tuple[int, int, int]]) -> None:
        sdl = _FakeSDL(script, self._event)
        _handle_events(
            sdl, self._event, self.machine, None, None, self.alt_combo,
            "", b"", 0, 0, False,
        )

    @property
    def matrix(self) -> list[int]:
        return self.machine.input.matrix


_HOME = KEY_NAME_TO_CELL["HOME"]
_INS = KEY_NAME_TO_CELL["INS"]
_DEL = KEY_NAME_TO_CELL["DEL"]
_STOP = KEY_NAME_TO_CELL["STOP"]
_SELECT = KEY_NAME_TO_CELL["SELECT"]
_GRAPH = KEY_MATRIX_INT[_FakeSDL.SDLK_LALT]
_F1_CELL = KEY_MATRIX_INT[_FakeSDL.SDLK_F1]


def test_left_alt_f1_asserts_home_not_f1_or_graph() -> None:
    h = _Harness()
    h.send([
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F1, 0),
    ])
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _F1_CELL)
    assert not _asserted(h.matrix, _GRAPH)


def test_left_alt_f4_asserts_stop_and_f5_asserts_select() -> None:
    h = _Harness()
    h.send([
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F4, 0),
    ])
    assert _asserted(h.matrix, _STOP)
    h.send([(_FakeSDL.SDL_KEYUP, _FakeSDL.SDLK_F4, 0)])
    assert not _asserted(h.matrix, _STOP)
    h.send([(_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F5, 0)])
    assert _asserted(h.matrix, _SELECT)


def test_releasing_fkey_first_restores_graph() -> None:
    h = _Harness()
    h.send([
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F2, 0),
    ])
    assert _asserted(h.matrix, _INS)
    h.send([(_FakeSDL.SDL_KEYUP, _FakeSDL.SDLK_F2, 0)])
    assert not _asserted(h.matrix, _INS)
    assert _asserted(h.matrix, _GRAPH)  # Alt is still held


def test_releasing_alt_first_ends_combo_without_graph() -> None:
    h = _Harness()
    h.send([
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F3, 0),
    ])
    assert _asserted(h.matrix, _DEL)
    h.send([(_FakeSDL.SDL_KEYUP, _FakeSDL.SDLK_LALT, 0)])
    assert not _asserted(h.matrix, _DEL)
    assert not _asserted(h.matrix, _GRAPH)


def test_plain_left_alt_still_sends_graph() -> None:
    h = _Harness()
    h.send([(_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0)])
    assert _asserted(h.matrix, _GRAPH)
    h.send([(_FakeSDL.SDL_KEYUP, _FakeSDL.SDLK_LALT, 0)])
    assert not _asserted(h.matrix, _GRAPH)


def test_second_fkey_during_active_combo_is_ignored() -> None:
    h = _Harness()
    h.send([
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_LALT, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F1, 0),
        (_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F4, 0),
    ])
    assert _asserted(h.matrix, _HOME)
    assert not _asserted(h.matrix, _STOP)
    assert h.alt_combo.active_fkey == _FakeSDL.SDLK_F1


def test_fkey_without_alt_behaves_as_before() -> None:
    h = _Harness()
    h.send([(_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_F1, 0)])
    assert _asserted(h.matrix, _F1_CELL)
    assert not _asserted(h.matrix, _HOME)


def test_right_alt_sends_select_through_normal_path() -> None:
    h = _Harness()
    h.send([(_FakeSDL.SDL_KEYDOWN, _FakeSDL.SDLK_RALT, 0)])
    assert _asserted(h.matrix, _SELECT)
    h.send([(_FakeSDL.SDL_KEYUP, _FakeSDL.SDLK_RALT, 0)])
    assert not _asserted(h.matrix, _SELECT)
