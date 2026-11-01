"""--printer-log: the bytes the guest prints, written to a file unchanged.

The core keeps the strobed bytes in a buffer (CoreApi.drain_printer). The run
loops and the debugger's step call drain; close takes the last bytes.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msx.core_api import CoreApi


class PrinterOutput:
    """Writes a core's printer bytes to a file, created or truncated here."""

    def __init__(self, core: CoreApi, path: Path) -> None:
        self._core = core
        self._file = open(path, "wb")

    def drain(self) -> None:
        data = self._core.drain_printer()
        if data:
            self._file.write(data)
            self._file.flush()

    def close(self) -> None:
        self.drain()
        self._file.close()
