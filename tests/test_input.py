from msx.input import (
    _K_COMMA,
    _K_DOWN,
    _K_LALT,
    _K_LEFT,
    _K_LEFTBRACKET,
    _K_MINUS,
    _K_QUOTE,
    _K_RIGHT,
    _K_RIGHTBRACKET,
    _K_SEMICOLON,
    _K_SLASH,
    _K_SPACE,
    _K_UP,
    JOY_MAP,
    KEY_MATRIX,
    KEY_MATRIX_INT,
    KEY_MATRIX_JP,
    InputState,
    _K_a,
    _K_s,
    _K_w,
    _K_x,
)


def make_input() -> InputState:
    return InputState()


def test_default_matrix_all_released() -> None:
    state = make_input()
    assert all(row == 0xFF for row in state.matrix)


def test_default_joy1_all_released() -> None:
    state = make_input()
    assert state.joy1 == 0x3F


def test_default_joy2_all_released() -> None:
    state = make_input()
    assert state.joy2 == 0x3F


def test_minus_key_maps_to_row1_col2() -> None:
    assert KEY_MATRIX[_K_MINUS] == (1, 2)
    state = InputState()
    state.key_down(_K_MINUS)
    assert state.matrix[1] & (1 << 2) == 0  # active-low: pressed


def test_slash_key_maps_to_row2_col4() -> None:
    # International layout: '/' is row 2, col 4 (shared with JIS).
    assert KEY_MATRIX[_K_SLASH] == (2, 4)
    state = InputState()
    state.key_down(_K_SLASH)
    assert state.matrix[2] & (1 << 4) == 0


def test_semicolon_key_maps_to_row1_col7() -> None:
    assert KEY_MATRIX[_K_SEMICOLON] == (1, 7)


def test_key_down_clears_matrix_bit() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_a]
    state.key_down(_K_a)
    assert state.matrix[row] & (1 << bit) == 0


def test_key_up_sets_matrix_bit() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_a]
    state.key_down(_K_a)
    state.key_up(_K_a)
    assert state.matrix[row] & (1 << bit) != 0


def test_key_down_space_clears_matrix_bit() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_SPACE]
    state.key_down(_K_SPACE)
    assert state.matrix[row] & (1 << bit) == 0


def test_key_up_space_restores_matrix_bit() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_SPACE]
    state.key_down(_K_SPACE)
    state.key_up(_K_SPACE)
    assert state.matrix[row] & (1 << bit) != 0


def test_key_down_clears_joystick_bit() -> None:
    state = make_input()
    _port, bit = JOY_MAP[_K_w]
    state.key_down(_K_w)
    assert state.joy1 & (1 << bit) == 0


def test_key_up_sets_joystick_bit() -> None:
    state = make_input()
    _port, bit = JOY_MAP[_K_w]
    state.key_down(_K_w)
    state.key_up(_K_w)
    assert state.joy1 & (1 << bit) != 0


def test_key_down_down_clears_joystick_down_bit() -> None:
    state = make_input()
    _port, bit = JOY_MAP[_K_s]
    state.key_down(_K_s)
    assert state.joy1 & (1 << bit) == 0


def test_unknown_key_noop_matrix() -> None:
    state = make_input()
    before = list(state.matrix)
    state.key_down(9999)
    state.key_up(9999)
    assert state.matrix == before


def test_unknown_key_noop_joystick() -> None:
    state = make_input()
    state.key_down(9999)
    state.key_up(9999)
    assert state.joy1 == 0x3F
    assert state.joy2 == 0x3F


def test_multiple_keys_down() -> None:
    state = make_input()
    state.key_down(_K_a)
    state.key_down(_K_SPACE)
    row_a, bit_a = KEY_MATRIX[_K_a]
    row_sp, bit_sp = KEY_MATRIX[_K_SPACE]
    assert state.matrix[row_a] & (1 << bit_a) == 0
    assert state.matrix[row_sp] & (1 << bit_sp) == 0


def test_cursor_keys_in_row8() -> None:
    assert KEY_MATRIX[_K_UP] == (8, 5)
    assert KEY_MATRIX[_K_DOWN] == (8, 6)
    assert KEY_MATRIX[_K_LEFT] == (8, 4)
    assert KEY_MATRIX[_K_RIGHT] == (8, 7)


def test_cursor_keys_clear_row8_bits() -> None:
    state = make_input()
    state.key_down(_K_UP)
    assert state.matrix[8] & (1 << 5) == 0
    state.key_down(_K_LEFT)
    assert state.matrix[8] & (1 << 4) == 0


def test_cursor_keys_joy1_in_joymap() -> None:
    assert JOY_MAP[_K_UP]    == (0, 0)
    assert JOY_MAP[_K_DOWN]  == (0, 1)
    assert JOY_MAP[_K_LEFT]  == (0, 2)
    assert JOY_MAP[_K_RIGHT] == (0, 3)


def test_other_bits_unaffected_on_key_down() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_a]
    state.key_down(_K_a)
    mask = ~(1 << bit) & 0xFF
    assert state.matrix[row] & mask == mask


def test_joystick_button_down_clears_joy1_bit() -> None:
    state = make_input()
    state.joystick_button_down(0, 0)
    assert state.joy1 & (1 << 0) == 0


def test_joystick_button_up_sets_joy1_bit() -> None:
    state = make_input()
    state.joystick_button_down(0, 0)
    state.joystick_button_up(0, 0)
    assert state.joy1 & (1 << 0) != 0


def test_joystick_button_down_other_bits_unaffected() -> None:
    state = make_input()
    state.joystick_button_down(0, 2)
    mask = ~(1 << 2) & 0x3F
    assert state.joy1 & mask == mask


def test_joystick_button_down_joy2() -> None:
    state = make_input()
    state.joystick_button_down(1, 0)
    assert state.joy2 & (1 << 0) == 0
    assert state.joy1 == 0x3F  # joy1 unaffected


def test_joystick_button_down_and_keyboard_stack() -> None:
    state = make_input()
    state.key_down(_K_w)               # keyboard presses Joy1 Up (bit 0)
    state.joystick_button_down(0, 0)   # hardware also presses bit 0
    assert state.joy1 & (1 << 0) == 0
    state.joystick_button_up(0, 0)     # hardware releases
    assert state.joy1 & (1 << 0) == 0  # keyboard still holds it
    state.key_up(_K_w)
    assert state.joy1 & (1 << 0) != 0  # now fully released


def test_trigger_b_keyboard_mapping() -> None:
    state = make_input()
    assert JOY_MAP[_K_x] == (0, 5)   # Joy1 Trigger B
    state.key_down(_K_x)
    assert state.joy1 & (1 << 5) == 0  # bit 5 pressed
    state.key_up(_K_x)
    assert state.joy1 & (1 << 5) != 0


def test_trigger_b_hardware() -> None:
    state = make_input()
    state.joystick_button_down(0, 5)
    assert state.joy1 & (1 << 5) == 0
    state.joystick_button_up(0, 5)
    assert state.joy1 & (1 << 5) != 0


# ---------------------------------------------------------------------------
# Keyboard layout selection (International vs Japanese/JIS)
# ---------------------------------------------------------------------------

def test_default_keyboard_type_is_int() -> None:
    assert InputState().keyboard_type == "int"


def test_int_and_jp_share_common_cells() -> None:
    # Digits, letters, comma/period/slash, minus, semicolon are identical.
    for key in (_K_a, _K_SPACE, _K_MINUS, _K_SEMICOLON, _K_SLASH, _K_COMMA):
        assert KEY_MATRIX_INT[key] == KEY_MATRIX_JP[key]


def test_int_specific_symbol_cells() -> None:
    assert KEY_MATRIX_INT[_K_RIGHTBRACKET] == (1, 6)  # ]
    assert KEY_MATRIX_INT[_K_LEFTBRACKET] == (1, 5)   # [
    assert KEY_MATRIX_INT[_K_QUOTE] == (2, 0)         # ' (int has a dedicated key)


def test_jp_specific_symbol_cells() -> None:
    assert KEY_MATRIX_JP[_K_RIGHTBRACKET] == (2, 1)   # ] differs from int
    assert KEY_MATRIX_JP[_K_LEFTBRACKET] == (1, 6)    # [ differs from int
    assert _K_QUOTE not in KEY_MATRIX_JP               # JIS: apostrophe is Shift+7


def test_keyboard_type_selects_matrix_on_keypress() -> None:
    # ']' lands in different cells depending on the selected layout.
    kint = InputState(keyboard_type="int")
    kint.key_down(_K_RIGHTBRACKET)
    assert kint.matrix[1] & (1 << 6) == 0  # int cell (1,6)
    kjp = InputState(keyboard_type="jp")
    kjp.key_down(_K_RIGHTBRACKET)
    assert kjp.matrix[2] & (1 << 1) == 0   # jp cell (2,1)


def test_jp_apostrophe_unmapped_is_noop() -> None:
    kjp = InputState(keyboard_type="jp")
    kjp.key_down(_K_QUOTE)  # no JIS cell → must not raise, matrix unchanged
    assert all(row == 0xFF for row in kjp.matrix)


def test_left_alt_maps_to_graph_key() -> None:
    # Left Alt/Option → MSX GRAPH at matrix (6, 2), shared by both layouts.
    assert KEY_MATRIX_INT[_K_LALT] == (6, 2)
    assert KEY_MATRIX_JP[_K_LALT] == (6, 2)
    state = InputState()
    state.key_down(_K_LALT)
    assert state.matrix[6] & (1 << 2) == 0  # GRAPH pressed (active-low)
    state.key_up(_K_LALT)
    assert state.matrix[6] & (1 << 2) != 0
