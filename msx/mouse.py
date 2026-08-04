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
# Plain int constants, not an Enum: _advance_phase's generic (phase + 1) % 8
# fallthrough relies on phase supporting arithmetic. A Rust port promoting
# this to an enum would need an explicit 8-arm match instead.

# Pin-8-idle timeout: if the host stops toggling pin 8 for longer than this,
# the phase resyncs to PHASE_YLOW2 so the next toggle starts a fresh scan
# round. 1500 microseconds at the MSX CPU clock (3,579,545 Hz), matching
# openMSX's JoyMega-derived timeout value.
_MOUSE_TIMEOUT_CYCLES = 5_369

# Symmetric clamp range for the latched delta byte; +/-128 would break the
# two's-complement byte's sign symmetry.
_MAX_ABS_DELTA = 127

_STATUS_TRIGGER_A = 0x10  # left button
_STATUS_TRIGGER_B = 0x20  # right button
_STATUS_DEFAULT = _STATUS_TRIGGER_A | _STATUS_TRIGGER_B  # both released


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
    # Portability note: _get_cycle is a closure assigned at wiring time
    # (frontend/sdl2_frontend.py sets `lambda: machine.cycle_count`), capturing
    # the Machine that owns this device — a reference cycle Rust/C++ cannot
    # express as a plain Fn field. A port should thread the cycle count through
    # write_pin8's caller (PSG's I/O dispatch) or hold a clock handle resolved
    # once at construction, mirroring the same note on PSG/VDP's _get_cycle.
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
        # Phase encoding makes the nibble selection a plain mod-4 group: 0=X
        # high, 1=X low, 2=Y high, 3=Y low (same group for the main and
        # alternate cycle, since they only differ in what _x_rel/_y_rel hold).
        group = self._phase & 3
        if group == 0:
            nibble = (self._x_rel & 0xFF) >> 4
        elif group == 1:
            nibble = self._x_rel & 0x0F
        elif group == 2:
            nibble = (self._y_rel & 0xFF) >> 4
        else:
            nibble = self._y_rel & 0x0F
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

        # Each phase advances on the edge that ends its half of the pin-8
        # square wave: HIGH phases (even-numbered) end on a falling edge,
        # LOW phases (odd-numbered) end on a rising edge.
        is_high_phase = (self._phase & 1) == 0
        ends_phase = (value == 0) if is_high_phase else (value == 1)
        if ends_phase:
            self._advance_phase()

    def _advance_phase(self) -> None:
        """Move to the next of the 8 scan phases (see module docstring)."""
        if self._phase == PHASE_YLOW2:
            self._latch_main_cycle_sample()
        elif self._phase == PHASE_YLOW1:
            self._zero_alternate_cycle_sample()
        self._phase = (self._phase + 1) % 8

    def _latch_main_cycle_sample(self) -> None:
        """Clamp+latch the accumulated delta, carrying the remainder forward."""
        x = max(-_MAX_ABS_DELTA, min(_MAX_ABS_DELTA, self._cur_x_rel))
        y = max(-_MAX_ABS_DELTA, min(_MAX_ABS_DELTA, self._cur_y_rel))
        self._x_rel = x
        self._y_rel = y
        self._cur_x_rel -= x
        self._cur_y_rel -= y

    def _zero_alternate_cycle_sample(self) -> None:
        """Force the alternate-cycle delta to zero (see module docstring)."""
        self._x_rel = 0
        self._y_rel = 0

    def add_motion(self, dx_px: int, dy_px: int) -> None:
        # divmod() floors: the quotient rounds toward -inf and the remainder
        # keeps the sign of _scale (always in [0, _scale)), regardless of
        # dx_px/dy_px's sign. A Rust/C++ port must use floored division here
        # explicitly (e.g. Rust's div_euclid/rem_euclid) — plain '/'/'%'
        # truncate toward zero and would corrupt the sub-pixel carry for
        # negative motion.
        dx, self._frac_x = divmod(dx_px + self._frac_x, self._scale)
        dy, self._frac_y = divmod(dy_px + self._frac_y, self._scale)
        self._cur_x_rel += dx
        self._cur_y_rel += dy

    def set_button(self, bit: int, pressed: bool) -> None:
        if pressed:
            self._status &= ~(1 << bit) & 0xFF
        else:
            self._status |= (1 << bit)
