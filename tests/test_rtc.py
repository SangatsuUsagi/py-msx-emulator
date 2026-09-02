"""Tests for the RP-5C01 RTC (register banks + CMOS RAM + running clock)."""
from datetime import datetime
from pathlib import Path

import pytest

from msx.machine_loader import MachineSpec, _RomEntry, build_machine
from msx.rtc import HOUR_MODE_REG, MODE_REG, RESET_REG, RTC, SRAM_SIZE, TEST_REG
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


def test_block0_write_never_survives_read() -> None:
    """A write to a block-0 register is stored (subject to its mask), but
    ReadDataPort unconditionally overwrites block 0 with the frozen epoch
    snapshot on every read of block 0 or block 1 -- so the write is never
    observable."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 13, 30, 45))
    _set_block(rtc, 0)
    _select(rtc, 0)  # seconds units
    baseline = rtc.read_port(0xB5) & 0x0F
    rtc.write_port(0xB5, (baseline + 1) % 10)
    assert rtc.read_port(0xB5) & 0x0F == baseline


def test_leap_counter_write_never_survives_read() -> None:
    """Block 1 register 11 (leap-year counter) gets the same
    always-overwritten-on-read treatment as block 0: it always reads back
    epoch_leap, regardless of what was last written."""
    rtc = RTC(_epoch=datetime(2026, 1, 1, 0, 0, 0))  # (2026 - 1980) % 4 == 2
    _set_block(rtc, 1)
    _select(rtc, 11)
    assert rtc.read_port(0xB5) & 0x0F == 2
    rtc.write_port(0xB5, 0x0)
    assert rtc.read_port(0xB5) & 0x0F == 2


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
    """A build_machine()-constructed RTC persists blocks 2/3 to a per-machine
    saves/sram/rtc_<machine_id>.sram (relative to CWD) and loads it back on
    the next construction."""
    monkeypatch.chdir(tmp_path)

    machine1 = make_machine_msx2(_ROM, _EXTROM)
    assert machine1.rtc is not None
    assert machine1.rtc_sram_save_path == Path("saves/sram/rtc_test_msx2.sram")
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


def _rtc_spec(machine_id: str) -> MachineSpec:
    return MachineSpec(
        name=machine_id,
        machine_id=machine_id,
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=True,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=True,
    )


def test_two_rtc_machines_do_not_share_cmos_ram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different machine_ids must each get their own
    saves/sram/rtc_<machine_id>.sram -- one machine's CMOS RAM write must
    not be visible to another machine's RTC."""
    monkeypatch.chdir(tmp_path)

    machine_a = build_machine(_rtc_spec("machine_a"), bios_override=_ROM, extrom_override=_EXTROM)
    machine_b = build_machine(_rtc_spec("machine_b"), bios_override=_ROM, extrom_override=_EXTROM)
    assert machine_a.rtc is not None and machine_b.rtc is not None
    assert machine_a.rtc_sram_save_path != machine_b.rtc_sram_save_path
    assert machine_a.rtc_sram_save_path == Path("saves/sram/rtc_machine_a.sram")
    assert machine_b.rtc_sram_save_path == Path("saves/sram/rtc_machine_b.sram")

    _set_block(machine_a.rtc, 2)
    _select(machine_a.rtc, 0)
    machine_a.rtc.write_port(0xB5, 0x7)
    assert machine_a.rtc_sram_save_path is not None
    machine_a.rtc_sram_save_path.parent.mkdir(parents=True, exist_ok=True)
    machine_a.rtc.save_sram(machine_a.rtc_sram_save_path)

    assert machine_b.rtc_sram_save_path is not None
    machine_b.rtc_sram_save_path.parent.mkdir(parents=True, exist_ok=True)
    machine_b.rtc.save_sram(machine_b.rtc_sram_save_path)

    reloaded_b = build_machine(_rtc_spec("machine_b"), bios_override=_ROM, extrom_override=_EXTROM)
    assert reloaded_b.rtc is not None
    _set_block(reloaded_b.rtc, 2)
    _select(reloaded_b.rtc, 0)
    assert reloaded_b.rtc.read_port(0xB5) & 0x0F == 0x0  # not machine_a's 0x7
