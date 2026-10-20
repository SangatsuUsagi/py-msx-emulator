"""Tests for the MSX printer port and raw logger (spec io-printer)."""
from __future__ import annotations

import io

from hypothesis import given
from hypothesis import strategies as st

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


# ---------------------------------------------------------------------------
# allium/printer-port.allium obligations not covered by the scenarios above
# ---------------------------------------------------------------------------

class _BusyRecorder(_Recorder):
    def is_busy(self) -> bool:
        return True


def test_port_constants() -> None:
    # config: status_port 0x90, ready_status 0xFD (= 0xFF with BUSY bit 1 clear)
    from msx.printer import _BUSY_BIT, _STATUS_PORT
    assert _STATUS_PORT == 0x90
    assert 0xFF & ~_BUSY_BIT == 0xFD


def test_busy_device_reads_busy() -> None:
    # ReadStatus: an attached device that reports busy reads 0xFF.
    assert PrinterPort(device=_BusyRecorder()).read_port(0x90) == 0xFF


def test_status_write_bits_other_than_strobe_are_ignored() -> None:
    # StrobeFallingEdge: only bit 0 of a 0x90 write is STROBE.
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0xFE)  # bit 0 clear, every other bit set
    assert rec.data == [0x41]


def test_status_write_does_not_touch_the_latch() -> None:
    # LatchData failure case: a 0x90 write is not a data write.
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x55)  # STROBE stays 1
    port.write_port(0x90, 0x00)
    assert rec.data == [0x41]


def test_data_latch_is_eight_bits() -> None:
    # invariant PortFieldsInRange: the latch holds 0-255.
    rec = _Recorder()
    port = PrinterPort(device=rec)
    _strobe(port, 0x1FF)
    assert rec.data == [0xFF]


def test_reset_clears_the_data_latch() -> None:
    # Reset: data latch 0, STROBE 1.
    rec = _Recorder()
    port = PrinterPort(device=rec)
    port.write_port(0x91, 0x41)
    port.reset()
    port.write_port(0x90, 0x00)
    assert rec.data == [0x00]


def test_devices_honour_the_printer_device_contract(tmp_path) -> None:
    # contract PrinterDevice: is_busy() -> False for both devices, write_byte
    # and close return nothing.
    from msx.printer_hbpf1 import HbpF1Printer, PrinterFonts

    logger = RawPrinterLogger(output=open(tmp_path / "out.prn", "wb"))
    hbp = HbpF1Printer(PrinterFonts(ank=bytes(2048)), lambda page: None,
                       open(tmp_path / "unsupported.log", "w"))
    for device in (logger, hbp):
        assert device.is_busy() is False
        assert device.write_byte(0x41) is None
        assert device.close() is None


@given(st.lists(st.tuples(st.sampled_from((0x90, 0x91)), st.integers(0, 0x1FF)),
                max_size=60))
def test_strobe_model_property(writes: list[tuple[int, int]]) -> None:
    # Property over StrobeFallingEdge / StrobeWithoutFallingEdge / LatchData:
    # exactly one delivery per 1 -> 0 STROBE transition, carrying the latch.
    rec = _Recorder()
    port = PrinterPort(device=rec)
    expected = []
    latch, strobe = 0, 1
    for address, value in writes:
        port.write_port(address, value)
        if address == 0x91:
            latch = value & 0xFF
        else:
            if strobe == 1 and value & 1 == 0:
                expected.append(latch)
            strobe = value & 1
    assert rec.data == expected
