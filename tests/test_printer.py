"""Tests for the MSX printer port and raw logger (spec io-printer)."""
from __future__ import annotations

import io

from msx.printer import PrinterPort, RawPrinterLogger


class _Recorder:
    def __init__(self) -> None:
        self.data: list[int] = []

    def is_busy(self) -> bool:
        return False

    def write_byte(self, value: int) -> None:
        self.data.append(value)

    def close(self) -> None:
        pass


def _strobe(port: PrinterPort, value: int) -> None:
    port.write_port(0x91, value)
    port.write_port(0x90, 0x00)
    port.write_port(0x90, 0x01)


def test_data_write_is_latched_not_emitted() -> None:
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    assert rec.data == []


def test_data_port_read() -> None:
    assert PrinterPort(device=_Recorder()).read_port(0x91) == 0xFF


def test_one_byte_per_strobe_pulse() -> None:
    rec = _Recorder()
    port = PrinterPort(device=rec)
    _strobe(port, 0x41)
    assert rec.data == [0x41]


def test_repeated_low_writes_do_not_duplicate() -> None:
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x00)
    port.write_port(0x90, 0x00)
    assert rec.data == [0x41]


def test_rising_edge_delivers_nothing() -> None:
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x01)
    assert rec.data == []


def test_data_changed_between_pulses() -> None:
    rec = _Recorder()
    port = PrinterPort(device=rec)
    _strobe(port, 0x1B)
    _strobe(port, 0x4B)
    assert rec.data == [0x1B, 0x4B]


def test_no_printer_attached_reads_busy() -> None:
    port = PrinterPort()
    assert port.read_port(0x90) == 0xFF
    _strobe(port, 0x41)  # delivered nowhere, must not raise


def test_logger_attached_reads_ready() -> None:
    port = PrinterPort(device=RawPrinterLogger(output=io.BytesIO()))
    assert port.read_port(0x90) == 0xFD


def test_bytes_recorded_verbatim(tmp_path) -> None:
    path = tmp_path / "out.prn"
    port = PrinterPort(device=RawPrinterLogger(output=open(path, "wb")))
    for value in (0x1B, 0x4B, 0x24, 0x22, 0x0D, 0x0A):
        _strobe(port, value)
    port.close()
    assert path.read_bytes() == bytes((0x1B, 0x4B, 0x24, 0x22, 0x0D, 0x0A))


def test_port_reset_keeps_the_file(tmp_path) -> None:
    path = tmp_path / "out.prn"
    port = PrinterPort(device=RawPrinterLogger(output=open(path, "wb")))
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x00)  # STROBE left low across the reset
    port.reset()
    assert path.read_bytes() == b"A"
    _strobe(port, 0x42)  # reset restored STROBE=1, so this falling edge counts
    port.close()
    assert path.read_bytes() == b"AB"
