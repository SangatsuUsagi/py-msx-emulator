"""The input lines the PPI and the PSG read (InputLines), and the per-frame
input record of the core boundary (InputFrame)."""
from __future__ import annotations

from dataclasses import dataclass, field

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
