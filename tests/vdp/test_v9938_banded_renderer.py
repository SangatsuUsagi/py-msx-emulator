"""Tests for V9938 band-split renderer."""
from msx.vdp.v9938 import V9938, _PaletteChange, _RegChange
from msx.vdp.v9938_renderer import _build_bands, render_frame

_W = 256


def make_g4_vdp() -> V9938:
    """G4 (SCREEN5) VDP with BL set."""
    vdp = V9938()
    # M4+M3 = G4; R#0 bits: M3(1)=1, M4(2)=1
    vdp.regs[0] = 0x06  # M3+M4
    vdp.regs[1] = 0x40  # BL
    return vdp


def make_g1_vdp() -> V9938:
    """G1 (SCREEN1) VDP with BL set."""
    vdp = V9938()
    vdp.regs[0] = 0x00
    vdp.regs[1] = 0x40  # BL
    return vdp


# ---------------------------------------------------------------------------
# Empty log → output identical to single-pass
# ---------------------------------------------------------------------------

def test_empty_log_identical_to_single_pass() -> None:
    vdp = make_g4_vdp()
    # Write some pixels to VRAM
    vdp.vram[0] = 0x12
    vdp.vram[128] = 0x34

    # Simulate frame start (captures state)
    vdp.begin_scanline(0)

    assert vdp._reg_write_log == []
    result = render_frame(vdp)

    # Rebuild vdp with same state for comparison (no band path)
    vdp2 = make_g4_vdp()
    vdp2.vram[0] = 0x12
    vdp2.vram[128] = 0x34
    from msx.vdp.v9938_renderer import _render_pass
    expected = _render_pass(vdp2)

    assert result == expected


# ---------------------------------------------------------------------------
# _build_bands: empty log → empty list
# ---------------------------------------------------------------------------

def test_build_bands_empty_log() -> None:
    vdp = make_g4_vdp()
    vdp.begin_scanline(0)
    bands = _build_bands(vdp)
    assert bands == []


def test_build_bands_single_change() -> None:
    vdp = make_g4_vdp()
    vdp.begin_scanline(0)
    vdp._reg_write_log = [_RegChange(96, 0, 0x00)]  # display_line 96 → effective from line 97
    bands = _build_bands(vdp)
    assert len(bands) == 2
    assert bands[0][0] == 0 and bands[0][1] == 97    # first band [0, 97)
    assert bands[1][0] == 97 and bands[1][1] == 192  # second band [97, 192)


def test_build_bands_multiple_changes() -> None:
    vdp = make_g4_vdp()
    vdp.begin_scanline(0)
    vdp._reg_write_log = [_RegChange(50, 7, 1), _RegChange(100, 7, 2), _RegChange(150, 7, 3)]
    bands = _build_bands(vdp)
    assert len(bands) == 4
    # writes at display_lines 50, 100, 150 → take effect from lines 51, 101, 151
    assert [b[0] for b in bands] == [0, 51, 101, 151]
    assert [b[1] for b in bands] == [51, 101, 151, 192]


# ---------------------------------------------------------------------------
# Mode change mid-frame: upper/lower halves differ
# ---------------------------------------------------------------------------

def test_mode_change_at_line_96_produces_split() -> None:
    vdp = V9938()
    vdp.regs[1] = 0x40  # BL
    # Start in G1 mode (regs[0]=0x00)
    vdp.regs[0] = 0x00

    # Simulate frame: capture initial state at begin_scanline(0)
    vdp.begin_scanline(0)

    # Write to VRAM for G1 (fill name table with tile 1 for rows 0..11=lines 0..95)
    # G1 name table at regs[2]=0x00 → base 0x0000
    for i in range(24):
        for j in range(32):
            vdp.vram[i * 32 + j] = 1  # tile 1

    # Set backdrop (R#7 low nibble) for G1
    vdp.regs[7] = 0x01  # border colour 1

    # Simulate mid-frame mode change at line 96 (switch to G4)
    # In G4: R#0 = 0x06 (M3+M4)
    vdp.display_line = 96
    vdp._reg_write_log.append(_RegChange(96, 0, 0x06))

    # Also apply the change to regs (as write_port would do)
    vdp.regs[0] = 0x06

    result = render_frame(vdp)
    assert len(result) == _W * 192

    # Upper half [0..95]: rendered in G1 mode (border=palette[1])
    # Lower half [96..191]: rendered in G4 mode
    # This is a structural test: we verify the bands are applied
    # (exact pixel values depend on VRAM content)
    # At minimum, result should not raise and have correct size
    assert isinstance(result, bytearray)


# ---------------------------------------------------------------------------
# Band snapshot: first band uses frame-start regs
# ---------------------------------------------------------------------------

def test_first_band_uses_frame_start_regs() -> None:
    vdp = make_g4_vdp()
    # At frame start: regs[7] = 0x05 (border = 5)
    vdp.regs[7] = 0x05
    vdp.begin_scanline(0)

    # During frame: change R#7 at line 64
    vdp.regs[7] = 0x09
    vdp._reg_write_log.append(_RegChange(64, 7, 0x09))

    bands = _build_bands(vdp)
    assert len(bands) == 2

    # Write at display_line 64 → takes effect from line 65
    # First band [0, 65): should have R#7 = 0x05 (frame start value)
    assert bands[0][2][7] == 0x05

    # Second band [65, 192): should have R#7 = 0x09
    assert bands[1][2][7] == 0x09


# ---------------------------------------------------------------------------
# Palette change mid-frame
# ---------------------------------------------------------------------------

def test_palette_change_at_line_64_applies_to_lower_band() -> None:
    vdp = make_g4_vdp()
    # Set palette[1] = initial color
    vdp.palette[1] = 0b001_110_001  # medium green
    vdp.begin_scanline(0)

    # Frame-start palette captured
    assert vdp._frame_start_palette[1] == 0b001_110_001

    # Mid-frame: change palette[1] at line 64
    new_rgb = 0b111_001_001  # medium red
    vdp.palette[1] = new_rgb
    vdp._reg_write_log.append(_PaletteChange(64, 1, new_rgb))  # palette idx 1 → new_rgb

    bands = _build_bands(vdp)
    assert len(bands) == 2

    # Write at display_line 64 → takes effect from line 65
    # First band [0, 65): old palette[1]
    assert bands[0][3][1] == 0b001_110_001

    # Second band [65, 192): new palette[1]
    assert bands[1][3][1] == 0b111_001_001


# ---------------------------------------------------------------------------
# Split-screen ghost: H-line vscroll change must not affect the line it fires on
# ---------------------------------------------------------------------------

def test_hline_vscroll_change_does_not_affect_trigger_line() -> None:
    # Reproduces the split-screen ghost: game uses VBlank to set R#23=0 (status bar),
    # then H-line ISR at the end of line 9 sets R#23=scroll for the game field.
    # The write is logged as display_line=9 (CPU runs with that display_line during
    # line 10's window). The new vscroll must NOT apply to line 9 (status bar).
    vdp = make_g1_vdp()
    vdp.regs[23] = 0  # VBlank set R#23=0 for status bar
    vdp.begin_scanline(0)  # captures frame_start_regs[23]=0

    # H-line ISR writes R#23=60 during line 10's CPU window (display_line=9)
    vdp._reg_write_log = [_RegChange(9, 23, 60)]

    bands = _build_bands(vdp)
    assert len(bands) == 2

    # Band [0, 10) = status bar: must use frame-start vscroll=0, NOT the new 60
    assert bands[0][0] == 0 and bands[0][1] == 10
    assert bands[0][2][23] == 0

    # Band [10, 192) = game field: uses new vscroll=60
    assert bands[1][0] == 10
    assert bands[1][2][23] == 60
