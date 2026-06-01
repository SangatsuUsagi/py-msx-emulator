from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msx.input import InputState

AXIS_DEAD_ZONE: int = 8192

# Joy1 uses bits 0-4; Joy2 uses bits 5-7.
_PORT_BIT_OFFSET = [0, 5]
_PORT_BIT_COUNT = [5, 3]

# GameController button → bit offset within port
_GC_BUTTON_BIT = {
    0: 4,   # SDL_CONTROLLER_BUTTON_A  → Trigger A (bit 4 / bit 7)
    1: 4,   # SDL_CONTROLLER_BUTTON_B  → Trigger A
    11: 0,  # SDL_CONTROLLER_BUTTON_DPAD_UP
    12: 1,  # SDL_CONTROLLER_BUTTON_DPAD_DOWN
    13: 2,  # SDL_CONTROLLER_BUTTON_DPAD_LEFT
    14: 3,  # SDL_CONTROLLER_BUTTON_DPAD_RIGHT
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
        offset = _PORT_BIT_OFFSET[port]
        count = _PORT_BIT_COUNT[port]
        for b in range(count):
            self._input.joystick_button_up(port, offset + b)

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
        if button not in _GC_BUTTON_BIT:
            return
        bit_offset = _GC_BUTTON_BIT[button]
        bit = _PORT_BIT_OFFSET[port] + bit_offset
        if event.type == sdl.SDL_CONTROLLERBUTTONDOWN:
            self._input.joystick_button_down(port, bit)
        else:
            self._input.joystick_button_up(port, bit)

    def _handle_gc_axis(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.caxis.which)
        if port is None:
            return
        axis = int(event.caxis.axis)
        if axis not in _GC_AXIS_BIT:
            return
        neg_off, pos_off = _GC_AXIS_BIT[axis]
        neg_bit = _PORT_BIT_OFFSET[port] + neg_off
        pos_bit = _PORT_BIT_OFFSET[port] + pos_off
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
        if int(event.jbutton.button) not in (0, 1):
            return
        bit = _PORT_BIT_OFFSET[port] + 4
        if event.type == sdl.SDL_JOYBUTTONDOWN:
            self._input.joystick_button_down(port, bit)
        else:
            self._input.joystick_button_up(port, bit)

    def _handle_joy_axis(self, event: Any) -> None:
        sdl = self._sdl
        port = self._port_for_instance(event.jaxis.which)
        if port is None:
            return
        axis = int(event.jaxis.axis)
        if axis not in _GC_AXIS_BIT:
            return
        neg_off, pos_off = _GC_AXIS_BIT[axis]
        neg_bit = _PORT_BIT_OFFSET[port] + neg_off
        pos_bit = _PORT_BIT_OFFSET[port] + pos_off
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
        offset = _PORT_BIT_OFFSET[port]
        hat = int(event.jhat.value)
        if hat & sdl.SDL_HAT_UP:
            self._input.joystick_button_down(port, offset + 0)
        else:
            self._input.joystick_button_up(port, offset + 0)
        if hat & sdl.SDL_HAT_DOWN:
            self._input.joystick_button_down(port, offset + 1)
        else:
            self._input.joystick_button_up(port, offset + 1)
        if hat & sdl.SDL_HAT_LEFT:
            self._input.joystick_button_down(port, offset + 2)
        else:
            self._input.joystick_button_up(port, offset + 2)
        if hat & sdl.SDL_HAT_RIGHT:
            self._input.joystick_button_down(port, offset + 3)
        else:
            self._input.joystick_button_up(port, offset + 3)
