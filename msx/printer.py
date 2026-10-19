"""MSX parallel printer port (I/O 0x90-0x91) and the raw-byte logger sink.

Port 0x91 latches the data byte; bit 0 of a 0x90 write drives STROBE and a
1 -> 0 transition hands the latched byte to the attached device (falling edge,
as openMSX's PrinterPortLogger records it). Reading 0x90 returns 0xFF with
bit 1 = BUSY; with no device attached the connector reads BUSY, like an empty
printer port (openMSX DummyPrinterPortDevice).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO, Protocol

_STATUS_PORT = 0x90
_BUSY_BIT = 0x02


class PrinterDevice(Protocol):
    """Something plugged into the printer port."""

    def is_busy(self) -> bool: ...

    def write_byte(self, value: int) -> None: ...

    def close(self) -> None: ...


@dataclass
class RawPrinterLogger:
    """Records every strobed byte verbatim; always ready."""

    output: BinaryIO

    def is_busy(self) -> bool:
        return False

    def write_byte(self, value: int) -> None:
        # Flushed per byte so the file is complete even on a crash or kill;
        # printing volume is a few KB at most.
        self.output.write(bytes((value & 0xFF,)))
        self.output.flush()

    def close(self) -> None:
        self.output.close()


@dataclass
class PrinterPort:
    """Printer port on 0x90 (STROBE / status) and 0x91 (data latch)."""

    device: PrinterDevice | None = None
    _data: int = field(default=0, init=False, repr=False)
    _strobe: int = field(default=1, init=False, repr=False)

    def reset(self) -> None:
        self._data = 0
        self._strobe = 1

    def read_port(self, port: int) -> int:
        if port != _STATUS_PORT:
            return 0xFF
        if self.device is None or self.device.is_busy():
            return 0xFF
        return 0xFF & ~_BUSY_BIT

    def write_port(self, port: int, value: int) -> None:
        if port == _STATUS_PORT:
            strobe = value & 0x01
            if self._strobe == 1 and strobe == 0 and self.device is not None:
                self.device.write_byte(self._data)
            self._strobe = strobe
        else:
            self._data = value & 0xFF

    def close(self) -> None:
        if self.device is not None:
            self.device.close()
