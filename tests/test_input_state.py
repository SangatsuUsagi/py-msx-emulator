"""The host-side input builder (host/input_state.py): matrix cells, the two
joystick line sources and the per-frame hand-over (input-keyboard,
input-joystick). Host key translation is tested in tests/test_sdl2_keyboard.py.
"""
from host.input_state import InputState
from msx.input import InputFrame, InputLines


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


def test_core_input_types_have_no_host_key_entry_point() -> None:
    # input-keyboard "InputState has no host-key entry point" (core side).
    for obj in (InputFrame(), InputLines()):
        for name in ("key_down", "key_up", "joy_map", "set_key_state"):
            assert not hasattr(obj, name)


def test_frame_carries_the_merged_lines() -> None:
    state = InputState()
    state.set_key_state(2, 6, True)
    state.keyboard_joystick_down(0, 0)
    state.joystick_button_down(1, 4)
    state.mouse_left = True
    frame = state.frame()
    assert frame.key_matrix[2] == 0xFF & ~(1 << 6)
    assert frame.joystick_ports == (0x3E, 0x2F)
    assert frame.mouse_left is True


def test_take_frame_hands_over_the_motion_once() -> None:
    # input-keyboard "take_frame hands over the motion once".
    state = InputState()
    state.set_key_state(0, 1, True)
    state.mouse_dx += 5
    first = state.take_frame()
    second = state.take_frame()
    assert (first.mouse_dx, second.mouse_dx) == (5, 0)
    assert first.key_matrix == second.key_matrix
    assert first.joystick_ports == second.joystick_ports


def test_joystick_release_keeps_the_lines_within_six_bits() -> None:
    # R20: every joystick update masks with the 6-bit line mask.
    state = InputState()
    state.joystick_button_up(0, 6)
    state.keyboard_joystick_up(1, 7)
    assert (state.joy1, state.joy2) == (0x3F, 0x3F)
