"""Phase 8: --mapper-trace / --mapper-trace-out and --count-frame.

Unit tests for the attach_to_machine helper plus CLI integration tests that
exercise the argparse wiring through __main__.main().
"""
from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from msx.machine import Machine
from msx.mapper_tracer import attach_to_machine
from tests.factories import make_machine

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _rom16(pages: int) -> bytes:
    """ROM where byte 0 of each 16 KB page holds the page index."""
    return bytes([(p if i == 0 else 0) for p in range(pages) for i in range(16384)])


# ---------------------------------------------------------------------------
# attach_to_machine helper (shared by `ce` and --mapper-trace)
# ---------------------------------------------------------------------------

class TestAttachToMachine:
    def test_attaches_and_emits_on_bank_change(self) -> None:
        m = make_machine(rom=bytes(32768), cartridge=_rom16(8), mapper="ASCII16")
        buf = StringIO()
        tracer = attach_to_machine(m, output=buf)
        assert tracer is not None
        m.memory._mapper.write(0x7000, 1)  # window 1: bank 0 -> 1
        assert "MAP_BANK win=1 00h->01h addr=7000h" in buf.getvalue()

    def test_returns_none_without_bank_switching_mapper(self) -> None:
        # No cartridge -> flat mapper in slot 1, no _tracer hook.
        m = make_machine(rom=bytes(32768))
        assert attach_to_machine(m, output=StringIO()) is None

    def test_output_target_is_honored(self) -> None:
        m = make_machine(rom=bytes(32768), cartridge=_rom16(8), mapper="ASCII16")
        buf = StringIO()
        attach_to_machine(m, output=buf)
        m.memory._mapper.write(0x6000, 2)
        assert buf.getvalue().startswith("CY=")


# ---------------------------------------------------------------------------
# CLI integration (mirror of tests/test_cli_breakpoint.py harness)
# ---------------------------------------------------------------------------

def _run_main(argv: list[str]) -> tuple[int, str, str]:
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
         patch("frontend.sdl2_frontend.run"), \
         patch("msx.debugger.prompt.Debugger"):
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
        with patch.object(Machine, "run_frame", return_value=None) as rf:
            code, out, err = _run_main(["--count-frame", "5"])
        assert code == 0
        assert rf.call_count == 5
        assert "frames" in out

    def test_no_count_frame_takes_sdl_path(self) -> None:
        # Without --count-frame the headless loop is skipped (SDL run is mocked
        # inside _run_main), so main() never drives run_frame itself.
        with patch.object(Machine, "run_frame", return_value=None) as rf:
            code, _out, _err = _run_main([])
        assert code == 0
        rf.assert_not_called()


class TestMapperTraceCLI:
    def test_inert_without_bank_switching_mapper(self) -> None:
        # Zero ROM cartridge -> flat mapper; flag is accepted but inert.
        with patch.object(Machine, "run_frame", return_value=None):
            code, _out, err = _run_main(["--mapper-trace", "--count-frame", "1"])
        assert code == 0
        assert "no bank-switching ROM mapper" in err
