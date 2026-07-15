"""Tests for programmatic input injection primitives (RPC key support)."""
from __future__ import annotations

import pytest

from msx.input import KEY_NAME_TO_CELL, InputState


def test_set_key_state_press_clears_bit() -> None:
    st = InputState()
    st.set_key_state(8, 0, True)  # SPACE
    assert st.matrix[8] & (1 << 0) == 0  # active-low: pressed = cleared


def test_set_key_state_release_restores_bit() -> None:
    st = InputState()
    st.set_key_state(8, 0, True)
    st.set_key_state(8, 0, False)
    assert st.matrix[8] == 0xFF


def test_set_key_state_only_touches_target_bit() -> None:
    st = InputState()
    st.set_key_state(6, 1, True)  # CTRL
    assert st.matrix[6] == 0xFF & ~(1 << 1)
    # Other rows untouched.
    assert all(st.matrix[r] == 0xFF for r in range(11) if r != 6)


@pytest.mark.parametrize("row,bit", [(-1, 0), (11, 0)])
def test_set_key_state_rejects_bad_row(row: int, bit: int) -> None:
    with pytest.raises(ValueError):
        InputState().set_key_state(row, bit, True)


@pytest.mark.parametrize("bit", [-1, 8])
def test_set_key_state_rejects_bad_bit(bit: int) -> None:
    with pytest.raises(ValueError):
        InputState().set_key_state(0, bit, True)


@pytest.mark.parametrize(
    "name,cell",
    [
        ("SPACE", (8, 0)),
        ("RETURN", (7, 7)),
        ("ESC", (7, 2)),
        ("UP", (8, 5)),
        ("DOWN", (8, 6)),
        ("LEFT", (8, 4)),
        ("RIGHT", (8, 7)),
        ("SHIFT", (6, 0)),
        ("CTRL", (6, 1)),
        ("GRAPH", (6, 2)),
        ("A", (2, 6)),
        ("Z", (5, 7)),
        ("0", (0, 0)),
        ("9", (1, 1)),
        ("F1", (6, 5)),
        ("F5", (7, 1)),
    ],
)
def test_key_name_to_cell_matches_matrix(name: str, cell: tuple[int, int]) -> None:
    assert KEY_NAME_TO_CELL[name] == cell


def test_key_name_table_covers_letters_and_digits() -> None:
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        assert ch in KEY_NAME_TO_CELL
    for ch in "0123456789":
        assert ch in KEY_NAME_TO_CELL


def test_named_key_injection_via_set_key_state() -> None:
    st = InputState()
    row, bit = KEY_NAME_TO_CELL["RETURN"]
    st.set_key_state(row, bit, True)
    assert st.matrix[7] & (1 << 7) == 0
