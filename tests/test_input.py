"""Core InputState: the MSX keyboard matrix and joystick lines (input-keyboard, input-joystick).

Host key translation is tested in tests/test_sdl2_keyboard.py.
"""
import dataclasses

import pytest

from msx.input import InputFrame, InputLines, InputState


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


# --- InputFrame / InputLines (core value and lines) ------------------------


def test_input_frame_defaults_are_all_released() -> None:
    frame = InputFrame()
    assert frame.key_matrix == (0xFF,) * 11
    assert frame.joystick_ports == (0x3F, 0x3F)
    assert (frame.mouse_dx, frame.mouse_dy) == (0, 0)
    assert (frame.mouse_left, frame.mouse_right) == (False, False)


@pytest.mark.parametrize("kwargs", [
    {"key_matrix": (0xFF,) * 10},
    {"key_matrix": (0xFF,) * 10 + (0x100,)},
    {"joystick_ports": (0x3F,)},
    {"joystick_ports": (0x40, 0x3F)},
])
def test_input_frame_rejects_a_malformed_value(kwargs) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        InputFrame(**kwargs)


def test_input_frame_cannot_be_changed() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        InputFrame().mouse_dx = 1  # type: ignore[misc]


def test_input_lines_apply_copies_into_the_same_list() -> None:
    lines = InputLines()
    rows = lines.matrix
    lines.apply(InputFrame(key_matrix=(0xFE,) + (0xFF,) * 10, joystick_ports=(0x3E, 0x1F)))
    assert lines.matrix is rows
    assert lines.matrix[0] == 0xFE and (lines.joy1, lines.joy2) == (0x3E, 0x1F)
