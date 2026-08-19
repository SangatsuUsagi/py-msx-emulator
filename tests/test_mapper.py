import struct

import pytest

from msx.mapper import (
    Ascii8Mapper,
    Ascii16Mapper,
    FixedPageMapper,
    FlatMapper,
    KonamiMapper,
    MajutsushiMapper,
)

_PAGE_8K = 8192
_PAGE_16K = 16384


def _rom_8k_pages(n: int) -> bytes:
    """Build a ROM of n 8 KB pages where page P starts with byte P."""
    return bytes([(p if i == 0 else 0) for p in range(n) for i in range(_PAGE_8K)])


def _rom_16k_pages(n: int) -> bytes:
    """Build a ROM of n 16 KB pages where page P starts with byte P."""
    return bytes([(p if i == 0 else 0) for p in range(n) for i in range(_PAGE_16K)])


# ---------------------------------------------------------------------------
# FlatMapper
# ---------------------------------------------------------------------------

def test_flat_read_returns_rom_byte() -> None:
    cart = bytes([0xAB] + [0] * 32767)
    m = FlatMapper(cart)
    assert m.read(0x4000) == 0xAB


def test_flat_read_correct_offset() -> None:
    cart = bytes([0x00] * 0x2000 + [0xCD] + [0x00] * (32768 - 0x2001))
    m = FlatMapper(cart)
    assert m.read(0x6000) == 0xCD


def test_flat_write_is_noop() -> None:
    cart = bytes([0x42] + [0] * 32767)
    m = FlatMapper(cart)
    m.write(0x6000, 0x01)
    assert m.read(0x4000) == 0x42


def test_flat_no_cartridge_returns_ff() -> None:
    m = FlatMapper(None)
    assert m.read(0x5000) == 0xFF


def test_flat_8kb_rom_mirrors_in_32kb_space() -> None:
    # 8 KB ROM: read at offset 8192 (= 0x4000 + 0x2000) should mirror back to offset 0.
    cart = bytes([0xAB] + [0] * (8192 - 1))
    m = FlatMapper(cart)
    assert m.read(0x4000) == 0xAB
    assert m.read(0x6000) == 0xAB  # 0x6000 - 0x4000 = 0x2000 = 8192 ≡ 0 mod 8192


def test_flat_16kb_rom_mirrors_in_32kb_space() -> None:
    # 16 KB ROM: read at 0x4000 + 16384 should mirror to 0x4000.
    cart = bytes([0xCD] + [0] * (16384 - 1))
    m = FlatMapper(cart)
    assert m.read(0x4000) == 0xCD
    assert m.read(0x8000) == 0xCD  # 0x8000 - 0x4000 = 16384 ≡ 0 mod 16384


def test_flat_snapshot_restore_is_empty_and_noop() -> None:
    cart = bytes([0xAB] + [0] * 32767)
    m = FlatMapper(cart)
    snap = m.snapshot()
    assert snap == {}
    m.restore(snap)
    assert m.read(0x4000) == 0xAB


# ---------------------------------------------------------------------------
# FixedPageMapper
# ---------------------------------------------------------------------------

def test_fixed_page_read_within_window_at_0x8000() -> None:
    rom = bytes([0xAB] + [0] * (16384 - 1))
    m = FixedPageMapper(rom, base=0x8000)
    assert m.read(0x8000) == 0xAB


def test_fixed_page_read_outside_window_at_0x8000_returns_ff() -> None:
    rom = bytes([0xAB] + [0] * (16384 - 1))
    m = FixedPageMapper(rom, base=0x8000)
    assert m.read(0x4000) == 0xFF
    assert m.read(0x6000) == 0xFF


def test_fixed_page_read_within_window_at_0x4000() -> None:
    rom = bytes([0xCD] + [0] * (16384 - 1))
    m = FixedPageMapper(rom, base=0x4000)
    assert m.read(0x4000) == 0xCD


def test_fixed_page_read_outside_window_at_0x4000_returns_ff() -> None:
    rom = bytes([0xCD] + [0] * (16384 - 1))
    m = FixedPageMapper(rom, base=0x4000)
    assert m.read(0x8000) == 0xFF
    assert m.read(0xA000) == 0xFF


def test_fixed_page_write_is_noop() -> None:
    rom = bytes([0x42] + [0] * (16384 - 1))
    m = FixedPageMapper(rom, base=0x8000)
    m.write(0x8000, 0x01)
    assert m.read(0x8000) == 0x42


def test_fixed_page_does_not_mirror_past_rom_end() -> None:
    # 8 KB ROM at 0x8000: 0xA000 is still inside the 0x8000-0xBFFF page but past
    # the ROM's own span, so it must read as open bus, not wrap around.
    rom = bytes([0xAB] + [0] * (8192 - 1))
    m = FixedPageMapper(rom, base=0x8000)
    assert m.read(0x8000) == 0xAB
    assert m.read(0xA000) == 0xFF


# ---------------------------------------------------------------------------
# Ascii8Mapper
# ---------------------------------------------------------------------------

def test_ascii8_initial_banks() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    assert m.read(0x4000) == 0  # page 0
    assert m.read(0x6000) == 0  # all windows reset to bank 0 (hardware/openMSX)
    assert m.read(0x8000) == 0
    assert m.read(0xA000) == 0


def test_ascii8_switch_window_0() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6000, 3)
    assert m.read(0x4000) == 3


def test_ascii8_switch_window_2() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x7000, 5)
    assert m.read(0x8000) == 5


def test_ascii8_control_reg_range_hits_window_0() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6400, 2)
    assert m.read(0x4000) == 2


def test_ascii8_page_wrap_around() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6000, 9)  # 9 % 8 == 1
    assert m.read(0x4000) == 1


def test_ascii8_switch_window_1() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6800, 4)
    assert m.read(0x6000) == 4


def test_ascii8_switch_window_3() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x7800, 7)
    assert m.read(0xA000) == 7


def test_ascii8_write_outside_control_range_ignored() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x4000, 5)  # outside control range; no effect
    assert m.read(0x4000) == 0  # still page 0


def test_ascii8_read_below_window_returns_open_bus() -> None:
    # A slot scan (e.g. BIOS RAM detection) can transiently address this
    # mapper's slot on a page it doesn't occupy (page 0, below 0x4000).
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    assert m.read(0x0000) == 0xFF


def test_ascii8_read_above_window_returns_open_bus() -> None:
    small_rom = _rom_8k_pages(1)
    m = Ascii8Mapper(small_rom)
    assert m.read(0xC000) == 0xFF


def test_ascii8_read_outside_window_is_open_bus_regardless_of_bank_state() -> None:
    # Real ASCII8 hardware does not mirror outside 0x4000-0xBFFF (openMSX
    # issue #1213). Pick a bank value that would have produced a real ROM
    # byte under the old window/base-extrapolation formula, to prove the
    # fix is unconditional, not coincidental with a zero bank.
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6000, 2)  # window 0 bank register -> page 2
    assert m.read(0x3000) == 0xFF  # old formula would have returned a real ROM byte
    m.write(0x7800, 5)  # window 3 bank register -> page 5
    assert m.read(0xD000) == 0xFF  # old formula would have returned a real ROM byte


def test_ascii8_snapshot_restore_roundtrips_banks() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    m.write(0x6000, 3)
    m.write(0x7800, 7)
    snap = m.snapshot()

    m2 = Ascii8Mapper(rom)
    m2.restore(snap)
    assert m2._banks == m._banks
    assert m2.read(0x4000) == 3
    assert m2.read(0xA000) == 7


# ---------------------------------------------------------------------------
# Ascii16Mapper
# ---------------------------------------------------------------------------

def test_ascii16_initial_banks() -> None:
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    assert m.read(0x4000) == 0  # page 0
    assert m.read(0x8000) == 0  # page 0 — both windows reset to bank 0 (hardware/openMSX)


def test_ascii16_switch_window_0() -> None:
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    m.write(0x6000, 2)
    assert m.read(0x4000) == 2
    # Last byte of page 2
    assert m.read(0x7FFF) == 0  # not the first byte, still from page 2


def test_ascii16_switch_window_1() -> None:
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    m.write(0x7000, 3)
    assert m.read(0x8000) == 3


def test_ascii16_page_wrap_around() -> None:
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    m.write(0x6000, 5)  # 5 % 4 == 1
    assert m.read(0x4000) == 1


def test_ascii16_last_byte_of_window_0() -> None:
    # First byte of each page is the page index; all others are 0
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    m.write(0x6000, 2)
    assert m.read(0x7FFF) == 0  # end of 16 KB window is within page 2


def test_ascii16_read_below_window_returns_open_bus() -> None:
    # A slot scan (e.g. BIOS RAM detection) can transiently address this
    # mapper's slot on a page it doesn't occupy (page 0, below 0x4000).
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    assert m.read(0x0000) == 0xFF


def test_ascii16_read_above_window_falls_back_to_bank_arithmetic() -> None:
    # Above 0xBFFF: re-uses window 1's bank/base arithmetic for any
    # addr >= 0x8000, so a small ROM (bank's page_offset out of range)
    # resolves to open bus via the same bounds check, not a crash.
    small_rom = _rom_16k_pages(1)
    m = Ascii16Mapper(small_rom)
    assert m.read(0xC000) == 0xFF


def test_ascii16_snapshot_restore_roundtrips_banks() -> None:
    rom = _rom_16k_pages(4)
    m = Ascii16Mapper(rom)
    m.write(0x6000, 2)
    m.write(0x7000, 3)
    snap = m.snapshot()

    m2 = Ascii16Mapper(rom)
    m2.restore(snap)
    assert m2._banks == m._banks
    assert m2.read(0x4000) == 2
    assert m2.read(0x8000) == 3


# ---------------------------------------------------------------------------
# KonamiMapper
# ---------------------------------------------------------------------------

def test_konami_initial_banks() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    assert m.read(0x4000) == 0  # page 0 (fixed)
    assert m.read(0x6000) == 1  # page 1
    assert m.read(0x8000) == 2  # page 2
    assert m.read(0xA000) == 3  # page 3


def test_konami_switch_window_1() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x6000, 4)
    assert m.read(0x6000) == 4


def test_konami_window_0_is_fixed() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x4000, 5)
    assert m.read(0x4000) == 0  # still page 0


def test_konami_switch_window_2() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x8000, 6)
    assert m.read(0x8000) == 6


def test_konami_switch_window_3() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0xA000, 7)
    assert m.read(0xA000) == 7


def test_konami_bank_select_is_direct_when_value_is_in_range() -> None:
    # openMSX's two-tier resolution: a raw value already below the ROM's
    # real page count selects that page directly, unmasked.
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x6000, 7)
    assert m.read(0x6000) == 7


def test_konami_bank_select_out_of_range_value_masks_then_opens_bus() -> None:
    # The bank register's mask is a fixed 5 bits (31), independent of the
    # ROM's actual page count -- unlike a modulo wrap, it does not fold an
    # out-of-range value back onto a real page. 9 is not less than this
    # ROM's 8 pages, so it is masked with 31 (9 & 31 == 9, unchanged), and
    # 9 is still not less than 8, so it resolves to open bus rather than
    # aliasing to page 1 (9 % 8).
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x6000, 9)
    assert m.read(0x6000) == 0xFF


def test_konami_read_below_window_mirrors_windows_0_and_1() -> None:
    # A slot scan (e.g. BIOS RAM detection) can transiently address this
    # mapper's slot on a page it doesn't occupy (page 0, below 0x4000).
    # Real hardware mirrors windows 0/1 there rather than reading open bus
    # (openMSX RomKonami::bankSwitch).
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    assert m.read(0x0000) == m.read(0x4000)  # window 0's first byte, mirrored
    assert m.read(0x3FFF) == m.read(0x7FFF)  # window 1's last byte, mirrored


def test_konami_read_above_window_mirrors_windows_2_and_3() -> None:
    # 0xC000-0xFFFF mirrors windows 2/3 (openMSX RomKonami::bankSwitch).
    # Window 2's power-on bank is 2, out of range for a 1-page ROM, so this
    # also happens to resolve to open bus -- via the mirror, not a crash.
    small_rom = _rom_8k_pages(1)
    m = KonamiMapper(small_rom)
    assert m.read(0xC000) == 0xFF


def test_konami_snapshot_restore_roundtrips_banks() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x6000, 4)
    m.write(0xA000, 7)
    snap = m.snapshot()

    m2 = KonamiMapper(rom)
    m2.restore(snap)
    assert m2._banks == m._banks
    assert m2.read(0x6000) == 4
    assert m2.read(0xA000) == 7


def test_konami_restore_rejects_negative_bank() -> None:
    # A negative bank register would make _sync_window slice self.rom with
    # a negative start, which Python resolves by wrapping from the end of
    # the ROM instead of raising -- silently wrong content rather than a
    # clear failure. restore() must reject this instead of trusting the
    # save-state file (matching SCCICart.restore()'s existing precedent).
    m = KonamiMapper(_rom_8k_pages(8))
    with pytest.raises(ValueError):
        m.restore({"banks": [0, 1, 2, -1]})


def test_konami_restore_rejects_wrong_bank_count() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    with pytest.raises(ValueError):
        m.restore({"banks": [0, 1, 2]})


# ---------------------------------------------------------------------------
# MajutsushiMapper
# ---------------------------------------------------------------------------

def _maj(n_pages: int = 4) -> MajutsushiMapper:
    m = MajutsushiMapper(_rom_8k_pages(n_pages))
    cycle = 0

    def _get_cycle() -> int:
        return cycle

    m._get_cycle = _get_cycle
    return m


def test_majutsushi_dac_write_stored() -> None:
    m = _maj()
    m.write(0x5000, 0xFF)
    assert m._dac_events[-1][1] == 0xFF


def test_majutsushi_dac_write_masked_to_byte() -> None:
    m = _maj()
    m.write(0x5000, 0x1FF)
    assert m._dac_events[-1][1] == 0xFF


def test_majutsushi_dac_write_does_not_affect_bank() -> None:
    m = _maj()
    m.write(0x5000, 0x03)
    assert m._banks[0] == 0  # window 0 unchanged


def test_majutsushi_non_dac_write_switches_bank() -> None:
    m = _maj()
    m.write(0x6000, 2)
    assert m._banks[1] == 2


def test_majutsushi_dac_write_does_not_touch_flat_mirror() -> None:
    # DAC writes (0x5000-0x5FFF) bypass KonamiMapper.write() entirely, so
    # they must not resync (or otherwise disturb) the inherited flat mirror;
    # window 0 (fixed to page 0) should still read correctly afterwards.
    m = _maj()
    m.write(0x5000, 0x03)
    assert m.read(0x4000) == 0  # still page 0


def test_majutsushi_generate_samples_silence() -> None:
    m = _maj()
    # no events written → _last_dac == 0x80 → silence
    buf = m.generate_samples(4, 0, 100)
    for i in range(4):
        s = struct.unpack_from("<h", buf, i * 2)[0]
        assert s == 0


def test_majutsushi_generate_samples_max() -> None:
    m = _maj()
    m.write(0x5000, 0xFF)  # cycle=0, maps to sample 0 in frame [0,100)
    buf = m.generate_samples(2, 0, 100)
    s = struct.unpack_from("<h", buf, 0)[0]
    assert s == (0xFF - 0x80) * 256  # 127 * 256 = 32512


def test_majutsushi_generate_samples_min() -> None:
    m = _maj()
    m.write(0x5000, 0x00)
    buf = m.generate_samples(2, 0, 100)
    s = struct.unpack_from("<h", buf, 0)[0]
    assert s == (0x00 - 0x80) * 256  # -128 * 256 = -32768


def test_majutsushi_generate_samples_length() -> None:
    m = _maj()
    assert len(m.generate_samples(735, 0, 59659)) == 735 * 2


def test_majutsushi_generate_samples_events_cleared() -> None:
    m = _maj()
    m.write(0x5000, 0xC0)
    m.generate_samples(4, 0, 100)
    assert m._dac_events == []


def test_majutsushi_generate_samples_last_dac_persists() -> None:
    m = _maj()
    m.write(0x5000, 0xC0)
    m.generate_samples(4, 0, 100)
    # next frame: no new events → uses _last_dac from previous frame
    buf = m.generate_samples(2, 100, 200)
    s = struct.unpack_from("<h", buf, 0)[0]
    assert s == (0xC0 - 0x80) * 256


def test_majutsushi_generate_samples_mid_frame_write() -> None:
    # Write at cycle 50 in a [0, 100) frame with 4 samples.
    # Samples 0-1: threshold < 50 → silence (0x80).
    # Samples 2-3: threshold >= 50 → 0xFF.
    m = MajutsushiMapper(_rom_8k_pages(4))
    cycle_val = [50]
    m._get_cycle = lambda: cycle_val[0]
    m.write(0x5000, 0xFF)
    buf = m.generate_samples(4, 0, 100)
    # sample 0: threshold = 0*100//4 = 0 < 50 → silence
    assert struct.unpack_from("<h", buf, 0)[0] == 0
    # sample 2: threshold = 2*100//4 = 50 → value applies
    assert struct.unpack_from("<h", buf, 4)[0] == (0xFF - 0x80) * 256


def test_majutsushi_snapshot_restore_roundtrips_banks_and_last_dac() -> None:
    m = _maj()
    m.write(0x6000, 2)     # inherited KonamiMapper bank switch
    m.write(0x5000, 0xC0)  # DAC write
    m.generate_samples(4, 0, 100)  # consumes _dac_events, sets _last_dac
    snap = m.snapshot()

    m2 = MajutsushiMapper(_rom_8k_pages(4))
    m2.restore(snap)
    assert m2._banks == m._banks
    assert m2._last_dac == 0xC0
