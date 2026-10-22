"""Machine-level audio: run_frame() mixes every frame (specs audio-mixer, machine-core)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from msx.audio_filter import BiquadLowPass
from msx.fmpac import FmPac
from msx.machine import Machine
from msx.opll import Opll
from msx.vdp._geometry import VideoStandard
from tests.factories import make_machine

_NOP_ROM = bytes(32768)
_NTSC_BYTES = 735 * 2


def _spy(monkeypatch: pytest.MonkeyPatch, obj: object, calls: list[tuple[Any, ...]]) -> None:
    """Record every generate_samples call on `obj`, passing it through."""
    real: Callable[..., Any] = obj.generate_samples  # type: ignore[attr-defined]

    def wrapper(*args: Any) -> Any:
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(obj, "generate_samples", wrapper)


def _pal_machine() -> Machine:
    parts = make_machine(_NOP_ROM)
    return Machine(
        cpu=parts.cpu, vdp=parts.vdp, memory=parts.memory, io=parts.io, psg=parts.psg,
        ppi=parts.ppi, video_standard=VideoStandard.PAL,
    )


def test_headless_run_advances_every_present_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # audio-mixer "Headless run advances the sound chips".
    machine = make_machine(_NOP_ROM, cartridge=bytes(65536), mapper="KonamiSCC")
    machine.fmpac = FmPac(rom=bytes(65536), opll=Opll())
    assert machine.scc is not None and machine.ppi is not None
    calls: dict[str, list[tuple[Any, ...]]] = {k: [] for k in ("psg", "scc", "click", "opll")}
    _spy(monkeypatch, machine.psg, calls["psg"])
    _spy(monkeypatch, machine.scc, calls["scc"])
    _spy(monkeypatch, machine.ppi.click, calls["click"])
    _spy(monkeypatch, machine.fmpac.opll, calls["opll"])
    machine.run_frame()
    assert {k: len(v) for k, v in calls.items()} == {"psg": 1, "scc": 1, "click": 1, "opll": 1}


def test_dac_is_asked_once_per_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    machine = make_machine(_NOP_ROM, cartridge=bytes(65536), mapper="Majutsushi")
    assert machine.dac is not None
    calls: list[tuple[Any, ...]] = []
    _spy(monkeypatch, machine.dac, calls)
    machine.run_frame()
    assert len(calls) == 1


def test_skipped_render_still_produces_audio() -> None:
    # audio-mixer "Skipped render still produces audio".
    machine = make_machine(_NOP_ROM)
    machine.run_frame(skip_render=True)
    assert len(machine.take_audio()) == _NTSC_BYTES


def test_take_audio_returns_the_last_frame_once() -> None:
    # audio-mixer "take_audio returns the last frame once".
    machine = make_machine(_NOP_ROM)
    machine.run_frame()
    assert len(machine.take_audio()) == _NTSC_BYTES
    assert machine.take_audio() == b""


def test_untaken_audio_is_replaced_not_accumulated() -> None:
    # audio-mixer "Untaken audio is replaced, not accumulated".
    machine = make_machine(_NOP_ROM)
    for _ in range(3):
        machine.run_frame()
    assert len(machine.take_audio()) == _NTSC_BYTES


def test_ntsc_machine_produces_735_samples_per_frame() -> None:
    # audio-mixer "An NTSC machine produces 735 samples per frame".
    machine = make_machine(_NOP_ROM)
    assert machine.samples_per_frame() == 735
    machine.run_frame()
    assert len(machine.take_audio()) == 735 * 2


def test_pal_machine_produces_882_samples_per_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    # audio-mixer "A PAL machine produces 882 samples per frame".
    machine = _pal_machine()
    calls: list[tuple[Any, ...]] = []
    _spy(monkeypatch, machine.psg, calls)
    machine.run_frame()
    assert len(machine.take_audio()) == 882 * 2
    assert calls[0][0] == 882


def test_psg_receives_the_frame_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # audio-mixer "PSG receives the frame boundaries" / "The frame window spans
    # one NTSC frame".
    machine = make_machine(_NOP_ROM)
    machine.run_frame()
    calls: list[tuple[Any, ...]] = []
    _spy(monkeypatch, machine.psg, calls)
    start = machine.cycle_count
    machine.run_frame()
    n, frame_start, frame_end = calls[0]
    assert (n, frame_start, frame_end) == (735, start, machine.cycle_count)
    assert 59_736 <= frame_end - frame_start <= 59_736 + 22


def test_reset_clears_the_filter_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # audio-mixer "Machine reset clears the filter state".
    machine = make_machine(_NOP_ROM)
    loud = bytes(b"\x20\x4e" * 735)  # 20000 per sample
    monkeypatch.setattr(machine.psg, "generate_samples", lambda *_a: bytearray(loud))
    machine.run_frame()
    machine.run_frame()
    machine.reset()
    machine.run_frame()
    assert machine.take_audio() == BiquadLowPass().filter(loud)


def test_run_frame_return_value_is_unchanged() -> None:
    # machine-core "run_frame return value is unchanged".
    machine = make_machine(_NOP_ROM)
    frame = machine.run_frame()
    assert isinstance(frame, bytearray) and len(frame) > 0
    assert machine.run_frame(skip_render=True) == bytearray()


def test_audio_is_available_after_run_frame() -> None:
    # machine-core "Audio is available after run_frame".
    machine = make_machine(_NOP_ROM)
    assert machine.take_audio() == b""
    machine.run_frame()
    assert machine.take_audio() != b""
