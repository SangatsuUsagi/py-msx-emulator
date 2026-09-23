"""Tests for the SDL2 frontend's real-time frame-pacing target selection.

Covers openspec/specs/app-sdl2-video/spec.md's "Frame pacing follows the
machine's video standard" requirement.
"""
from frontend.sdl2_frontend import _target_fps_for
from msx.machine import Machine


def _machine_with_lines_per_frame(lines_per_frame: int) -> Machine:
    # _target_fps_for only reads lines_per_frame; skip Machine's full
    # (cpu/vdp/memory/io/psg) construction rather than building a real one.
    machine = Machine.__new__(Machine)
    machine.lines_per_frame = lines_per_frame
    return machine


def test_ntsc_machine_paces_at_60_fps() -> None:
    assert _target_fps_for(_machine_with_lines_per_frame(262)) == 60.0


def test_pal_machine_paces_at_50_fps() -> None:
    assert _target_fps_for(_machine_with_lines_per_frame(313)) == 50.0
