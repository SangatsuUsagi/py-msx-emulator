"""Tests for the RP-5C01 RTC (register banks + CMOS RAM + running clock)."""
from pathlib import Path

import pytest

from host.machine_config import LoadedMachineConfig, load_machine_config, sram_paths
from host.media import persist_dirty_media
from msx.boundary import RTC_CMOS_SIZE, MediaId, RtcTime
from msx.clock import CoreClock
from msx.machine_loader import build_machine
from msx.machine_spec import MachineSpec, RomEntry
from msx.rtc import (
    HOUR_MODE_REG,
    MODE_REG,
    RESET_ALARM,
    RESET_FRACTION,
    RESET_REG,
    RTC,
    TEST_REG,
    TEST_SECONDS,
)
from tests.factories import make_loaded_config_msx2, make_machine_msx2

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000
_SECOND = 3_579_545


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


def test_register_select_only_low_nibble_decoded() -> None:
    """Only bits 0-3 of a register-select write are decoded; bits 4-7 are
    ignored (rtc.addr = low_bits(value, 4))."""
    rtc = RTC()
    rtc.write_port(0xB4, 0xFD)  # low nibble 0xD == MODE_REG (13)
    rtc.write_port(0xB5, 0x0B)
    _select(rtc, MODE_REG)
    assert rtc.read_port(0xB5) & 0x0F == 0x0B


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


def test_block1_masked_registers_clamp_write_to_mask() -> None:
    """Block 1's per-register write mask genuinely clamps storable bits --
    registers 3, 5, 6, 8 (alarm minutes tens, alarm hours tens, alarm
    weekday, alarm day tens) each keep only their masked low bits."""
    rtc = RTC()
    _set_block(rtc, 1)
    for reg, mask in ((3, 0x7), (5, 0x3), (6, 0x7), (8, 0x3)):
        _select(rtc, reg)
        rtc.write_port(0xB5, 0x0F)
        assert rtc.read_port(0xB5) & 0x0F == mask


def test_block1_unused_registers_are_unusable() -> None:
    """Block 1 registers 0, 1, 9, 12 are unused (mask 0): bits marked 0 are
    unusable, not merely unwritten -- any write is discarded entirely."""
    rtc = RTC()
    _set_block(rtc, 1)
    for reg in (0, 1, 9, 12):
        _select(rtc, reg)
        rtc.write_port(0xB5, 0x0F)
        assert rtc.read_port(0xB5) & 0x0F == 0x0


def test_mode_register_reads_back() -> None:
    rtc = RTC()
    _select(rtc, MODE_REG)
    rtc.write_port(0xB5, 0x0B)
    assert rtc.read_port(0xB5) & 0x0F == 0x0B


def test_test_register_read_ignores_stored_value() -> None:
    """Register 14 (TEST) is write-only on real hardware: ReadDataPort
    always returns a fixed 0x0F regardless of what was stored."""
    rtc = RTC()
    _select(rtc, TEST_REG)
    rtc.write_port(0xB5, 0x5)
    assert rtc.read_port(0xB5) & 0x0F == 0x0F


def test_reset_register_read_ignores_stored_value() -> None:
    """Register 15 (RESET) is write-only on real hardware: ReadDataPort
    always returns a fixed 0x0F regardless of what was stored."""
    rtc = RTC()
    _select(rtc, RESET_REG)
    rtc.write_port(0xB5, 0x3)
    assert rtc.read_port(0xB5) & 0x0F == 0x0F


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


def test_a_block0_write_sets_the_clock() -> None:
    """io-rtc "a block 0 write sets the clock": the written minute tens digit
    is kept, and the clock counts on from the written time."""
    rtc, clock = _clocked(RtcTime(2000, 1, 1, 0, 0, 0))
    _set_block(rtc, 0)
    _select(rtc, 3)  # minute tens
    rtc.write_port(0xB5, 4)
    clock.cycle = 60 * _SECOND
    d = _read_block0(rtc)
    assert (d[2], d[3]) == (1, 4)  # 00:41


def test_a_leap_counter_write_gives_february_29_days() -> None:
    """Block 1 register 11 is the leap-year counter: written 0, February of
    the current year has 29 days."""
    rtc, clock = _clocked(RtcTime(2026, 2, 28, 23, 59, 59))  # 2026 % 4 == 2
    _set_block(rtc, 1)
    _select(rtc, 11)
    assert rtc.read_port(0xB5) & 0x0F == 2
    rtc.write_port(0xB5, 0x0)
    clock.cycle = _SECOND
    d = _read_block0(rtc)
    assert (d[7], d[8], d[9], d[10]) == (9, 2, 2, 0)  # 02-29


def test_cmos_ram_save_and_load_round_trip(tmp_path: Path) -> None:
    """cmos.data()'s bytes, passed back as sram=, reproduce the same RAM."""
    rtc = RTC()
    _set_block(rtc, 2)
    for reg in range(13):
        _select(rtc, reg)
        rtc.write_port(0xB5, (reg + 1) & 0x0F)
    _set_block(rtc, 3)
    for reg in range(13):
        _select(rtc, reg)
        rtc.write_port(0xB5, (reg + 2) & 0x0F)

    assert rtc.cmos.dirty is True
    image = rtc.cmos.data()
    assert len(image) == RTC_CMOS_SIZE

    reloaded = RTC(sram=bytearray(image))
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


def _settings_image(hour_mode_24h: bool) -> bytearray:
    """A saved register file whose block 1 register 10 holds the mode."""
    image = bytearray(RTC_CMOS_SIZE)
    image[13 + HOUR_MODE_REG] = 1 if hour_mode_24h else 0
    return image


def test_hour_encoding_24h() -> None:
    rtc = RTC(sram=_settings_image(True), start=RtcTime(2026, 1, 1, 13, 0, 0))
    assert _read_hour(rtc) == (3, 1)  # 13 -> units 3, tens 1


def test_a_blank_register_file_starts_in_12_hour_mode() -> None:
    rtc = RTC(start=RtcTime(2026, 1, 1, 13, 0, 0))
    assert _read_hour(rtc) == (1, 2)  # 1 PM -> "21"


def test_hour_encoding_12h_am() -> None:
    rtc = RTC(start=RtcTime(2026, 1, 1, 9, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (9, 0)


def test_hour_encoding_12h_pm() -> None:
    """A PM hour reads the hour minus 12, plus 20."""
    rtc = RTC(start=RtcTime(2026, 1, 1, 13, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (1, 2)  # 1 PM -> "21"


def test_hour_encoding_12h_midnight() -> None:
    """Midnight (hour 0) reads 00."""
    rtc = RTC(start=RtcTime(2026, 1, 1, 0, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (0, 0)


def test_hour_encoding_12h_noon() -> None:
    """Noon (hour 12) reads 20: the hour minus 12, plus 20."""
    rtc = RTC(start=RtcTime(2026, 1, 1, 12, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    assert _read_hour(rtc) == (0, 2)


def test_a_12h_pm_write_sets_a_pm_hour() -> None:
    rtc, clock = _clocked(RtcTime(2026, 1, 1, 0, 0, 0))
    _set_hour_mode(rtc, is_24h=False)
    _set_block(rtc, 0)
    _select(rtc, 5)
    rtc.write_port(0xB5, 2)
    _select(rtc, 4)
    rtc.write_port(0xB5, 3)  # "23": 3 PM
    clock.cycle = 3600 * _SECOND
    assert _read_hour(rtc) == (4, 2)  # 4 PM


def test_machine_loads_previously_saved_rtc_sram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load_machine_config()-built RTC persists blocks 2/3 to a per-machine
    saves/sram/rtc_<machine_id>.sram (relative to CWD) and loads it back on
    the next construction."""
    monkeypatch.chdir(tmp_path)

    config1 = make_loaded_config_msx2(_ROM, _EXTROM)
    machine1 = build_machine(config1.config)
    assert machine1.rtc is not None
    paths1 = sram_paths(config1)
    assert paths1[MediaId.RTC_CMOS] == Path("saves/sram/rtc_test_msx2.sram")
    _set_block(machine1.rtc, 2)
    _select(machine1.rtc, 0)
    machine1.rtc.write_port(0xB5, 0xA)
    assert MediaId.RTC_CMOS in machine1.dirty_media()
    persist_dirty_media(machine1, paths1)
    assert machine1.dirty_media() == []

    machine2 = make_machine_msx2(_ROM, _EXTROM)
    assert machine2.rtc is not None
    _set_block(machine2.rtc, 2)
    _select(machine2.rtc, 0)
    assert machine2.rtc.read_port(0xB5) & 0x0F == 0xA


def _rtc_spec(machine_id: str) -> MachineSpec:
    return MachineSpec(
        name=machine_id,
        machine_id=machine_id,
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=True,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=True,
    )


def _rtc_config(machine_id: str) -> LoadedMachineConfig:
    return load_machine_config(
        _rtc_spec(machine_id), bios_override=_ROM, extrom_override=_EXTROM
    )


def test_two_rtc_machines_do_not_share_cmos_ram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different machine_ids must each get their own
    saves/sram/rtc_<machine_id>.sram -- one machine's CMOS RAM write must
    not be visible to another machine's RTC."""
    monkeypatch.chdir(tmp_path)

    config_a = _rtc_config("machine_a")
    config_b = _rtc_config("machine_b")
    machine_a = build_machine(config_a.config)
    machine_b = build_machine(config_b.config)
    assert machine_a.rtc is not None and machine_b.rtc is not None
    paths_a = sram_paths(config_a)
    paths_b = sram_paths(config_b)
    assert paths_a[MediaId.RTC_CMOS] == Path("saves/sram/rtc_machine_a.sram")
    assert paths_b[MediaId.RTC_CMOS] == Path("saves/sram/rtc_machine_b.sram")

    _set_block(machine_a.rtc, 2)
    _select(machine_a.rtc, 0)
    machine_a.rtc.write_port(0xB5, 0x7)
    persist_dirty_media(machine_a, paths_a)
    persist_dirty_media(machine_b, paths_b)

    reloaded_b = build_machine(_rtc_config("machine_b").config)
    assert reloaded_b.rtc is not None
    _set_block(reloaded_b.rtc, 2)
    _select(reloaded_b.rtc, 0)
    assert reloaded_b.rtc.read_port(0xB5) & 0x0F == 0x0  # not machine_a's 0x7


# --- io-rtc: time from the host's start value, advanced by emulated cycles ---

def _read_block0(rtc: RTC) -> list[int]:
    _set_block(rtc, 0)
    digits = []
    for reg in range(13):
        _select(rtc, reg)
        digits.append(rtc.read_port(0xB5) & 0x0F)
    return digits


def _read_digits(rtc: RTC, mode: int) -> list[int]:
    """Block 0's 13 digits, read with register 13 set to `mode`."""
    _select(rtc, MODE_REG)
    rtc.write_port(0xB5, mode)
    digits = []
    for reg in range(13):
        _select(rtc, reg)
        digits.append(rtc.read_port(0xB5) & 0x0F)
    return digits


def _clocked(start: RtcTime) -> tuple[RTC, CoreClock]:
    clock = CoreClock()
    rtc = RTC(start=start, _clock=clock)
    return rtc, clock


def test_block0_reflects_the_supplied_start_time() -> None:
    rtc = RTC(start=RtcTime(2026, 10, 23, 12, 34, 56))
    d = _read_block0(rtc)
    assert (d[2], d[3]) == (4, 3)                       # minute 34


def test_no_start_time_gives_the_fixed_epoch() -> None:
    d = _read_block0(RTC())
    # 2000-01-01 00:00:00, Saturday (6); year register = 2000 - 1980 = 20
    assert d == [0, 0, 0, 0, 0, 0, 6, 1, 0, 1, 0, 0, 2]


def test_one_emulated_second_advances_the_seconds_digit() -> None:
    rtc, clock = _clocked(RtcTime(2026, 1, 1, 12, 0, 0))
    clock.cycle = 3_579_545 - 1
    assert _read_block0(rtc)[0:2] == [0, 0]
    clock.cycle = 3_579_545
    assert _read_block0(rtc)[0:2] == [1, 0]


def test_midnight_carries_into_the_next_day() -> None:
    rtc, clock = _clocked(RtcTime(1999, 12, 31, 23, 59, 59))
    before = _read_block0(rtc)
    clock.cycle = 3_579_545
    after = _read_block0(rtc)
    # 2000-01-01 00:00:00; year register (2000 - 1980) % 100 = 20
    assert after[0:6] == [0, 0, 0, 0, 0, 0]
    assert after[7:13] == [1, 0, 1, 0, 0, 2]
    assert after[6] == (before[6] + 1) % 7


def test_rtc_never_reads_the_host_clock() -> None:
    # Two RTCs with the same start and no elapsed cycles read the same digits.
    a = RTC(start=RtcTime(2026, 10, 23, 1, 2, 3))
    b = RTC(start=RtcTime(2026, 10, 23, 1, 2, 3))
    assert _read_block0(a) == _read_block0(b)


def test_build_machine_wires_the_rtc_to_the_machine_clock() -> None:
    machine = make_machine_msx2(bytes(0x8000), bytes(0x4000))
    assert machine.rtc is not None
    machine.clock.cycle = 3 * 3_579_545
    assert _read_block0(machine.rtc)[0:2] == [3, 0]


def test_snapshot_regs_are_the_register_file() -> None:
    rtc = RTC(sram=bytearray(range(RTC_CMOS_SIZE)))
    regs = rtc.snapshot()["regs"]
    assert len(regs) == RTC_CMOS_SIZE
    assert regs == list(rtc.cmos.data())
    # Block 2/3 bytes are stored as given (values above 0x0F included).
    assert regs[26:] == list(range(26, 52))


def test_restoring_regs_with_different_cmos_marks_it_dirty() -> None:
    rtc = RTC()
    state = rtc.parse_state(rtc.snapshot(), "rtc")
    state["regs"] = state["regs"][:26] + [0x5] * 26
    rtc.apply_state(state)
    assert rtc.cmos.dirty is True
    assert rtc.snapshot()["regs"][26:] == [0x5] * 26


def test_a_stored_byte_above_0x0f_reads_through_the_mask() -> None:
    # io-rtc "a stored byte above 0x0F reads through the mask".
    image = bytearray(RTC_CMOS_SIZE)
    image[26] = 0xF7  # block 2, register 0
    rtc = RTC(sram=image)
    _set_block(rtc, 2)
    _select(rtc, 0)
    assert rtc.read_port(0xB5) == 0xF7
    loaded = RTC()
    loaded.apply_state(loaded.parse_state(rtc.snapshot(), "rtc"))
    assert list(loaded.cmos.data()) == list(rtc.cmos.data())


def test_block1_settings_survive_a_save_reload_cycle() -> None:
    rtc = RTC()
    _set_hour_mode(rtc, is_24h=True)
    reloaded = RTC(sram=rtc.cmos.data())
    _set_block(reloaded, 1)
    _select(reloaded, HOUR_MODE_REG)
    assert reloaded.read_port(0xB5) & 0x0F == 1


def test_the_start_time_replaces_the_saved_time_digits() -> None:
    saved = RTC(start=RtcTime(2010, 5, 6, 7, 8, 9))
    rtc = RTC(sram=saved.cmos.data(), start=RtcTime(2026, 10, 23, 12, 34, 56))
    assert _read_block0(rtc) == _read_block0(RTC(start=RtcTime(2026, 10, 23, 12, 34, 56)))


def test_creating_an_rtc_alone_leaves_nothing_to_save() -> None:
    assert RTC(start=RtcTime(2026, 10, 23, 12, 34, 56)).cmos.dirty is False


# --- io-rtc: MODE/TEST/RESET and the machine reset ---------------------------

def _write(rtc: RTC, reg: int, value: int) -> None:
    _select(rtc, reg)
    rtc.write_port(0xB5, value)


def test_timer_enable_clear_stops_the_clock() -> None:
    rtc, clock = _clocked(RtcTime(2026, 1, 1, 12, 0, 0))
    _write(rtc, MODE_REG, 0x00)  # Timer-EN clear, block 0
    clock.cycle = _SECOND
    assert _read_digits(rtc, 0x00)[0:2] == [0, 0]


def test_test_bit0_advances_the_seconds_once_per_tick() -> None:
    rtc, clock = _clocked(RtcTime(2026, 1, 1, 12, 0, 0))
    _write(rtc, TEST_REG, TEST_SECONDS)
    clock.cycle = 219  # one 16384 Hz tick
    assert _read_digits(rtc, 0x08)[0:2] == [1, 0]


def test_reset_bit1_clears_the_fraction() -> None:
    rtc, clock = _clocked(RtcTime(2026, 1, 1, 12, 0, 0))
    clock.cycle = 1_789_773
    _write(rtc, RESET_REG, RESET_FRACTION)
    clock.cycle = 2 * 1_789_773
    assert _read_digits(rtc, 0x08)[0:2] == [0, 0]
    clock.cycle = 1_789_773 + _SECOND
    assert _read_digits(rtc, 0x08)[0:2] == [1, 0]


def test_reset_bit0_clears_the_alarm_registers() -> None:
    rtc = RTC()
    _set_hour_mode(rtc, is_24h=True)
    for reg in range(2, 9):
        _write(rtc, reg, 1)
    _write(rtc, RESET_REG, RESET_ALARM)
    _set_block(rtc, 1)
    for reg in range(2, 9):
        _select(rtc, reg)
        assert rtc.read_port(0xB5) & 0x0F == 0
    _select(rtc, HOUR_MODE_REG)
    assert rtc.read_port(0xB5) & 0x0F == 1
    _select(rtc, 11)
    assert rtc.read_port(0xB5) & 0x0F == 2000 % 4


def test_a_machine_reset_restores_mode_and_the_latch() -> None:
    # io-rtc "a machine reset restores MODE and the latch".
    machine = make_machine_msx2(bytes(0x8000), bytes(0x4000))
    rtc = machine.rtc
    assert rtc is not None
    _write(rtc, MODE_REG, 0x2)
    _write(rtc, TEST_REG, 0x1)
    _select(rtc, 5)
    machine.reset()
    assert rtc.read_port(0xB5) & 0x0F == 0  # register 0 of block 0
    _select(rtc, MODE_REG)
    assert rtc.read_port(0xB5) & 0x0F == 0x8
    machine.clock.cycle += _SECOND
    assert _read_digits(rtc, 0x08)[0:2] == [1, 0]


def test_a_date_set_by_software_is_kept_until_the_day_advances() -> None:
    rtc, clock = _clocked(RtcTime(2026, 7, 31, 12, 0, 0))
    _set_block(rtc, 0)
    _write(rtc, 9, 2)   # month units: 07 -> 02, 02-31
    d = _read_block0(rtc)
    assert (d[7], d[8], d[9], d[10]) == (1, 3, 2, 0)
    clock.cycle = _SECOND
    d = _read_block0(rtc)
    assert (d[7], d[8], d[9], d[10]) == (1, 3, 2, 0)
