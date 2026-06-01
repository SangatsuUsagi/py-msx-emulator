from msx.input import InputState, KEY_MATRIX, JOY_MAP, _K_a, _K_SPACE, _K_w, _K_s


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


def test_other_bits_unaffected_on_key_down() -> None:
    state = make_input()
    row, bit = KEY_MATRIX[_K_a]
    state.key_down(_K_a)
    mask = ~(1 << bit) & 0xFF
    assert state.matrix[row] & mask == mask
