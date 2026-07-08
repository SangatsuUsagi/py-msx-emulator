from __future__ import annotations

from dataclasses import dataclass, field

# SDL2 SDLK key constants as integers so sdl2 is not a hard dependency at import time.
# ASCII-range keys share values between SDL2 and pygame; special keys use SDL2 SDLK_* values.
# SDL2 uses 0x40000000 | SDL_SCANCODE for non-ASCII keys.
_K_BACKSPACE = 8
_K_TAB = 9
_K_RETURN = 13
_K_ESCAPE = 27
_K_SPACE = 32
_K_QUOTE = 39
_K_COMMA = 44
_K_MINUS = 45
_K_PERIOD = 46
_K_SLASH = 47
_K_0 = 48
_K_1 = 49
_K_2 = 50
_K_3 = 51
_K_4 = 52
_K_5 = 53
_K_6 = 54
_K_7 = 55
_K_8 = 56
_K_9 = 57
_K_SEMICOLON = 59
_K_EQUALS = 61
_K_LEFTBRACKET = 91
_K_BACKSLASH = 92
_K_RIGHTBRACKET = 93
_K_BACKQUOTE = 96
_K_a = 97
_K_b = 98
_K_c = 99
_K_d = 100
_K_e = 101
_K_f = 102
_K_g = 103
_K_h = 104
_K_i = 105
_K_j = 106
_K_k = 107
_K_l = 108
_K_m = 109
_K_n = 110
_K_o = 111
_K_p = 112
_K_q = 113
_K_r = 114
_K_s = 115
_K_t = 116
_K_u = 117
_K_v = 118
_K_w = 119
_K_x = 120
_K_y = 121
_K_z = 122
_K_DELETE = 127
# SDL2 SDLK values for non-ASCII keys (0x40000000 | SDL_SCANCODE_*)
_K_CAPSLOCK = 1073741881   # SDL_SCANCODE_CAPSLOCK = 57
_K_F1       = 1073741882   # SDL_SCANCODE_F1 = 58
_K_F2       = 1073741883
_K_F3       = 1073741884
_K_F4       = 1073741885
_K_F5       = 1073741886
_K_HOME     = 1073741898   # SDL_SCANCODE_HOME = 74
_K_INSERT   = 1073741897   # SDL_SCANCODE_INSERT = 73
_K_LEFT     = 1073741904   # SDL_SCANCODE_LEFT = 80
_K_RIGHT    = 1073741903   # SDL_SCANCODE_RIGHT = 79
_K_DOWN     = 1073741905   # SDL_SCANCODE_DOWN = 81
_K_UP       = 1073741906   # SDL_SCANCODE_UP = 82
_K_LCTRL    = 1073742048   # SDL_SCANCODE_LCTRL = 224
_K_LSHIFT   = 1073742049   # SDL_SCANCODE_LSHIFT = 225
_K_LALT     = 1073742050   # SDL_SCANCODE_LALT = 226
_K_RCTRL    = 1073742052   # SDL_SCANCODE_RCTRL = 228
_K_RSHIFT   = 1073742053   # SDL_SCANCODE_RSHIFT = 229

# MSX keyboard matrix: maps an SDL2 key constant to a (row, bit) cell.
# Active-low: a cleared bit = key pressed.
#
# The base cells for digits, letters (A-Z), modifiers, function keys, arrows and
# editing keys are identical between the International and Japanese (JIS) MSX
# keyboards. Only a handful of row-1/row-2 symbol cells differ, so the shared
# cells live in _COMMON_MATRIX and the layout-specific symbols are overlaid from
# _INT_SYMBOLS / _JP_SYMBOLS. InputState selects one via keyboard_type.
# (openMSX unicodemap.int / unicodemap.jp_jis; map.grauw.nl keymatrix.)
_COMMON_MATRIX: dict[int, tuple[int, int]] = {
    # Row 0: digits 0-7
    _K_0: (0, 0), _K_1: (0, 1), _K_2: (0, 2), _K_3: (0, 3),
    _K_4: (0, 4), _K_5: (0, 5), _K_6: (0, 6), _K_7: (0, 7),
    # Row 1: 8, 9 and the symbols common to both layouts
    _K_8: (1, 0),
    _K_9: (1, 1),
    _K_MINUS: (1, 2),       # -
    _K_BACKSLASH: (1, 4),   # \ (int) / ¥ (jp) — same cell, same 0x5C code
    _K_SEMICOLON: (1, 7),   # ;
    # Row 2: common symbols + A, B
    _K_COMMA: (2, 2),       # ,
    _K_PERIOD: (2, 3),      # .
    _K_SLASH: (2, 4),       # /
    _K_a: (2, 6),
    _K_b: (2, 7),
    # Row 3: C-J
    _K_c: (3, 0), _K_d: (3, 1), _K_e: (3, 2), _K_f: (3, 3),
    _K_g: (3, 4), _K_h: (3, 5), _K_i: (3, 6), _K_j: (3, 7),
    # Row 4: K-R
    _K_k: (4, 0), _K_l: (4, 1), _K_m: (4, 2), _K_n: (4, 3),
    _K_o: (4, 4), _K_p: (4, 5), _K_q: (4, 6), _K_r: (4, 7),
    # Row 5: S-Z
    _K_s: (5, 0), _K_t: (5, 1), _K_u: (5, 2), _K_v: (5, 3),
    _K_w: (5, 4), _K_x: (5, 5), _K_y: (5, 6), _K_z: (5, 7),
    # Row 6: modifiers and F1-F3
    _K_LSHIFT: (6, 0), _K_RSHIFT: (6, 0),
    _K_LCTRL: (6, 1), _K_RCTRL: (6, 1),
    _K_LALT: (6, 2),   # left Alt/Option → MSX GRAPH
    _K_CAPSLOCK: (6, 3),
    _K_F1: (6, 5), _K_F2: (6, 6), _K_F3: (6, 7),
    # Row 7: F4, F5, ESC, TAB, BS, RETURN
    _K_F4: (7, 0),
    _K_F5: (7, 1),
    _K_ESCAPE: (7, 2),
    _K_TAB: (7, 3),
    _K_BACKSPACE: (7, 5),
    _K_RETURN: (7, 7),
    # Row 8: space, editing keys, cursor keys
    _K_SPACE: (8, 0),
    _K_HOME: (8, 1),
    _K_INSERT: (8, 2),
    _K_DELETE: (8, 3),
    _K_LEFT: (8, 4),
    _K_UP: (8, 5),
    _K_DOWN: (8, 6),
    _K_RIGHT: (8, 7),
}

# International layout: '=' [ ] ' ` occupy row-1/row-2 cells; apostrophe is a
# dedicated key (JIS has none — there it is Shift+7).
_INT_SYMBOLS: dict[int, tuple[int, int]] = {
    _K_EQUALS: (1, 3),        # =
    _K_LEFTBRACKET: (1, 5),   # [
    _K_RIGHTBRACKET: (1, 6),  # ]
    _K_QUOTE: (2, 0),         # '
    _K_BACKQUOTE: (2, 1),     # `
}

# Japanese (JIS) layout: '[' and ']' sit at different cells; '=', "'" and '`'
# have no direct JIS key and are left unmapped.
_JP_SYMBOLS: dict[int, tuple[int, int]] = {
    _K_LEFTBRACKET: (1, 6),   # [
    _K_RIGHTBRACKET: (2, 1),  # ]
}

KEY_MATRIX_INT: dict[int, tuple[int, int]] = {**_COMMON_MATRIX, **_INT_SYMBOLS}
KEY_MATRIX_JP: dict[int, tuple[int, int]] = {**_COMMON_MATRIX, **_JP_SYMBOLS}

# JOY_MAP[key] = (port, bit)  port 0=Joy1, 1=Joy2
# Per-joystick 6-bit active-low layout (bits 0-5 of the selected port):
#   bit0=Up, bit1=Down, bit2=Left, bit3=Right, bit4=Trigger A, bit5=Trigger B
#
# PSG register 14 (PORT A) returns the *selected* port's six signals on bits
# 0-5; JOY_SELECT (PSG register 15 bit 6) picks the port (0 → Joy1, 1 → Joy2).
# Bits 6-7 are not joystick lines (PSG.read_port pulls them high).
JOY_MAP: dict[int, tuple[int, int]] = {
    _K_w:      (0, 0),  # Joy1 Up
    _K_s:      (0, 1),  # Joy1 Down
    _K_a:      (0, 2),  # Joy1 Left
    _K_d:      (0, 3),  # Joy1 Right
    _K_z:      (0, 4),  # Joy1 Trigger A
    _K_COMMA:  (0, 4),  # Joy1 Trigger A (alternate)
    _K_x:      (0, 5),  # Joy1 Trigger B
    _K_PERIOD: (0, 5),  # Joy1 Trigger B (alternate)
    _K_UP:     (0, 0),  # Joy1 Up (alternate)
    _K_DOWN:   (0, 1),  # Joy1 Down (alternate)
    _K_LEFT:   (0, 2),  # Joy1 Left (alternate)
    _K_RIGHT:  (0, 3),  # Joy1 Right (alternate)
}

_NUM_ROWS = 11


@dataclass
class InputState:
    matrix: list[int] = field(default_factory=lambda: [0xFF] * _NUM_ROWS)
    # Keyboard layout: "int" (International) or "jp" (Japanese/JIS). Selects
    # which key→matrix table key_down/key_up use.
    keyboard_type: str = "int"
    # Per-joystick 6-bit active-low state: bits 0-5 = up/down/left/right/trigA/trigB
    _joy1_kbd: int = field(default=0x3F, init=False, repr=False)
    _joy1_hw:  int = field(default=0x3F, init=False, repr=False)
    _joy2_kbd: int = field(default=0x3F, init=False, repr=False)
    _joy2_hw:  int = field(default=0x3F, init=False, repr=False)
    _matrix_map: dict[int, tuple[int, int]] = field(
        default_factory=lambda: KEY_MATRIX_INT, init=False, repr=False
    )
    # Currently held matrix keys, so shared cells (LSHIFT/RSHIFT → (6,0),
    # LCTRL/RCTRL → (6,1)) only release when every mapped key is released.
    _held_keys: set[int] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self._matrix_map = KEY_MATRIX_JP if self.keyboard_type == "jp" else KEY_MATRIX_INT

    @property
    def joy1(self) -> int:
        return self._joy1_kbd & self._joy1_hw

    @property
    def joy2(self) -> int:
        return self._joy2_kbd & self._joy2_hw

    def key_down(self, key: int) -> None:
        if key in self._matrix_map:
            self._held_keys.add(key)
            row, bit = self._matrix_map[key]
            self.matrix[row] &= ~(1 << bit) & 0xFF
        if key in JOY_MAP:
            port, bit = JOY_MAP[key]
            if port == 0:
                self._joy1_kbd &= ~(1 << bit) & 0x3F
            else:
                self._joy2_kbd &= ~(1 << bit) & 0x3F

    def key_up(self, key: int) -> None:
        if key in self._matrix_map:
            self._held_keys.discard(key)
            row, bit = self._matrix_map[key]
            # Only release the matrix bit when no other held key shares this
            # cell (LSHIFT/RSHIFT and LCTRL/RCTRL each share one cell).
            cell = (row, bit)
            still_held = any(self._matrix_map.get(k) == cell for k in self._held_keys)
            if not still_held:
                self.matrix[row] |= (1 << bit)
        if key in JOY_MAP:
            port, bit = JOY_MAP[key]
            if port == 0:
                self._joy1_kbd |= (1 << bit)
            else:
                self._joy2_kbd |= (1 << bit)

    def joystick_button_down(self, port: int, bit: int) -> None:
        if port == 0:
            self._joy1_hw &= ~(1 << bit) & 0x3F
        else:
            self._joy2_hw &= ~(1 << bit) & 0x3F

    def joystick_button_up(self, port: int, bit: int) -> None:
        if port == 0:
            self._joy1_hw |= (1 << bit)
        else:
            self._joy2_hw |= (1 << bit)
