"""Tests for JoystickManager device lifecycle, hot-plug, and event handling."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from msx.input import InputState
from msx.joystick import JoystickManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sdl(is_gc: bool = True) -> MagicMock:
    """Return a minimal SDL2 mock."""
    sdl = MagicMock()
    sdl.SDL_IsGameController.return_value = is_gc

    # Hat constants
    sdl.SDL_HAT_UP = 0x01
    sdl.SDL_HAT_RIGHT = 0x02
    sdl.SDL_HAT_DOWN = 0x04
    sdl.SDL_HAT_LEFT = 0x08

    # Event type constants
    sdl.SDL_CONTROLLERDEVICEADDED = 1
    sdl.SDL_CONTROLLERDEVICEREMOVED = 2
    sdl.SDL_JOYDEVICEADDED = 3
    sdl.SDL_JOYDEVICEREMOVED = 4
    sdl.SDL_CONTROLLERBUTTONDOWN = 5
    sdl.SDL_CONTROLLERBUTTONUP = 6
    sdl.SDL_CONTROLLERAXISMOTION = 7
    sdl.SDL_JOYBUTTONDOWN = 8
    sdl.SDL_JOYBUTTONUP = 9
    sdl.SDL_JOYAXISMOTION = 10
    sdl.SDL_JOYHATMOTION = 11
    return sdl


def make_manager(is_gc: bool = True) -> tuple[JoystickManager, InputState, MagicMock]:
    inp = InputState()
    sdl = make_sdl(is_gc)
    mgr = JoystickManager(_input=inp, _sdl=sdl)
    return mgr, inp, sdl


def gc_event(sdl: MagicMock, etype: int, which: int, **kwargs) -> SimpleNamespace:
    ev = SimpleNamespace(type=etype)
    ev.cdevice = SimpleNamespace(which=which)
    ev.cbutton = SimpleNamespace(which=which, **kwargs)
    ev.caxis = SimpleNamespace(which=which, **kwargs)
    return ev


def joy_event(sdl: MagicMock, etype: int, which: int, **kwargs) -> SimpleNamespace:
    ev = SimpleNamespace(type=etype)
    ev.jdevice = SimpleNamespace(which=which)
    ev.jbutton = SimpleNamespace(which=which, **kwargs)
    ev.jaxis = SimpleNamespace(which=which, **kwargs)
    ev.jhat = SimpleNamespace(which=which, **kwargs)
    return ev


# ---------------------------------------------------------------------------
# 4.1  open_device assigns port 0 then port 1; third device ignored
# ---------------------------------------------------------------------------

def _setup_gc(
    sdl: MagicMock, device_indices: list[int], instance_ids: list[int]
) -> list[MagicMock]:
    handles = [MagicMock(name=f"gc_{i}") for i in device_indices]
    joys = [MagicMock(name=f"j_{i}") for i in device_indices]
    sdl.SDL_GameControllerOpen.side_effect = handles
    sdl.SDL_GameControllerGetJoystick.side_effect = joys
    sdl.SDL_JoystickInstanceID.side_effect = instance_ids
    return handles


def test_open_device_assigns_port0_first() -> None:
    mgr, inp, sdl = make_manager()
    _setup_gc(sdl, [0], [42])
    mgr.open_device(0)
    assert mgr._slots[0] is not None
    assert mgr._slots[1] is None


def test_open_device_assigns_port1_second() -> None:
    mgr, inp, sdl = make_manager()
    handles = _setup_gc(sdl, [0, 1], [100, 101])
    mgr.open_device(0)
    mgr.open_device(1)
    assert mgr._slots[0] is handles[0]
    assert mgr._slots[1] is handles[1]


def test_third_device_ignored() -> None:
    mgr, inp, sdl = make_manager()
    _setup_gc(sdl, [0, 1, 2], [100, 101, 102])
    mgr.open_device(0)
    mgr.open_device(1)
    mgr.open_device(2)
    assert sdl.SDL_GameControllerOpen.call_count == 2


def test_close_all_releases_bits_and_frees_slots() -> None:
    mgr, inp, sdl = make_manager()
    _setup_gc(sdl, [0, 1], [100, 101])
    mgr.open_device(0)
    mgr.open_device(1)

    inp.joystick_button_down(0, 0)  # Joy1 Up
    inp.joystick_button_down(1, 0)  # Joy2 Up
    assert inp.joy1 & (1 << 0) == 0
    assert inp.joy2 & (1 << 0) == 0

    mgr.close_all()

    assert mgr._slots[0] is None
    assert mgr._slots[1] is None
    assert inp.joy1 & (1 << 0) != 0
    assert inp.joy2 & (1 << 0) != 0


# ---------------------------------------------------------------------------
# 4.2  Hot-plug
# ---------------------------------------------------------------------------

def test_hot_plug_removal_releases_pressed_bits() -> None:
    mgr, inp, sdl = make_manager()
    _setup_gc(sdl, [0], [42])
    mgr.open_device(0)
    inp.joystick_button_down(0, 0)  # Joy1 Up pressed
    assert inp.joy1 & 0x01 == 0

    ev = gc_event(sdl, sdl.SDL_CONTROLLERDEVICEREMOVED, 42)
    mgr.handle_event(ev)

    assert inp.joy1 & 0x01 != 0  # released
    assert mgr._slots[0] is None


def test_reconnected_device_reuses_free_port() -> None:
    mgr, inp, sdl = make_manager()
    handles = _setup_gc(sdl, [0, 0], [42, 43])
    mgr.open_device(0)
    mgr.close_device(42)
    mgr.open_device(0)
    assert mgr._slots[0] is handles[1]


# ---------------------------------------------------------------------------
# 4.3  GameController D-pad and button events
# ---------------------------------------------------------------------------

def _open_single_gc(mgr: JoystickManager, sdl: MagicMock, instance_id: int = 42) -> None:
    _setup_gc(sdl, [0], [instance_id])
    mgr.open_device(0)


def test_gc_dpad_up_sets_joy1_up_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    ev = gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=11)  # DPAD_UP
    mgr.handle_event(ev)
    assert inp.joy1 & 0x01 == 0  # bit 0 pressed


def test_gc_dpad_up_release_clears_joy1_up_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=11))
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONUP, 42, button=11))
    assert inp.joy1 & 0x01 != 0  # bit 0 released


def test_gc_dpad_other_bits_unaffected() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=11))
    assert inp.joy1 & 0x3E == 0x3E  # bits 1-5 unaffected


def test_gc_button_a_sets_trigger_a_bit4() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=0))
    assert inp.joy1 & (1 << 4) == 0  # bit 4 (Trigger A) pressed


def test_gc_button_b_sets_trigger_b_bit5() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=1))
    assert inp.joy1 & (1 << 5) == 0  # bit 5 (Trigger B) pressed


def test_gc_button_b_release_clears_trigger_b() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONDOWN, 42, button=1))
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERBUTTONUP, 42, button=1))
    assert inp.joy1 & (1 << 5) != 0  # bit 5 released


# ---------------------------------------------------------------------------
# 4.4  Analog axis dead-zone
# ---------------------------------------------------------------------------

def test_axis_above_deadzone_up_sets_up_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    ev = gc_event(sdl, sdl.SDL_CONTROLLERAXISMOTION, 42, axis=1, value=20000)
    mgr.handle_event(ev)
    assert inp.joy1 & 0x01 == 0  # bit 0 (up) pressed


def test_axis_negative_deadzone_sets_down_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    ev = gc_event(sdl, sdl.SDL_CONTROLLERAXISMOTION, 42, axis=1, value=-20000)
    mgr.handle_event(ev)
    assert inp.joy1 & 0x02 == 0  # bit 1 (down) pressed


def test_axis_within_deadzone_does_not_set_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERAXISMOTION, 42, axis=1, value=1000))
    assert inp.joy1 & 0x01 != 0


def test_axis_returns_to_deadzone_releases_bit() -> None:
    mgr, inp, sdl = make_manager()
    _open_single_gc(mgr, sdl)
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERAXISMOTION, 42, axis=1, value=20000))
    assert inp.joy1 & 0x01 == 0
    mgr.handle_event(gc_event(sdl, sdl.SDL_CONTROLLERAXISMOTION, 42, axis=1, value=0))
    assert inp.joy1 & 0x01 != 0


# ---------------------------------------------------------------------------
# 4.5  Raw Joystick hat and button events
# ---------------------------------------------------------------------------

def _open_single_joy(mgr: JoystickManager, sdl: MagicMock, instance_id: int = 55) -> None:
    h = MagicMock(name="joy")
    sdl.SDL_JoystickOpen.side_effect = [h]
    sdl.SDL_JoystickInstanceID.side_effect = [instance_id]
    mgr.open_device(0)


def test_hat_up_sets_up_bit() -> None:
    mgr, inp, sdl = make_manager(is_gc=False)
    _open_single_joy(mgr, sdl)
    mgr.handle_event(joy_event(sdl, sdl.SDL_JOYHATMOTION, 55, value=sdl.SDL_HAT_UP))
    assert inp.joy1 & 0x01 == 0


def test_hat_diagonal_sets_two_bits() -> None:
    mgr, inp, sdl = make_manager(is_gc=False)
    _open_single_joy(mgr, sdl)
    mgr.handle_event(
        joy_event(sdl, sdl.SDL_JOYHATMOTION, 55, value=sdl.SDL_HAT_LEFT | sdl.SDL_HAT_UP)
    )
    assert inp.joy1 & 0x01 == 0  # up
    assert inp.joy1 & 0x04 == 0  # left


def test_hat_centered_releases_all_direction_bits() -> None:
    mgr, inp, sdl = make_manager(is_gc=False)
    _open_single_joy(mgr, sdl)
    mgr.handle_event(joy_event(sdl, sdl.SDL_JOYHATMOTION, 55, value=sdl.SDL_HAT_UP))
    assert inp.joy1 & 0x01 == 0
    mgr.handle_event(joy_event(sdl, sdl.SDL_JOYHATMOTION, 55, value=0))
    assert inp.joy1 & 0x0F == 0x0F  # bits 0-3 all released


def test_raw_joy_button0_sets_trigger_a() -> None:
    mgr, inp, sdl = make_manager(is_gc=False)
    _open_single_joy(mgr, sdl)
    mgr.handle_event(joy_event(sdl, sdl.SDL_JOYBUTTONDOWN, 55, button=0))
    assert inp.joy1 & (1 << 4) == 0  # bit 4 (Trigger A) pressed


def test_raw_joy_button1_sets_trigger_b() -> None:
    mgr, inp, sdl = make_manager(is_gc=False)
    _open_single_joy(mgr, sdl)
    mgr.handle_event(joy_event(sdl, sdl.SDL_JOYBUTTONDOWN, 55, button=1))
    assert inp.joy1 & (1 << 5) == 0  # bit 5 (Trigger B) pressed
