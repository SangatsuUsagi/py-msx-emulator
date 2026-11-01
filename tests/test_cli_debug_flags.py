"""--debug and --log through __main__.main() (diag-debug-logger "DebugLogger
is opt-in and zero-cost when absent", "Log output goes to stderr and
optionally to a file")."""
from __future__ import annotations

from pathlib import Path

from tests.test_cli_trace_flags import _run_main

# JR $ at 0x0000 in every ROM image: a PC-loop hang in the first frames.
_HANG_IMAGE = bytes((0x18, 0xFE)) + bytes(32768 - 2)


def test_no_logger_constructed_without_debug(capsys) -> None:
    code, _out, _err = _run_main(["--count-frame", "2"], image=_HANG_IMAGE, real_debugger=True)
    assert code == 0
    assert "[HANG]" not in capsys.readouterr().err


def test_logger_constructed_with_debug(capsys) -> None:
    code, _out, _err = _run_main(
        ["--debug", "--count-frame", "2"], image=_HANG_IMAGE, real_debugger=True)
    assert code == 0
    assert "[HANG]  PC-loop detected  PC=0000" in capsys.readouterr().err


def test_stderr_output_without_log(capsys, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, _out, _err = _run_main(
        ["--debug", "--count-frame", "2"], image=_HANG_IMAGE, real_debugger=True)
    assert code == 0
    assert "[CPU]  trace  PC=0000  op=18" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_file_output_with_log(capsys, tmp_path: Path) -> None:
    log = tmp_path / "debug.txt"
    code, _out, _err = _run_main(
        ["--debug", "--log", str(log), "--count-frame", "2"],
        image=_HANG_IMAGE, real_debugger=True)
    assert code == 0
    err = capsys.readouterr().err
    text = log.read_text(encoding="utf-8")
    assert "[HANG]  PC-loop detected  PC=0000" in err
    assert "[HANG]  PC-loop detected  PC=0000" in text
    hang_lines = [ln for ln in err.splitlines() if ln.startswith(("[CPU]", "[HANG]"))]
    assert text.splitlines() == hang_lines
