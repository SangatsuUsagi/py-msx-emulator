from msx.mapper import Ascii8Mapper, Ascii16Mapper, FlatMapper, KonamiMapper

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


def test_flat_address_beyond_rom_returns_ff() -> None:
    cart = bytes(0x1000)
    m = FlatMapper(cart)
    assert m.read(0x5001) == 0xFF


# ---------------------------------------------------------------------------
# Ascii8Mapper
# ---------------------------------------------------------------------------

def test_ascii8_initial_banks() -> None:
    rom = _rom_8k_pages(8)
    m = Ascii8Mapper(rom)
    assert m.read(0x4000) == 0  # page 0
    assert m.read(0x6000) == 1  # page 1
    assert m.read(0x8000) == 2  # page 2
    assert m.read(0xA000) == 3  # page 3


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
    assert m.read(0x8000) == 1  # page 1


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
