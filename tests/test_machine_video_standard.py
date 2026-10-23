"""Machine.video_standard and the shared per-line timing constants.

openspec app-sdl2-video "Frame pacing follows the machine's video standard"
and machine-core "Machine carries per-instance timing parameters": the
frame budget is CYCLES_PER_LINE x lines for the machine's VideoStandard, and
a Machine built without a machine YAML (tests, tools) is NTSC.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from frontend.sdl2_frontend import _target_fps_for
from host.machine_config import (
    build_machine_from_spec,
    load_device_registry,
    load_machine_spec,
)
from msx.machine import Machine
from msx.machine_spec import MachineLoadError, MachineSpec
from msx.vdp._geometry import (
    CYCLES_PER_LINE,
    NTSC_LINES_PER_FRAME,
    PAL_LINES_PER_FRAME,
    VideoStandard,
)
from tests.factories import make_machine, make_machine_msx2

_ROOT = Path(__file__).resolve().parent.parent


def test_timing_constants_keep_the_hardware_values() -> None:
    assert CYCLES_PER_LINE == 228
    assert CYCLES_PER_LINE * NTSC_LINES_PER_FRAME == 59_736
    assert CYCLES_PER_LINE * PAL_LINES_PER_FRAME == 71_364
    assert VideoStandard.NTSC.cycles_per_frame == 59_736
    assert VideoStandard.NTSC.lines_per_frame == 262


def test_video_standard_values_are_the_yaml_spellings() -> None:
    assert VideoStandard("ntsc") is VideoStandard.NTSC
    assert VideoStandard("pal") is VideoStandard.PAL


def test_directly_constructed_machine_defaults_to_ntsc() -> None:
    parts = make_machine(b"\x00" * 0x8000)
    machine = Machine(
        cpu=parts.cpu, vdp=parts.vdp, memory=parts.memory, io=parts.io, psg=parts.psg
    )
    assert machine.video_standard is VideoStandard.NTSC
    assert (machine.cycles_per_frame, machine.lines_per_frame) == (59_736, 262)
    assert _target_fps_for(machine) == 60.0


def test_machine_spec_defaults_to_ntsc() -> None:
    # Dataclass field default (a spec built without a machine YAML).
    assert MachineSpec.video_standard is VideoStandard.NTSC


@pytest.mark.parametrize(
    ("standard", "cycles", "lines"),
    [(VideoStandard.NTSC, 59_736, 262), (VideoStandard.PAL, 71_364, 313)],
)
def test_timing_derives_from_the_video_standard(standard, cycles, lines) -> None:
    assert (standard.cycles_per_frame, standard.lines_per_frame) == (cycles, lines)
    parts = make_machine(b"\x00" * 0x8000)
    machine = Machine(
        cpu=parts.cpu, vdp=parts.vdp, memory=parts.memory, io=parts.io, psg=parts.psg,
        video_standard=standard,
    )
    assert (machine.cycles_per_frame, machine.lines_per_frame) == (cycles, lines)


@pytest.mark.parametrize("factory", ["msx1", "msx2"])
def test_factory_machines_are_ntsc(factory: str) -> None:
    if factory == "msx1":
        machine = make_machine(b"\x00" * 0x8000)
    else:
        machine = make_machine_msx2(b"\x00" * 0x8000, b"\x00" * 0x4000)
    assert machine.video_standard is VideoStandard.NTSC
    assert machine.lines_per_frame == NTSC_LINES_PER_FRAME


@pytest.mark.parametrize(
    ("machine_id", "standard", "lines"),
    [
        ("cbios_msx1_jp", VideoStandard.NTSC, 262),
        ("cbios_msx1_eu", VideoStandard.PAL, 313),
        ("cbios_msx2_jp", VideoStandard.NTSC, 262),
    ],
)
def test_loader_derives_timing_from_video_standard(
    machine_id: str, standard: VideoStandard, lines: int
) -> None:
    registry = load_device_registry(_ROOT / "config")
    spec = load_machine_spec(machine_id, _ROOT / "config", registry, _ROOT)
    assert spec.video_standard is standard
    assert spec.lines_per_frame == lines
    assert spec.cycles_per_frame == CYCLES_PER_LINE * lines


def test_loader_rejects_an_unknown_video_standard(tmp_path: Path) -> None:
    config = tmp_path / "config"
    (config / "machines").mkdir(parents=True)
    src = (_ROOT / "config" / "machines" / "cbios_msx1_jp.yaml").read_text(encoding="utf-8")
    assert "video_standard" in src
    lines = [
        "video_standard: secam" if line.startswith("video_standard:") else line
        for line in src.splitlines()
    ]
    bad = config / "machines" / "cbios_msx1_jp.yaml"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    registry = load_device_registry(_ROOT / "config")

    with pytest.raises(MachineLoadError, match="video_standard 'secam'"):
        load_machine_spec("cbios_msx1_jp", config, registry, _ROOT)


def test_pal_machine_frame_budget_matches_its_timing() -> None:
    registry = load_device_registry(_ROOT / "config")
    spec = load_machine_spec("cbios_msx1_eu", _ROOT / "config", registry, _ROOT)
    machine = build_machine_from_spec(spec)
    assert machine.cycles_per_frame == 71_364
    assert _target_fps_for(machine) == 50.0


@pytest.mark.parametrize("skip_render", [True, False])
def test_tms9918a_run_frame_executes_the_whole_frame_budget(skip_render: bool) -> None:
    # machine-core: the TMS9918A scanline split leaves the per-frame T-state
    # budget unchanged (± one instruction; a NOP sled here).
    machine = make_machine(b"\x00" * 0x8000)
    before = machine.cycle_count
    machine.run_frame(skip_render=skip_render)
    elapsed = machine.cycle_count - before
    assert machine.cycles_per_frame <= elapsed <= machine.cycles_per_frame + 22


def test_pal_tms9918a_run_frame_executes_the_pal_budget() -> None:
    registry = load_device_registry(_ROOT / "config")
    spec = load_machine_spec("cbios_msx1_eu", _ROOT / "config", registry, _ROOT)
    machine = build_machine_from_spec(spec, bios_override=b"\x00" * 0x8000)
    machine.run_frame(skip_render=True)
    assert 71_364 <= machine.cycle_count <= 71_364 + 22
    assert machine.vdp.status & 0x80                  # VBlank raised within the frame


def test_directly_constructed_pal_machine_runs_the_pal_budget() -> None:
    # machine-core "PAL timing is respected in run_frame".
    parts = make_machine(b"\x00" * 0x8000)
    machine = Machine(
        cpu=parts.cpu, vdp=parts.vdp, memory=parts.memory, io=parts.io, psg=parts.psg,
        video_standard=VideoStandard.PAL,
    )
    assert (machine.cycles_per_frame, machine.lines_per_frame) == (71_364, 313)
    machine.run_frame(skip_render=True)
    assert 71_364 <= machine.cycle_count <= 71_364 + 22
