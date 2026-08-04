from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# 8-phase scan cycle: a main cycle reporting the real relative X/Y delta,
# immediately followed by an alternate cycle forced to zero delta. MSX BIOS
# mouse detection reads both cycles and classifies the device as a trackball
# if the alternate cycle is also non-zero, so the zeroing is required for
# the device to be recognised as a mouse (see openMSX src/input/Mouse.cc).
PHASE_XHIGH1 = 0
PHASE_XLOW1 = 1
PHASE_YHIGH1 = 2
PHASE_YLOW1 = 3
PHASE_XHIGH2 = 4
PHASE_XLOW2 = 5
PHASE_YHIGH2 = 6
PHASE_YLOW2 = 7

# Pin-8-idle timeout: if the host stops toggling pin 8 for longer than this,
# the phase resyncs to PHASE_YLOW2 so the next toggle starts a fresh scan
# round. 1500 microseconds at the MSX CPU clock (3,579,545 Hz), matching
# openMSX's JoyMega-derived timeout value.
_MOUSE_TIMEOUT_CYCLES = 5_369

_STATUS_TRIGGER_A = 0x10  # left button
_STATUS_TRIGGER_B = 0x20  # right button
_STATUS_DEFAULT = _STATUS_TRIGGER_A | _STATUS_TRIGGER_B  # both released


def _nibbles(value: int) -> tuple[int, int]:
    """Two's-complement byte of `value`, split into (high nibble, low nibble)."""
    byte = value & 0xFF
    return (byte >> 4) & 0xF, byte & 0xF


@dataclass
class MouseDevice:
    """MSX mouse plugged into a joystick port: pin-8-clocked nibble protocol.

    Pins 1-4 (normally joystick directions) carry a 4-bit nibble of the
    signed relative X/Y motion since the last scan round; pin 8 (normally
    the PSG's always-high strobe output) is toggled by the host to clock
    through the nibble sequence; pins 6/7 (normally triggers) carry the
    left/right mouse button state on every read, independent of phase.
    """

    _scale: int = 1
    _get_cycle: Callable[[], int] | None = field(default=None, repr=False)

    _phase: int = field(default=PHASE_YLOW2, init=False)
    _x_rel: int = field(default=0, init=False)
    _y_rel: int = field(default=0, init=False)
    _cur_x_rel: int = field(default=0, init=False)
    _cur_y_rel: int = field(default=0, init=False)
    _frac_x: int = field(default=0, init=False)
    _frac_y: int = field(default=0, init=False)
    _status: int = field(default=_STATUS_DEFAULT, init=False)
    _last_pin8: int = field(default=0, init=False)
    _last_cycle: int = field(default=0, init=False)

    def read(self) -> int:
        if self._phase in (PHASE_XHIGH1, PHASE_XHIGH2):
            nibble = _nibbles(self._x_rel)[0]
        elif self._phase in (PHASE_XLOW1, PHASE_XLOW2):
            nibble = _nibbles(self._x_rel)[1]
        elif self._phase in (PHASE_YHIGH1, PHASE_YHIGH2):
            nibble = _nibbles(self._y_rel)[0]
        else:  # PHASE_YLOW1, PHASE_YLOW2
            nibble = _nibbles(self._y_rel)[1]
        return nibble | self._status

    def write_pin8(self, value: int) -> None:
        value &= 1
        if value == self._last_pin8:
            return
        self._last_pin8 = value
        cycle = self._get_cycle() if self._get_cycle is not None else 0
        if cycle - self._last_cycle > _MOUSE_TIMEOUT_CYCLES:
            self._phase = PHASE_YLOW2
        self._last_cycle = cycle

        is_high_phase = self._phase in (
            PHASE_XHIGH1, PHASE_XHIGH2, PHASE_YHIGH1, PHASE_YHIGH2,
        )
        if is_high_phase:
            if value == 0:
                self._advance_phase()
        else:
            if value == 1:
                self._advance_phase()

    def _advance_phase(self) -> None:
        if self._phase == PHASE_YLOW2:
            self._phase = PHASE_XHIGH1
            x = max(-127, min(127, self._cur_x_rel))
            y = max(-127, min(127, self._cur_y_rel))
            self._x_rel = x
            self._y_rel = y
            self._cur_x_rel -= x
            self._cur_y_rel -= y
        elif self._phase == PHASE_YLOW1:
            self._phase = PHASE_XHIGH2
            self._x_rel = 0
            self._y_rel = 0
        else:
            self._phase += 1

    def add_motion(self, dx_px: int, dy_px: int) -> None:
        dx, self._frac_x = divmod(dx_px + self._frac_x, self._scale)
        dy, self._frac_y = divmod(dy_px + self._frac_y, self._scale)
        self._cur_x_rel += dx
        self._cur_y_rel += dy

    def set_button(self, bit: int, pressed: bool) -> None:
        if pressed:
            self._status &= ~(1 << bit) & 0xFF
        else:
            self._status |= (1 << bit)
