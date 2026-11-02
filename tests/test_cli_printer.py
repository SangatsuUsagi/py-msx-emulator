"""--printer-log (app-cli "--printer-log writes the printed bytes to a file").

The headless runs use the real debugger, which drains after each frame.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from host.debugger.prompt import Debugger
from msx.boundary import FrameOutput
from msx.machine import Machine
from tests.factories import make_machine
from tests.test_cli_trace_flags import _FRAME_DONE, _run_main


def _strobe(machine: Machine, *values: int) -> None:
    for value in values:
        machine.io.write_port(0x91, value)
        machine.io.write_port(0x90, 0x00)
        machine.io.write_port(0x90, 0x01)


def _frames(*steps: Callable[[Machine], None]) -> Callable[..., FrameOutput]:
    """A Machine.step_frame that runs one step per call."""
    pending = list(steps)

    def step_frame(self: Machine, *_args: object, **_kwargs: object) -> FrameOutput:
        pending.pop(0)(self)
        return _FRAME_DONE

    return step_frame


def _run(
    tmp_path: Path, step_frame: Callable[..., FrameOutput], count: int, machine: str = "hb_10"
) -> tuple[int, Path, str]:
    out = tmp_path / "out.prn"
    with patch.object(Machine, "step_frame", step_frame):
        code, _stdout, err = _run_main(
            ["--count-frame", str(count), "--printer-log", str(out)],
            machine=machine, real_debugger=True,
        )
    return code, out, err


def _not_created(path: Path) -> bool:
    # _run_main patches Path.exists to True, so ask the operating system.
    return not os.path.lexists(path)


def _contents(path: Path) -> bytes:
    # _run_main patches Path.read_bytes while main() runs.
    with open(path, "rb") as fh:
        return fh.read()


def test_bytes_are_recorded(tmp_path: Path) -> None:
    step = _frames(lambda m: _strobe(m, 0x1B, 0x4B), lambda m: _strobe(m, 0x0D))
    code, out, _err = _run(tmp_path, step, 2)
    assert code == 0
    assert out.read_bytes() == b"\x1B\x4B\x0D"


def test_bytes_reach_the_file_after_each_frame(tmp_path: Path) -> None:
    seen: list[bytes] = []
    out = tmp_path / "out.prn"
    step = _frames(lambda m: _strobe(m, 0x41), lambda m: seen.append(_contents(out)))
    code, _out, _err = _run(tmp_path, step, 2)
    assert code == 0
    assert seen == [b"\x41"]


def test_a_reset_keeps_the_file(tmp_path: Path) -> None:
    def reset_and_print(machine: Machine) -> None:
        machine.reset()
        _strobe(machine, 0x42)

    code, out, _err = _run(tmp_path, _frames(lambda m: _strobe(m, 0x41), reset_and_print), 2)
    assert code == 0
    assert out.read_bytes() == b"\x41\x42"


def test_an_error_exit_keeps_the_bytes(tmp_path: Path) -> None:
    def fail(machine: Machine) -> None:
        _strobe(machine, 0x41)
        raise RuntimeError("forced")

    with pytest.raises(RuntimeError):
        _run(tmp_path, _frames(fail), 1)
    assert (tmp_path / "out.prn").read_bytes() == b"\x41"


def test_a_machine_with_no_port(tmp_path: Path) -> None:
    code, out, err = _run(tmp_path, _frames(), 0, machine="cbios_msx2")
    assert code == 1
    assert "no printer port" in err
    assert _not_created(out)


@pytest.mark.parametrize("is_empty", [True, False], ids=["empty", "directory"])
def test_a_path_that_cannot_be_opened(tmp_path: Path, is_empty: bool) -> None:
    path = "" if is_empty else str(tmp_path)
    code, _stdout, err = _run_main(
        ["--count-frame", "0", "--printer-log", path], machine="hb_10", real_debugger=True
    )
    assert code == 1
    assert "--printer-log" in err


def test_the_benchmark_conflict(tmp_path: Path) -> None:
    out = tmp_path / "out.prn"
    code, _stdout, err = _run_main(["--benchmark", "100", "--printer-log", str(out)])
    assert code == 1
    assert "--printer-log" in err and "--benchmark" in err
    assert _not_created(out)


def test_no_option_attaches_no_printer() -> None:
    seen: list[int] = []
    step = _frames(lambda m: seen.append(m.io.read_port(0x90)))
    with patch.object(Machine, "step_frame", step):
        code, _stdout, _err = _run_main(
            ["--count-frame", "1"], machine="hb_10", real_debugger=True
        )
    assert code == 0
    assert seen == [0xFF]


def test_the_debugger_step_drains() -> None:
    written: list[int] = []

    class Output:
        def drain(self) -> None:
            written.append(1)

    debugger = Debugger(make_machine(bytes(0x8000)), printer=Output())  # type: ignore[arg-type]
    debugger._cmd_step([])
    assert written == [1]
