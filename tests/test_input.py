"""Core InputState: the MSX keyboard matrix and joystick lines (input-keyboard, input-joystick).

Host key translation is tested in tests/test_sdl2_keyboard.py.
"""
from msx.input import InputState


def test_input_state_has_no_host_key_entry_point() -> None:
    # input-keyboard "InputState has no host-key entry point".
    state = InputState()
    assert hasattr(state, "set_key_state")
    for name in ("key_down", "key_up", "joy_map"):
        assert not hasattr(state, name)


def test_default_matrix_and_ports_released() -> None:
    state = InputState()
    assert state.matrix == [0xFF] * 11
    assert (state.joy1, state.joy2) == (0x3F, 0x3F)


def test_keyboard_source_presses_a_line() -> None:
    # input-joystick "Keyboard source presses a line".
    state = InputState()
    state.keyboard_joystick_down(0, 4)
    assert state.joy1 & (1 << 4) == 0
    assert state.joy2 == 0x3F


def test_keyboard_source_on_port_1() -> None:
    state = InputState()
    state.keyboard_joystick_down(1, 0)
    assert state.joy2 & 1 == 0
    assert state.joy1 == 0x3F


def test_both_sources_must_release() -> None:
    # input-joystick "Both sources must release".
    state = InputState()
    state.keyboard_joystick_down(0, 0)
    state.joystick_button_down(0, 0)
    state.joystick_button_up(0, 0)
    assert state.joy1 & 1 == 0
    state.keyboard_joystick_up(0, 0)
    assert state.joy1 & 1 == 1
