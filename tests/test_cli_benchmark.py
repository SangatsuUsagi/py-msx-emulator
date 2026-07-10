"""CLI --benchmark integration tests."""
from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from msx.machine import Machine

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str]) -> tuple[int, str, str, Mock]:
    """Run __main__.main() in MSX2 mode with patched filesystem.

    Returns (exit_code, stdout, stderr, sdl_run_mock).
    """
    stdout_buf = StringIO()
    stderr_buf = StringIO()

    with patch.object(sys, "argv", [".", "--machine", "cbios_msx2", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", lambda self: b"\x00" * 32768), \
         patch("frontend.sdl2_frontend.run") as sdl_run, \
         patch("msx.debugger.prompt.Debugger"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue(), sdl_run
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue(), sdl_run


class TestBenchmarkCLI:
    def test_reports_frame_count_elapsed_and_fps(self) -> None:
        perf_values = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 10.0])
        with patch.object(Machine, "run_frame", return_value=None) as rf, \
             patch("time.perf_counter", side_effect=lambda: next(perf_values)):
            code, out, err, sdl_run = _run_main(["--benchmark", "10"])
        assert code == 0
        assert rf.call_count == 5
        assert "frames  : 5" in out
        assert "elapsed : 10.00s" in out
        assert "avg fps : 0.50" in out
        sdl_run.assert_not_called()
        # Offscreen VDP rendering must not be skipped: benchmark measures whether
        # emulation *and* rendering together sustain 60fps, not raw CPU throughput.
        for call in rf.call_args_list:
            assert call.kwargs.get("skip_render", False) is False
            assert not call.args

    def test_bare_flag_defaults_to_ten_seconds(self) -> None:
        perf_values = iter([0.0, 10.0, 10.0])
        with patch.object(Machine, "run_frame", return_value=None), \
             patch("time.perf_counter", side_effect=lambda: next(perf_values)):
            code, out, err, sdl_run = _run_main(["--benchmark"])
        assert code == 0
        assert "benchmark: 10.0s (headless)" in out


class TestBenchmarkResume:
    def test_resume_loads_state_before_timed_loop(self) -> None:
        calls: list[tuple[str, object]] = []

        def fake_load_state(machine: Machine, path: Path | None = None) -> None:
            calls.append(("load_state", path))

        def fake_run_frame() -> None:
            calls.append(("run_frame", None))

        perf_values = iter([0.0, 1.0, 10.0, 10.0])
        with patch("msx.state.load_state", side_effect=fake_load_state) as load_state_mock, \
             patch.object(Machine, "run_frame", side_effect=fake_run_frame), \
             patch("time.perf_counter", side_effect=lambda: next(perf_values)):
            code, out, err, sdl_run = _run_main(["--benchmark", "10", "--resume"])
        assert code == 0
        assert load_state_mock.call_count == 1
        assert load_state_mock.call_args.kwargs["path"] is None
        assert calls[0] == ("load_state", None)
        assert calls[1] == ("run_frame", None)

    def test_resume_with_explicit_path_loads_that_state(self) -> None:
        perf_values = iter([0.0, 10.0, 10.0])
        with patch("msx.state.load_state") as load_state_mock, \
             patch.object(Machine, "run_frame", return_value=None), \
             patch("time.perf_counter", side_effect=lambda: next(perf_values)):
            code, out, err, sdl_run = _run_main(
                ["--benchmark", "10", "--resume", "path/to/scene.state"])
        assert code == 0
        load_state_mock.assert_called_once()
        assert load_state_mock.call_args.kwargs["path"] == Path("path/to/scene.state")


class TestBenchmarkCountFrameMutualExclusion:
    def test_both_flags_exits_nonzero(self) -> None:
        with patch.object(Machine, "run_frame", return_value=None) as rf:
            code, out, err, sdl_run = _run_main(["--benchmark", "5", "--count-frame", "10"])
        assert code != 0
        assert "mutually exclusive" in err.lower()
        rf.assert_not_called()
        sdl_run.assert_not_called()


class TestBenchmarkNoSDLWindow:
    def test_benchmark_skips_sdl_frontend(self) -> None:
        perf_values = iter([0.0, 10.0, 10.0])
        with patch.object(Machine, "run_frame", return_value=None), \
             patch("time.perf_counter", side_effect=lambda: next(perf_values)):
            code, out, err, sdl_run = _run_main(["--benchmark", "10"])
        assert code == 0
        sdl_run.assert_not_called()
