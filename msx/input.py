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
_K_COMMA = 44
_K_PERIOD = 46
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

# MSX keyboard matrix: KEY_MATRIX[pygame_key] = (row, bit)
# Active-low: bit cleared = key pressed
#
# MSX keyboard matrix layout (11 rows × 8 bits, per MSX Technical Handbook):
# Row 0:  bit7=7    bit6=6    bit5=5    bit4=4    bit3=3    bit2=2    bit1=1    bit0=0
# Row 1:  bit7=;    bit6=]    bit5=[    bit4=\    bit3==    bit2=-    bit1=9    bit0=8
# Row 2:  bit7=B    bit6=A    bit5=`    bit4=ESC  bit3=BS   bit2=/    bit1=.    bit0=,
# Row 3:  bit7=J    bit6=I    bit5=H    bit4=G    bit3=F    bit2=E    bit1=D    bit0=C
# Row 4:  bit7=R    bit6=Q    bit5=P    bit4=O    bit3=N    bit2=M    bit1=L    bit0=K
# Row 5:  bit7=Z    bit6=Y    bit5=X    bit4=W    bit3=V    bit2=U    bit1=T    bit0=S
# Row 6:  bit7=F3   bit6=F2   bit5=F1   bit4=DEAD bit3=CAPS bit2=GRP  bit1=CTRL bit0=SHIFT
# Row 7:  bit7=RET  bit6=SEL  bit5=BS   bit4=STOP bit3=TAB  bit2=ESC  bit1=F5   bit0=F4
# Row 8:  bit7=RIGHT bit6=DOWN bit5=UP  bit4=LEFT bit3=DEL  bit2=INS  bit1=HOME bit0=SPACE
# Row 9:  bit7=NUM9 bit6=NUM8 bit5=NUM7 bit4=NUM6 bit3=NUM5 bit2=NUM4 bit1=NUM3 bit0=NUM2
# Row 10: bit7=NUM. bit6=NUM, bit5=NUM- bit4=NUM+ bit3=NUM* bit2=NUM/ bit1=NUM1 bit0=NUM0
KEY_MATRIX: dict[int, tuple[int, int]] = {
    # Row 0: digits 0-7
    _K_0: (0, 0),
    _K_1: (0, 1),
    _K_2: (0, 2),
    _K_3: (0, 3),
    _K_4: (0, 4),
    _K_5: (0, 5),
    _K_6: (0, 6),
    _K_7: (0, 7),
    # Row 1: digits 8-9 and punctuation
    _K_8: (1, 0),
    _K_9: (1, 1),
    _K_SEMICOLON: (1, 2),  # - (MSX uses this column for minus)
    _K_EQUALS: (1, 3),     # =
    _K_BACKSLASH: (1, 4),  # backslash
    _K_LEFTBRACKET: (1, 5),
    _K_RIGHTBRACKET: (1, 6),
    # Row 2: A-B and special
    _K_COMMA: (2, 0),
    _K_PERIOD: (2, 1),
    # _K_SLASH: (2, 2),  # / - no constant defined above
    _K_BACKSPACE: (2, 3),
    _K_ESCAPE: (2, 4),
    _K_BACKQUOTE: (2, 5),
    _K_a: (2, 6),
    _K_b: (2, 7),
    # Row 3: C-J
    _K_c: (3, 0),
    _K_d: (3, 1),
    _K_e: (3, 2),
    _K_f: (3, 3),
    _K_g: (3, 4),
    _K_h: (3, 5),
    _K_i: (3, 6),
    _K_j: (3, 7),
    # Row 4: K-R
    _K_k: (4, 0),
    _K_l: (4, 1),
    _K_m: (4, 2),
    _K_n: (4, 3),
    _K_o: (4, 4),
    _K_p: (4, 5),
    _K_q: (4, 6),
    _K_r: (4, 7),
    # Row 5: S-Z
    _K_s: (5, 0),
    _K_t: (5, 1),
    _K_u: (5, 2),
    _K_v: (5, 3),
    _K_w: (5, 4),
    _K_x: (5, 5),
    _K_y: (5, 6),
    _K_z: (5, 7),
    # Row 6: modifiers and F-keys F1-F3
    _K_LSHIFT: (6, 0),
    _K_RSHIFT: (6, 0),
    _K_LCTRL: (6, 1),
    _K_RCTRL: (6, 1),
    _K_CAPSLOCK: (6, 3),
    _K_F1: (6, 5),
    _K_F2: (6, 6),
    _K_F3: (6, 7),
    # Row 7: return, tab, F4, F5
    _K_F4: (7, 0),
    _K_F5: (7, 1),
    _K_TAB: (7, 3),
    _K_RETURN: (7, 7),
    # Row 8: cursor keys, editing keys, space (MSX Technical Handbook layout)
    _K_SPACE: (8, 0),
    _K_HOME: (8, 1),
    _K_INSERT: (8, 2),
    _K_DELETE: (8, 3),
    _K_LEFT: (8, 4),
    _K_UP: (8, 5),
    _K_DOWN: (8, 6),
    _K_RIGHT: (8, 7),
}

# JOY_MAP[pygame_key] = bit index in InputState.joystick (active-low)
# PSG register 14 (Port A):
#   bit0=Joy1 Up, bit1=Joy1 Down, bit2=Joy1 Left, bit3=Joy1 Right,
#   bit4=Joy1 Trigger A, bit5=Joy2 Up, bit6=Joy2 Down, bit7=Joy2 Trigger A
JOY_MAP: dict[int, int] = {
    _K_w: 0,        # Joy1 Up
    _K_s: 1,        # Joy1 Down
    _K_a: 2,        # Joy1 Left
    _K_d: 3,        # Joy1 Right
    _K_z: 4,        # Joy1 Trigger A
    _K_COMMA: 4,    # Joy1 Trigger A (alternate)
    _K_UP: 0,       # Joy1 Up (alternate, same as W)
    _K_DOWN: 1,     # Joy1 Down (alternate, same as S)
    _K_LEFT: 2,     # Joy1 Left (alternate, same as A)
    _K_RIGHT: 3,    # Joy1 Right (alternate, same as D)
    _K_x: 7,        # Joy2 Trigger A
    _K_PERIOD: 7,   # Joy2 Trigger A (alternate)
}

_NUM_ROWS = 11


@dataclass
class InputState:
    matrix: list[int] = field(default_factory=lambda: [0xFF] * _NUM_ROWS)
    joystick: int = 0xFF

    def key_down(self, key: int) -> None:
        if key in KEY_MATRIX:
            row, bit = KEY_MATRIX[key]
            self.matrix[row] &= ~(1 << bit) & 0xFF
        if key in JOY_MAP:
            bit = JOY_MAP[key]
            self.joystick &= ~(1 << bit) & 0xFF

    def key_up(self, key: int) -> None:
        if key in KEY_MATRIX:
            row, bit = KEY_MATRIX[key]
            self.matrix[row] |= (1 << bit)
        if key in JOY_MAP:
            bit = JOY_MAP[key]
            self.joystick |= (1 << bit)
