"""Tests for the SDL2 frontend's real-time frame-pacing target selection.

Covers openspec/specs/app-sdl2-video/spec.md's "Frame pacing follows the
machine's video standard" requirement.
"""
from pathlib import Path

import pytest

from frontend.sdl2_frontend import _target_fps_for
from msx.machine import Machine
from msx.vdp._geometry import VideoStandard


def _machine_with_video_standard(standard: VideoStandard) -> Machine:
    # _target_fps_for only reads video_standard; skip Machine's full
    # (cpu/vdp/memory/io/psg) construction rather than building a real one.
    machine = Machine.__new__(Machine)
    machine.video_standard = standard
    return machine


def test_ntsc_machine_paces_at_60_fps() -> None:
    assert _target_fps_for(_machine_with_video_standard(VideoStandard.NTSC)) == 60.0


def test_pal_machine_paces_at_50_fps() -> None:
    assert _target_fps_for(_machine_with_video_standard(VideoStandard.PAL)) == 50.0


@pytest.mark.parametrize("machine_id", ["cbios_msx1_eu", "cbios_msx2_eu"])
def test_loader_sets_video_standard_from_machine_yaml(machine_id: str) -> None:
    from host.machine_config import (
        build_machine_from_spec,
        load_device_registry,
        load_machine_spec,
    )

    root = Path(__file__).resolve().parent.parent
    registry = load_device_registry(root / "config")
    spec = load_machine_spec(machine_id, root / "config", registry, root)
    machine = build_machine_from_spec(spec)
    assert machine.video_standard is VideoStandard.PAL
    assert machine.lines_per_frame == 313
