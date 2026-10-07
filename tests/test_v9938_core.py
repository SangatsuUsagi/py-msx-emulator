"""Tests for msx.vdp.v9938.V9938 core: VRAM, ports, registers, palette."""
import pytest

from msx.vdp.v9938 import _MSX2_DEFAULT_PALETTE, V9938

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pending_palette_byte(vdp: V9938) -> int | None:
    """The first byte a pending port-0x9A pair will use, or None."""
    return vdp._first_byte if vdp._pal_pending else None


def _set_write_addr(vdp: V9938, addr: int) -> None:
    """Load a 14-bit write address via port 0x99 (assumes R#14=0)."""
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, 0x40 | ((addr >> 8) & 0x3F))


def _set_read_addr(vdp: V9938, addr: int) -> None:
    """Load a 14-bit read address via port 0x99 (assumes R#14=0)."""
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, (addr >> 8) & 0x3F)


def _set_reg(vdp: V9938, reg: int, value: int) -> None:
    """Write control register R#reg via port 0x99."""
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


_R0_GRAPHIC1 = 0x00
_R0_GRAPHIC4 = 0x06  # M4 and M3 set


# ---------------------------------------------------------------------------
# VRAM size
# ---------------------------------------------------------------------------

def test_vram_size_is_128kb() -> None:
    vdp = V9938()
    assert len(vdp.vram) == 131072


# ---------------------------------------------------------------------------
# Ports 0x9A/0x9B (palette / indirect register): write-only, read as open bus
# ---------------------------------------------------------------------------

def test_read_port_0x9a_palette_port_returns_0xff() -> None:
    vdp = V9938()
    assert vdp.read_port(0x9A) == 0xFF


def test_read_port_0x9b_indirect_register_port_returns_0xff() -> None:
    vdp = V9938()
    assert vdp.read_port(0x9B) == 0xFF


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
    _set_reg(vdp, 0, _R0_GRAPHIC4)
    _set_reg(vdp, 14, 0x07)
    _set_write_addr(vdp, 0x3FFF)  # addr = 0x1FFFF (last valid)
    vdp.write_port(0x98, 0xAA)    # writes to 0x1FFFF, addr wraps to 0x00000
    vdp.write_port(0x98, 0xBB)    # writes to 0x00000
    assert vdp.vram[0x1FFFF] == 0xAA
    assert vdp.vram[0x00000] == 0xBB
    assert vdp.regs[14] == 0


def test_port_98_address_wraps_inside_16kb_bank_in_graphic1() -> None:
    vdp = V9938()
    _set_reg(vdp, 0, _R0_GRAPHIC1)
    _set_write_addr(vdp, 0x3FFF)
    vdp.write_port(0x98, 0xAA)
    vdp.write_port(0x98, 0xBB)
    assert vdp.vram[0x3FFF] == 0xAA
    assert vdp.vram[0x0000] == 0xBB
    assert vdp.vram[0x4000] == 0x00
    assert vdp.regs[14] == 0


def test_port_98_address_wraps_inside_upper_bank_in_graphic1() -> None:
    vdp = V9938()
    _set_reg(vdp, 0, _R0_GRAPHIC1)
    _set_reg(vdp, 14, 0x01)
    _set_write_addr(vdp, 0x3FFF)  # addr = 0x7FFF
    vdp.write_port(0x98, 0xAA)
    vdp.write_port(0x98, 0xBB)
    assert vdp.vram[0x7FFF] == 0xAA
    assert vdp.vram[0x4000] == 0xBB
    assert vdp.regs[14] == 0x01


def test_port_98_address_carries_into_r14_in_graphic4() -> None:
    vdp = V9938()
    _set_reg(vdp, 0, _R0_GRAPHIC4)
    _set_write_addr(vdp, 0x3FFF)
    vdp.write_port(0x98, 0xAA)
    vdp.write_port(0x98, 0xBB)
    assert vdp.vram[0x3FFF] == 0xAA
    assert vdp.vram[0x4000] == 0xBB
    assert vdp.vram[0x0000] == 0x00
    assert vdp.regs[14] == 0x01


def test_address_setup_after_carry_uses_incremented_r14() -> None:
    # HB-F1XD VRAMWRAP.BAS, SCREEN 5: the A3 write lands at 0x4010.
    vdp = V9938()
    _set_reg(vdp, 0, _R0_GRAPHIC4)
    _set_write_addr(vdp, 0x3FFF)
    vdp.write_port(0x98, 0xA1)
    vdp.write_port(0x98, 0xA2)
    _set_write_addr(vdp, 0x0010)  # R#14 not written
    vdp.write_port(0x98, 0xA3)
    assert vdp.vram[0x4010] == 0xA3
    assert vdp.vram[0x0010] == 0x00


@pytest.mark.parametrize("r0", [0x04, 0x08, 0x0A, 0x0E])  # GRAPHIC 3 / TEXT 2, G5, G6, G7
def test_port_98_address_carries_in_every_m4_m5_mode(r0: int) -> None:
    vdp = V9938()
    _set_reg(vdp, 0, r0)
    _set_write_addr(vdp, 0x3FFF)
    vdp.write_port(0x98, 0xAA)
    vdp.write_port(0x98, 0xBB)
    assert vdp.vram[0x4000] == 0xBB
    assert vdp.regs[14] == 0x01


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
        mask = _V9938_REG_MASKS[reg] if reg < len(_V9938_REG_MASKS) else 0xFF
        assert vdp.regs[reg] == reg & mask


def test_port_99_register_write_ignores_28_to_31() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0xFF)
    vdp.write_port(0x99, 0x80 | 28)  # reg 28 — silently ignored
    assert len(vdp.regs) == 28  # list unchanged


def test_port_99_second_byte_0xc0_to_0xff_is_not_a_register_write() -> None:
    # Unlike an MSX1 VDP, a real V9938 performs no register write when the
    # second control byte has bits 7 and 6 both set (openMSX VDP.cc;
    # confirmed on real hardware, both an MSX1 VDP and a real V99x8 -- see
    # allium/v9938.allium's WriteControlPort).
    vdp = V9938()
    vdp.write_port(0x99, 0x42)
    vdp.write_port(0x99, 0xC0 | 3)
    assert vdp.regs[3] == 0


def test_port_99_second_byte_0xc0_to_0xff_leaves_address_pointer_unchanged() -> None:
    vdp = V9938()
    _set_write_addr(vdp, 0x1234)
    before = vdp._addr
    vdp.write_port(0x99, 0x42)
    vdp.write_port(0x99, 0xC0 | 3)
    assert vdp._addr == before


def test_port_99_second_byte_0xc0_to_0xff_still_clears_latch() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x42)
    vdp.write_port(0x99, 0xC0 | 3)  # no register write, but latch clears
    vdp.write_port(0x99, 0x99)      # fresh first byte
    vdp.write_port(0x99, 0x80 | 5)  # register select: reg 5
    assert vdp.regs[5] == 0x99


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
    # corrupting every later register write. (This froze a Konami SCC MegaROM
    # title: R#15 was stuck at 2 because its R#15:=0 writes never completed.)
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


def test_port_98_write_discards_stray_control_byte() -> None:
    vdp = V9938()
    _set_write_addr(vdp, 0x0100)
    vdp.write_port(0x99, 0x55)  # stray first byte
    vdp.write_port(0x98, 0xAB)
    vdp.write_port(0x99, 0x01)
    vdp.write_port(0x99, 0x80 | 5)
    assert vdp.regs[5] == 0x01


def test_port_98_read_discards_stray_control_byte() -> None:
    vdp = V9938()
    _set_read_addr(vdp, 0x0100)
    vdp.write_port(0x99, 0x55)  # stray first byte
    vdp.read_port(0x98)
    vdp.write_port(0x99, 0x01)
    vdp.write_port(0x99, 0x80 | 5)
    assert vdp.regs[5] == 0x01


def test_port_98_read_address_wraps_at_17_bits() -> None:
    vdp = V9938()
    _set_reg(vdp, 0, _R0_GRAPHIC4)
    _set_reg(vdp, 14, 0x07)
    vdp.vram[0x1FFFF] = 0xAA
    vdp.vram[0x00000] = 0xBB
    _set_read_addr(vdp, 0x3FFF)   # addr = 0x1FFFF (last valid)
    assert vdp.read_port(0x98) == 0xAA
    assert vdp.read_port(0x98) == 0xBB  # wrapped to 0x00000
    assert vdp.regs[14] == 0


@pytest.mark.parametrize(("r0", "second", "r14"), [
    (_R0_GRAPHIC1, 0xBB, 0x00),   # 0x3FFF -> 0x0000
    (_R0_GRAPHIC4, 0xCC, 0x01),   # 0x3FFF -> 0x4000, R#14 carried
])
def test_port_98_read_advances_like_write(r0: int, second: int, r14: int) -> None:
    vdp = V9938()
    _set_reg(vdp, 0, r0)
    vdp.vram[0x3FFF] = 0xAA
    vdp.vram[0x0000] = 0xBB
    vdp.vram[0x4000] = 0xCC
    _set_read_addr(vdp, 0x3FFF)   # the read-ahead itself crosses the boundary
    assert vdp.read_port(0x98) == 0xAA
    assert vdp.read_port(0x98) == second
    assert vdp.regs[14] == r14


def test_port_98_write_refills_the_read_buffer() -> None:
    # HB-F1XD RDBUF.BAS: read setup 0x3000, OUT 0x5A, IN twice -> 5A 33.
    vdp = V9938()
    for i in range(4):
        vdp.vram[0x3000 + i] = (i + 1) * 0x11
    _set_read_addr(vdp, 0x3000)
    vdp.write_port(0x98, 0x5A)  # lands at 0x3001
    assert vdp.read_port(0x98) == 0x5A
    assert vdp.read_port(0x98) == 0x33
    assert vdp.vram[0x3001] == 0x5A


def test_port_98_read_returns_buffered_byte_not_live_vram() -> None:
    vdp = V9938()
    vdp.vram[0x0300] = 0x11
    vdp.vram[0x0301] = 0x22
    _set_read_addr(vdp, 0x0300)
    vdp.vram[0x0300] = 0x99       # changed after the read-ahead buffer filled
    assert vdp.read_port(0x98) == 0x11
    assert vdp.read_buf == 0x22


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
    # a Konami SCC MegaROM title hangs on "Init ROM Slot").
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


# Ports 0x99 and 0x9A share one first-byte latch (0x9B does not touch it).

def test_register_write_inside_palette_pair_replaces_palette_first_byte() -> None:
    vdp = V9938()
    vdp.regs[16] = 0
    vdp.write_port(0x9A, 0x75)      # palette first byte
    vdp.write_port(0x99, 0x02)      # ISR-style register write: R#15 := 2
    vdp.write_port(0x99, 0x80 | 15)
    vdp.write_port(0x9A, 0x03)      # palette second byte
    assert vdp.regs[15] == 0x02
    entry = vdp.palette[0]
    assert (entry >> 6) & 7 == 0  # R from 0x02
    assert (entry >> 3) & 7 == 3  # G
    assert entry & 7 == 2          # B from 0x02
    assert vdp.regs[16] == 1


def test_palette_first_byte_inside_control_pair_replaces_low_byte() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x34)       # control first byte
    vdp.write_port(0x9A, 0x56)       # palette first byte
    vdp.write_port(0x99, 0x40 | 0x12)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x1256] == 0xAB
    assert _pending_palette_byte(vdp) is not None  # palette pair still pending


def test_port_9b_write_does_not_touch_shared_latch() -> None:
    vdp = V9938()
    vdp.regs[17] = 0x80 | 7          # R#7, no auto-increment
    vdp.write_port(0x99, 0x34)
    vdp.write_port(0x9B, 0x0F)
    vdp.write_port(0x99, 0x40 | 0x12)
    vdp.write_port(0x98, 0xAB)
    assert vdp.regs[7] == 0x0F
    assert vdp.vram[0x1234] == 0xAB


def test_port_9b_write_does_not_touch_pending_palette_pair() -> None:
    vdp = V9938()
    vdp.regs[16] = 0
    vdp.regs[17] = 0x80 | 7          # R#7, no auto-increment
    vdp.write_port(0x9A, 0x75)       # palette first byte
    vdp.write_port(0x9B, 0x0F)
    vdp.write_port(0x9A, 0x03)       # palette second byte
    assert vdp.regs[7] == 0x0F
    assert vdp.latch is None
    entry = vdp.palette[0]
    assert (entry >> 6) & 7 == 7  # R from 0x75
    assert (entry >> 3) & 7 == 3  # G
    assert entry & 7 == 5          # B from 0x75


def test_palette_first_byte_without_control_pair_leaves_control_latch_empty() -> None:
    vdp = V9938()
    vdp.write_port(0x9A, 0x75)
    assert vdp.latch is None
    vdp.write_port(0x99, 0x01)
    vdp.write_port(0x99, 0x80 | 5)
    assert vdp.regs[5] == 0x01


def test_control_first_byte_without_palette_pair_leaves_palette_latch_empty() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x34)
    assert _pending_palette_byte(vdp) is None


def test_first_control_byte_leaves_vram_address_unchanged() -> None:
    # Unlike the TMS9918A, the V9938 moves the pointer only when an address
    # setup completes.
    vdp = V9938()
    _set_write_addr(vdp, 0x1234)
    vdp.write_port(0x99, 0x56)
    assert vdp.addr == 0x1234
    vdp.read_port(0x99)              # drop the stray byte
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x1234] == 0xAB


def _pend_control_and_palette_pairs(vdp: V9938) -> None:
    """Leave both a port-0x99 pair and a port-0x9A pair half-written (0x75)."""
    vdp.regs[16] = 0
    vdp.write_port(0x99, 0x34)       # control first byte
    vdp.write_port(0x9A, 0x75)       # palette first byte, replaces 0x34
    assert vdp.latch == 0x75
    assert _pending_palette_byte(vdp) == 0x75


def _assert_palette_pair_completes_from_0x75(vdp: V9938) -> None:
    vdp.write_port(0x9A, 0x03)       # palette second byte
    entry = vdp.palette[0]
    assert (entry >> 6) & 7 == 7  # R
    assert (entry >> 3) & 7 == 3  # G
    assert entry & 7 == 5          # B
    assert vdp.regs[16] == 1


def test_status_read_clears_control_pair_but_keeps_palette_pair_pending() -> None:
    vdp = V9938()
    _pend_control_and_palette_pairs(vdp)
    vdp.read_port(0x99)
    assert vdp.latch is None
    assert _pending_palette_byte(vdp) == 0x75
    _assert_palette_pair_completes_from_0x75(vdp)
    vdp.write_port(0x99, 0x01)       # fresh pair after the palette completes
    vdp.write_port(0x99, 0x80 | 5)
    assert vdp.regs[5] == 0x01


def test_port_98_write_clears_control_pair_but_keeps_palette_pair_pending() -> None:
    vdp = V9938()
    _pend_control_and_palette_pairs(vdp)
    vdp.write_port(0x98, 0xAB)
    assert vdp.latch is None
    assert _pending_palette_byte(vdp) == 0x75
    _assert_palette_pair_completes_from_0x75(vdp)


def test_port_98_read_clears_control_pair_but_keeps_palette_pair_pending() -> None:
    vdp = V9938()
    _pend_control_and_palette_pairs(vdp)
    vdp.read_port(0x98)
    assert vdp.latch is None
    assert _pending_palette_byte(vdp) == 0x75
    _assert_palette_pair_completes_from_0x75(vdp)


def test_palette_second_byte_leaves_pending_control_pair_intact() -> None:
    vdp = V9938()
    vdp.regs[16] = 0
    vdp.write_port(0x9A, 0x75)       # palette first byte
    vdp.write_port(0x99, 0x34)       # control first byte, replaces 0x75
    vdp.write_port(0x9A, 0x03)       # palette second byte completes the pair
    assert _pending_palette_byte(vdp) is None
    assert vdp.latch == 0x34
    entry = vdp.palette[0]
    assert (entry >> 6) & 7 == 3  # R from 0x34
    assert (entry >> 3) & 7 == 3  # G
    assert entry & 7 == 4          # B from 0x34
    vdp.write_port(0x99, 0x40 | 0x12)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x1234] == 0xAB


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
# interlaced / even_odd_enabled (R#9 IL/EO)
# ---------------------------------------------------------------------------

def test_interlaced_false_by_default() -> None:
    vdp = V9938()
    assert vdp.interlaced is False


def test_interlaced_true_when_il_set() -> None:
    vdp = V9938()
    vdp.regs[9] = 0x08  # IL bit
    assert vdp.interlaced is True


def test_even_odd_enabled_false_by_default() -> None:
    vdp = V9938()
    assert vdp.even_odd_enabled is False


def test_even_odd_enabled_true_when_eo_set() -> None:
    vdp = V9938()
    vdp.regs[9] = 0x04  # EO bit
    assert vdp.even_odd_enabled is True


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


# ---------------------------------------------------------------------------
# R#14 is the top 3 bits of the VRAM address pointer (writing it relocates it)
# ---------------------------------------------------------------------------

def test_r14_write_relocates_vram_pointer() -> None:
    """Setting the low address then writing R#14 must move the live pointer's
    bank (A14-A16), so a following port-0x98 write lands in the new bank."""
    vdp = V9938()
    vdp.write_port(0x99, 0x00)          # address low byte = 0
    vdp.write_port(0x99, 0x40)          # write-mode, A8-A13 = 0 -> _addr = 0x00000
    assert vdp.addr == 0x00000
    vdp.write_port(0x99, 6)             # data for R#14
    vdp.write_port(0x99, 0x80 | 14)     # R#14 = 6 -> pointer bank 6
    assert vdp.addr == (6 << 14)        # 0x18000
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x18000] == 0xAB


def test_r14_preserves_low_14_bits() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x34)          # low
    vdp.write_port(0x99, 0x40 | 0x12)   # write-mode, A8-A13 = 0x12 -> _addr=0x1234
    assert vdp.addr == 0x1234
    vdp.write_port(0x99, 3)
    vdp.write_port(0x99, 0x80 | 14)     # R#14 = 3
    assert vdp.addr == ((3 << 14) | 0x1234)


def test_r14_via_indirect_relocates_pointer() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x00)
    vdp.write_port(0x99, 0x40)          # _addr = 0
    vdp.write_port(0x99, 14)
    vdp.write_port(0x99, 0x80 | 17)     # R#17 -> point R#14
    vdp.write_port(0x9B, 5)             # indirect write R#14 = 5
    assert vdp.addr == (5 << 14)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def test_reset_restores_registers_status_palette_and_address_state() -> None:
    vdp = V9938()
    vdp.regs = [0xFF] * 28
    vdp.status = 0xFF
    vdp.palette = [0x1FF] * 16
    vdp.addr = 0x12345
    vdp._first_byte = 0x33
    vdp._ctrl_pending = True
    vdp._pal_pending = True
    vdp.read_buf = 0x99
    vdp.irq = True
    vdp.display_line = 100
    vdp.regs[9] = 0x80  # LN set, so display_height would read 212 pre-reset

    vdp.reset()

    power_on = [0] * 28
    power_on[21] = 0x3B                # colour-burst presets (V9938 data book)
    power_on[22] = 0x05
    assert vdp.regs == power_on
    assert vdp.status == 0
    assert vdp.palette == list(_MSX2_DEFAULT_PALETTE)
    assert vdp.addr == 0
    assert vdp.latch is None
    assert _pending_palette_byte(vdp) is None
    assert vdp.read_buf == 0
    assert vdp.irq is False
    assert vdp.display_line == 0
    assert vdp.display_height == 192


def test_reset_retains_vram() -> None:
    vdp = V9938()
    vdp.vram[0x10000] = 0xAB

    vdp.reset()

    assert vdp.vram[0x10000] == 0xAB


def test_reset_clears_current_field_but_preserves_frame_count() -> None:
    """reset() zeroes the whole S#2 byte (clearing the EO current-field bit),
    but deliberately does NOT reset frame_count -- the field-parity counter
    keeps running across a reset, and current_field is simply rewritten from
    frame parity on the next render."""
    vdp = V9938()
    vdp._status2 = 0x02  # simulate an odd field having just been rendered
    vdp.frame_count = 5

    vdp.reset()  # also zeroes regs, including R#15 (status register pointer)

    vdp.regs[15] = 2  # select S#2 for port 0x99 reads
    assert vdp.read_port(0x99) & 0x02 == 0  # current_field cleared
    assert vdp.frame_count == 5  # NOT reset
def test_r15_upper_bits_are_dropped_via_port_99() -> None:
    vdp = V9938()
    vdp.status = 0x80                  # F set
    vdp.write_port(0x99, 0x10)
    vdp.write_port(0x99, 0x80 | 15)
    assert vdp.regs[15] == 0x00
    assert vdp.read_port(0x99) & 0x80  # S#0 read returns F ...
    assert not (vdp.status & 0x80)     # ... and clears it


def test_r15_upper_bits_are_dropped_via_port_9b() -> None:
    vdp = V9938()
    vdp.regs[17] = 0x80 | 15           # R#15, no auto-increment
    vdp.write_port(0x9B, 0x12)
    assert vdp.regs[15] == 0x02


@pytest.mark.parametrize("value", range(0x0A, 0x10))
def test_r15_nonexistent_status_registers_read_ff(value: int) -> None:
    vdp = V9938()
    vdp.status = 0x80
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | 15)
    assert vdp.read_port(0x99) == 0xFF
    assert vdp.status & 0x80           # S#0 was not read


def test_r15_masked_value_still_selects_status_register() -> None:
    vdp = V9938()
    vdp.status = 0x80
    vdp.write_port(0x99, 0x1A)         # & 0x0F -> S#10
    vdp.write_port(0x99, 0x80 | 15)
    assert vdp.regs[15] == 0x0A
    assert vdp.read_port(0x99) == 0xFF
    assert vdp.status & 0x80


# Control registers R#0-R#23 keep only their implemented bits (the data book
# shows the rest as 0); R#21/R#22 are preset to the colour-burst defaults.

_V9938_REG_MASKS = (
    0x7E, 0x7F, 0x7F, 0xFF, 0x3F, 0xFF, 0x3F, 0xFF,
    0xFB, 0xBF, 0x07, 0x03, 0xFF, 0xFF, 0x07, 0x0F,
    0x0F, 0xBF, 0xFF, 0xFF, 0x3F, 0x3F, 0x3F, 0xFF,
)


def test_every_control_register_keeps_only_implemented_bits() -> None:
    for reg in range(24):
        vdp = V9938()
        vdp.write_port(0x99, 0xFF)
        vdp.write_port(0x99, 0x80 | reg)
        assert vdp.regs[reg] == _V9938_REG_MASKS[reg], f"R#{reg}"


def test_port_9b_masks_register_values_the_same_way() -> None:
    vdp = V9938()
    vdp.regs[17] = 0x80 | 10           # R#10, no auto-increment
    vdp.write_port(0x9B, 0xFF)
    assert vdp.regs[10] == 0x07


def test_colour_burst_registers_power_on_and_reset_values() -> None:
    vdp = V9938()
    expected = [0] * 28
    expected[21] = 0x3B
    expected[22] = 0x05
    assert vdp.regs == expected
    vdp.regs = [0xFF] * 28
    vdp.reset()
    assert vdp.regs == expected


def test_port_9b_tracer_records_the_byte_written_not_the_stored_value() -> None:
    from msx.vdp.tracer import Tracer

    seen: list[int] = []

    class _Rec(Tracer):
        def port9b_write(self, pc, cycle, value, r17, frame):  # type: ignore[override]
            seen.append(value)

    vdp = V9938()
    vdp.tracer = _Rec.__new__(_Rec)
    vdp.regs[17] = 0x80 | 10           # R#10 (mask 0x07), no auto-increment
    vdp.write_port(0x9B, 0xFF)
    assert vdp.regs[10] == 0x07
    assert seen == [0xFF]
