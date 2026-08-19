from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from msx.input import InputState


class _SdlJoystickApi(Protocol):
    """Typed subset of the `sdl2` module this class actually calls.

    `sdl2` (pysdl2) ships no type stubs, so importing it gives `Any`; this
    Protocol exists purely to make the surface JoystickManager depends on
    explicit and closed, instead of an open-ended `Any` standing in for
    "whatever attributes get accessed at runtime."
    """

    SDL_CONTROLLERDEVICEADDED: int
    SDL_CONTROLLERDEVICEREMOVED: int
    SDL_JOYDEVICEADDED: int
    SDL_JOYDEVICEREMOVED: int
    SDL_CONTROLLERBUTTONDOWN: int
    SDL_CONTROLLERBUTTONUP: int
    SDL_CONTROLLERAXISMOTION: int
    SDL_JOYBUTTONDOWN: int
    SDL_JOYBUTTONUP: int
    SDL_JOYAXISMOTION: int
    SDL_JOYHATMOTION: int
    SDL_HAT_UP: int
    SDL_HAT_DOWN: int
    SDL_HAT_LEFT: int
    SDL_HAT_RIGHT: int

    def SDL_IsGameController(self, device_index: int) -> int: ...
    def SDL_GameControllerOpen(self, device_index: int) -> Any: ...
    def SDL_GameControllerGetJoystick(self, controller: Any) -> Any: ...
    def SDL_JoystickInstanceID(self, joy: Any) -> int: ...
    def SDL_JoystickOpen(self, device_index: int) -> Any: ...
    def SDL_GameControllerClose(self, controller: Any) -> None: ...
    def SDL_JoystickClose(self, joy: Any) -> None: ...

AXIS_DEAD_ZONE: int = 8192
TURBO_PERIOD: int = 3   # frames per turbo cycle at 60 fps → 20 Hz
TURBO_ON_COUNT: int = 1  # frames ON per cycle (ON-OFF-OFF pattern)

# Each port uses bits 0-5: up(0) down(1) left(2) right(3) trigA(4) trigB(5).
# Both ports maintain their own 6-bit state independently in InputState.
_PORT_BIT_COUNT = 6
_BIT_UP = 0
_BIT_DOWN = 1
_BIT_LEFT = 2
_BIT_RIGHT = 3
_BIT_TRIGGER_A = 4
_BIT_TRIGGER_B = 5

# Default GameController button → bit index within the port's 6-bit joystick
# state. Overridable per instance via py_emulator.yaml (see msx.app_config).
_DEFAULT_GC_BUTTON_BIT = {
    0: 4,   # SDL_CONTROLLER_BUTTON_A  → Trigger A
    1: 5,   # SDL_CONTROLLER_BUTTON_B  → Trigger B
    11: 0,  # SDL_CONTROLLER_BUTTON_DPAD_UP
    12: 1,  # SDL_CONTROLLER_BUTTON_DPAD_DOWN
    13: 2,  # SDL_CONTROLLER_BUTTON_DPAD_LEFT
    14: 3,  # SDL_CONTROLLER_BUTTON_DPAD_RIGHT
}

# Default GameController button → bit index (turbo fire; same bits as A/B but
# driven by tick()). Overridable per instance via py_emulator.yaml.
_DEFAULT_GC_TURBO_BUTTON_BIT = {
    2: 5,   # SDL_CONTROLLER_BUTTON_X → Trigger B (turbo)
    3: 4,   # SDL_CONTROLLER_BUTTON_Y → Trigger A (turbo)
}

# Analog-stick axis → (negative_bit_offset, positive_bit_offset). Shared by the
# GameController and raw-joystick axis handlers.
# macOS SDL2 reports left-stick Y as positive=up, negative=down (inverted from SDL2 spec)
_AXIS_BIT = {
    0: (2, 3),  # left-stick X: neg=left, pos=right
    1: (1, 0),  # left-stick Y: neg=down, pos=up
}

# Raw-joystick (non-GameController) button index → MSX bit. Buttons 0/1 are
# direct triggers; 2/3 drive the same triggers through the turbo state machine.
_JOY_BUTTON_BIT = {0: _BIT_TRIGGER_A, 1: _BIT_TRIGGER_B}
_JOY_TURBO_BUTTON_BIT = {2: _BIT_TRIGGER_A, 3: _BIT_TRIGGER_B}


@dataclass
class JoystickManager:
    """Owns up to two host joystick/gamepad slots (ports 0 and 1) and drives
    their signal lines into InputState.

    Devices are claimed via SDL's GameController API when supported, else
    the raw Joystick API. Each port's six lines (see module-level _BIT_*)
    are asserted/released directly by held buttons/axes/hat, or driven by
    the turbo autofire duty cycle (see tick()) for turbo-bound buttons.
    """

    _input: InputState
    _sdl: _SdlJoystickApi = field(repr=False)

    # GameController button maps and turbo period; default to the built-in
    # tables/rate but may be replaced from py_emulator.yaml (see msx.app_config).
    _gc_button_bit: dict[int, int] = field(
        default_factory=lambda: dict(_DEFAULT_GC_BUTTON_BIT), repr=False
    )
    _gc_turbo_button_bit: dict[int, int] = field(
        default_factory=lambda: dict(_DEFAULT_GC_TURBO_BUTTON_BIT), repr=False
    )
    _turbo_period: int = TURBO_PERIOD

    _slots: list[Any] = field(default_factory=lambda: [None, None], init=False, repr=False)
    _is_gc: list[bool] = field(default_factory=lambda: [False, False], init=False, repr=False)
    _instance_ids: list[int] = field(default_factory=lambda: [-1, -1], init=False, repr=False)
    _turbo_held: set[tuple[int, int]] = field(default_factory=set, init=False, repr=False)
    _turbo_counter: int = field(default=0, init=False, repr=False)

    def tick(self) -> None:
        """Advance the turbo fire state machine by one emulated frame."""
        if not self._turbo_held:
            return
        on = (self._turbo_counter % self._turbo_period) < TURBO_ON_COUNT
        for port, bit in self._turbo_held:
            if on:
                self._input.joystick_button_down(port, bit)
            else:
                self._input.joystick_button_up(port, bit)
        self._turbo_counter += 1

    def _free_port(self) -> int | None:
        for i, slot in enumerate(self._slots):
            if slot is None:
                return i
        return None

    def _port_for_instance(self, instance_id: int) -> int | None:
        iid_int = int(instance_id)
        for i, iid in enumerate(self._instance_ids):
            if iid == iid_int:
                return i
        return None

    def open_device(self, device_index: int) -> None:
        """Claim a free port (0 then 1) for a newly connected SDL device.

        Prefers the GameController API when the device supports it, else
        the raw Joystick API. No-ops if both ports are already claimed, or
        if SDL fails to open the device.
        """
        port = self._free_port()
        if port is None:
            return
        sdl = self._sdl
        is_gc = bool(sdl.SDL_IsGameController(device_index))
        if is_gc:
            handle = sdl.SDL_GameControllerOpen(device_index)
            if not handle:
                return
            joy = sdl.SDL_GameControllerGetJoystick(handle)
            instance_id = sdl.SDL_JoystickInstanceID(joy)
        else:
            handle = sdl.SDL_JoystickOpen(device_index)
            if not handle:
                return
            instance_id = sdl.SDL_JoystickInstanceID(handle)
        self._slots[port] = handle
        self._is_gc[port] = is_gc
        self._instance_ids[port] = instance_id

    def close_device(self, instance_id: int) -> None:
        """Release the port claimed by the given SDL instance ID, if any."""
        port = self._port_for_instance(instance_id)
        if port is None:
            return
        self._release_port_bits(port)
        sdl = self._sdl
        if self._is_gc[port]:
            sdl.SDL_GameControllerClose(self._slots[port])
        else:
            sdl.SDL_JoystickClose(self._slots[port])
        self._slots[port] = None
        self._is_gc[port] = False
        self._instance_ids[port] = -1

    def close_all(self) -> None:
        for i in range(2):
            if self._slots[i] is not None:
                self.close_device(self._instance_ids[i])

    def _release_port_bits(self, port: int) -> None:
        self._turbo_held = {(p, b) for p, b in self._turbo_held if p != port}
        for b in range(_PORT_BIT_COUNT):
            self._input.joystick_button_up(port, b)

    def handle_event(self, event: Any) -> None:
        """Dispatch one SDL2 event: device hotplug, or a button/axis/hat
        change on an already-claimed device. Ignores any other event type.

        PORT-NOTE: `event` stays untyped here (rather than a Protocol like
          `_sdl`'s `_SdlJoystickApi`) because it's a real ctypes union
          (`sdl2.SDL_Event`) whose member structs (cdevice/cbutton/caxis/
          jdevice/jbutton/jaxis/jhat) don't type-check meaningfully against a
          Python Protocol either way.
        Rust equivalent: the `sdl2` crate's `Event` is already a proper
          tagged enum (`Event::ControllerButtonDown { which, button, .. }`),
          so a port gets this dispatch shape for free via `match` -- no
          separate typed-wrapper step needed.
        C++ equivalent: SDL_Event is a C union too, so a port typically
          wraps it in its own tagged-variant type at the dispatch boundary
          rather than passing the raw union deeper into the call chain.
        Kept as-is here because: dispatched a handful of times per frame
          (event-loop cadence, not per-sample), so the untyped union costs
          nothing measurable; and Python has no useful way to express a
          ctypes union's active-member-by-tag shape as a static type.
        """
        sdl = self._sdl
        event_type = event.type
        if event_type == sdl.SDL_CONTROLLERDEVICEADDED:
            self.open_device(event.cdevice.which)
        elif event_type == sdl.SDL_CONTROLLERDEVICEREMOVED:
            self.close_device(event.cdevice.which)
        elif event_type == sdl.SDL_JOYDEVICEADDED:
            # Only open as raw joystick if it is NOT a GameController (GC path handles its own add)
            if not sdl.SDL_IsGameController(event.jdevice.which):
                self.open_device(event.jdevice.which)
        elif event_type == sdl.SDL_JOYDEVICEREMOVED:
            self.close_device(event.jdevice.which)
        elif event_type in (sdl.SDL_CONTROLLERBUTTONDOWN, sdl.SDL_CONTROLLERBUTTONUP):
            self._handle_gc_button(event)
        elif event_type == sdl.SDL_CONTROLLERAXISMOTION:
            self._handle_gc_axis(event)
        elif event_type in (sdl.SDL_JOYBUTTONDOWN, sdl.SDL_JOYBUTTONUP):
            self._handle_joy_button(event)
        elif event_type == sdl.SDL_JOYAXISMOTION:
            self._handle_joy_axis(event)
        elif event_type == sdl.SDL_JOYHATMOTION:
            self._handle_joy_hat(event)

    def _set_button(self, port: int, bit: int, is_down: bool) -> None:
        """Directly assert/release a plain (non-turbo) button line."""
        if is_down:
            self._input.joystick_button_down(port, bit)
        else:
            self._input.joystick_button_up(port, bit)

    def _set_turbo(self, port: int, bit: int, is_down: bool) -> None:
        """Add/remove a turbo binding on press/release; see tick()."""
        if is_down:
            if not self._turbo_held:
                self._turbo_counter = 0
            self._turbo_held.add((port, bit))
        else:
            self._turbo_held.discard((port, bit))
            self._input.joystick_button_up(port, bit)

    def _handle_gc_button(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.cbutton.which)
        if port is None:
            return
        is_down = event.type == sdl.SDL_CONTROLLERBUTTONDOWN
        button = int(event.cbutton.button)
        if button in self._gc_button_bit:
            self._set_button(port, self._gc_button_bit[button], is_down)
        elif button in self._gc_turbo_button_bit:
            self._set_turbo(port, self._gc_turbo_button_bit[button], is_down)

    def _apply_axis(self, port: int, axis: int, value: int) -> None:
        """Apply one analog-stick axis reading to the port's direction bits."""
        if axis not in _AXIS_BIT:
            return
        neg_bit, pos_bit = _AXIS_BIT[axis]
        if value < -AXIS_DEAD_ZONE:
            self._input.joystick_button_down(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)
        elif value > AXIS_DEAD_ZONE:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_down(port, pos_bit)
        else:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)

    def _handle_gc_axis(self, event: Any) -> None:
        port = self._port_for_instance(event.caxis.which)
        if port is None:
            return
        self._apply_axis(port, int(event.caxis.axis), int(event.caxis.value))

    def _handle_joy_button(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.jbutton.which)
        if port is None:
            return
        is_down = event.type == sdl.SDL_JOYBUTTONDOWN
        btn = int(event.jbutton.button)
        if btn in _JOY_BUTTON_BIT:
            self._set_button(port, _JOY_BUTTON_BIT[btn], is_down)
        elif btn in _JOY_TURBO_BUTTON_BIT:
            self._set_turbo(port, _JOY_TURBO_BUTTON_BIT[btn], is_down)

    def _handle_joy_axis(self, event: Any) -> None:
        port = self._port_for_instance(event.jaxis.which)
        if port is None:
            return
        self._apply_axis(port, int(event.jaxis.axis), int(event.jaxis.value))

    def _handle_joy_hat(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.jhat.which)
        if port is None:
            return
        hat = int(event.jhat.value)
        for mask, bit in (
            (sdl.SDL_HAT_UP, _BIT_UP),
            (sdl.SDL_HAT_DOWN, _BIT_DOWN),
            (sdl.SDL_HAT_LEFT, _BIT_LEFT),
            (sdl.SDL_HAT_RIGHT, _BIT_RIGHT),
        ):
            if hat & mask:
                self._input.joystick_button_down(port, bit)
            else:
                self._input.joystick_button_up(port, bit)
