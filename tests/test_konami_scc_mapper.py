"""Tests for KonamiSCCMapper — bank switching and SCC register routing."""
import pytest

from msx.mapper import KonamiSCCMapper
from msx.scc import SCC

_PAGE = 8192  # 8 KB


def _rom(num_pages: int = 8) -> bytes:
    """Generate a ROM where each page is filled with its page index byte."""
    buf = bytearray()
    for page_idx in range(num_pages):
        buf.extend(bytes([page_idx & 0xFF] * _PAGE))
    return bytes(buf)


@pytest.fixture()
def mapper() -> KonamiSCCMapper:
    return KonamiSCCMapper(rom=_rom(8), scc=SCC())


# ---------------------------------------------------------------------------
# Initial bank state (mirrors KonamiMapper)
# ---------------------------------------------------------------------------

def test_initial_bank_state(mapper: KonamiSCCMapper) -> None:
    assert mapper.read(0x4000) == 0  # page 0
    assert mapper.read(0x6000) == 1  # page 1
    assert mapper.read(0x8000) == 2  # page 2
    assert mapper.read(0xA000) == 3  # page 3


def test_bank_switch_window0(mapper: KonamiSCCMapper) -> None:
    # Konami SCC window 0 is switchable via the 0x5000–0x57FF register.
    mapper.write(0x5000, 5)
    assert mapper.read(0x4000) == 5


def test_window0_write_outside_register_ignored(mapper: KonamiSCCMapper) -> None:
    # 0x4000–0x4FFF is not the bank-0 register; writes there are ignored.
    mapper.write(0x4000, 5)
    assert mapper.read(0x4000) == 0  # still page 0


def test_bank_switch_window1(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x7000, 4)  # bank-1 register is 0x7000-0x77FF
    assert mapper.read(0x6000) == 4


def test_bank_switch_window2(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 5)  # bank-2 register is 0x9000-0x97FF
    assert mapper.read(0x8000) == 5


def test_bank_switch_window3(mapper: KonamiSCCMapper) -> None:
    mapper.write(0xB000, 6)  # bank-3 register is 0xB000-0xB7FF
    assert mapper.read(0xA000) == 6


def test_bank_page_wrap(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x7000, 9)  # 9 % 8 = 1
    assert mapper.read(0x6000) == 1


def test_writes_outside_register_zones_ignored(mapper: KonamiSCCMapper) -> None:
    # Bank registers are only the low 2 KB of each window. Writes elsewhere
    # (e.g. a BIOS RAM test hitting 0xBF00) must NOT switch a bank — this was
    # the Metal Gear 2 boot bug: 0xBF00 wrongly set bank 3.
    mapper.write(0xBF00, 0x0F)
    assert mapper.read(0xA000) == 3  # bank 3 unchanged
    mapper.write(0x6000, 4)
    assert mapper.read(0x6000) == 1  # 0x6000 is not the bank-1 register
    mapper.write(0x8000, 5)
    assert mapper.read(0x8000) == 2  # 0x8000 is not the bank-2 register


# ---------------------------------------------------------------------------
# SCC mode activation / deactivation
# ---------------------------------------------------------------------------

def test_scc_mode_disabled_by_default(mapper: KonamiSCCMapper) -> None:
    assert mapper._scc_mode is False


def test_scc_mode_enabled_by_0x3f_to_window2(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    assert mapper._scc_mode is True


def test_scc_mode_cleared_by_non_0x3f(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9000, 0x02)
    assert mapper._scc_mode is False


def test_scc_mode_not_affected_by_window1_write(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x7000, 0x3F)  # bank-1 register; 0x3F selects a page, not SCC
    assert mapper._scc_mode is False


def test_scc_mode_not_affected_by_window3_write(mapper: KonamiSCCMapper) -> None:
    mapper.write(0xB000, 0x3F)  # bank-3 register; 0x3F selects a page, not SCC
    assert mapper._scc_mode is False


# ---------------------------------------------------------------------------
# SCC register routing
# ---------------------------------------------------------------------------

def test_scc_read_routed_when_scc_mode(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.scc.write(0x00, 0x42)  # set waveform byte directly on SCC
    assert mapper.read(0x9800) == 0x42


def test_scc_write_routed_when_scc_mode(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9800, 0x7F)
    assert mapper.scc.read(0x00) == 0x7F


def test_rom_read_when_scc_mode_false(mapper: KonamiSCCMapper) -> None:
    # SCC mode off: window 2 defaults to page 2, so reads return 2.
    assert mapper._scc_mode is False
    assert mapper.read(0x9800) == 2


def test_scc_volume_register_routed(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9800 + 0x8A, 0x0F)   # channel 1 volume
    assert mapper.scc.read(0x8A) == 0x0F


def test_scc_enable_register_routed(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9800 + 0x8F, 0x1F)
    assert mapper.scc.read(0x8F) == 0x1F
