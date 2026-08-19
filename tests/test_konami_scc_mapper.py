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


def test_bank_select_masks_to_a_page_on_this_power_of_two_rom(mapper: KonamiSCCMapper) -> None:
    # Page select is two-tier (see _select_page), not modulo: 9 is not less
    # than this fixture's 8 pages, so it is masked with (8 - 1) = 7,
    # giving 9 & 7 == 1. Indistinguishable from modulo here only because 8
    # is a power of two -- see test_konami_mapper_spec.py::
    # test_konami_scc_bank_select_uses_bit_and_not_modulo for a page count
    # where the two diverge.
    mapper.write(0x7000, 9)
    assert mapper.read(0x6000) == 1


def test_writes_outside_register_zones_ignored(mapper: KonamiSCCMapper) -> None:
    # Bank registers are only the low 2 KB of each window. Writes elsewhere
    # (e.g. a BIOS RAM test hitting 0xBF00) must NOT switch a bank — a known
    # boot bug in a Konami SCC MegaROM title: 0xBF00 wrongly set bank 3.
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


def test_scc_mode_enabled_when_low_six_bits_set(mapper: KonamiSCCMapper) -> None:
    # 0xFF & 0x3F == 0x3F: upper two bits are don't-care.
    mapper.write(0x9000, 0xFF)
    assert mapper._scc_mode is True


def test_scc_mode_not_enabled_when_low_six_bits_differ(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3E)  # 0x3E & 0x3F != 0x3F
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


def test_scc_write_not_routed_when_scc_mode_false(mapper: KonamiSCCMapper) -> None:
    # Write-side twin of test_rom_read_when_scc_mode_false: with SCC mode
    # off, a write into 0x9800-0x9FFF must not reach the chip either.
    assert mapper._scc_mode is False
    mapper.write(0x9800, 0x7F)
    assert mapper.scc._waves[0][0] == 0  # SCC waveform bank 0 untouched


def test_scc_read_forward_only_applies_within_9800_9fff(mapper: KonamiSCCMapper) -> None:
    # SCC mode on, but the address is window 2's body (below 0x9800): must
    # still resolve via ROM, not the chip.
    mapper.write(0x9000, 0x3F)
    assert mapper._scc_mode is True
    # The same write also updates window 2's bank register (openMSX's
    # page-selection check is not gated on the enable code): 0x3F is not
    # less than this fixture's 8 pages, so it is masked with (8 - 1) = 7,
    # landing on page 7 rather than leaving the power-on page 2 in place.
    assert mapper.read(0x8000) == 7  # ROM page 7 byte, not the SCC


def test_scc_write_forward_only_applies_within_9800_9fff(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x8000, 0x7F)  # window 2 body, below the SCC zone
    assert mapper.scc._waves[0][0] == 0  # SCC waveform bank 0 untouched


def test_scc_volume_register_routed(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9800 + 0x8A, 0x0F)   # channel 1 volume
    # 0x80-0x9F is write-only on real hardware (see msx/scc.py), so the
    # write is checked via internal state rather than a read-back.
    assert mapper.scc._vol[0] == 0x0F


def test_scc_enable_register_routed(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9800 + 0x8F, 0x1F)
    assert mapper.scc._enable == 0x1F


def test_snapshot_restore_roundtrips_banks_and_scc_mode(mapper: KonamiSCCMapper) -> None:
    mapper.write(0x5000, 5)      # window 0 bank
    mapper.write(0x9000, 0x3F)   # enable SCC mode
    snap = mapper.snapshot()

    other = KonamiSCCMapper(rom=_rom(8), scc=SCC())
    other.restore(snap)
    assert other._banks == mapper._banks
    assert other._scc_mode is True


def test_restore_rejects_negative_bank(mapper: KonamiSCCMapper) -> None:
    # Mirrors KonamiMapper's restore() validation: a negative bank would
    # make _sync_window slice self.rom with a negative start, which Python
    # wraps from the end of the ROM rather than raising.
    with pytest.raises(ValueError):
        mapper.restore({"banks": [0, 1, 2, -1], "scc_mode": False})


def test_restore_with_scc_mode_true_routes_reads_to_scc(mapper: KonamiSCCMapper) -> None:
    # restore() must reproduce the restored _scc_mode (not just recompute the
    # flat mirror) so an SCC-range read routes to the SCC chip -- verified via
    # behavior, not by inspecting internal state directly.
    mapper.write(0x9000, 0x3F)   # enable SCC mode
    snap = mapper.snapshot()

    other = KonamiSCCMapper(rom=_rom(8), scc=SCC())
    other.restore(snap)
    other.scc.write(0x00, 0x55)
    assert other.read(0x9800) == 0x55


def test_restore_with_scc_mode_false_routes_reads_to_rom(mapper: KonamiSCCMapper) -> None:
    # The inverse: restoring onto a mapper that was previously in SCC mode
    # must route reads back to the ROM path.
    mapper.write(0x9000, 0x3F)
    mapper.write(0x9000, 0x02)   # disable SCC mode, window 2 -> bank 2
    snap = mapper.snapshot()

    other = KonamiSCCMapper(rom=_rom(8), scc=SCC())
    other.write(0x9000, 0x3F)   # start `other` in SCC mode
    other.restore(snap)
    assert other.read(0x9800) == 2  # ROM page 2 byte, not routed to SCC


# ---------------------------------------------------------------------------
# Addresses outside the four windows (0x4000-0xBFFF)
# ---------------------------------------------------------------------------

def test_read_below_window_mirrors_windows_2_and_3(mapper: KonamiSCCMapper) -> None:
    # A slot scan (e.g. BIOS RAM detection) can transiently address this
    # mapper's slot on a page it doesn't occupy (page 0, below 0x4000).
    # Real hardware mirrors windows 2/3 there -- the opposite direction
    # from plain KonamiMapper (openMSX RomKonamiSCC::bankSwitch) -- rather
    # than reading open bus.
    assert mapper.read(0x0000) == mapper.read(0x8000)  # window 2's first byte, mirrored
    assert mapper.read(0x3FFF) == mapper.read(0xBFFF)  # window 3's last byte, mirrored


def test_read_above_window_mirrors_windows_0_and_1() -> None:
    # 0xC000-0xFFFF mirrors windows 0/1 for KonamiSCCMapper. Window 0's
    # power-on bank is 0, which a 1-page ROM does have, so this resolves
    # to real ROM content -- unlike plain KonamiMapper's mirror, which
    # would land on window 2 (out of range for a 1-page ROM).
    small_mapper = KonamiSCCMapper(rom=_rom(1), scc=SCC())
    assert small_mapper.read(0xC000) == small_mapper.read(0x4000)
