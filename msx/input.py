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


@dataclass
class InputState:
    """MSX-side input state: the keyboard matrix and both joystick ports.

    Host input arrives only as matrix cells (set_key_state) and joystick
    lines. Each port has a keyboard-sourced and a pad-sourced line state; a
    line reads as pressed while either source holds it.
    """

    matrix: list[int] = field(default_factory=lambda: [0xFF] * _NUM_ROWS)
    # The machine's keyboard layout: "int" (International) or "jp"
    # (Japanese/JIS). A hardware fact from the machine definition; host
    # keyboards read it to pick their key-to-cell table.
    keyboard_type: str = "int"
    # Per-joystick 6-bit active-low state: bits 0-5 = up/down/left/right/trigA/trigB
    _joy1_kbd: int = field(default=0x3F, init=False, repr=False)
    _joy1_hw:  int = field(default=0x3F, init=False, repr=False)
    _joy2_kbd: int = field(default=0x3F, init=False, repr=False)
    _joy2_hw:  int = field(default=0x3F, init=False, repr=False)

    @property
    def joy1(self) -> int:
        return self._joy1_kbd & self._joy1_hw

    @property
    def joy2(self) -> int:
        return self._joy2_kbd & self._joy2_hw

    def set_key_state(self, row: int, bit: int, pressed: bool) -> None:
        """Assert or release a matrix cell directly by (row, bit).

        The matrix is active-low, so a pressed key clears the bit and a released
        key sets it. Host keyboards (frontend) and programmatic key injection
        (e.g. the RPC server) both press keys through it. It does no
        shared-cell bookkeeping, so callers own their press/release pairing.

        Args:
            row: Matrix row index (0-10).
            bit: Bit index within the row (0-7).
            pressed: True to press (clear the bit), False to release (set it).

        Raises:
            ValueError: If row or bit is out of range.
        """
        if not 0 <= row < _NUM_ROWS:
            raise ValueError(f"row out of range: {row}")
        if not 0 <= bit <= 7:
            raise ValueError(f"bit out of range: {bit}")
        if pressed:
            self.matrix[row] &= ~(1 << bit) & 0xFF
        else:
            self.matrix[row] |= (1 << bit) & 0xFF

    def joystick_button_down(self, port: int, bit: int) -> None:
        if port == 0:
            self._joy1_hw &= ~(1 << bit) & 0x3F
        else:
            self._joy2_hw &= ~(1 << bit) & 0x3F

    def joystick_button_up(self, port: int, bit: int) -> None:
        if port == 0:
            self._joy1_hw |= (1 << bit)
        else:
            self._joy2_hw |= (1 << bit)

    def keyboard_joystick_down(self, port: int, bit: int) -> None:
        """Press a keyboard-sourced joystick line (active-low)."""
        if port == 0:
            self._joy1_kbd &= ~(1 << bit) & 0x3F
        else:
            self._joy2_kbd &= ~(1 << bit) & 0x3F

    def keyboard_joystick_up(self, port: int, bit: int) -> None:
        """Release a keyboard-sourced joystick line."""
        if port == 0:
            self._joy1_kbd |= (1 << bit)
        else:
            self._joy2_kbd |= (1 << bit)
