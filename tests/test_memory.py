from msx.mapper import FlatMapper
from msx.memory import Memory

# Standard MSX1 boot slot layout:
#   page 0 (0x0000-0x3FFF): slot 0 = BIOS ROM
#   page 1 (0x4000-0x7FFF): slot 1 = cartridge
#   page 2 (0x8000-0xBFFF): slot 1 = cartridge
#   page 3 (0xC000-0xFFFF): slot 3 = RAM
# slot_register = 0b11_01_01_00 = 0xD4
_MSX1_SLOTS = 0xD4


def make_mem(rom: bytes | None = None, cartridge: bytes | None = None,
             slot_register: int = _MSX1_SLOTS) -> Memory:
    if rom is None:
        rom = bytes(32768)
    return Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(cartridge),
                  slot_register=slot_register)


# ---------------------------------------------------------------------------
# ROM region (slot 0)
# ---------------------------------------------------------------------------

def test_rom_read() -> None:
    rom = bytes([0xAB] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x0000) == 0xAB


def test_rom_read_only() -> None:
    rom = bytes([0] * 0x0100 + [0x12] + [0] * (32768 - 0x0101))
    mem = make_mem(rom=rom)
    mem.write(0x0100, 0xFF)
    assert mem.read(0x0100) == 0x12


# ---------------------------------------------------------------------------
# RAM region (slot 3, page 3)
# ---------------------------------------------------------------------------

def test_ram_round_trip() -> None:
    mem = make_mem()
    mem.write(0xC000, 0x42)
    assert mem.read(0xC000) == 0x42


def test_ram_mirror() -> None:
    mem = make_mem()
    mem.write(0xE000, 0x55)
    assert mem.read(0xE000) == 0x55


# ---------------------------------------------------------------------------
# 32 KB RAM: page 2 (0x8000-0xBFFF) via slot 3
# slot_register: page2(bits5:4)=11 → slot3, page3(bits7:6)=11 → slot3
# 0b11_11_01_00 = 0xF4
# ---------------------------------------------------------------------------
_PAGE2_AND_3_RAM = 0xF4  # page2+page3 = slot3 (RAM), page1=slot1(cart), page0=slot0(BIOS)


def test_ram_page2_round_trip() -> None:
    mem = make_mem(slot_register=_PAGE2_AND_3_RAM)
    mem.write(0x8000, 0xAB)
    assert mem.read(0x8000) == 0xAB


def test_ram_page2_top_round_trip() -> None:
    mem = make_mem(slot_register=_PAGE2_AND_3_RAM)
    mem.write(0xBFFF, 0xCD)
    assert mem.read(0xBFFF) == 0xCD


def test_ram_page2_and_page3_independent() -> None:
    # page2 RAM and page3 RAM are separate 16 KB banks
    mem = make_mem(slot_register=_PAGE2_AND_3_RAM)
    mem.write(0x8000, 0x11)
    mem.write(0xC000, 0x22)
    assert mem.read(0x8000) == 0x11
    assert mem.read(0xC000) == 0x22


def test_ram_page2_shadowed_by_cartridge() -> None:
    # With standard 0xD4 layout, page2 is slot1 (cartridge), not RAM.
    # Writing to 0x8000 goes to the mapper (no-op for FlatMapper); RAM is not affected.
    cart = bytes(32768)
    mem = make_mem(cartridge=cart, slot_register=_MSX1_SLOTS)  # page2 = slot1
    mem.write(0x8000, 0x99)
    # Switch page2 to slot3 to verify RAM was not written
    mem.slot_register = _PAGE2_AND_3_RAM
    assert mem.read(0x8000) == 0x00  # RAM untouched


# ---------------------------------------------------------------------------
# Cartridge region (slot 1, pages 1+2)
# ---------------------------------------------------------------------------

def test_cartridge_read() -> None:
    cart = bytes([0xCC] + [0] * 32767)
    mem = make_mem(cartridge=cart)
    assert mem.read(0x4000) == 0xCC


def test_empty_cartridge_returns_ff() -> None:
    mem = make_mem()
    assert mem.read(0x6000) == 0xFF


# ---------------------------------------------------------------------------
# Address wraparound
# ---------------------------------------------------------------------------

def test_address_wraps_at_16_bits() -> None:
    rom = bytes([0x77] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x10000) == mem.read(0x0000)


# ---------------------------------------------------------------------------
# Slot register (port 0xA8 state) — accessed via slot_register directly, as
# production PPI does (msx/ppi.py:22,43).
# ---------------------------------------------------------------------------

def test_slot_register_read_back() -> None:
    mem = make_mem()
    mem.slot_register = 0x09
    assert mem.slot_register == 0x09


def test_slot_register_dataclass_default_msx1_layout() -> None:
    # Default is standard MSX1 layout: page3=slot3(RAM), page1+2=slot1(cart)
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    assert mem.slot_register == 0xD4


# ---------------------------------------------------------------------------
# Slot dispatch — new tests
# ---------------------------------------------------------------------------

def test_page1_slot0_reads_bios_rom() -> None:
    rom = bytes(0x4001)
    rom = bytes([0] * 0x4000 + [0xBB])
    # slot_register: page 1 (bits 3:2) = slot 0 → reads BIOS ROM
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None), slot_register=0x00)
    assert mem.read(0x4000) == 0xBB


def test_page1_slot1_reads_cartridge() -> None:
    cart = bytes([0xCC] + [0] * 32767)
    # bits 3:2 = 0b01 → page 1 = slot 1 = cartridge
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(cart),
                 slot_register=0x04)
    assert mem.read(0x4000) == 0xCC


def test_slot2_open_bus_returns_ff() -> None:
    # bits 1:0 = 0b10 → page 0 = slot 2 = open bus
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0x02)
    assert mem.read(0x0000) == 0xFF


def test_slot2_open_bus_write_is_noop() -> None:
    # page 0 = slot 2
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0x02)
    mem.write(0x0000, 0xAB)  # should not raise


def test_initial_slot_register_all_pages_slot0() -> None:
    # Default slot_register=0: all pages → slot 0 (BIOS ROM)
    rom = bytes([0xAA] + [0] * 32767)
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None), slot_register=0x00)
    assert mem.read(0x0000) == 0xAA
    assert mem.read(0x4000) == 0x00  # ROM byte at 0x4000


# ---------------------------------------------------------------------------
# Mapper delegation tests
# ---------------------------------------------------------------------------

def test_slot1_read_delegates_to_mapper() -> None:
    from msx.mapper import Ascii8Mapper
    _PAGE_8K = 8192
    rom = bytes([(p if i == 0 else 0) for p in range(8) for i in range(_PAGE_8K)])
    mapper = Ascii8Mapper(rom)
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=mapper,
                 slot_register=_MSX1_SLOTS)
    assert mem.read(0x4000) == 0  # page 0


def test_slot1_write_delegates_to_mapper() -> None:
    from msx.mapper import Ascii8Mapper
    _PAGE_8K = 8192
    rom = bytes([(p if i == 0 else 0) for p in range(8) for i in range(_PAGE_8K)])
    mapper = Ascii8Mapper(rom)
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=mapper,
                 slot_register=_MSX1_SLOTS)
    mem.write(0x6000, 3)  # bank switch via mapper write
    assert mem.read(0x4000) == 3  # window 0 now shows page 3


def test_non_slot1_regions_unchanged() -> None:
    rom = bytes([0xAB] + [0] * 32767)
    mem = make_mem(rom=rom)
    assert mem.read(0x0000) == 0xAB  # BIOS ROM still works
    mem.write(0xC000, 0x77)
    assert mem.read(0xC000) == 0x77  # RAM still works


# ---------------------------------------------------------------------------
# Slot 2 with _mapper2
# page 1 (0x4000-0x7FFF) → slot 2: slot_register bits 3:2 = 0b10 → 0x08
# ---------------------------------------------------------------------------
_PAGE1_SLOT2 = 0x08  # page 0=slot0, page 1=slot2, page 2=slot0, page 3=slot0


def test_slot2_no_cartridge_returns_ff() -> None:
    # _mapper2 defaults to FlatMapper(None); slot 2 reads return 0xFF
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=_PAGE1_SLOT2)
    assert mem.read(0x4000) == 0xFF


def test_slot2_cartridge_read() -> None:
    # _mapper2 = FlatMapper with actual ROM; slot 2 reads return ROM data
    cart2 = b"\xAB" + b"\x00" * 32767
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None),
                 _mapper2=FlatMapper(cart2), slot_register=_PAGE1_SLOT2)
    assert mem.read(0x4000) == 0xAB


def test_slot1_and_slot2_independent() -> None:
    # page 1 → slot 1 (_mapper), page 2 → slot 2 (_mapper2)
    # slot_register: page0=slot0(00), page1=slot1(01), page2=slot2(10), page3=slot3(11)
    # = 0b11_10_01_00 = 0xE4
    cart1 = b"\x11" + b"\x00" * 32767
    cart2 = b"\x00" * 16384 + b"\x22" + b"\x00" * 16383  # byte at 0x8000 offset = index 0x4000
    mem = Memory(rom=bytes(32768), ram=bytearray(32768),
                 _mapper=FlatMapper(cart1), _mapper2=FlatMapper(cart2),
                 slot_register=0xE4)
    assert mem.read(0x4000) == 0x11   # slot 1 → _mapper
    assert mem.read(0x8000) == 0x22   # slot 2 → _mapper2
