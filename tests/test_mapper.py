import struct

from msx.mapper import Ascii8Mapper, Ascii16Mapper, FlatMapper, KonamiMapper, MajutsushiMapper

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


def test_konami_page_wrap_around() -> None:
    rom = _rom_8k_pages(8)
    m = KonamiMapper(rom)
    m.write(0x6000, 9)  # 9 % 8 == 1
    assert m.read(0x6000) == 1


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
