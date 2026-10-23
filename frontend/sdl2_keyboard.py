"""SDL2 host keyboard: host key codes to MSX matrix cells and joystick lines.

The core InputState takes keyboard input only as matrix cells
(set_key_state) and keyboard-sourced joystick lines
(keyboard_joystick_down/up). Everything keyed by SDL key codes lives here:
the INT/JP matrix tables, the keyboard-joystick overlay, the shared
SHIFT/CTRL cells and the JIS SHIFT+"0" redirect.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from msx.input import InputState

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
_K_COLON = 58
_K_SEMICOLON = 59
_K_EQUALS = 61
_K_AT = 64
_K_LEFTBRACKET = 91
_K_BACKSLASH = 92
_K_RIGHTBRACKET = 93
_K_CARET = 94
_K_UNDERSCORE = 95
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
_K_RALT     = 1073742054   # SDL_SCANCODE_RALT = 230
_K_YEN      = 1073741961   # SDL_SCANCODE_INTERNATIONAL3 = 137 (JIS ¥ key, distinct
                           # from backslash — a dedicated key/scancode on real JIS
                           # hardware, not an alternate character on the backslash key)

# Public sentinel for the JIS ¥ key. On macOS, SDL2 reports this key with a
# consistent scancode (SDL_SCANCODE_INTERNATIONAL3) but an inconsistent
# keysym.sym -- observed as both SDLK_UNKNOWN (0, host layout misdetected as
# US) and the Unicode YEN SIGN codepoint (165, U+00A5, host layout correctly
# resolved). A frontend cannot rely on keysym.sym to identify this key;
# it must detect the scancode itself and pass this constant to key_down/
# key_up instead of the raw (unreliable) sym.
SDLK_JIS_YEN: int = _K_YEN

_K_APOSTROPHE_KEY = 1073741876   # 0x40000000 | SDL_SCANCODE_APOSTROPHE (52)

# Public sentinel for the host's dedicated apostrophe/quote key, identified
# by scancode rather than sym. On a JIS input source (see SDLK_JIS_YEN's
# macOS note above), that host key and SHIFT+"7" both report keysym.sym ==
# SDLK_QUOTE -- two different physical actions colliding on one sym -- so
# _JP_SYMBOLS cannot bind SDLK_QUOTE itself to more than one cell. A
# frontend must detect this key by scancode and pass this constant to
# key_down/key_up instead of the ambiguous sym, exactly as SDLK_JIS_YEN does.
SDLK_HOST_APOSTROPHE_KEY: int = _K_APOSTROPHE_KEY

_K_RO = 1073741959   # 0x40000000 | SDL_SCANCODE_INTERNATIONAL1 (135)

# Public sentinel for the JIS "\"/"_" key (to the right of "/", left of
# right-Shift on a 106-key board), identified by scancode rather than sym --
# like SDLK_JIS_YEN, this key's sym is unreliable (probe-confirmed against
# real JIS hardware: keysym.sym == 0, constant across SHIFT), so a frontend
# must detect it via scancode and pass this constant to key_down/key_up
# instead of the raw sym. JIS only: an International/US host never reports
# this scancode, since it has no key at that position.
SDLK_JIS_RO: int = _K_RO

# Internal-only sentinel for InputState's SHIFT+"0" -> "_" escape hatch (see
# SdlKeyboard.key_down). Not a real SDL keycode -- every real SDL key
# constant is non-negative -- so it cannot collide with any host key.
# Callers never pass this value directly; SdlKeyboard substitutes it
# internally, never exported.
_K_ZERO_SHIFT = -1

# MSX keyboard matrix: maps an SDL2 key constant to a (row, bit) cell.
# Active-low: a cleared bit = key pressed.
#
# The base cells for digits, letters (A-Z), modifiers, function keys, arrows and
# editing keys are identical between the International and Japanese (JIS) MSX
# keyboards. Only a handful of row-1/row-2 symbol cells differ, so the shared
# cells live in _COMMON_MATRIX and the layout-specific symbols are overlaid from
# _INT_SYMBOLS / _JP_SYMBOLS. SdlKeyboard selects one via the machine's keyboard_type.
# (openMSX unicodemap.int / unicodemap.jp_jis; map.grauw.nl keymatrix.)
#
# PORT-LIBRARY-NOTE: _COMMON_MATRIX/_INT_SYMBOLS/_JP_SYMBOLS/KEY_MATRIX_INT/
#   KEY_MATRIX_JP/JOY_MAP/KEY_NAME_TO_SDLKEY (this file; KEY_NAME_TO_CELL in msx/input.py) are
#   sizable dict literals used as static, immutable lookup tables, built once
#   at import via MappingProxyType.
# Rust crate candidates: phf (compile-time perfect-hash maps) for the same
#   "immutable static lookup table" shape with zero runtime construction cost.
# C++ library candidates: none needed -- a constexpr array + linear/binary
#   search, or a std::unordered_map initialized once at static-init, covers
#   this without an external dependency.
# Not adopted now because: these tables are small enough that Python's plain
#   dict is already fine performance-wise; noted for the port, not a gap in
#   the current implementation.
_COMMON_MATRIX: dict[int, tuple[int, int]] = {
    # Row 0: digits 0-7
    _K_0: (0, 0), _K_1: (0, 1), _K_2: (0, 2), _K_3: (0, 3),
    _K_4: (0, 4), _K_5: (0, 5), _K_6: (0, 6), _K_7: (0, 7),
    # Row 1: 8, 9 and the symbols common to both layouts
    _K_8: (1, 0),
    _K_9: (1, 1),
    _K_MINUS: (1, 2),       # -
    _K_BACKSLASH: (1, 4),   # \ (International layout backslash key)
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
    _K_RALT: (6, 4),   # right Alt/Option → MSX CODE/KANA
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
# dedicated key (JIS has none — there it is Shift+7). Row 2 bit 5 (the
# International DEAD/£/accent-composer key, per allium/ppi.allium's
# Deferred Specifications) is left unmapped: it has no single corresponding ASCII
# character on any host layout, unlike every other cell here, so there is
# no SDL keycode to bind it to.
#
# SDLK_HOST_APOSTROPHE_KEY (scancode-based, see above) is bound to the same
# cell as the plain _K_QUOTE sym it duplicates, as a defensive fallback --
# both name the same physical key here, so it is harmless if either arrives.
_INT_SYMBOLS: dict[int, tuple[int, int]] = {
    _K_EQUALS: (1, 3),           # =
    _K_LEFTBRACKET: (1, 5),      # [
    _K_RIGHTBRACKET: (1, 6),     # ]
    _K_QUOTE: (2, 0),            # '
    _K_APOSTROPHE_KEY: (2, 0),   # ' (scancode fallback, see SDLK_HOST_APOSTROPHE_KEY)
    _K_BACKQUOTE: (2, 1),        # `
}

# Japanese (JIS) layout: '[' and ']' sit at different cells. '@', '^', ':'
# and '_' are real ASCII characters, so unlike the International DEAD cell
# above they map onto ordinary SDL keycodes regardless of host layout.
#
# "=" IS reachable on real JIS hardware without a dedicated cell of its own:
# JIS "=" is SHIFT plus the row-1 bit-2 cell ("-"). Unlike the apostrophe
# case below, this genuinely needs no alias here -- probed empirically
# (scancode 45, sym 45, both SHIFT states): the "-" key's own sym does not
# change under SHIFT, so _COMMON_MATRIX's plain _K_MINUS entry combined with
# a real SHIFT keypress already reaches the row-1 bit-2 cell correctly.
#
# Apostrophe works the same way on real JIS hardware (SHIFT plus the
# row-0 bit-7 cell, "7") -- probe-confirmed. The _K_QUOTE alias below is a
# harmless belt-and-braces entry either way: if SHIFT+"7" replays plain "7"
# under SHIFT on some host, _COMMON_MATRIX's _K_7 entry already reaches this
# cell and the alias is simply never hit; on a host where it instead
# collapses to a distinct sym the way the host's dedicated apostrophe key
# does (see next paragraph), the alias is what makes it work.
#
# The host's dedicated apostrophe/quote key (SDLK_HOST_APOSTROPHE_KEY,
# scancode-based, see above) reports keysym.sym == SDLK_QUOTE regardless of
# SHIFT, which collides with SHIFT+"7"'s own sym on a JIS input source -- so
# it can't also be bound to the row-0 bit-7 cell without risking swallowing
# that path; only scancode tells the two apart. Likewise the host's "`"/"~"
# key has no JIS keyboard key of its own at that host position, so it is
# deliberately repurposed below rather than left dead: the apostrophe key
# onto ":"/"*" (row 2 bit 0, next to ";" the way "'"/'"' sits next to ";" on
# an International host keyboard), "`"/"~" onto "^" (row 1 bit 3, JIS's own
# unicode-real cell that a plain International/US keyboard has no dedicated
# key for). Keytop label mismatch is accepted here in exchange for the
# character being reachable at all.
#
# "^" is also reachable directly through the real JIS "^"/"~" key's own sym
# (_K_CARET, 94, SHIFT-invariant -- probe-confirmed against real JIS
# hardware) with no scancode involvement -- but this cannot be relied on as
# the only path: on a real macOS/SDL2 quirk (see README's Known
# limitations), the host keyboard layout can be misdetected, in which case
# this same key instead reports SDLK_EQUALS (61), same as an International
# keyboard's dead "=" key, and "^" becomes unreachable through it entirely
# until the user works around the misdetection (e.g. switching input source
# away and back). This table's "`" repurposing was briefly removed for
# readability (git history), then reinstated once real-hardware testing hit
# exactly that failure mode -- it stays as the only reliable fallback for a
# host that hasn't (yet) correctly recognized the keyboard layout.
#
# "_" is instead reached via the JIS "\"/"_" key itself (SDLK_JIS_RO,
# scancode-based like SDLK_JIS_YEN -- its sym is unreliable) bound directly
# to row 2 bit 5: unshifted asserts the cell alone (JIS BIOS renders
# nothing, matching the real key), SHIFT+cell renders "_", exactly
# reproducing real JIS underscore-key behaviour. For a host with no such
# key at all (an International/US keyboard, possibly under a JIS input
# source), InputState's SHIFT+"0" escape hatch reaches the same cell --
# see SdlKeyboard.key_down's own comment.
#
# The ¥ key is different: on real JIS hardware it is a dedicated key with its
# own scancode (SDL_SCANCODE_INTERNATIONAL3), not an alternate character on
# the backslash key, so it needs its own entry (_K_YEN) rather than sharing
# _K_BACKSLASH with the International layout.
_JP_SYMBOLS: dict[int, tuple[int, int]] = {
    _K_LEFTBRACKET: (1, 6),      # [
    _K_RIGHTBRACKET: (2, 1),     # ]
    _K_AT: (1, 5),               # @
    _K_CARET: (1, 3),            # ^
    _K_COLON: (2, 0),            # :
    _K_UNDERSCORE: (2, 5),       # _
    _K_YEN: (1, 4),              # ¥ (same MSX matrix cell as International \)
    _K_QUOTE: (0, 7),            # ' (belt-and-braces; see above)
    _K_APOSTROPHE_KEY: (2, 0),   # : / * (repurposed host apostrophe key; see above)
    _K_BACKQUOTE: (1, 3),        # ^ / ~ (repurposed host `/~ key; see above)
    _K_RO: (2, 5),               # \ / _ (real JIS key, scancode fallback; see above)
    # Host-only escape hatch, JIS only: SdlKeyboard.key_down redirects "0" to
    # this synthetic key when SHIFT is held, reaching the "_" cell. Safe here
    # because row 0 bit 0 has no SHIFT character on JIS (openMSX
    # unicodemap.jp_jis); on International SHIFT+"0" is ")" (unicodemap.int),
    # so KEY_MATRIX_INT has no entry and the redirect does not apply there.
    _K_ZERO_SHIFT: (2, 5),
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
#
# MappingProxyType: every default-constructed InputState (and every
# AppConfig.keyboard_joy_map() call with no overrides) shares this exact
# object rather than a copy — the proxy makes accidental in-place mutation of
# the shared default raise immediately instead of silently corrupting it for
# every other instance.
JOY_MAP: Mapping[int, tuple[int, int]] = MappingProxyType({
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
})

# Keyboard key name → SDL2 key constant, for resolving config-file key names
# (py_emulator.yaml `keyboard_joystick.buttons`) to the codes key_down/key_up
# accept. Covers the keys reachable by the built-in JOY_MAP plus digits, so a
# config override can pick any letter, digit, arrow, comma, or period key.
KEY_NAME_TO_SDLKEY: dict[str, int] = {
    "a": _K_a, "b": _K_b, "c": _K_c, "d": _K_d, "e": _K_e, "f": _K_f, "g": _K_g,
    "h": _K_h, "i": _K_i, "j": _K_j, "k": _K_k, "l": _K_l, "m": _K_m, "n": _K_n,
    "o": _K_o, "p": _K_p, "q": _K_q, "r": _K_r, "s": _K_s, "t": _K_t, "u": _K_u,
    "v": _K_v, "w": _K_w, "x": _K_x, "y": _K_y, "z": _K_z,
    "0": _K_0, "1": _K_1, "2": _K_2, "3": _K_3, "4": _K_4,
    "5": _K_5, "6": _K_6, "7": _K_7, "8": _K_8, "9": _K_9,
    "up": _K_UP, "down": _K_DOWN, "left": _K_LEFT, "right": _K_RIGHT,
    "comma": _K_COMMA, "period": _K_PERIOD,
}

# MSX Joy1 function -> port bit, for keyboard_joystick.buttons config names.
_KEYBOARD_JOY_FUNCTIONS: dict[str, int] = {
    "up": 0, "down": 1, "left": 2, "right": 3, "trigger_a": 4, "trigger_b": 5,
}


def resolve_keyboard_joy_map(buttons: Mapping[str, str]) -> Mapping[int, tuple[int, int]]:
    """Resolve keyboard_joystick.buttons (function -> key name) to a key map.

    Each configured function's default key(s) are dropped and replaced with
    its configured key. The config loader has already rejected unknown names
    and duplicate keys.

    Returns:
        JOY_MAP unchanged when nothing is configured, otherwise a resolved
        ``sdlkey -> (port, bit)`` map.
    """
    if not buttons:
        return JOY_MAP
    overridden_bits = set()
    for func in buttons:
        overridden_bits.add(_KEYBOARD_JOY_FUNCTIONS[func])
    result: dict[int, tuple[int, int]] = {}
    for sdlkey, (port, bit) in JOY_MAP.items():
        if not (port == 0 and bit in overridden_bits):
            result[sdlkey] = (port, bit)
    for func, key_name in buttons.items():
        result[KEY_NAME_TO_SDLKEY[key_name]] = (0, _KEYBOARD_JOY_FUNCTIONS[func])
    return result


# Function-key name -> SDL keycode (0x40000000 | SDL_SCANCODE_Fn; F1 is 58),
# for the names host/app_config.py's FKEY_NAMES accepts.
FKEY_NAME_TO_SDLKEY: dict[str, int] = {}
for _n in range(1, 13):
    FKEY_NAME_TO_SDLKEY[f"f{_n}"] = 0x40000000 | (57 + _n)


def resolve_hotkeys(bindings: Mapping[tuple[str, bool], str]) -> dict[tuple[int, bool], str]:
    """Turn ``(function-key name, Shift held) -> action`` into key codes."""
    result: dict[tuple[int, bool], str] = {}
    for (key_name, shift), action in bindings.items():
        result[(FKEY_NAME_TO_SDLKEY[key_name], shift)] = action
    return result


@dataclass
class SdlKeyboard:
    """Feeds SDL key events into an InputState as matrix cells and joystick lines.

    The matrix table is chosen once from the machine's keyboard_type.
    """

    input_state: InputState
    # Joy1 keyboard overlay: SDL key -> (port, bit). Defaults to JOY_MAP; a
    # config-resolved map replaces it wholesale (resolve_keyboard_joy_map).
    joy_map: Mapping[int, tuple[int, int]] = field(default_factory=lambda: JOY_MAP)
    _matrix_map: dict[int, tuple[int, int]] = field(
        default_factory=lambda: KEY_MATRIX_INT, init=False, repr=False
    )
    # Currently held matrix keys, so shared cells (LSHIFT/RSHIFT -> (6,0),
    # LCTRL/RCTRL -> (6,1)) only release when every mapped key is released.
    _held_keys: set[int] = field(default_factory=set, init=False, repr=False)
    # True while a "0" key_down was redirected to _K_ZERO_SHIFT (see
    # key_down below), so the matching key_up releases the same cell even if
    # SHIFT was released first.
    _zero_shift_redirect: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        jp = self.input_state.keyboard_type == "jp"
        self._matrix_map = KEY_MATRIX_JP if jp else KEY_MATRIX_INT

    def _shift_held(self) -> bool:
        return _K_LSHIFT in self._held_keys or _K_RSHIFT in self._held_keys

    def key_down(self, key: int) -> None:
        # SHIFT+"0" escape hatch (see _K_ZERO_SHIFT): redirected here rather
        # than in _matrix_map, since a static key->cell table can't express a
        # binding that depends on another key's current state. Latched into
        # _zero_shift_redirect so the matching key_up releases the same cell
        # regardless of what SHIFT is doing by then.
        matrix_key = key
        if key == _K_0 and self._shift_held() and _K_ZERO_SHIFT in self._matrix_map:
            matrix_key = _K_ZERO_SHIFT
            self._zero_shift_redirect = True
        if matrix_key in self._matrix_map:
            self._held_keys.add(matrix_key)
            row, bit = self._matrix_map[matrix_key]
            self.input_state.set_key_state(row, bit, True)
        if key in self.joy_map:
            port, bit = self.joy_map[key]
            self.input_state.keyboard_joystick_down(port, bit)

    def key_up(self, key: int) -> None:
        matrix_key = key
        if key == _K_0 and self._zero_shift_redirect:
            matrix_key = _K_ZERO_SHIFT
            self._zero_shift_redirect = False
        if matrix_key in self._matrix_map:
            self._held_keys.discard(matrix_key)
            row, bit = self._matrix_map[matrix_key]
            # Only release the matrix bit when no other held key shares this
            # cell (LSHIFT/RSHIFT and LCTRL/RCTRL each share one cell).
            cell = (row, bit)
            still_held = False
            for held in self._held_keys:
                if self._matrix_map.get(held) == cell:
                    still_held = True
                    break
            if not still_held:
                self.input_state.set_key_state(row, bit, False)
        if key in self.joy_map:
            port, bit = self.joy_map[key]
            self.input_state.keyboard_joystick_up(port, bit)
