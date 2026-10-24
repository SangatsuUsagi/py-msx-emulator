"""Core input: the per-frame InputFrame value and the InputLines the PPI/PSG
read (input-keyboard "InputState dataclass", machine-core "One InputState
value per frame"). The host-side builder is tested in tests/test_input_state.py.
"""
import dataclasses

import pytest

from msx.input import InputFrame, InputLines


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
