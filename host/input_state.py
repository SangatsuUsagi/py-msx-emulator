"""The input builder: host keys, gamepads and RPC injection meet here.

The SDL keyboard, the gamepad manager, the host mouse and the RPC server all
write into one InputState; once per frame the frontend hands the machine its
current value as an InputFrame (take_frame), and RPC injection applies it at
once (Machine.apply_input). Each joystick port has a keyboard-sourced and a
pad-sourced line state: a line reads as pressed while either source holds it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from msx.input import InputFrame

_NUM_ROWS = 11
_JOY_LINES_MASK = 0x3F  # bits 0-5: up/down/left/right/trigger A/trigger B


@dataclass
class InputState:
    """Mutable input being built between frames: the keyboard matrix, both
    joystick ports' two line sources and this frame's mouse motion/buttons."""

    matrix: list[int] = field(default_factory=lambda: [0xFF] * _NUM_ROWS)
    # Per-port 6-bit active-low line states, one per source.
    _joy1_kbd: int = field(default=_JOY_LINES_MASK, init=False, repr=False)
    _joy1_pad: int = field(default=_JOY_LINES_MASK, init=False, repr=False)
    _joy2_kbd: int = field(default=_JOY_LINES_MASK, init=False, repr=False)
    _joy2_pad: int = field(default=_JOY_LINES_MASK, init=False, repr=False)
    # Mouse motion in MSX counts accumulated since the last take_frame(), and
    # the two buttons.
    mouse_dx: int = 0
    mouse_dy: int = 0
    mouse_left: bool = False
    mouse_right: bool = False

    @property
    def joy1(self) -> int:
        return self._joy1_kbd & self._joy1_pad

    @property
    def joy2(self) -> int:
        return self._joy2_kbd & self._joy2_pad

    def frame(self) -> InputFrame:
        """The current input as a frame value (motion included, not reset)."""
        return InputFrame(
            key_matrix=tuple(self.matrix),
            joystick_ports=(self.joy1, self.joy2),
            mouse_dx=self.mouse_dx,
            mouse_dy=self.mouse_dy,
            mouse_left=self.mouse_left,
            mouse_right=self.mouse_right,
        )

    def take_frame(self) -> InputFrame:
        """frame(), then zero the mouse motion: the machine takes each count
        once."""
        current = self.frame()
        self.mouse_dx = 0
        self.mouse_dy = 0
        return current

    def set_key_state(self, row: int, bit: int, pressed: bool) -> None:
        """Assert or release a matrix cell directly by (row, bit).

        The matrix is active-low, so a pressed key clears the bit and a released
        key sets it. Host keyboards (frontend) and programmatic key injection
        (e.g. the RPC server) both press keys through it. It does no
        shared-cell bookkeeping, so callers own their press/release pairing.

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
        """Press a pad-sourced joystick line (active-low)."""
        if port == 0:
            self._joy1_pad &= ~(1 << bit) & _JOY_LINES_MASK
        else:
            self._joy2_pad &= ~(1 << bit) & _JOY_LINES_MASK

    def joystick_button_up(self, port: int, bit: int) -> None:
        """Release a pad-sourced joystick line."""
        if port == 0:
            self._joy1_pad |= (1 << bit) & _JOY_LINES_MASK
        else:
            self._joy2_pad |= (1 << bit) & _JOY_LINES_MASK

    def keyboard_joystick_down(self, port: int, bit: int) -> None:
        """Press a keyboard-sourced joystick line (active-low)."""
        if port == 0:
            self._joy1_kbd &= ~(1 << bit) & _JOY_LINES_MASK
        else:
            self._joy2_kbd &= ~(1 << bit) & _JOY_LINES_MASK

    def keyboard_joystick_up(self, port: int, bit: int) -> None:
        """Release a keyboard-sourced joystick line."""
        if port == 0:
            self._joy1_kbd |= (1 << bit) & _JOY_LINES_MASK
        else:
            self._joy2_kbd |= (1 << bit) & _JOY_LINES_MASK
