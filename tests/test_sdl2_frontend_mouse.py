"""Host-pixel to MSX-count mouse conversion in the SDL2 frontend (input-mouse)."""
from frontend.sdl2_frontend import _HostMouse
from msx.mouse import MouseDevice


def test_sub_scale_motion_accumulates_instead_of_being_dropped() -> None:
    mouse = _HostMouse(MouseDevice(), scale=3)
    mouse.motion(1, 0)
    assert mouse.device._cur_x_rel == 0
    mouse.motion(1, 0)
    assert mouse.device._cur_x_rel == 0
    mouse.motion(1, 0)
    assert mouse.device._cur_x_rel == 1


def test_negative_sub_scale_motion_carries_with_floor_division() -> None:
    # A truncating '/'/'%' port would give (0, -1) here instead of (-1, 2),
    # corrupting the carry.
    mouse = _HostMouse(MouseDevice(), scale=3)
    mouse.motion(-1, 0)
    assert mouse.device._cur_x_rel == -1
    assert mouse.frac_x == 2


def test_scale_one_passes_pixels_through() -> None:
    mouse = _HostMouse(MouseDevice(), scale=1)
    mouse.motion(-7, 4)
    assert (mouse.device._cur_x_rel, mouse.device._cur_y_rel) == (-7, 4)
