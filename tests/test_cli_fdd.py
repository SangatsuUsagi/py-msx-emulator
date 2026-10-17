"""CLI --fdd1/--fdd2 tests — patches filesystem and SDL2, never opens a window."""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(
    argv: list[str], *, fdd_exists: bool = True, missing: str | None = None,
    run_mock: MagicMock | None = None,
) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_exists(self: Path) -> bool:
        if self.name == missing:
            return False
        if self.name.endswith(".dsk"):
            return fdd_exists
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
         patch("frontend.sdl2_frontend.run", run_mock or MagicMock()):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


def test_fdd1_missing_file_exits_nonzero() -> None:
    code, _out, err = _run_main(["--machine", "hb_f1xd", "--fdd1", "nope.dsk"],
                                fdd_exists=False)
    assert code != 0
    assert "disk image not found" in err.lower()


def test_fdd1_mounted_on_hb_f1xd(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["--machine", "hb_f1xd", "--fdd1", "game.dsk"])
    assert code == 0
    assert "fdd1" in out
    assert "game.dsk" in out


def test_fdd1_without_floppy_machine_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    # Default machine (cbios_msx2_jp) has no floppy interface.
    code, _out, err = _run_main(["--machine", "cbios_msx2_jp", "--fdd1", "game.dsk"])
    assert code == 0
    assert "no floppy interface" in err.lower()


def test_fdd2_on_single_drive_machine_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    # HB-F1XD declares a single drive, so drive B has nowhere to mount.
    code, _out, err = _run_main(["--machine", "hb_f1xd", "--fdd2", "b.dsk"])
    assert code == 0
    assert "only 1 drive" in err.lower()


def test_fdd1_list_missing_entry_exits_nonzero() -> None:
    code, _out, err = _run_main(
        ["--machine", "hb_f1xd", "--fdd1", "a.dsk", "missing.dsk"], missing="missing.dsk")
    assert code != 0
    assert "missing.dsk" in err


def test_fdd1_list_passed_to_the_frontend(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    run_mock = MagicMock()
    code, out, _err = _run_main(
        ["--machine", "hb_f1xd", "--fdd1", "a.dsk", "b.dsk", "c.dsk"], run_mock=run_mock)
    assert code == 0
    assert "a.dsk, b.dsk, c.dsk" in out
    assert run_mock.call_args.kwargs["fdd1_images"] == [
        Path("a.dsk"), Path("b.dsk"), Path("c.dsk"),
    ]


def test_fdd1_without_floppy_machine_hands_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    run_mock = MagicMock()
    code, _out, _err = _run_main(
        ["--machine", "cbios_msx2_jp", "--fdd1", "a.dsk", "b.dsk"], run_mock=run_mock)
    assert code == 0
    assert run_mock.call_args.kwargs["fdd1_images"] == []


def test_fdd1_help_text_names_the_cartridge_order() -> None:
    spec = importlib.util.spec_from_file_location("_emulator_main_help", _MAIN_PATH)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    help_text = " ".join(m._build_arg_parser().format_help().split())
    assert "cartridge before --fdd1" in help_text
    assert "--slot1" in help_text
