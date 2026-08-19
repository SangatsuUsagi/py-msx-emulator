from __future__ import annotations

from dataclasses import dataclass, field

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
# PORT-NOTE: phase is a plain int (0-7), not an enum, because
#   _advance_phase's (phase + 1) % 8 wraparound and read()'s `phase & 3`
#   nibble-group selection rely on it supporting arithmetic directly.
# Rust equivalent: keep phase: u8 with wrapping arithmetic, or an enum with
#   explicit next()/group() methods if enum-safety is wanted instead (an
#   8-arm match for the wraparound, an explicit nibble-selector for the
#   grouping).
# C++ equivalent: keep as uint8_t, or an enum class with the same helpers.
# Kept as-is here because: semantic necessity, not performance -- the
#   arithmetic (mod-8 advance, &3 grouping) is simpler and cheaper to
#   express directly than via an enum abstraction.

# Pin-8-idle timeout: if the host stops toggling pin 8 for longer than this,
# the phase resyncs to PHASE_YLOW2 so the next toggle starts a fresh scan
# round. 1500 microseconds at the MSX CPU clock (3,579,545 Hz), matching
# openMSX's JoyMega-derived timeout value.
_MOUSE_TIMEOUT_CYCLES = 5_369

# Symmetric clamp range for the latched delta byte; +/-128 would break the
# two's-complement byte's sign symmetry.
_MAX_ABS_DELTA = 127

# Bit indices for set_button() below: 1 << TRIGGER_A_BIT == _STATUS_TRIGGER_A,
# 1 << TRIGGER_B_BIT == _STATUS_TRIGGER_B. Public so callers (the frontend's
# mouse-button handling) can name them instead of hardcoding 4/5.
TRIGGER_A_BIT = 4  # left button
TRIGGER_B_BIT = 5  # right button

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

    def write_pin8(self, value: int, cycle: int = 0) -> None:
        value &= 1
        if value == self._last_pin8:
            return
        self._last_pin8 = value
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
        # PORT-NOTE: divmod() floors -- the quotient rounds toward -inf and
        #   the remainder keeps the sign of _scale (always in [0, _scale)),
        #   regardless of dx_px/dy_px's sign.
        # Rust equivalent: div_euclid/rem_euclid, not plain '/'/'%' (which
        #   truncate toward zero).
        # C++ equivalent: std::div with an explicit floor adjustment, or a
        #   hand-written floor-division helper -- plain '/'/'%' also truncate
        #   toward zero in C++.
        # Kept as-is here because: semantic necessity, not performance -- the
        #   sub-pixel carry (_frac_x/_frac_y) must stay in [0, _scale)
        #   regardless of motion sign; truncating division would flip its
        #   sign for negative motion and corrupt the carry.
        dx, self._frac_x = divmod(dx_px + self._frac_x, self._scale)
        dy, self._frac_y = divmod(dy_px + self._frac_y, self._scale)
        self._cur_x_rel += dx
        self._cur_y_rel += dy

    def set_button(self, bit: int, pressed: bool) -> None:
        """Set the left (TRIGGER_A_BIT) or right (TRIGGER_B_BIT) button state.

        Live status, not latched: reflected on the next read() regardless of
        scan phase (see the module docstring).
        """
        if pressed:
            self._status &= ~(1 << bit) & 0xFF
        else:
            self._status |= (1 << bit)
