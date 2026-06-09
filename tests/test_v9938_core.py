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
    assert vdp.read_port(0x99) == 0xA5


def test_status_read_clears_f_flag() -> None:
    vdp = V9938()
    vdp.status = 0x80
    result = vdp.read_port(0x99)
    assert result == 0x80
    assert vdp.status & 0x80 == 0


def test_status_read_preserves_non_f_bits() -> None:
    vdp = V9938()
    vdp.status = 0xFF
    vdp.read_port(0x99)
    assert vdp.status == 0x7F  # only bit 7 cleared


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
