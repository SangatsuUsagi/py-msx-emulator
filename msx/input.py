from __future__ import annotations

from dataclasses import dataclass, field

# MSX key name → (row, bit) matrix cell, for programmatic key injection (RPC).
# Derived directly from the MSX keyboard matrix (map.grauw.nl keymatrix); the
# names are the layout-independent MSX key labels, so this table is shared by
# both the International and Japanese layouts. It maps human-facing names such
# as "SPACE" or "F1"; host keyboards map their own key codes to cells.
KEY_NAME_TO_CELL: dict[str, tuple[int, int]] = {
    # Row 0: digits 0-7
    "0": (0, 0), "1": (0, 1), "2": (0, 2), "3": (0, 3),
    "4": (0, 4), "5": (0, 5), "6": (0, 6), "7": (0, 7),
    # Row 1: digits 8-9
    "8": (1, 0), "9": (1, 1),
    # Rows 2-5: letters A-Z
    "A": (2, 6), "B": (2, 7),
    "C": (3, 0), "D": (3, 1), "E": (3, 2), "F": (3, 3),
    "G": (3, 4), "H": (3, 5), "I": (3, 6), "J": (3, 7),
    "K": (4, 0), "L": (4, 1), "M": (4, 2), "N": (4, 3),
    "O": (4, 4), "P": (4, 5), "Q": (4, 6), "R": (4, 7),
    "S": (5, 0), "T": (5, 1), "U": (5, 2), "V": (5, 3),
    "W": (5, 4), "X": (5, 5), "Y": (5, 6), "Z": (5, 7),
    # Row 6: modifiers and F1-F3
    "SHIFT": (6, 0), "CTRL": (6, 1), "GRAPH": (6, 2), "CAPS": (6, 3),
    "CODE": (6, 4), "F1": (6, 5), "F2": (6, 6), "F3": (6, 7),
    # Row 7: F4, F5, ESC, TAB, STOP, BS, SELECT, RETURN
    "F4": (7, 0), "F5": (7, 1), "ESC": (7, 2), "TAB": (7, 3),
    "STOP": (7, 4), "BS": (7, 5), "SELECT": (7, 6), "RETURN": (7, 7),
    # Row 8: SPACE, editing keys, cursor keys
    "SPACE": (8, 0), "HOME": (8, 1), "INS": (8, 2), "DEL": (8, 3),
    "LEFT": (8, 4), "UP": (8, 5), "DOWN": (8, 6), "RIGHT": (8, 7),
}

_NUM_ROWS = 11
_RELEASED_ROW = 0xFF
_RELEASED_MATRIX = bytes((_RELEASED_ROW,)) * _NUM_ROWS
_JOY_LINES_MASK = 0x3F  # bits 0-5: up/down/left/right/trigger A/trigger B


@dataclass(frozen=True)
class InputFrame:
    """One frame's input, the value the core takes (allium/core-frontend.allium
    InputState), a flat record of integers and bytes: 11 active-low keyboard
    matrix rows, the two merged 6-bit active-low joystick ports, this frame's
    mouse motion in MSX counts and the mouse buttons as the bit flags
    MOUSE_LEFT and MOUSE_RIGHT (msx/boundary.py). Immutable, so a caller
    cannot change it after handing it over; the defaults are "nothing
    pressed, no motion". The core masks the ports and buttons and clamps the
    motion to 16 bits (machine-core "The core masks or clamps an input value
    outside its range").
    """

    key_matrix: bytes = _RELEASED_MATRIX
    joy1: int = _JOY_LINES_MASK
    joy2: int = _JOY_LINES_MASK
    mouse_dx: int = 0
    mouse_dy: int = 0
    mouse_buttons: int = 0

    def __post_init__(self) -> None:
        # The one check: a C struct has a fixed 11-byte array, bytes do not.
        if len(self.key_matrix) != _NUM_ROWS:
            raise ValueError(f"key_matrix needs {_NUM_ROWS} rows, got {len(self.key_matrix)}")


@dataclass
class InputLines:
    """The input lines the PPI (keyboard rows) and the PSG (joystick ports)
    read, owned by the Machine and set from an InputFrame by apply()."""

    matrix: list[int] = field(default_factory=lambda: [_RELEASED_ROW] * _NUM_ROWS)
    joy1: int = _JOY_LINES_MASK
    joy2: int = _JOY_LINES_MASK

    def apply(self, frame: InputFrame) -> None:
        """Take `frame`'s rows and ports. The matrix list object is kept, so
        readers holding this InputLines see the new rows."""
        self.matrix[:] = frame.key_matrix
        self.joy1 = frame.joy1 & _JOY_LINES_MASK
        self.joy2 = frame.joy2 & _JOY_LINES_MASK
