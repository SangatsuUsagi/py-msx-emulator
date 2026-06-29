"""CLI auto-detect integration tests — patches filesystem and romdb, never touches SDL2."""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(argv: list[str], bios_exists: bool = True, ext_exists: bool = True,
              cart_exists: bool = True, biosrom_exists: bool = True) -> tuple[int, str, str]:
    """Run __main__.main() with patched argv, filesystem, and romdb.

    Loads the emulator's __main__.py via importlib.util to avoid conflicts with
    pytest's own __main__ module in sys.modules.
    Returns (exit_code, stdout, stderr).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_exists(self: Path) -> bool:
        name = self.name
        if name in ("cbios_main_msx1.rom", "cbios_main_msx2.rom"):
            return bios_exists
        if name == "cbios_sub.rom":
            return ext_exists
        if name.endswith(".rom") and "cart" in name:
            return cart_exists
        if name == "my_bios.rom":
            return biosrom_exists
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


# ---------------------------------------------------------------------------
# Core CLI behaviour
# ---------------------------------------------------------------------------

def test_msx2_auto_detect_selects_msx2_bios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msx.romdb._db", None)
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX2")
    monkeypatch.setattr(romdb, "lookup", lambda _c: "KonamiSCC")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: "TestGame")

    code, out, _err = _run_main(["cart.rom"])
    assert code == 0
    assert "MSX2" in out
    assert "cbios_main_msx2.rom" in out
    assert "cbios_sub.rom" in out


def test_msx2_flag_overrides_msx1_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX")
    monkeypatch.setattr(romdb, "lookup", lambda _c: "Mirrored")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom", "--msx2"])
    assert code == 0
    assert "MSX2" in out


def test_extrom_warning_for_msx1_cart(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX")
    monkeypatch.setattr(romdb, "lookup", lambda _c: "Mirrored")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, _out, err = _run_main(["cart.rom", "--extrom", "roms/cbios_sub.rom"])
    assert code == 0
    assert "warning" in err.lower()
    assert "MSX1" in err


def test_biosrom_overrides_auto_bios(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX")
    monkeypatch.setattr(romdb, "lookup", lambda _c: "Mirrored")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom", "--biosrom", "my_bios.rom"])
    assert code == 0
    assert "my_bios.rom" in out


def test_missing_auto_bios_exits_with_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, _out, err = _run_main(["cart.rom"], bios_exists=False)
    assert code != 0
    assert "error" in err.lower()
    assert "cbios_main_msx1.rom" in err


def test_unknown_cart_defaults_to_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom"])
    assert code == 0
    assert "MSX1" in out
    assert "cbios_main_msx1.rom" in out


def test_no_cartridge_defaults_to_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main([])
    assert code == 0
    assert "MSX1" in out


# ---------------------------------------------------------------------------
# Startup summary format
# ---------------------------------------------------------------------------

def test_msx2_summary_includes_ext_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX2")
    monkeypatch.setattr(romdb, "lookup", lambda _c: "KonamiSCC")
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main(["cart.rom"])
    assert code == 0
    lines = {ln.split(":")[0].strip(): ln for ln in out.splitlines() if ":" in ln}
    assert "machine" in lines
    assert "bios" in lines
    assert "ext" in lines
    assert "mapper" in lines


def test_msx1_summary_omits_ext_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: "MSX")
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
    monkeypatch.setattr(romdb, "lookup_system", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup", lambda _c: None)
    monkeypatch.setattr(romdb, "lookup_title", lambda _c: None)

    code, out, _err = _run_main([])
    assert code == 0
    assert "mapper  : auto" in out
