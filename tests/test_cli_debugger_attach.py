"""The interactive debugger is attached for all machines (Ctrl-C drop-in).

Regression test for the MSX1 case: previously the Debugger was only attached
when spec.generation == "msx2", so Ctrl-C could not enter the REPL on MSX1.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.machine import Machine

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main_capture_debugger(machine_id: str):
    """Run main() headlessly (1 frame) for machine_id; return the patched Debugger mock."""
    with patch.object(sys, "argv", [".", "--machine", machine_id, "--count-frame", "1"]), \
         patch("builtins.print"), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", lambda self: b"\x00" * 32768), \
         patch.object(Machine, "run_frame", return_value=None), \
         patch("frontend.sdl2_frontend.run"), \
         patch("msx.debugger.prompt.Debugger") as dbg:
        spec = importlib.util.spec_from_file_location("_emulator_main", _MAIN_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        try:
            mod.main()
        except SystemExit:
            pass
        return dbg


@pytest.mark.parametrize("machine_id", ["cbios_msx1", "cbios_msx2"])
def test_debugger_attached_for_all_machines(machine_id: str) -> None:
    dbg = _run_main_capture_debugger(machine_id)
    assert dbg.called, f"Debugger was not attached for {machine_id}"
