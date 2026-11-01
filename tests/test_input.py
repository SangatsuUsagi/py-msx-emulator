"""Core input: the per-frame InputFrame value and the InputLines the PPI/PSG
read (input-keyboard "InputState dataclass", machine-core "One InputState
value per frame"). The host-side builder is tested in tests/test_input_state.py.
"""
import dataclasses

import pytest

from msx.input import InputFrame, InputLines


def test_input_frame_defaults_are_all_released() -> None:
    frame = InputFrame()
    assert frame.key_matrix == b"\xff" * 11
    assert (frame.joy1, frame.joy2) == (0x3F, 0x3F)
    assert (frame.mouse_dx, frame.mouse_dy) == (0, 0)
    assert frame.mouse_buttons == 0


@pytest.mark.parametrize("rows", [10, 12])
def test_a_short_key_matrix_is_rejected_at_construction(rows: int) -> None:
    # machine-core "A short key matrix is rejected at construction".
    with pytest.raises(ValueError):
        InputFrame(key_matrix=b"\xff" * rows)


def test_input_frame_cannot_be_changed() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        InputFrame().mouse_dx = 1  # type: ignore[misc]


def test_input_lines_apply_copies_into_the_same_list() -> None:
    lines = InputLines()
    rows = lines.matrix
    lines.apply(InputFrame(key_matrix=b"\xfe" + b"\xff" * 10, joy1=0x3E, joy2=0x1F))
    assert lines.matrix is rows
    assert lines.matrix[0] == 0xFE and (lines.joy1, lines.joy2) == (0x3E, 0x1F)
