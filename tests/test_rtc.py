"""Tests for the RP-5C01 RTC (register banks + CMOS RAM + running clock)."""
from datetime import datetime
from pathlib import Path

import pytest

from msx.rtc import HOUR_MODE_REG, MODE_REG, RTC, SRAM_SIZE
from tests.factories import make_machine_msx2

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000


def _select(rtc: RTC, reg: int) -> None:
    rtc.write_port(0xB4, reg)


def _set_block(rtc: RTC, block: int) -> None:
    _select(rtc, MODE_REG)
    # keep timer-enable bit set, low 2 bits select the block
    rtc.write_port(0xB5, 0x08 | (block & 0x03))


def _read_hour(rtc: RTC) -> tuple[int, int]:
    """Return block 0's (units, tens) hour digits."""
    _set_block(rtc, 0)
    _select(rtc, 4)
    units = rtc.read_port(0xB5) & 0x0F
    _select(rtc, 5)
    tens = rtc.read_port(0xB5) & 0x0F
    return units, tens


def _set_hour_mode(rtc: RTC, *, is_24h: bool) -> None:
    _set_block(rtc, 1)
    _select(rtc, HOUR_MODE_REG)
    rtc.write_port(0xB5, 0x1 if is_24h else 0x0)


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


def test_cmos_ram_save_and_load_round_trip(tmp_path: Path) -> None:
    """save_sram()'s bytes, passed back as sram=, reproduce the same RAM."""
    rtc = RTC()
    _set_block(rtc, 2)
    for reg in range(13):
        _select(rtc, reg)
        rtc.write_port(0xB5, (reg + 1) & 0x0F)
    _set_block(rtc, 3)
    for reg in range(13):
        _select(rtc, reg)
        rtc.write_port(0xB5, (reg + 2) & 0x0F)

    save_path = tmp_path / "rtc.sram"
    rtc.save_sram(save_path)
    assert len(save_path.read_bytes()) == SRAM_SIZE

    reloaded = RTC(sram=bytearray(save_path.read_bytes()))
    _set_block(reloaded, 2)
    for reg in range(13):
        _select(reloaded, reg)
        assert reloaded.read_port(0xB5) & 0x0F == (reg + 1) & 0x0F
    _set_block(reloaded, 3)
    for reg in range(13):
        _select(reloaded, reg)
        assert reloaded.read_port(0xB5) & 0x0F == (reg + 2) & 0x0F


def test_sram_wrong_size_starts_fresh() -> None:
    """A wrong-size sram= argument is ignored, matching FmPac's own guard."""
    rtc = RTC(sram=bytearray(3))
    _set_block(rtc, 2)
    _select(rtc, 0)
    assert rtc.read_port(0xB5) & 0x0F == 0x0


def test_hour_encoding_24h_is_default() -> None:
    """Register 10 defaults to 24-hour when never written."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 13, 0, 0))
    assert _read_hour(rtc) == (3, 1)  # 13 -> units 3, tens 1


def test_hour_encoding_12h_am() -> None:
    rtc = RTC(_epoch=datetime(2026, 1, 1, 9, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (9, 0)


def test_hour_encoding_12h_pm() -> None:
    """PM hours offset the tens digit by 2 (RP/RF5C01A datasheet)."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 13, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (1, 2)  # 1 PM -> "21"


def test_hour_encoding_12h_midnight() -> None:
    """Midnight (hour 0) is hour-of-12 12, AM (no +2 offset)."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 0, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (2, 1)  # "12"


def test_hour_encoding_12h_noon() -> None:
    """Noon (hour 12) is hour-of-12 12, PM (+2 offset)."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 12, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (2, 3)  # "32"


def test_machine_loads_previously_saved_rtc_sram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build_machine()-constructed RTC persists blocks 2/3 to
    saves/sram/rtc.sram (relative to CWD, matching fmpac's own default-path
    convention) and loads it back on the next construction."""
    monkeypatch.chdir(tmp_path)

    machine1 = make_machine_msx2(_ROM, _EXTROM)
    assert machine1.rtc is not None
    assert machine1.rtc_sram_save_path == Path("saves/sram/rtc.sram")
    _set_block(machine1.rtc, 2)
    _select(machine1.rtc, 0)
    machine1.rtc.write_port(0xB5, 0xA)
    assert machine1.rtc_sram_save_path is not None
    machine1.rtc_sram_save_path.parent.mkdir(parents=True, exist_ok=True)
    machine1.rtc.save_sram(machine1.rtc_sram_save_path)

    machine2 = make_machine_msx2(_ROM, _EXTROM)
    assert machine2.rtc is not None
    _set_block(machine2.rtc, 2)
    _select(machine2.rtc, 0)
    assert machine2.rtc.read_port(0xB5) & 0x0F == 0xA
