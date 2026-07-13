"""Tests for msx.vdp.v9938.V9938 core: VRAM, ports, registers, palette."""
from msx.vdp.v9938 import V9938

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_write_addr(vdp: V9938, addr: int) -> None:
    """Load a 14-bit write address via port 0x99 (assumes R#14=0)."""
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, 0x40 | ((addr >> 8) & 0x3F))


def _set_read_addr(vdp: V9938, addr: int) -> None:
    """Load a 14-bit read address via port 0x99 (assumes R#14=0)."""
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, (addr >> 8) & 0x3F)


# ---------------------------------------------------------------------------
# VRAM size
# ---------------------------------------------------------------------------

def test_vram_size_is_128kb() -> None:
    vdp = V9938()
    assert len(vdp.vram) == 131072


# ---------------------------------------------------------------------------
# Port 0x98: VRAM read/write with address auto-increment
# ---------------------------------------------------------------------------

def test_port_98_write_stores_byte() -> None:
    vdp = V9938()
    _set_write_addr(vdp, 0x100)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x100] == 0xAB


def test_port_98_write_increments_address() -> None:
    vdp = V9938()
    _set_write_addr(vdp, 0x000)
    vdp.write_port(0x98, 0x11)
    vdp.write_port(0x98, 0x22)
    assert vdp.vram[0x000] == 0x11
    assert vdp.vram[0x001] == 0x22


def test_port_98_read_returns_vram_byte() -> None:
    vdp = V9938()
    vdp.vram[0x050] = 0xCD
    _set_read_addr(vdp, 0x050)
    assert vdp.read_port(0x98) == 0xCD


def test_port_98_read_increments_address() -> None:
    vdp = V9938()
    vdp.vram[0x010] = 0x11
    vdp.vram[0x011] = 0x22
    _set_read_addr(vdp, 0x010)
    assert vdp.read_port(0x98) == 0x11
    assert vdp.read_port(0x98) == 0x22


def test_port_98_address_wraps_at_17_bits() -> None:
    vdp = V9938()
    vdp.regs[14] = 0x07
    _set_write_addr(vdp, 0x3FFF)  # addr = 0x1FFFF (last valid)
    vdp.write_port(0x98, 0xAA)    # writes to 0x1FFFF, addr wraps to 0x00000
    vdp.write_port(0x98, 0xBB)    # writes to 0x00000
    assert vdp.vram[0x1FFFF] == 0xAA
    assert vdp.vram[0x00000] == 0xBB


# ---------------------------------------------------------------------------
# Port 0x99: register write
# ---------------------------------------------------------------------------

def test_port_99_register_write() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x53)
    vdp.write_port(0x99, 0x80 | 7)
    assert vdp.regs[7] == 0x53


def test_port_99_register_write_all_28() -> None:
    vdp = V9938()
    for reg in range(28):
        vdp.write_port(0x99, reg)
        vdp.write_port(0x99, 0x80 | reg)
        assert vdp.regs[reg] == reg


def test_port_99_register_write_ignores_28_to_31() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0xFF)
    vdp.write_port(0x99, 0x80 | 28)  # reg 28 — silently ignored
    assert len(vdp.regs) == 28  # list unchanged


# ---------------------------------------------------------------------------
# Port 0x99: address latch
# ---------------------------------------------------------------------------

def test_port_99_sets_write_address() -> None:
    vdp = V9938()
    _set_write_addr(vdp, 0x1234)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x1234] == 0xAB


def test_port_99_read_resets_address_latch() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x55)  # first byte of address latch
    vdp.read_port(0x99)          # reading status resets latch
    # Now a fresh two-byte sequence should work
    vdp.write_port(0x99, 0x01)
    vdp.write_port(0x99, 0x80 | 5)  # reg 5 = 0x01
    assert vdp.regs[5] == 0x01


def test_port_99_read_resets_latch_when_status_reg_not_zero() -> None:
    # Reading the status port resets the write latch regardless of which status
    # register R#15 selects. Resetting it only for S#0 lets a half-finished
    # write (interrupted between its two bytes) desync the latch permanently,
    # corrupting every later register write. (This froze Space Manbow: R#15 was
    # stuck at 2 because its R#15:=0 writes never completed.)
    vdp = V9938()
    vdp.write_port(0x99, 0x02)
    vdp.write_port(0x99, 0x80 | 15)  # R#15 = 2 → status reads select S#2
    assert vdp.regs[15] == 2

    vdp.write_port(0x99, 0x55)  # stray first byte left in the latch
    vdp.read_port(0x99)          # read S#2 — must still reset the latch
    # A fresh two-byte register write must now land correctly.
    vdp.write_port(0x99, 0x00)
    vdp.write_port(0x99, 0x80 | 15)  # R#15 = 0
    assert vdp.regs[15] == 0


def test_port_99_17bit_address_via_r14() -> None:
    vdp = V9938()
    vdp.regs[14] = 0x04            # high bits: 4 → addr base 0x10000
    _set_write_addr(vdp, 0x0000)   # low 14 bits = 0 → addr = 0x10000
    vdp.write_port(0x98, 0x77)
    assert vdp.vram[0x10000] == 0x77


# ---------------------------------------------------------------------------
# Port 0x99: status read clears F flag
# ---------------------------------------------------------------------------

def test_status_read_returns_current_status() -> None:
    vdp = V9938()
    vdp.status = 0xA5
    # The returned byte keeps the current F/5S/C bits and OR-s the idle
    # last-sprite number (0x1F) into bits 4-0 (the stored bits are cleared
    # by the read; see test_status_read_clears_f_5s_and_c).
    assert vdp.read_port(0x99) == 0xBF  # (0xA5 & 0xE0) | 0x1F


def test_status_read_clears_f_flag() -> None:
    vdp = V9938()
    vdp.status = 0x80
    result = vdp.read_port(0x99)
    assert result == 0x9F  # F set + idle last-sprite number (0x1F)
    assert vdp.status & 0x80 == 0


def test_status_read_clears_f_5s_and_c() -> None:
    # Real V9938: an S#0 read clears F (bit7), 5S (bit6) and C (bit5) together
    # (mask ~0xE0). Previously only F was cleared, leaving 5S/C stuck set.
    vdp = V9938()
    vdp.status = 0xFF
    vdp.read_port(0x99)
    assert vdp.status == 0x1F  # F, 5S and C cleared; low sprite-number bits kept


def test_status_s0_reports_idle_sprite_number() -> None:
    # Real V9938 S#0 bits 4-0 hold the 5th/last sprite number; the idle value is
    # 31 (0x1F). MSX2 C-BIOS cartridge boot reads S#0 and feeds it into its
    # cartridge-scan loop counter, so reporting 0 there stalls boot (e.g.
    # King's Valley II hangs on "Init ROM Slot").
    vdp = V9938()
    vdp.status = 0x00
    assert vdp.read_port(0x99) & 0x1F == 0x1F


# ---------------------------------------------------------------------------
# Palette: port 0x9A
# ---------------------------------------------------------------------------

def test_palette_write_via_port_9a() -> None:
    vdp = V9938()
    vdp.regs[16] = 0
    vdp.write_port(0x9A, 0x75)  # R=7, B=5
    vdp.write_port(0x9A, 0x03)  # G=3
    entry = vdp.palette[0]
    assert (entry >> 6) & 7 == 7  # R
    assert (entry >> 3) & 7 == 3  # G
    assert entry & 7 == 5          # B


def test_palette_auto_increment() -> None:
    vdp = V9938()
    vdp.regs[16] = 0
    vdp.write_port(0x9A, 0x00)
    vdp.write_port(0x9A, 0x00)
    assert vdp.regs[16] == 1


def test_palette_wrap_at_16() -> None:
    vdp = V9938()
    vdp.regs[16] = 15
    vdp.write_port(0x9A, 0x00)
    vdp.write_port(0x9A, 0x00)
    assert vdp.regs[16] == 0


def test_palette_partial_write_not_committed() -> None:
    vdp = V9938()
    original = vdp.palette[0]
    vdp.write_port(0x9A, 0xFF)  # only first byte written — not committed yet
    assert vdp.palette[0] == original


# ---------------------------------------------------------------------------
# Initial palette
# ---------------------------------------------------------------------------

def test_initial_palette_entry_15_is_white() -> None:
    vdp = V9938()
    entry = vdp.palette[15]
    assert (entry >> 6) & 7 == 7  # R=7
    assert (entry >> 3) & 7 == 7  # G=7
    assert entry & 7 == 7          # B=7


def test_initial_palette_entry_0_is_black() -> None:
    vdp = V9938()
    assert vdp.palette[0] == 0  # transparent/black


def test_initial_palette_has_16_entries() -> None:
    vdp = V9938()
    assert len(vdp.palette) == 16


def test_initial_palette_is_msx2_default_not_tms() -> None:
    """The power-on palette must be the V9938 (MSX2) default, not the TMS9918A
    approximation. Check the entries that differ between the two."""
    vdp = V9938()

    def rgb(i: int) -> tuple[int, int, int]:
        p = vdp.palette[i]
        return ((p >> 6) & 7, (p >> 3) & 7, p & 7)

    assert rgb(5) == (2, 3, 7)   # light blue  (TMS was 2,2,7)
    assert rgb(7) == (2, 6, 7)   # cyan        (TMS was 2,7,7)
    assert rgb(8) == (7, 1, 1)   # medium red  (TMS was 7,2,2)
    assert rgb(9) == (7, 3, 3)   # light red   (TMS was 7,4,4)
    assert rgb(11) == (6, 6, 4)  # light yellow(TMS was 7,7,4)
    assert rgb(12) == (1, 4, 1)  # dark green  (TMS was 1,5,1)


# ---------------------------------------------------------------------------
# display_height
# ---------------------------------------------------------------------------

def test_display_height_default_192() -> None:
    vdp = V9938()
    assert vdp.display_height == 192


def test_display_height_212_when_ln_set() -> None:
    vdp = V9938()
    vdp.regs[9] = 0x80  # LN bit
    assert vdp.display_height == 212


def test_display_height_back_to_192_when_ln_cleared() -> None:
    vdp = V9938()
    vdp.regs[9] = 0x80
    vdp.regs[9] = 0x00
    assert vdp.display_height == 192


def test_display_width_default_256() -> None:
    vdp = V9938()
    assert vdp.display_width == 256


def test_display_width_512_for_screen6() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x08  # M5 only → SCREEN 6 (G5)
    assert vdp.display_width == 512


def test_display_width_512_for_screen7() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x0A  # M3+M5 → SCREEN 7 (G6)
    assert vdp.display_width == 512


def test_display_width_256_for_screen8() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x0E  # M3+M4+M5 → SCREEN 8 (G7), 256 wide 8bpp
    assert vdp.display_width == 256


# ---------------------------------------------------------------------------
# Port 0x9B: indirect register access via the R#17 pointer (auto-increment)
# ---------------------------------------------------------------------------

def _set_r17(vdp: V9938, value: int) -> None:
    """Point R#17 at a register (bit7 set = no auto-increment)."""
    vdp.write_port(0x99, value & 0xFF)
    vdp.write_port(0x99, 0x80 | 17)


def test_indirect_write_auto_increments_r17() -> None:
    vdp = V9938()
    _set_r17(vdp, 0)  # point R#0, auto-increment on
    for val in (0x00, 0x20, 0x06, 0x80):
        vdp.write_port(0x9B, val)
    assert vdp.regs[0] == 0x00
    assert vdp.regs[1] == 0x20
    assert vdp.regs[2] == 0x06
    assert vdp.regs[3] == 0x80


def test_indirect_write_through_r17_continues_to_r18() -> None:
    """Writing a register table across R#17 must keep advancing (17 -> 18),
    not restart from the value just stored into R#17. Regression: the MSX2 BIOS
    writes R#0-R#23 in one auto-incrementing block; storing R#17=0 mid-block
    used to reset the pointer to 1 and clobber R#1+."""
    vdp = V9938()
    _set_r17(vdp, 15)  # point R#15, auto-increment on
    # R#15, R#16, R#17(=0), R#18, R#19
    for val in (0x01, 0x02, 0x00, 0x44, 0x55):
        vdp.write_port(0x9B, val)
    assert vdp.regs[18] == 0x44
    assert vdp.regs[19] == 0x55


def test_indirect_write_no_increment_when_aii_set() -> None:
    vdp = V9938()
    # point R#2 with AII (bit7) set -> no auto-increment: all writes hit R#2
    vdp.write_port(0x99, 0x80 | 2)
    vdp.write_port(0x99, 0x80 | 17)
    for val in (0x11, 0x22, 0x33):
        vdp.write_port(0x9B, val)
    assert vdp.regs[2] == 0x33
    assert vdp.regs[3] == 0x00
