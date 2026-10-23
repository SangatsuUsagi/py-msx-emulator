"""Machine.step_frame: the core's per-frame API (machine-core "step_frame is the
core's per-frame API" / "One InputState value per frame")."""
from __future__ import annotations

from msx.input import InputState
from msx.mouse import MouseDevice
from msx.psg import JoystickPort
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes([0xF3, 0x18, 0xFE]) + bytes(0x8000 - 3)   # DI; JR $


def test_rendered_frame_returns_rgb_video_and_audio() -> None:
    machine = make_machine(_ROM)
    out = machine.step_frame(InputState(), render_video=True)
    assert out.video is not None
    assert (out.video.width, out.video.height) == (256, 212)
    assert len(out.video.pixels) == 256 * 212 * 3
    assert len(out.audio) == 735 * 2
    assert out.frame_number == machine.vdp.frame_count == 1


def test_skipped_video_still_returns_audio() -> None:
    machine = make_machine(_ROM)
    out = machine.step_frame(InputState(), render_video=False)
    assert out.video is None
    assert len(out.audio) == 735 * 2


def test_512_wide_frame_reports_its_width() -> None:
    machine = make_machine_msx2(_ROM, bytes(0x4000))
    machine.vdp.regs[0] = 0x08            # GRAPHIC5 (SCREEN 6, 512 wide)
    machine.vdp.regs[1] = 0x40
    out = machine.step_frame(InputState(), render_video=True)
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
    machine.step_frame(frame_input, render_video=False)
    machine.ppi.write_port(0xAA, 0x02)            # select row 2
    assert machine.ppi.read_port(0xA9) & (1 << 6) == 0


def test_input_replaces_the_previous_frames_lines() -> None:
    machine = make_machine(_ROM)
    pressed = InputState()
    pressed.set_key_state(2, 6, True)
    pressed.keyboard_joystick_down(0, 4)
    machine.step_frame(pressed, render_video=False)
    machine.step_frame(InputState(), render_video=False)
    assert machine.input.matrix[2] & (1 << 6)
    assert machine.input.joy1 == 0x3F


def test_mouse_motion_is_applied_once_per_frame() -> None:
    machine = make_machine(_ROM)
    mouse = MouseDevice()
    machine.attach_mouse(mouse, JoystickPort.JOY2)
    frame_input = InputState(mouse_dx=5, mouse_dy=-3, mouse_left=True)
    machine.step_frame(frame_input, render_video=False)
    assert (mouse._cur_x_rel, mouse._cur_y_rel) == (5, -3)
    assert mouse.read() & 0x10 == 0                 # left button pressed (active-low)
    machine.step_frame(InputState(), render_video=False)
    assert (mouse._cur_x_rel, mouse._cur_y_rel) == (5, -3)
    assert mouse.read() & 0x10
