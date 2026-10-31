"""The MSX key-name table of the host: RPC key injection and the frontend's
function-key bindings. No core module uses it (machine-core "The host takes
no constant or table from a core module")."""
from __future__ import annotations

# MSX joystick function → its bit in the 6-bit active-low port state: the RPC
# joystick methods, the keyboard joystick and its py_emulator.yaml section.
JOY_FUNCTION_BITS: dict[str, int] = {
    "up": 0, "down": 1, "left": 2, "right": 3, "trigger_a": 4, "trigger_b": 5,
}

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
