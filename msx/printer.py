"""MSX parallel printer port (I/O 0x90-0x91, or 0x90-0x97 on an S-1985).

A write to a data port latches a byte. Bit 0 of a write to a status port drives
STROBE, and its falling edge delivers the latched byte. A read of a status port
gives the form's unused bits with bit 1 as BUSY. A read of any other port gives
0xFF. On an S-1985, port & 3 selects: 0 is status, 1 is data, and 2 and 3 ignore
writes. The decode and the unused bits are facts from openMSX's MSXPrinterPort
and its machine files.

Delivered bytes wait in a fixed buffer until the host drains them: the core
writes no file and calls no host code. BUSY depends on the attachment only, so
what the guest reads does not depend on when the host drains. A byte strobed
into a full buffer is dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from msx.boundary import PRINTER_BUFFER_SIZE
from msx.machine_spec import PrinterPortForm

_BUSY_BIT = 0x02


class PrinterState(TypedDict):
    data: int
    strobe: int


@dataclass
class PrinterPort:
    """The port lines and the buffer. Not built for PrinterPortForm.NONE."""

    form: PrinterPortForm
    _attached: bool = field(default=False, init=False, repr=False)
    _data: int = field(default=0, init=False, repr=False)
    _strobe: int = field(default=1, init=False, repr=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)

    def first_port(self) -> int:
        return 0x90

    def last_port(self) -> int:
        return 0x97 if self.form is PrinterPortForm.S1985 else 0x91

    def _mask(self) -> int:
        return 0x03 if self.form is PrinterPortForm.S1985 else 0x01

    def reset(self) -> None:
        """The lines return to power-on. The attachment and the buffer stay."""
        self._data = 0
        self._strobe = 1

    def attach(self) -> None:
        self._attached = True

    def drain(self) -> bytes:
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    def read_port(self, port: int) -> int:
        if port & self._mask() != 0:
            return 0xFF
        unused = 0x00 if self.form is PrinterPortForm.S1985 else 0xFF
        if self._busy():
            return unused | _BUSY_BIT
        return unused & ~_BUSY_BIT & 0xFF

    def write_port(self, port: int, value: int) -> None:
        select = port & self._mask()
        if select == 0:
            self._write_strobe(value & 0x01)
        elif select == 1:
            self._data = value & 0xFF

    def _write_strobe(self, strobe: int) -> None:
        falling = self._strobe == 1 and strobe == 0
        self._strobe = strobe
        if falling and self._attached and len(self._buffer) < PRINTER_BUFFER_SIZE:
            self._buffer.append(self._data)

    def _busy(self) -> bool:
        return not self._attached

    def snapshot_state(self) -> PrinterState:
        return {"data": self._data, "strobe": self._strobe}

    def apply_state(self, state: PrinterState) -> None:
        self._data = state["data"]
        self._strobe = state["strobe"]
