"""CLI --fmpac tests — patches filesystem and SDL2, never opens a window."""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_read_bytes(self: Path) -> bytes:
        return b"\x00" * 32768

    with patch.object(sys, "argv", [".", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch("frontend.sdl2_frontend.run"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main_fmpac", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


def test_fmpac_and_slot2_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--fmpac", "--slot2", "game2.rom"])
    assert code != 0
    assert "--fmpac" in err and "--slot2" in err
