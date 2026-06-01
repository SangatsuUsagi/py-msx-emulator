from msx.input import (
    InputState, KEY_MATRIX, JOY_MAP,
    _K_a, _K_SPACE, _K_w, _K_s,
    _K_UP, _K_DOWN, _K_LEFT, _K_RIGHT,
)


def make_input() -> InputState:
    return InputState()


def test_default_matrix_all_released() -> None:
    state = make_input()
    assert all(row == 0xFF for row in state.matrix)


def test_default_joystick_all_released() -> None:
    state = make_input()
    assert state.joystick == 0xFF


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
    bit = JOY_MAP[_K_w]
    state.key_down(_K_w)
    assert state.joystick & (1 << bit) == 0


def test_key_up_sets_joystick_bit() -> None:
    state = make_input()
    bit = JOY_MAP[_K_w]
    state.key_down(_K_w)
    state.key_up(_K_w)
    assert state.joystick & (1 << bit) != 0


def test_key_down_down_clears_joystick_down_bit() -> None:
    state = make_input()
    bit = JOY_MAP[_K_s]
    state.key_down(_K_s)
    assert state.joystick & (1 << bit) == 0


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
    assert state.joystick == 0xFF


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
    assert JOY_MAP[_K_UP] == 0
    assert JOY_MAP[_K_DOWN] == 1
    assert JOY_MAP[_K_LEFT] == 2
    assert JOY_MAP[_K_RIGHT] == 3


def test_other_bits_unaffected_on_key_down() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_a]
    state.key_down(_K_a)
    mask = ~(1 << bit) & 0xFF
    assert state.matrix[row] & mask == mask


def test_joystick_button_down_clears_bit() -> None:
    state = make_input()
    state.joystick_button_down(0, 0)
    assert state.joystick & (1 << 0) == 0


def test_joystick_button_up_sets_bit() -> None:
    state = make_input()
    state.joystick_button_down(0, 0)
    state.joystick_button_up(0, 0)
    assert state.joystick & (1 << 0) != 0


def test_joystick_button_down_other_bits_unaffected() -> None:
    state = make_input()
    state.joystick_button_down(0, 2)
    mask = ~(1 << 2) & 0xFF
    assert state.joystick & mask == mask


def test_joystick_button_down_and_keyboard_stack() -> None:
    state = make_input()
    state.key_down(_K_w)          # keyboard presses bit 0
    state.joystick_button_down(0, 0)   # hardware also presses bit 0
    assert state.joystick & (1 << 0) == 0
    state.joystick_button_up(0, 0)     # hardware releases
    assert state.joystick & (1 << 0) == 0  # keyboard still holds it
    state.key_up(_K_w)            # keyboard releases
    assert state.joystick & (1 << 0) != 0  # now fully released


def test_joystick_button_port_param_ignored() -> None:
    state = make_input()
    state.joystick_button_down(1, 0)
    assert state.joystick & (1 << 0) == 0
    state.joystick_button_up(1, 0)
    assert state.joystick & (1 << 0) != 0
