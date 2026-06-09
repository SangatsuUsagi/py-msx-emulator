"""Tests for msx.rtc.RTC stub."""
from msx.rtc import RTC


def test_read_port_b5_returns_zero() -> None:
    rtc = RTC()
    assert rtc.read_port(0xB5) == 0x00


def test_read_port_b4_returns_zero() -> None:
    rtc = RTC()
    assert rtc.read_port(0xB4) == 0x00


def test_write_b4_does_not_raise() -> None:
    rtc = RTC()
    rtc.write_port(0xB4, 0x0D)  # mode register select


def test_write_b5_does_not_raise() -> None:
    rtc = RTC()
    rtc.write_port(0xB5, 0x08)


def test_read_after_write_still_returns_zero() -> None:
    rtc = RTC()
    rtc.write_port(0xB4, 0x0D)
    rtc.write_port(0xB5, 0x08)
    assert rtc.read_port(0xB5) == 0x00


def test_multiple_register_selects_all_return_zero() -> None:
    rtc = RTC()
    for reg in range(0x10):
        rtc.write_port(0xB4, reg)
        assert rtc.read_port(0xB5) == 0x00
