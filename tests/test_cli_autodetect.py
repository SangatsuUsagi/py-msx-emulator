"""CLI integration tests — patches filesystem and romdb, never touches SDL2."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(argv: list[str], bios_exists: bool = True,
              cart_exists: bool = True, biosrom_exists: bool = True) -> tuple[int, str, str]:
    """
    Run __main__.main() with patched argv, filesystem, romdb, and machine factory.
    Returns (exit_code, stdout, stderr).
    """
    import io

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_exists(self: Path) -> bool:
        name = self.name
        if name == "cbios_main_msx1.rom":
            return bios_exists
        if name.endswith(".rom") and "cart" in name:
            return cart_exists
        if name == "my_bios.rom":
            return biosrom_exists
        return True  # slot2, logo ROMs, etc.

    def fake_read_bytes(self: Path) -> bytes:
        return b"\x00" * 32768

    fake_machine = MagicMock()
    fake_machine.vdp.display_height = 192

    with patch.object(sys, "argv", [".", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", fake_exists), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch("msx.machine.make_machine", return_value=fake_machine), \
         patch("frontend.sdl2_frontend.run"):
        try:
            import importlib
            import __main__ as m
            importlib.reload(m)
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# Core CLI behaviour
# ---------------------------------------------------------------------------

def test_biosrom_overrides_auto_bios(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: "Mirrored")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom", "--biosrom", "my_bios.rom"])
    assert code == 0
    assert "my_bios.rom" in out


def test_missing_auto_bios_exits_with_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, _out, err = _run_main(["cart.rom"], bios_exists=False)
    assert code != 0
    assert "error" in err.lower()
    assert "cbios_main_msx1.rom" in err


def test_unknown_cart_defaults_to_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom"])
    assert code == 0
    assert "MSX1" in out
    assert "cbios_main_msx1.rom" in out


def test_no_cartridge_defaults_to_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main([])
    assert code == 0
    assert "MSX1" in out


# ---------------------------------------------------------------------------
# Startup summary format
# ---------------------------------------------------------------------------

def test_summary_has_no_ext_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: "Mirrored")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom"])
    assert code == 0
    assert "ext" not in out
    assert "machine" in out
    assert "bios" in out
    assert "mapper" in out


def test_no_cartridge_shows_mapper_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main([])
    assert code == 0
    assert "mapper  : auto" in out
