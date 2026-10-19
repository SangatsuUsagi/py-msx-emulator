"""--printer-log / --printer-out CLI options and printer-port wiring (app-cli, io-printer)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.machine import Machine
from msx.machine_loader import build_machine
from tests.factories import _msx1_spec

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _strobe(machine: Machine, value: int) -> None:
    machine.io.write_port(0x91, value)
    machine.io.write_port(0x90, 0x00)
    machine.io.write_port(0x90, 0x01)


def test_option_attaches_the_logger(tmp_path: Path) -> None:
    out = tmp_path / "out.prn"
    m = build_machine(_msx1_spec(), bios_override=bytes(32768), printer_log=out)
    assert m.io.read_port(0x90) == 0xFD
    _strobe(m, 0x41)
    _strobe(m, 0x42)
    m.printer.close()
    assert out.read_bytes() == b"AB"


def test_option_omitted(tmp_path: Path) -> None:
    m = build_machine(_msx1_spec(), bios_override=bytes(32768))
    assert m.io.read_port(0x90) == 0xFF
    _strobe(m, 0x41)
    assert list(tmp_path.iterdir()) == []


def _run_main(argv: list[str]) -> list[tuple[object, object]]:
    seen: list[tuple[object, object]] = []

    def fake_build_machine(*args: object, **kwargs: object) -> Machine:
        printer_log = kwargs.pop("printer_log", None)
        printer_out = kwargs.pop("printer_out", None)
        seen.append((printer_log, printer_out))
        return build_machine(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(sys, "argv", [".", "--machine", "cbios_msx2", *argv]), \
         patch("builtins.print"), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", lambda self: b"\x00" * 32768), \
         patch("msx.machine_loader.build_machine", side_effect=fake_build_machine), \
         patch.object(Machine, "run_frame", return_value=None), \
         patch("msx.debugger.prompt.Debugger"):
        spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    return seen


def test_cli_passes_printer_log_path() -> None:
    assert _run_main(["--count-frame", "1", "--printer-log", "out.prn"]) == [
        (Path("out.prn"), None)]


def test_cli_without_printer_log_passes_none() -> None:
    assert _run_main(["--count-frame", "1"]) == [(None, None)]


def test_option_attaches_the_hbp_f1_printer(tmp_path: Path) -> None:
    out = tmp_path / "pages"
    m = build_machine(_msx1_spec(), bios_override=bytes(32768), printer_out=out)
    assert m.io.read_port(0x90) == 0xFD
    assert (out / "unsupported.log").exists()
    m.printer.close()


def test_build_machine_rejects_both_printer_options(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_machine(_msx1_spec(), bios_override=bytes(32768),
                      printer_log=tmp_path / "out.prn", printer_out=tmp_path / "pages")


def test_cli_passes_printer_out_path() -> None:
    assert _run_main(["--count-frame", "1", "--printer-out", "pages"]) == [
        (None, Path("pages"))]


def test_options_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        _run_main(["--count-frame", "1", "--printer-out", "pages",
                   "--printer-log", "out.prn"])
    assert exc.value.code == 2

