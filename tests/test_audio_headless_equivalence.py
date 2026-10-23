"""Headless and SDL2-frontend runs produce the same audio and state (audio-mixer).

audio-mixer "Same PCM and snapshot with and without the frontend": one machine
runs frames through the SDL2 frontend's per-frame step with a stub SDL API,
an identical machine runs the same frames headless. Before the core mixed
audio, only the frontend path ticked the sound chips, so the two diverged.
"""
from __future__ import annotations

import ctypes

import pytest

import frontend.sdl2_frontend as fe
from frontend.sdl2_frontend import _CtrlComboState, _DiskList, _HotkeyState, _LoopState
from msx.machine import Machine
from msx.state import _snapshot_from_machine
from tests.factories import make_machine

_FRAMES = 120
_AUDIO_DEV = 1


def _psg_program() -> bytes:
    """BIOS image that starts PSG tone A under a continuous envelope, then spins.

    The envelope keeps the PSG's internal generator state changing every
    frame, so a run that skipped synthesis would leave a different snapshot.
    """
    writes = [
        (0, 0x80), (1, 0x00),   # tone A period
        (7, 0x3E),              # mixer: tone A on, everything else off
        (8, 0x10),              # channel A volume from the envelope
        (11, 0x00), (12, 0x08),  # envelope period
        (13, 0x0E),             # continuous triangle envelope
    ]
    code = bytearray([0xF3])                        # DI
    for reg, value in writes:
        code += bytes([0x3E, reg, 0xD3, 0xA0])      # LD A,reg / OUT (0xA0),A
        code += bytes([0x3E, value, 0xD3, 0xA1])    # LD A,value / OUT (0xA1),A
    code += bytes([0x18, 0xFE])                     # JR $
    return bytes(code) + bytes(0x8000 - len(code))


class _StubSDL:
    """The SDL calls _run_frame makes for an event-free, unpaused frame."""

    def __init__(self) -> None:
        self.queued: list[bytes] = []

    def SDL_PollEvent(self, _event: object) -> int:
        return 0

    def SDL_RenderClear(self, _renderer: object) -> int:
        return 0

    def SDL_RenderCopy(self, *_args: object) -> int:
        return 0

    def SDL_RenderPresent(self, _renderer: object) -> None:
        pass

    def SDL_QueueAudio(self, _dev: int, data: bytes, _len: int) -> int:
        self.queued.append(bytes(data))
        return 0

    def SDL_PauseAudioDevice(self, _dev: int, _pause_on: int) -> None:
        pass

    def SDL_ClearQueuedAudio(self, _dev: int) -> None:
        pass


class _Timer:
    fps = 60.0
    frame_interval = 1.0 / 60.0

    def tick(self) -> float:
        return self.frame_interval

    def tick_unpaced(self) -> float:
        return 0.0


class _NoJoystick:
    def tick(self) -> None:
        pass


def _run_through_frontend(machine: Machine, frames: int) -> list[bytes]:
    sdl = _StubSDL()
    state = _LoopState(
        running=True, texture=object(), tex_w=256, tex_h=212, rgb_buf=b"", skip_counter=0,
    )
    hotkeys = _HotkeyState(bindings={}, disks=_DiskList([]))
    event = ctypes.c_int()
    for _ in range(frames):
        fe._run_frame(
            sdl, machine, None, None, state, _NoJoystick(), _CtrlComboState(),
            {}, "", None, event, None, _Timer(), "auto", _AUDIO_DEV, 3, hotkeys,
        )
        assert state.running
    return sdl.queued


def _run_headless(machine: Machine, frames: int) -> list[bytes]:
    pcm = []
    for _ in range(frames):
        machine.run_frame()
        pcm.append(machine.take_audio())
    return pcm


def test_same_pcm_and_snapshot_with_and_without_the_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fe, "_upload_to_texture", lambda *_a: None)
    rom = _psg_program()
    via_frontend = make_machine(rom)
    headless = make_machine(rom)
    synth_at_start = headless.psg.snapshot_synth()

    frontend_pcm = _run_through_frontend(via_frontend, _FRAMES)
    headless_pcm = _run_headless(headless, _FRAMES)

    assert len(frontend_pcm) == _FRAMES
    assert frontend_pcm == headless_pcm
    assert any(frame != bytes(len(frame)) for frame in headless_pcm)  # the PSG sounded
    assert headless.psg.snapshot_synth() != synth_at_start            # and its state moved
    assert _snapshot_from_machine(via_frontend) == _snapshot_from_machine(headless)
