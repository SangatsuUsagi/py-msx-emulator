"""CLI --disc1 tests — patches filesystem and SDL2, never opens a real window."""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str], *, disc_exists: bool = True) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_exists(self: Path) -> bool:
        if self.name.endswith(".dsk"):
            return disc_exists
        return True

    def fake_read_bytes(self: Path) -> bytes:
        return b"\x00" * 32768

    with patch.object(sys, "argv", [".", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", fake_exists), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch("frontend.sdl2_frontend.run"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


def test_disc1_missing_file_exits_nonzero() -> None:
    code, _out, err = _run_main(["--machine", "hb_f1xd", "--disc1", "nope.dsk"],
                                disc_exists=False)
    assert code != 0
    assert "disk image not found" in err.lower()


def test_disc1_mounted_on_hb_f1xd(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["--machine", "hb_f1xd", "--disc1", "game.dsk"])
    assert code == 0
    assert "disc1" in out
    assert "game.dsk" in out


def test_disc1_without_floppy_machine_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    # Default machine (cbios_msx2_jp) has no floppy interface.
    code, _out, err = _run_main(["--machine", "cbios_msx2_jp", "--disc1", "game.dsk"])
    assert code == 0
    assert "no floppy interface" in err.lower()
