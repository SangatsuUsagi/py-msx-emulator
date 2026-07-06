from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msx.input import InputState

AXIS_DEAD_ZONE: int = 8192
TURBO_PERIOD: int = 3   # frames per turbo cycle at 60 fps → 20 Hz
TURBO_ON_COUNT: int = 1  # frames ON per cycle (ON-OFF-OFF pattern)

# Each port uses bits 0-5: up(0) down(1) left(2) right(3) trigA(4) trigB(5).
# Both ports maintain their own 6-bit state independently in InputState.
_PORT_BIT_COUNT = 6

# GameController button → bit index within the port's 6-bit joystick state
_GC_BUTTON_BIT = {
    0: 4,   # SDL_CONTROLLER_BUTTON_A  → Trigger A
    1: 5,   # SDL_CONTROLLER_BUTTON_B  → Trigger B
    11: 0,  # SDL_CONTROLLER_BUTTON_DPAD_UP
    12: 1,  # SDL_CONTROLLER_BUTTON_DPAD_DOWN
    13: 2,  # SDL_CONTROLLER_BUTTON_DPAD_LEFT
    14: 3,  # SDL_CONTROLLER_BUTTON_DPAD_RIGHT
}

# GameController button → bit index (turbo fire; same bits as A/B but driven by tick())
_GC_TURBO_BUTTON_BIT = {
    2: 5,   # SDL_CONTROLLER_BUTTON_X → Trigger B (turbo)
    3: 4,   # SDL_CONTROLLER_BUTTON_Y → Trigger A (turbo)
}

# GameController axis → (negative_bit_offset, positive_bit_offset)
# macOS SDL2 reports left-stick Y as positive=up, negative=down (inverted from SDL2 spec)
_GC_AXIS_BIT = {
    0: (2, 3),  # left-stick X: neg=left, pos=right
    1: (1, 0),  # left-stick Y: neg=down, pos=up
}


@dataclass
class JoystickManager:
    _input: InputState
    _sdl: Any = field(default=None, repr=False)

    _slots: list = field(default_factory=lambda: [None, None], init=False, repr=False)
    _is_gc: list[bool] = field(default_factory=lambda: [False, False], init=False, repr=False)
    _instance_ids: list[int] = field(default_factory=lambda: [-1, -1], init=False, repr=False)
    _turbo_held: set = field(default_factory=set, init=False, repr=False)
    _turbo_counter: int = field(default=0, init=False, repr=False)

    def tick(self) -> None:
        """Advance the turbo fire state machine by one emulated frame."""
        if not self._turbo_held:
            return
        on = (self._turbo_counter % TURBO_PERIOD) < TURBO_ON_COUNT
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
        port = self._free_port()
        if port is None:
            return
        sdl = self._sdl
        if sdl.SDL_IsGameController(device_index):
            handle = sdl.SDL_GameControllerOpen(device_index)
            if not handle:
                return
            joy = sdl.SDL_GameControllerGetJoystick(handle)
            instance_id = sdl.SDL_JoystickInstanceID(joy)
            self._slots[port] = handle
            self._is_gc[port] = True
            self._instance_ids[port] = instance_id
        else:
            handle = sdl.SDL_JoystickOpen(device_index)
            if not handle:
                return
            instance_id = sdl.SDL_JoystickInstanceID(handle)
            self._slots[port] = handle
            self._is_gc[port] = False
            self._instance_ids[port] = instance_id

    def close_device(self, instance_id: int) -> None:
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
        sdl = self._sdl
        t = event.type
        if t == sdl.SDL_CONTROLLERDEVICEADDED:
            self.open_device(event.cdevice.which)
        elif t == sdl.SDL_CONTROLLERDEVICEREMOVED:
            self.close_device(event.cdevice.which)
        elif t == sdl.SDL_JOYDEVICEADDED:
            # Only open as raw joystick if it is NOT a GameController (GC path handles its own add)
            if not sdl.SDL_IsGameController(event.jdevice.which):
                self.open_device(event.jdevice.which)
        elif t == sdl.SDL_JOYDEVICEREMOVED:
            self.close_device(event.jdevice.which)
        elif t in (sdl.SDL_CONTROLLERBUTTONDOWN, sdl.SDL_CONTROLLERBUTTONUP):
            self._handle_gc_button(event)
        elif t == sdl.SDL_CONTROLLERAXISMOTION:
            self._handle_gc_axis(event)
        elif t in (sdl.SDL_JOYBUTTONDOWN, sdl.SDL_JOYBUTTONUP):
            self._handle_joy_button(event)
        elif t == sdl.SDL_JOYAXISMOTION:
            self._handle_joy_axis(event)
        elif t == sdl.SDL_JOYHATMOTION:
            self._handle_joy_hat(event)

    def _handle_gc_button(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.cbutton.which)
        if port is None:
            return
        button = int(event.cbutton.button)
        if button in _GC_BUTTON_BIT:
            bit = _GC_BUTTON_BIT[button]
            if event.type == sdl.SDL_CONTROLLERBUTTONDOWN:
                self._input.joystick_button_down(port, bit)
            else:
                self._input.joystick_button_up(port, bit)
        elif button in _GC_TURBO_BUTTON_BIT:
            bit = _GC_TURBO_BUTTON_BIT[button]
            if event.type == sdl.SDL_CONTROLLERBUTTONDOWN:
                if not self._turbo_held:
                    self._turbo_counter = 0
                self._turbo_held.add((port, bit))
            else:
                self._turbo_held.discard((port, bit))
                self._input.joystick_button_up(port, bit)

    def _handle_gc_axis(self, event: Any) -> None:
        port = self._port_for_instance(event.caxis.which)
        if port is None:
            return
        axis = int(event.caxis.axis)
        if axis not in _GC_AXIS_BIT:
            return
        neg_bit, pos_bit = _GC_AXIS_BIT[axis]
        value = int(event.caxis.value)
        if value < -AXIS_DEAD_ZONE:
            self._input.joystick_button_down(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)
        elif value > AXIS_DEAD_ZONE:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_down(port, pos_bit)
        else:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)

    def _handle_joy_button(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.jbutton.which)
        if port is None:
            return
        btn = int(event.jbutton.button)
        if btn == 0:
            bit = 4  # Trigger A
            if event.type == sdl.SDL_JOYBUTTONDOWN:
                self._input.joystick_button_down(port, bit)
            else:
                self._input.joystick_button_up(port, bit)
        elif btn == 1:
            bit = 5  # Trigger B
            if event.type == sdl.SDL_JOYBUTTONDOWN:
                self._input.joystick_button_down(port, bit)
            else:
                self._input.joystick_button_up(port, bit)
        elif btn == 2:
            bit = 4  # Trigger A (turbo)
            if event.type == sdl.SDL_JOYBUTTONDOWN:
                if not self._turbo_held:
                    self._turbo_counter = 0
                self._turbo_held.add((port, bit))
            else:
                self._turbo_held.discard((port, bit))
                self._input.joystick_button_up(port, bit)
        elif btn == 3:
            bit = 5  # Trigger B (turbo)
            if event.type == sdl.SDL_JOYBUTTONDOWN:
                if not self._turbo_held:
                    self._turbo_counter = 0
                self._turbo_held.add((port, bit))
            else:
                self._turbo_held.discard((port, bit))
                self._input.joystick_button_up(port, bit)

    def _handle_joy_axis(self, event: Any) -> None:
        port = self._port_for_instance(event.jaxis.which)
        if port is None:
            return
        axis = int(event.jaxis.axis)
        if axis not in _GC_AXIS_BIT:
            return
        neg_bit, pos_bit = _GC_AXIS_BIT[axis]
        value = int(event.jaxis.value)
        if value < -AXIS_DEAD_ZONE:
            self._input.joystick_button_down(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)
        elif value > AXIS_DEAD_ZONE:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_down(port, pos_bit)
        else:
            self._input.joystick_button_up(port, neg_bit)
            self._input.joystick_button_up(port, pos_bit)

    def _handle_joy_hat(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.jhat.which)
        if port is None:
            return
        hat = int(event.jhat.value)
        if hat & sdl.SDL_HAT_UP:
            self._input.joystick_button_down(port, 0)
        else:
            self._input.joystick_button_up(port, 0)
        if hat & sdl.SDL_HAT_DOWN:
            self._input.joystick_button_down(port, 1)
        else:
            self._input.joystick_button_up(port, 1)
        if hat & sdl.SDL_HAT_LEFT:
            self._input.joystick_button_down(port, 2)
        else:
            self._input.joystick_button_up(port, 2)
        if hat & sdl.SDL_HAT_RIGHT:
            self._input.joystick_button_down(port, 3)
        else:
            self._input.joystick_button_up(port, 3)
