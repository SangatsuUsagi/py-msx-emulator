"""--vdp-trace, --mapper-trace, their -out files and --count-frame: CLI
integration tests that exercise the argparse wiring through
__main__.main().
"""
from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from msx.boundary import FrameOutput
from msx.machine import Machine

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"



# What the mocked step_frame returns: a completed frame with no stop.
_FRAME_DONE = FrameOutput(0, 0, b"", b"", 0)

# ---------------------------------------------------------------------------
# CLI integration (mirror of tests/test_cli_breakpoint.py harness)
# ---------------------------------------------------------------------------

def _run_main(
    argv: list[str], *, machine: str = "cbios_msx2", image: bytes = b"\x00" * 32768,
    real_debugger: bool = False,
) -> tuple[int, str, str]:
    """Run main() with every ROM and cartridge file reading as `image`. The
    trace lines (printed to sys.stdout) count as stdout."""
    import contextlib

    stdout_buf = StringIO()
    stderr_buf = StringIO()
    real_stdout = sys.stdout

    def fake_print(*a: object, **kw: object) -> None:
        line = " ".join(str(x) for x in a) + "\n"
        target = kw.get("file")
        if target is None or target is real_stdout:
            stdout_buf.write(line)
        elif target is sys.stderr:
            stderr_buf.write(line)
        else:
            target.write(line)  # type: ignore[attr-defined]

    debugger_patch = (
        contextlib.nullcontext() if real_debugger else patch("host.debugger.prompt.Debugger")
    )
    with patch.object(sys, "argv", [".", "--machine", machine, *argv]), \
         patch("builtins.print", side_effect=fake_print), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", lambda self: image), \
         patch("frontend.sdl2_frontend.run"), \
         debugger_patch:
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


class TestCountFrameCLI:
    def test_runs_exactly_n_frames(self) -> None:
        with patch.object(Machine, "step_frame", return_value=_FRAME_DONE) as rf:
            code, out, err = _run_main(["--count-frame", "5"])
        assert code == 0
        assert rf.call_count == 5
        # Headless: no video is needed, and rendering never changes state.
        assert all(call.kwargs["render_video"] is False for call in rf.call_args_list)
        assert "frames" in out

    def test_no_count_frame_takes_sdl_path(self) -> None:
        # Without --count-frame the headless loop is skipped (SDL run is mocked
        # inside _run_main), so main() never drives step_frame itself.
        with patch.object(Machine, "step_frame", return_value=_FRAME_DONE) as rf:
            code, _out, _err = _run_main([])
        assert code == 0
        rf.assert_not_called()


class TestCountFrameResume:
    def test_resume_loads_state_before_the_headless_loop(self) -> None:
        calls: list[tuple[str, object]] = []

        def fake_load_state(machine: Machine, path: Path | None = None) -> None:
            calls.append(("load_state", path))

        def fake_step_frame(*_args: object, **_kwargs: object) -> FrameOutput:
            calls.append(("step_frame", None))
            return _FRAME_DONE

        with patch(
            "host.state_files.load_state", side_effect=fake_load_state
        ) as load_state_mock, \
             patch.object(Machine, "step_frame", side_effect=fake_step_frame):
            code, _out, _err = _run_main(["--count-frame", "3", "--resume"])
        assert code == 0
        assert load_state_mock.call_count == 1
        assert load_state_mock.call_args.kwargs["path"] is None
        # The state must be restored before any frame of the headless run.
        assert calls[0] == ("load_state", None)
        assert [c for c in calls if c[0] == "step_frame"] == [("step_frame", None)] * 3

    def test_resume_with_explicit_path_loads_that_state(self) -> None:
        with patch("host.state_files.load_state") as load_state_mock, \
             patch.object(Machine, "step_frame", return_value=_FRAME_DONE) as rf:
            code, _out, _err = _run_main(
                ["--count-frame", "3", "--resume", "path/to/scene.state"])
        assert code == 0
        load_state_mock.assert_called_once()
        assert load_state_mock.call_args.kwargs["path"] == Path("path/to/scene.state")
        assert rf.call_count == 3

    def test_no_resume_flag_does_not_load_state(self) -> None:
        with patch("host.state_files.load_state") as load_state_mock, \
             patch.object(Machine, "step_frame", return_value=_FRAME_DONE):
            code, _out, _err = _run_main(["--count-frame", "3"])
        assert code == 0
        load_state_mock.assert_not_called()


class TestMapperTraceCLI:
    def test_inert_without_bank_switching_mapper(self) -> None:
        # Zero ROM cartridge -> flat mapper; flag is accepted but inert.
        with patch.object(Machine, "step_frame", return_value=_FRAME_DONE):
            code, _out, err = _run_main(["--mapper-trace", "--count-frame", "1"])
        assert code == 0
        assert "no bank-switching ROM mapper" in err


# ---------------------------------------------------------------------------
# --vdp-trace / --mapper-trace output through the core's trace buffer
# ---------------------------------------------------------------------------

def _trace_image() -> bytes:
    """A 32 KB image whose code at 0x0000 selects slot 1 in pages 1-2,
    switches ASCII16 window 1 to bank 1, writes VDP R#1 = 0x40 and spins."""
    code = bytes((
        0x3E, 0x14, 0xD3, 0xA8,  # LD A,14h / OUT (A8h),A
        0x3E, 0x01, 0x32, 0x00, 0x70,  # LD A,01h / LD (7000h),A
        0x3E, 0x40, 0xD3, 0x99,  # LD A,40h / OUT (99h),A
        0x3E, 0x81, 0xD3, 0x99,  # LD A,81h / OUT (99h),A
        0x18, 0xFE,  # JR $
    ))
    return code + bytes(32768 - len(code))


class TestVdpTraceCLI:
    def test_vdp_trace_without_out_writes_to_stdout(self) -> None:
        code, out, _err = _run_main(
            ["--vdp-trace", "--count-frame", "1"], image=_trace_image(), real_debugger=True)
        assert code == 0
        assert "VDP_REG R#01=40h" in out

    def test_vdp_trace_out_redirects_trace_to_file(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.log"
        code, out, _err = _run_main(
            ["--vdp-trace", "--vdp-trace-out", str(log), "--count-frame", "1"],
            image=_trace_image(), real_debugger=True)
        assert code == 0
        assert "VDP_REG R#01=40h" in log.read_text(encoding="utf-8")
        assert "VDP_REG" not in out

    def test_vdp_trace_traces_an_msx1_machine(self) -> None:
        code, out, _err = _run_main(
            ["--vdp-trace", "--count-frame", "1"], machine="cbios_msx1",
            image=_trace_image(), real_debugger=True)
        assert code == 0
        assert "VDP_REG R#01=40h" in out


class TestMapperTraceOutputCLI:
    def test_mapper_trace_emits_map_bank_headlessly(self) -> None:
        code, out, _err = _run_main(
            ["game.rom", "--mapper", "ASCII16", "--mapper-trace", "--count-frame", "1"],
            image=_trace_image(), real_debugger=True)
        assert code == 0
        assert "MAP_BANK win=1 00h->01h addr=7000h" in out

    def test_mapper_trace_out_writes_to_a_file(self, tmp_path: Path) -> None:
        log = tmp_path / "trace.log"
        code, out, _err = _run_main(
            ["game.rom", "--mapper", "ASCII16", "--mapper-trace",
             "--mapper-trace-out", str(log), "--count-frame", "1"],
            image=_trace_image(), real_debugger=True)
        assert code == 0
        assert "MAP_BANK win=1" in log.read_text(encoding="utf-8")
        assert "MAP_BANK" not in out
