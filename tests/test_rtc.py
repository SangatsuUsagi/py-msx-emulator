"""Tests for the RP-5C01 RTC (register banks + CMOS RAM + running clock)."""
from msx.rtc import MODE_REG, RTC


def _select(rtc: RTC, reg: int) -> None:
    rtc.write_port(0xB4, reg)


def _set_block(rtc: RTC, block: int) -> None:
    _select(rtc, MODE_REG)
    # keep timer-enable bit set, low 2 bits select the block
    rtc.write_port(0xB5, 0x08 | (block & 0x03))


def test_data_read_has_high_nibble_set() -> None:
    """The 4-bit device floats the high nibble to 1s on data reads."""
    rtc = RTC()
    _set_block(rtc, 2)
    _select(rtc, 0)
    assert rtc.read_port(0xB5) & 0xF0 == 0xF0


def test_address_port_is_write_only() -> None:
    rtc = RTC()
    assert rtc.read_port(0xB4) == 0xFF


def test_cmos_ram_persists_written_values() -> None:
    """Blocks 2/3 are battery-backed RAM: what is written reads back (low nibble)."""
    rtc = RTC()
    _set_block(rtc, 2)
    for reg in range(13):
        _select(rtc, reg)
        rtc.write_port(0xB5, (reg + 1) & 0x0F)
    for reg in range(13):
        _select(rtc, reg)
        assert rtc.read_port(0xB5) & 0x0F == (reg + 1) & 0x0F


def test_blocks_are_independent() -> None:
    rtc = RTC()
    _set_block(rtc, 2)
    _select(rtc, 0)
    rtc.write_port(0xB5, 0x0A)
    _set_block(rtc, 3)
    _select(rtc, 0)
    rtc.write_port(0xB5, 0x05)
    _set_block(rtc, 2)
    _select(rtc, 0)
    assert rtc.read_port(0xB5) & 0x0F == 0x0A


def test_mode_register_reads_back() -> None:
    rtc = RTC()
    _select(rtc, MODE_REG)
    rtc.write_port(0xB5, 0x0B)
    assert rtc.read_port(0xB5) & 0x0F == 0x0B


def test_time_block_returns_valid_bcd() -> None:
    """Block 0 seconds digits are valid BCD (units 0-9, tens 0-5)."""
    rtc = RTC()
    _set_block(rtc, 0)
    _select(rtc, 0)
    units = rtc.read_port(0xB5) & 0x0F
    _select(rtc, 1)
    tens = rtc.read_port(0xB5) & 0x0F
    assert 0 <= units <= 9
    assert 0 <= tens <= 5
