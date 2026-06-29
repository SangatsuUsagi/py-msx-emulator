"""CLI --break-point integration tests."""
from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    """Run __main__.main() in MSX2 mode with patched filesystem. Returns (exit_code, stdout, stderr)."""
    stdout_buf = StringIO()
    stderr_buf = StringIO()

    def fake_exists(self: Path) -> bool:
        return True

    def fake_read_bytes(self: Path) -> bytes:
        return b"\x00" * 32768

    with patch.object(sys, "argv", [".", "--msx2", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", fake_exists), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch("frontend.sdl2_frontend.run"), \
         patch("msx.debugger.prompt.Debugger"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


class TestBreakPointCLI:
    def test_single_breakpoint(self) -> None:
        code, out, err = _run_main(["--break-point", "C000"])
        assert code == 0
        assert "C000h" in out

    def test_multiple_breakpoints(self) -> None:
        code, out, err = _run_main(["--break-point", "0000,C000,D000"])
        assert code == 0
        assert "0000h" in out
        assert "C000h" in out
        assert "D000h" in out

    def test_no_breakpoint_arg(self) -> None:
        code, out, err = _run_main([])
        assert code == 0
        assert "break" not in out.lower()

    def test_invalid_hex_address_exits_with_error(self) -> None:
        code, out, err = _run_main(["--break-point", "ZZZZ"])
        assert code != 0
        assert "invalid" in err.lower()

    def test_more_than_4_warns_and_truncates(self) -> None:
        code, out, err = _run_main(["--break-point", "1000,2000,3000,4000,5000"])
        assert code == 0
        assert "warning" in err.lower()
        assert out.count("h") >= 4

    def test_mixed_valid_invalid_exits(self) -> None:
        code, _out, err = _run_main(["--break-point", "C000,ZZZZ"])
        assert code != 0
        assert "invalid" in err.lower()
