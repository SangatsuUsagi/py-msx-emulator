"""--mouse in the headless runs (input-mouse "Mouse attaches to one joystick
port": "A headless run attaches the mouse before the resume", and, through
the headless runs, "--mouse alone attaches to Joy2", "--mouse=1 attaches to
Joy1" and "No flag means no mouse")."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from msx.boundary import STATUS_OK
from msx.machine import Machine
from tests.test_cli_trace_flags import _FRAME_DONE, _run_main

_HEADLESS = (["--count-frame", "1"], ["--benchmark", "1"])


def _run_recording(argv: list[str]) -> tuple[int, list[tuple[str, tuple[object, ...]]]]:
    """Run main() and return its exit code and the attach and resume calls,
    in order."""
    calls: list[tuple[str, tuple[object, ...]]] = []

    def attach(_self: Machine, port: int) -> int:
        calls.append(("attach_mouse", (port,)))
        return STATUS_OK

    def resume(_machine: object, path: object) -> None:
        calls.append(("resume_state", (path,)))

    with patch.object(Machine, "attach_mouse", attach), \
         patch.object(Machine, "step_frame", return_value=_FRAME_DONE), \
         patch("host.state_files.resume_state", MagicMock(side_effect=resume)):
        code, _out, _err = _run_main(argv)
    return code, calls


@pytest.mark.parametrize("mode", _HEADLESS)
def test_a_headless_run_attaches_the_mouse_before_the_resume(mode: list[str]) -> None:
    code, calls = _run_recording(["--mouse", *mode, "--resume", "s.state"])
    assert code == 0
    assert calls == [("attach_mouse", (1,)), ("resume_state", ("s.state",))]


@pytest.mark.parametrize("mode", _HEADLESS)
def test_mouse_alone_attaches_to_joy2(mode: list[str]) -> None:
    code, calls = _run_recording(["--mouse", *mode])
    assert code == 0
    assert calls == [("attach_mouse", (1,))]


@pytest.mark.parametrize("flag", [["--mouse=1"], ["--mouse", "1"]])
@pytest.mark.parametrize("mode", _HEADLESS)
def test_mouse_1_attaches_to_joy1(mode: list[str], flag: list[str]) -> None:
    code, calls = _run_recording([*flag, *mode])
    assert code == 0
    assert calls == [("attach_mouse", (0,))]


@pytest.mark.parametrize("mode", _HEADLESS)
def test_no_flag_means_no_mouse(mode: list[str]) -> None:
    code, calls = _run_recording([*mode, "--resume", "s.state"])
    assert code == 0
    assert calls == [("resume_state", ("s.state",))]
