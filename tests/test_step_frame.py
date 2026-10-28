"""Machine.step_frame: the core's per-frame API (machine-core "step_frame is the
core's per-frame API" / "One InputFrame value per frame")."""
from __future__ import annotations

from host.input_state import InputState
from msx.boundary import STOP_BREAKPOINT, STOP_NONE
from msx.input import InputFrame
from msx.mouse import MouseDevice
from msx.psg import JoystickPort
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes([0xF3, 0x18, 0xFE]) + bytes(0x8000 - 3)   # DI; JR $


def test_rendered_frame_returns_rgb_video_and_audio() -> None:
    machine = make_machine(_ROM)
    out = machine.step_frame(InputFrame(), render_video=True)
    assert out.video is not None
    assert (out.video.width, out.video.height) == (256, 212)
    assert len(out.video.pixels) == 256 * 212 * 3
    assert len(out.audio) == 735 * 2
    assert out.frame_number == machine.vdp.frame_count == 1


def test_skipped_video_still_returns_audio() -> None:
    machine = make_machine(_ROM)
    out = machine.step_frame(InputFrame(), render_video=False)
    assert out.video is None
    assert len(out.audio) == 735 * 2


def test_512_wide_frame_reports_its_width() -> None:
    machine = make_machine_msx2(_ROM, bytes(0x4000))
    machine.vdp.regs[0] = 0x08            # GRAPHIC5 (SCREEN 6, 512 wide)
    machine.vdp.regs[1] = 0x40
    out = machine.step_frame(InputFrame(), render_video=True)
    assert out.video is not None
    assert out.video.width == 512
    assert len(out.video.pixels) == 512 * 212 * 3


def test_matrix_row_reaches_the_ppi_for_the_whole_frame() -> None:
    # The value is copied into the machine's lines before the frame runs and
    # nothing changes it during the frame, so every PPI read of row 2 sees it.
    machine = make_machine(_ROM)
    assert machine.ppi is not None
    frame_input = InputState()
    frame_input.set_key_state(2, 6, True)
    machine.step_frame(frame_input.take_frame(), render_video=False)
    machine.ppi.write_port(0xAA, 0x02)            # select row 2
    assert machine.ppi.read_port(0xA9) & (1 << 6) == 0


def test_input_replaces_the_previous_frames_lines() -> None:
    machine = make_machine(_ROM)
    pressed = InputState()
    pressed.set_key_state(2, 6, True)
    pressed.keyboard_joystick_down(0, 4)
    machine.step_frame(pressed.take_frame(), render_video=False)
    machine.step_frame(InputFrame(), render_video=False)
    assert machine.input.matrix[2] & (1 << 6)
    assert machine.input.joy1 == 0x3F


def test_mouse_motion_is_applied_once_per_frame() -> None:
    machine = make_machine(_ROM)
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    frame_input = InputFrame(mouse_dx=5, mouse_dy=-3, mouse_left=True)
    machine.step_frame(frame_input, render_video=False)
    assert (mouse._cur_x_rel, mouse._cur_y_rel) == (5, -3)
    assert mouse.read() & 0x10 == 0                 # left button pressed (active-low)
    machine.step_frame(InputFrame(), render_video=False)
    assert (mouse._cur_x_rel, mouse._cur_y_rel) == (5, -3)
    assert mouse.read() & 0x10


def _row_pressed(row: int, bit: int) -> tuple[int, ...]:
    rows = [0xFF] * 11
    rows[row] &= ~(1 << bit) & 0xFF
    return tuple(rows)


def test_injected_key_is_visible_to_a_paused_single_step() -> None:
    # machine-core "Injected key is visible to a paused single step".
    machine = make_machine(_ROM)
    assert machine.ppi is not None
    machine.apply_input(InputFrame(key_matrix=_row_pressed(8, 0)))
    machine.ppi.write_port(0xAA, 0x08)            # select row 8, no frame run
    assert machine.ppi.read_port(0xA9) & 1 == 0


def test_apply_input_does_not_move_the_mouse() -> None:
    # machine-core "apply_input does not move the mouse".
    machine = make_machine(_ROM)
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    machine.apply_input(InputFrame(mouse_dx=7, mouse_right=True))
    assert mouse._cur_x_rel == 0
    assert mouse.read() & 0x20 == 0                 # right button pressed (active-low)


def test_a_frame_value_cannot_be_changed_after_it_is_passed() -> None:
    # machine-core "A frame value cannot be changed after it is passed".
    import dataclasses

    import pytest

    machine = make_machine(_ROM)
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    frame = InputFrame(key_matrix=_row_pressed(2, 6), mouse_dx=3)
    machine.step_frame(frame, render_video=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.mouse_dx = 0  # type: ignore[misc]
    machine.step_frame(frame, render_video=False)
    assert machine.input.matrix[2] & (1 << 6) == 0
    assert mouse._cur_x_rel == 6                    # 3 per frame, twice


def test_a_frame_without_a_break_reports_no_stop() -> None:
    out = make_machine(_ROM).step_frame(InputFrame(), render_video=False)
    assert out.stop_reason == STOP_NONE
    assert out.frame_complete


def test_a_stopped_call_returns_no_video_and_no_audio() -> None:
    machine = make_machine(_ROM)
    machine.set_breakpoints([0x0001])  # the JR $ loop
    out = machine.step_frame(InputFrame(), render_video=True)
    assert out.stop_reason == STOP_BREAKPOINT
    assert out.video is None
    assert out.audio == b""
    assert not out.frame_complete


def test_continuing_a_stopped_frame_feeds_no_motion() -> None:
    machine = make_machine(_ROM)
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    machine.set_breakpoints([0x0001])
    frame_input = InputFrame(mouse_dx=5)
    assert not machine.step_frame(frame_input, render_video=False).frame_complete
    machine.set_breakpoints([])
    assert machine.step_frame(frame_input, render_video=False).frame_complete
    assert mouse._cur_x_rel == 5


def test_a_stopped_run_frame_produces_no_audio() -> None:
    # machine-core "run_frame produces the frame's audio": a stopped call.
    machine = make_machine(_ROM)
    machine.set_breakpoints([0x0001])
    machine.run_frame()
    assert machine.take_audio() == b""
