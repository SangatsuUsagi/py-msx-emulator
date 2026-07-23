"""Tests for VDP.to_rgb24 palette-index → RGB24 conversion."""
from msx.vdp.v9938 import V9938, _PaletteChange, _RegChange
from msx.vdp.vdp import TMS9918A_PALETTE, VDP


def _rgb(buf: bytes, i: int) -> tuple[int, int, int]:
    return (buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2])


def test_v9938_indexed_uses_programmable_palette() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x00  # not SCREEN 8
    vdp.palette[5] = 0b010_011_111  # R2 G3 B7
    out = vdp.to_rgb24(bytearray([5]))
    assert _rgb(out, 0) == (2 * 255 // 7, 3 * 255 // 7, 7 * 255 // 7)


def test_v9938_indexed_default_palette_entry_8() -> None:
    """Default MSX2 palette entry 8 = R7 G1 B1 (not the old TMS approximation)."""
    vdp = V9938()
    out = vdp.to_rgb24(bytearray([8]))
    assert _rgb(out, 0) == (255, 255 // 7, 255 // 7)  # (255, 36, 36)


def test_v9938_palette_change_is_reflected() -> None:
    vdp = V9938()
    out0 = vdp.to_rgb24(bytearray([1]))
    vdp.palette[1] = 0b111_111_111  # reprogram index 1 to white
    out1 = vdp.to_rgb24(bytearray([1]))
    assert _rgb(out0, 0) == (0, 0, 0)
    assert _rgb(out1, 0) == (255, 255, 255)


def test_v9938_screen8_uses_grb332_direct() -> None:
    vdp = V9938()
    vdp.regs[0] = 0x0E  # SCREEN 8 (M3+M4+M5)
    out = vdp.to_rgb24(bytearray([0x00, 0xFF, 0x9D]))
    assert _rgb(out, 0) == (0, 0, 0)
    assert _rgb(out, 1) == (255, 255, 255)
    # 0x9D = G4 R7 B1 → R=255, G=255*4//7=145, B(2-bit=1)=255*2//7=72
    assert _rgb(out, 2) == (255, 255 * 4 // 7, 255 * 2 // 7)


def test_tms9918a_uses_fixed_palette() -> None:
    """The MSX1 (TMS9918A) VDP maps indices through the fixed hardware palette."""
    out = VDP().to_rgb24(bytearray([2]))
    assert _rgb(out, 0) == TMS9918A_PALETTE[2]


def test_v9938_populated_reg_write_log_does_not_break_conversion() -> None:
    """Regression: a non-empty _reg_write_log must not break to_rgb24.

    A _PaletteChange entry triggers the mid-frame banded palette path, which
    previously crashed accessing the old tuple/sentinel (entry[1] == -1) after
    the log became a tagged union of _RegChange/_PaletteChange records.
    """
    vdp = V9938()
    vdp.regs[0] = 0x06  # G4 / SCREEN 5
    vdp.regs[1] = 0x40  # BL
    vdp.begin_scanline(0)
    vdp._reg_write_log.append(_RegChange(96, 0, 0x06))
    vdp._reg_write_log.append(_PaletteChange(64, 1, 0b111_001_001))
    # render_frame now emits the constant 212-line output height, so to_rgb24
    # consumes a 212-row buffer regardless of display_height (192 here).
    buf = bytearray([1]) * (256 * 212)
    out = vdp.to_rgb24(buf)
    assert len(out) == len(buf) * 3
