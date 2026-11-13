"""V9938 command-engine obligations from allium/v9938_command_engine.allium.

Complements tests/vdp/test_v9938_commands.py, which already covers most of the
spec's rules (dispatch, STOP, PSET/POINT, SRCH, LINE, LMMV/LMMM/HMMV/HMMM/YMMM,
the HMMC/LMMC/LMCM transfers and the CE timer). This file adds only what that
file does not reach:

* obligations no test exercised yet (reserved command codes, per-mode dot
  geometry, DIX/DIY, NOT/NX=0 handling, the TR handshake, YMMM timing, S#9
  packing, command-engine reset state);
* the spec's invariants, as property-based tests;
* the behaviour that used to be listed under the spec's "Deviations": screen-
  edge clipping, the undefined logical operations, dot-unit LMCM, the BD
  lifecycle, POINT staging its result in CLR, whole-byte iteration in the
  byte-unit commands, the 10-bit NX decode, commands being confined to the
  bitmap modes, TR surviving completion, the LINE error term and its edge
  terminations, and MXS/MXD.
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from msx.vdp.v9938 import (
    _ARG_DIY,
    _ARG_MAJ,
    _ARG_MXD,
    _ARG_MXS,
    _COST_BLANK,
    _COST_SPRITES_OFF,
    _COST_SPRITES_ON,
    _HMMM_COST16,
    _HMMV_COST16,
    _LMMM_COST16,
    _LMMV_COST16,
    _TR_REASSERT_CYCLES,
    _YMMM_COST16,
    V9938,
)

# Screen-mode register values for the four command-capable modes, plus one
# mode in which the V9938 does not execute commands at all.
_G4 = 0x06  # SCREEN 5: 256 dots, 4 bpp, 2 dots/byte
_G5 = 0x08  # SCREEN 6: 512 dots, 2 bpp, 4 dots/byte
_G6 = 0x0A  # SCREEN 7: 512 dots, 4 bpp, 2 dots/byte
_G7 = 0x0C  # SCREEN 8: 256 dots, 8 bpp, 1 dot/byte

_CE = 0x01
_BD = 0x10
_TR = 0x80


def _make_vdp(r0: int = _G4, r1: int = 0x60) -> V9938:
    vdp = V9938()
    vdp.regs[0] = r0
    vdp.regs[1] = r1  # BL=1 (display enabled)
    return vdp


def _write_reg(vdp: V9938, reg: int, value: int) -> None:
    vdp.write_port(0x99, value & 0xFF)
    vdp.write_port(0x99, 0x80 | (reg & 0x3F))


def _dispatch(vdp: V9938, cmd_code: int, log: int = 0,
              sx: int = 0, sy: int = 0, dx: int = 0, dy: int = 0,
              nx: int = 1, ny: int = 1, clr: int = 0, arg: int = 0,
              nx_high: int | None = None) -> None:
    """Load R#32-R#45 then dispatch `cmd_code` by writing R#46.

    nx_high overrides the R#41 byte so the 9-bit / 10-bit NX decoding can be
    exercised directly.
    """
    _write_reg(vdp, 17, 32)  # R#17 pointer at R#32, auto-increment on
    values = [
        sx & 0xFF, (sx >> 8) & 0x01,
        sy & 0xFF, (sy >> 8) & 0x03,
        dx & 0xFF, (dx >> 8) & 0x01,
        dy & 0xFF, (dy >> 8) & 0x03,
        nx & 0xFF, (nx >> 8) & 0x03 if nx_high is None else nx_high,
        ny & 0xFF, (ny >> 8) & 0x03,
        clr & 0xFF, arg & 0xFF,
    ]
    for val in values:
        vdp.write_port(0x9B, val)
    _write_reg(vdp, 17, 0x80 | 46)  # AII=1, pointer at R#46
    vdp.write_port(0x9B, ((cmd_code & 0xF) << 4) | (log & 0xF))


def _read_status(vdp: V9938, index: int) -> int:
    vdp.regs[15] = index
    return vdp.read_port(0x99)


def _write_data(vdp: V9938, value: int) -> None:
    """Send one CPU transfer byte the hardware way: R#17 -> R#44, then 0x9B."""
    _write_reg(vdp, 17, 0x80 | 44)
    vdp.write_port(0x9B, value & 0xFF)


# ---------------------------------------------------------------------------
# ResetCommandEngine
# ---------------------------------------------------------------------------

def test_reset_clears_command_engine_state() -> None:
    """tests/test_v9938_core.py covers reset() for the display registers; the
    command engine's own registers and status are part of the same rule."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xF, dx=0, dy=0, nx=64, ny=8, clr=0xAB)  # HMMC, left active
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0xAB)               # SRCH, sets BD/S#8

    vdp.reset()

    assert vdp.cmd_regs == [0] * 15  # includes CLR, which S#7 reads back
    assert vdp._status2 == 0        # CE, TR and BD all clear
    assert vdp._status8 == 0
    assert vdp._status9 == 0
    assert not vdp._cmd_active
    assert vdp._cmd_remaining == 0
    assert vdp._cmd_code == 0


def test_s9_reads_0xfe_before_any_srch() -> None:
    vdp = _make_vdp()
    assert _read_status(vdp, 9) == 0xFE
    vdp.reset()
    assert _read_status(vdp, 9) == 0xFE


def test_reset_retains_vram_written_by_a_command() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=2, ny=1, clr=0xAB)  # HMMV
    vdp.reset()
    assert vdp.vram[0] == 0xAB


# ---------------------------------------------------------------------------
# AbortCommand: the reserved command codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_code", [0x1, 0x2, 0x3])
def test_reserved_command_codes_behave_as_stop(cmd_code: int) -> None:
    """Handbook Table 4.5 leaves codes 1-3 reserved; openMSX routes them to
    startAbrt, so they touch no VRAM and leave CE clear."""
    vdp = _make_vdp()
    vdp.vram[0] = 0x11
    vdp._cmd_active = True
    vdp._status2 = _CE

    _dispatch(vdp, cmd_code=cmd_code, dx=0, dy=0, nx=8, ny=8, clr=0xFF)

    assert vdp.vram[0] == 0x11
    assert vdp._status2 & _CE == 0
    assert not vdp._cmd_active


# ---------------------------------------------------------------------------
# Mode geometry: bits_per_dot / dots_per_byte / dots_per_line
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "r0, dot_x, colour, expected_addr, expected_byte",
    [
        (_G4, 1, 0xB, 0, 0x0B),          # 4 bpp, 2 dots/byte: odd dot = low nibble
        (_G5, 2, 0x3, 0, 0x0C),          # 2 bpp, 4 dots/byte: dot 2 = bits 3:2
        (_G6, 3, 0x9, 1, 0x09),          # 4 bpp, 2 dots/byte, 256 bytes per line
        (_G7, 1, 0xAB, 1, 0xAB),         # 8 bpp, 1 dot/byte: the whole byte
    ],
)
def test_pset_dot_geometry_per_screen_mode(
    r0: int, dot_x: int, colour: int, expected_addr: int, expected_byte: int
) -> None:
    """The engine's dot size and line pitch come from the screen mode, not from
    any command register (GRAPHIC 4/5/6/7 = 4/2/4/8 bits per dot)."""
    vdp = _make_vdp(r0)
    _dispatch(vdp, cmd_code=0x5, dx=dot_x, dy=0, clr=colour)
    assert vdp.vram[expected_addr] == expected_byte


def test_graphic5_colour_is_masked_to_two_bits() -> None:
    """GRAPHIC 5 dots are 2 bits wide, so CLR is masked to 0-3."""
    vdp = _make_vdp(_G5)
    _dispatch(vdp, cmd_code=0x5, dx=0, dy=0, clr=0xFF)
    assert vdp.vram[0] == 0xC0  # only the top dot's two bits are set


def test_graphic7_line_pitch_is_256_bytes() -> None:
    vdp = _make_vdp(_G7)
    _dispatch(vdp, cmd_code=0x5, dx=0, dy=1, clr=0x5A)
    assert vdp.vram[256] == 0x5A


# ---------------------------------------------------------------------------
# Logical operations: NOT (the one Table 4.6 code no other test exercises)
# ---------------------------------------------------------------------------

def test_lmmv_not_log_inverts_the_source_colour() -> None:
    """NOT (LOG=4) writes the complement of the source colour, masked to the
    mode's dot width -- the destination dot plays no part."""
    vdp = _make_vdp()
    vdp.vram[0] = 0x5A
    _dispatch(vdp, cmd_code=0x8, log=0x4, dx=0, dy=0, nx=2, ny=1, clr=0x3)
    assert vdp.vram[0] == 0xCC  # both dots = ~0x3 & 0xF


def test_lmmv_transparent_not_keeps_destination_for_source_zero() -> None:
    """TNOT (LOG=0xC): source colour 0 leaves the destination alone even though
    NOT would otherwise have written 0xF."""
    vdp = _make_vdp()
    vdp.vram[0] = 0x5A
    _dispatch(vdp, cmd_code=0x8, log=0xC, dx=0, dy=0, nx=2, ny=1, clr=0x0)
    assert vdp.vram[0] == 0x5A


# ---------------------------------------------------------------------------
# Direction bits and the NX / NY defaults
# ---------------------------------------------------------------------------

def test_lmmv_dix_fills_leftwards_from_dx() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=3, dy=0, nx=4, ny=1, clr=0x7, arg=0x04)  # DIX
    assert vdp.vram[0] == 0x77  # dots 0,1
    assert vdp.vram[1] == 0x77  # dots 2,3
    assert vdp.vram[2] == 0x00  # dot 4 untouched: the fill ran leftwards


def test_lmmv_diy_fills_upwards_from_dy() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=0, dy=2, nx=2, ny=2, clr=0x7, arg=0x08)  # DIY
    assert vdp.vram[2 * 128] == 0x77
    assert vdp.vram[1 * 128] == 0x77
    assert vdp.vram[3 * 128] == 0x00  # row below the origin untouched


def test_lmmv_nx_zero_fills_a_whole_line() -> None:
    """NX = 0 means the mode's dots-per-line (256 in GRAPHIC 4), not 512."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=0, dy=0, nx=0, ny=1, clr=0xF)
    assert all(vdp.vram[i] == 0xFF for i in range(128))
    assert vdp.vram[128] == 0x00  # the next row is a different line


def test_hmmv_ny_zero_means_1024_lines() -> None:
    """NY = 0 means 1024 lines. Asserted through the CE duration rather than
    1024 rows of VRAM."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=8, ny=0, clr=0x00)
    assert vdp._cmd_remaining == (8 // 2) * 1024 * _HMMV_COST16[vdp._line_kind] // 16


# ---------------------------------------------------------------------------
# YMMM: direction and byte-unit timing (test_v9938_commands.py covers the copy)
# ---------------------------------------------------------------------------

def test_ymmm_dix_copies_the_strip_left_of_dx() -> None:
    """YMMM has no NX: the strip runs from DX to whichever edge DIX points at."""
    vdp = _make_vdp()
    vdp.vram[0] = 0x11  # dots 0,1 of row 0
    vdp.vram[1] = 0x22  # dots 2,3
    vdp.vram[2] = 0x33  # dots 4,5 -- right of DX=3, must not move
    _dispatch(vdp, cmd_code=0xE, dx=3, sy=0, dy=1, ny=1, arg=0x04)  # DIX
    assert vdp.vram[128 + 1] == 0x22
    assert vdp.vram[128 + 0] == 0x11
    assert vdp.vram[128 + 2] == 0x00


def test_ymmm_ce_duration_is_byte_unit() -> None:
    vdp = _make_vdp()
    # DIX clear, DX=0 -> the strip spans the whole 128-byte line.
    _dispatch(vdp, cmd_code=0xE, dx=0, sy=0, dy=1, ny=4)
    duration = 128 * 4 * _YMMM_COST16[vdp._line_kind] // 16
    assert vdp._cmd_remaining == duration
    vdp.tick(duration)
    assert vdp._status2 & _CE == 0


# ---------------------------------------------------------------------------
# AdvanceCommandClock: the TR handshake of a CPU transfer
# ---------------------------------------------------------------------------

def test_transfer_ready_drops_on_a_byte_and_returns_one_byte_time_later() -> None:
    """TR falls while the engine writes the byte and comes back after one
    byte-time, which is what the CPU polls between bytes."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xF, dx=0, dy=0, nx=8, ny=1)  # HMMC, 4 bytes
    assert vdp._status2 & _TR

    _write_data(vdp, 0x11)
    assert vdp._status2 & _TR == 0          # busy
    vdp.tick(_TR_REASSERT_CYCLES - 1)
    assert vdp._status2 & _TR == 0          # still busy
    vdp.tick(1)
    assert vdp._status2 & _TR               # ready for the next byte
    assert vdp._status2 & _CE               # and the transfer is still running


def test_cpu_transfer_is_not_timed_out_by_the_clock() -> None:
    """A CPU transfer has no CE duration: only the CPU finishing the rectangle
    ends it (spec invariant BlockCommandsAreTimed)."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xF, dx=0, dy=0, nx=64, ny=4)
    assert vdp._cmd_remaining == 0
    vdp.tick(10_000_000)
    assert vdp._status2 & _CE
    assert vdp._cmd_active


# ---------------------------------------------------------------------------
# SRCH result packing (S#8 / S#9)
# ---------------------------------------------------------------------------

def test_search_result_high_packs_bit_eight_with_ones() -> None:
    """S#9 carries bit 8 of the result with the unused bits reading as 1. A
    GRAPHIC 4 search that finds nothing stops at x = 256, so bit 8 is set."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0xF)  # all-zero VRAM: no match
    assert _read_status(vdp, 8) == 0x00
    assert _read_status(vdp, 9) == 0xFF  # 0xFE | bit 8


def test_search_result_high_reads_ones_when_bit_eight_is_clear() -> None:
    vdp = _make_vdp()
    vdp.vram[2] = 0x50  # dot (4,0) = 5
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0x5)
    assert _read_status(vdp, 8) == 4
    assert _read_status(vdp, 9) == 0xFE


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

def _on_line(vdp: V9938, line: int, sprites: bool = True, lines_212: bool = False) -> None:
    """Start `line` with sprites on or off (R#8 SPD) and 192 or 212 lines (R#9 LN)."""
    vdp.regs[8] = 0x08 if sprites else 0x0A
    vdp.regs[9] = 0x80 if lines_212 else 0x00
    vdp.begin_scanline(line)


def test_cycle_cost_defaults() -> None:
    """Scenario: Each command uses its own cost -- 1/16 T-state per element for a
    blank, sprites-off and sprites-on line (real HB-F1XD, CMDTIME2/CMDTIME3), and
    the HMMC/LMMC TR re-assert delay."""
    assert _HMMV_COST16 == (130, 170, 177)
    assert _HMMM_COST16 == (246, 263, 366)
    assert _YMMM_COST16 == (175, 186, 334)
    assert _LMMV_COST16 == (261, 331, 368)
    assert _LMMM_COST16 == (347, 356, 525)
    assert (_COST_BLANK, _COST_SPRITES_OFF, _COST_SPRITES_ON) == (0, 1, 2)
    assert _TR_REASSERT_CYCLES == 8


@pytest.mark.parametrize(("cmd_code", "dy", "units", "cost16"), [
    (0xC, 0, 27136, _HMMV_COST16),    # HMMV
    (0xD, 256, 27136, _HMMM_COST16),  # HMMM page 0 -> 1
    (0xE, 256, 27136, _YMMM_COST16),  # YMMM page 0 -> 1
    (0x8, 0, 54272, _LMMV_COST16),    # LMMV
    (0x9, 256, 54272, _LMMM_COST16),  # LMMM page 0 -> 1
])
@pytest.mark.parametrize(("line", "sprites", "kind"), [
    (200, True, _COST_BLANK),
    (0, False, _COST_SPRITES_OFF),
    (0, True, _COST_SPRITES_ON),
])
def test_full_page_command_durations(cmd_code: int, dy: int, units: int,
                                     cost16: tuple[int, int, int], line: int,
                                     sprites: bool, kind: int) -> None:
    """Each command covers the whole GRAPHIC 4 page, on each line kind."""
    vdp = _make_vdp()
    _on_line(vdp, line, sprites)
    _dispatch(vdp, cmd_code=cmd_code, sx=0, sy=0, dx=0, dy=dy, nx=256, ny=212)
    assert vdp._cmd_remaining == units * cost16[kind] // 16


def test_hmmv_ce_held_on_a_sprites_on_line() -> None:
    """Scenario: HMMV CE held after dispatch."""
    vdp = _make_vdp()
    _on_line(vdp, 0)
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=256, ny=212, clr=0x00)
    assert vdp._status2 & _CE
    assert vdp._cmd_remaining == 300192
    vdp.tick(300191)
    assert vdp._status2 & _CE
    vdp.tick(1)
    assert vdp._status2 & _CE == 0


def test_lmmm_uses_pixel_unit_coefficient() -> None:
    """Scenario: LMMM uses pixel-unit coefficient (on a blank line)."""
    vdp = _make_vdp()
    _on_line(vdp, 250)
    _dispatch(vdp, cmd_code=0x9, sx=0, sy=0, dx=0, dy=4, nx=256, ny=1)
    assert vdp._cmd_remaining == 5552


@pytest.mark.parametrize(("r1", "r8", "r9", "line", "kind"), [
    (0x60, 0x08, 0x00, 0, _COST_SPRITES_ON),
    (0x60, 0x08, 0x00, 191, _COST_SPRITES_ON),
    (0x60, 0x08, 0x00, 192, _COST_BLANK),      # 192-line mode
    (0x60, 0x08, 0x80, 192, _COST_SPRITES_ON),  # 212-line mode
    (0x60, 0x08, 0x80, 212, _COST_BLANK),
    (0x60, 0x08, 0x00, 261, _COST_BLANK),
    (0x60, 0x0A, 0x00, 0, _COST_SPRITES_OFF),  # SPD
    (0x20, 0x08, 0x00, 0, _COST_BLANK),        # BL = 0
    (0x20, 0x0A, 0x00, 0, _COST_BLANK),        # BL = 0, SPD makes no difference
])
def test_line_kinds(r1: int, r8: int, r9: int, line: int, kind: int) -> None:
    """Scenario: Line kinds -- from BL, the display height and SPD."""
    vdp = _make_vdp(r1=r1)
    vdp.regs[8] = r8
    vdp.regs[9] = r9
    vdp.begin_scanline(line)
    assert vdp._line_kind == kind


def test_line_kind_ignores_the_sprite_attribute_table() -> None:
    """Scenario: Line kinds -- sprites at Y 216, or on the line, do not change
    the kind; only SPD does."""
    vdp = _make_vdp()
    vdp.regs[5] = 0xEF  # sprite attribute table at 0x7600 (SCREEN 5)
    vdp.regs[11] = 0x00
    vdp.vram[0x7600] = 216
    _on_line(vdp, 10)
    assert vdp._line_kind == _COST_SPRITES_ON
    vdp.vram[0x7600] = 5
    _on_line(vdp, 10)
    assert vdp._line_kind == _COST_SPRITES_ON


def test_rescale_when_the_line_kind_changes() -> None:
    """Scenario: Rescale when the line kind changes."""
    vdp = _make_vdp()
    _on_line(vdp, 211, lines_212=True)
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=256, ny=212, clr=0x00)
    vdp._cmd_remaining = 100000
    vdp.begin_scanline(212)
    assert vdp._line_kind == _COST_BLANK
    assert vdp._cmd_remaining == 73446


def test_no_rescale_on_the_same_line_kind() -> None:
    """Scenario: No rescale on the same line kind."""
    vdp = _make_vdp()
    _on_line(vdp, 10)
    _dispatch(vdp, cmd_code=0xD, sx=0, sy=0, dx=0, dy=256, nx=256, ny=212)
    before = vdp._cmd_remaining
    vdp.begin_scanline(11)
    assert vdp._cmd_remaining == before


def test_smallest_command_duration() -> None:
    """One element still holds CE for more than 8 T-states, so no minimum is
    needed."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=2, ny=1, clr=0x00)  # one byte
    assert vdp._cmd_remaining == _HMMV_COST16[vdp._line_kind] // 16
    assert vdp._cmd_remaining >= 8


# ---------------------------------------------------------------------------
# Invariants (property-based)
# ---------------------------------------------------------------------------

_COMMAND_CODES = [0x0, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF]

# Rectangle sizes are kept small so a property run stays fast; the invariants
# under test do not depend on the size.
_dispatch_args = st.fixed_dictionaries({
    "cmd_code": st.sampled_from(_COMMAND_CODES),
    "log": st.integers(min_value=0, max_value=15),
    "sx": st.integers(min_value=0, max_value=16),
    "sy": st.integers(min_value=0, max_value=4),
    "dx": st.integers(min_value=0, max_value=16),
    "dy": st.integers(min_value=0, max_value=4),
    "nx": st.integers(min_value=1, max_value=8),
    "ny": st.integers(min_value=1, max_value=4),
    "clr": st.integers(min_value=0, max_value=255),
    "arg": st.integers(min_value=0, max_value=15),
})


def _assert_command_invariants(vdp: V9938) -> None:
    # CommandRegistersAreEightBit
    assert all(0 <= r <= 255 for r in vdp.cmd_regs)
    # ExecutingImpliesActiveCommand
    if vdp._cmd_active:
        assert vdp._cmd_code != 0x0
    # BlockCommandsAreTimed: a CPU transfer is never on the CE countdown
    if vdp._cmd_active and vdp._cmd_code in (0xB, 0xF):
        assert vdp._cmd_remaining == 0
        # TransferCursorInsideRectangle
        assert 0 <= vdp._cmd_x < vdp._cmd_nx
        assert 0 <= vdp._cmd_y < vdp._cmd_ny
        # CoordinateRegistersInRange, for the coordinates the engine retained
        assert 0 <= vdp._cmd_dx <= 511
        assert 0 <= vdp._cmd_dy <= 1023
    # SearchResultIsNineBit
    assert 0 <= vdp._status8 <= 255
    assert 0 <= (vdp._status9 & 0x01) * 256 + vdp._status8 <= 511


@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(args=_dispatch_args, r0=st.sampled_from([_G4, _G5, _G6, _G7]))
def test_invariants_hold_after_any_dispatch(args: dict[str, int], r0: int) -> None:
    vdp = _make_vdp(r0)
    _dispatch(vdp, **args)
    _assert_command_invariants(vdp)


@settings(max_examples=50, deadline=None)
@given(
    args=_dispatch_args,
    feed=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=40),
)
def test_invariants_hold_while_feeding_a_transfer(
    args: dict[str, int], feed: list[int]
) -> None:
    """Feeding bytes at the data port and ticking the clock must not break the
    cursor or status invariants, whatever command is actually running."""
    vdp = _make_vdp()
    _dispatch(vdp, **args)
    for value in feed:
        _write_data(vdp, value)
        vdp.tick(4)
        _assert_command_invariants(vdp)


# ---------------------------------------------------------------------------
# Screen-edge clipping (openMSX clipNX_* / clipNY_*)
# ---------------------------------------------------------------------------

def test_block_fill_stops_at_the_right_edge() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=254, dy=0, nx=8, ny=1, clr=0xF)
    assert vdp.vram[127] == 0xFF  # dots 254, 255 filled
    assert vdp.vram[0] == 0x00    # the line ended; nothing wrapped to dot 0


def test_upward_fill_stops_at_the_top_border() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=0, dy=1, nx=2, ny=4, clr=0xF, arg=0x08)  # DIY
    assert vdp.vram[128] == 0xFF  # row 1
    assert vdp.vram[0] == 0xFF    # row 0
    # NY is clipped to DY + 1 = 2 rows, so the command must not wrap to the
    # bottom of the 1024-line coordinate space.
    assert vdp.vram[1023 * 128] == 0x00


def test_origin_past_the_right_edge_processes_one_element_per_line() -> None:
    """openMSX clipNX_*: an origin already off the line does exactly one
    element per row rather than a full-width run."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=300, dy=0, nx=8, ny=1, clr=0xF)
    assert vdp._cmd_remaining == 1 * 1 * _LMMV_COST16[vdp._line_kind] // 16


def test_copy_clips_against_whichever_operand_is_nearer_the_edge() -> None:
    vdp = _make_vdp()
    for addr in range(128):
        vdp.vram[addr] = 0xEE
    # SX=0, DX=252: the destination has 4 dots of room, so only 4 dots move.
    _dispatch(vdp, cmd_code=0x9, sx=0, sy=0, dx=252, dy=1, nx=64, ny=1)
    assert vdp.vram[128 + 126] == 0xEE
    assert vdp.vram[128 + 127] == 0xEE
    assert vdp.vram[128 + 0] == 0x00  # nothing wrapped around to dot 0


# ---------------------------------------------------------------------------
# Undefined logical operations (openMSX DummyOp)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("log", [0x5, 0x6, 0x7])
def test_undefined_logical_operations_write_nothing(log: int) -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x33
    _dispatch(vdp, cmd_code=0x8, log=log, dx=0, dy=0, nx=2, ny=1, clr=0xA)
    assert vdp.vram[0] == 0x33


@pytest.mark.parametrize("log", [0xD, 0xE, 0xF])
def test_undefined_transparent_operations_write_nothing(log: int) -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x33
    _dispatch(vdp, cmd_code=0x8, log=log, dx=0, dy=0, nx=2, ny=1, clr=0xA)
    assert vdp.vram[0] == 0x33


# ---------------------------------------------------------------------------
# LMCM: one dot per read, staged in the colour register
# ---------------------------------------------------------------------------

def test_lmcm_delivers_one_dot_per_read() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB  # dots (0,0)=0xA, (1,0)=0xB
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1)
    assert _read_status(vdp, 7) == 0x0A
    assert _read_status(vdp, 7) == 0x0B


def test_lmcm_walks_rows_in_the_diy_direction() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x12        # row 0: dots 0x1, 0x2
    vdp.vram[128] = 0x34      # row 1: dots 0x3, 0x4
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=2)
    assert [_read_status(vdp, 7) for _ in range(4)] == [0x1, 0x2, 0x3, 0x4]
    assert vdp._status2 & _CE == 0  # the rectangle is exhausted


def test_lmcm_sees_a_vram_write_made_mid_transfer() -> None:
    """The dots are sampled one at a time, not buffered at dispatch."""
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)
    assert _read_status(vdp, 7) == 0x0A
    vdp.vram[1] = 0xCD  # dots 2 and 3, not yet read
    assert _read_status(vdp, 7) == 0x0B
    assert _read_status(vdp, 7) == 0x0C
    assert _read_status(vdp, 7) == 0x0D


def test_lmcm_after_a_block_command_is_not_aborted_by_the_stale_ce_countdown() -> None:
    """LMCM dispatch must reset _cmd_remaining like every other CPU-feed
    command; before this fix, a preceding LMMV/LMMM/HMMV/HMMM/YMMM left a
    stale non-zero countdown that tick() would run down and abort the LMCM
    mid-transfer, even though LMCM has no CE-timed duration of its own."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x8, dx=0, dy=0, nx=2, ny=1)  # LMMV: leaves a nonzero CE countdown
    assert vdp._cmd_remaining > 0
    vdp.vram[0] = 0xAB
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=8, ny=8)  # LMCM
    vdp.tick(20)  # more than enough T-states to exhaust the stale countdown
    assert vdp._cmd_active is True
    assert vdp._status2 & _CE
    assert vdp._cmd_code == 0xA
    assert _read_status(vdp, 7) == 0x0A  # the transfer still works


# ---------------------------------------------------------------------------
# BD lifecycle (S#2 bit 4)
# ---------------------------------------------------------------------------

def test_reading_s9_clears_border_detected() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x50
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0x5)
    assert vdp._status2 & _BD
    _read_status(vdp, 9)
    assert vdp._status2 & _BD == 0


def test_failed_search_leaves_border_detected_alone() -> None:
    """openMSX executeSrch: running into the border does NOT reset BD."""
    vdp = _make_vdp()
    vdp.vram[0] = 0x50
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0x5)  # found: BD set
    _dispatch(vdp, cmd_code=0x6, sx=0, sy=0, clr=0xF)  # not found: BD untouched
    assert vdp._status2 & _BD


# ---------------------------------------------------------------------------
# POINT stages its result in the colour register
# ---------------------------------------------------------------------------

def test_point_result_lands_in_the_colour_register() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x7F
    _dispatch(vdp, cmd_code=0x4, sx=0, sy=0, clr=0x00)
    assert vdp.cmd_regs[12] == 0x7
    assert _read_status(vdp, 7) == 0x7


# ---------------------------------------------------------------------------
# Byte-unit commands work in whole bytes
# ---------------------------------------------------------------------------

def test_hmmv_truncates_nx_to_whole_bytes() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=3, ny=1, clr=0xAB)
    assert vdp.vram[0] == 0xAB  # NX = 3 dots -> 1 whole byte
    assert vdp.vram[1] == 0x00


def test_hmmv_nx_below_one_byte_covers_the_whole_line() -> None:
    """NX truncates to 0 bytes, which the hardware reads as a full line
    (openMSX clipNX_1_byte: `NX = NX ? NX : BYTES_PER_LINE`)."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=1, ny=1, clr=0xAB)
    assert vdp.vram[0] == 0xAB
    assert vdp.vram[127] == 0xAB


def test_nx_is_ten_bits() -> None:
    vdp = _make_vdp()
    # NX = 0x201 (513 dots): with a 10-bit decode the fill covers the whole
    # 256-dot line; with a 9-bit decode R#41 bit 1 is dropped and NX reads 1.
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=1, nx_high=0x02, ny=1, clr=0xAB)
    assert vdp.vram[127] == 0xAB


# ---------------------------------------------------------------------------
# Commands are confined to the bitmap modes
# ---------------------------------------------------------------------------

def test_commands_do_nothing_outside_the_bitmap_modes() -> None:
    vdp = _make_vdp(r0=0x00, r1=0x70)  # TEXT1
    vdp.vram[0] = 0xAB
    _dispatch(vdp, cmd_code=0xD, sx=0, sy=0, dx=0, dy=4, nx=8, ny=2)  # HMMM
    assert not any(vdp.vram[1:])
    assert vdp._status2 & _CE == 0


# ---------------------------------------------------------------------------
# HMMV outside the bitmap modes (real SONY HB-F1XD, CMDSCAN2/CMDSCAN3)
# ---------------------------------------------------------------------------

_GRAPHIC1 = (0x00, 0x60)
_TEXT2 = (0x04, 0x70)


def _written(vdp: V9938) -> dict[int, int]:
    return {addr: value for addr, value in enumerate(vdp.vram) if value}


def _hmmv_outside(mode: tuple[int, int] = _GRAPHIC1, **kwargs: int) -> V9938:
    vdp = _make_vdp(r0=mode[0], r1=mode[1])
    kwargs.setdefault("clr", 0xA5)
    _dispatch(vdp, cmd_code=0xC, **kwargs)
    return vdp


def test_hmmv_outside_writes_one_byte_per_line_pair() -> None:
    vdp = _hmmv_outside(dx=0, dy=0, nx=16, ny=4)
    assert _written(vdp) == {0x000FF: 0xA5, 0x001FF: 0xA5}


def test_hmmv_outside_crosses_y_255_without_wrapping() -> None:
    vdp = _hmmv_outside(dy=254, nx=16, ny=4)
    assert _written(vdp) == {0x07FFF: 0xA5, 0x080FF: 0xA5}


def test_hmmv_outside_reaches_the_end_of_the_first_bank() -> None:
    vdp = _hmmv_outside(dy=511, nx=16, ny=1)
    assert _written(vdp) == {0x0FFFF: 0xA5}


def test_hmmv_outside_with_ny_0_covers_all_128_kb() -> None:
    vdp = _hmmv_outside(dy=0, nx=16, ny=0)
    assert _written(vdp) == {(block << 8) | 0xFF: 0xA5 for block in range(512)}
    assert vdp._status2 & _CE
    assert vdp._cmd_remaining == 1024 * _HMMV_COST16[vdp._line_kind] // 16


def test_hmmv_outside_runs_upward_with_diy() -> None:
    vdp = _hmmv_outside(dy=3, nx=16, ny=4, arg=_ARG_DIY)
    assert _written(vdp) == {0x000FF: 0xA5, 0x001FF: 0xA5}


@pytest.mark.parametrize("dx, nx", [(16, 16), (0, 0)])
def test_hmmv_outside_ignores_dx_and_nx(dx: int, nx: int) -> None:
    vdp = _hmmv_outside(dx=dx, dy=0, nx=nx, ny=4)
    assert _written(vdp) == {0x000FF: 0xA5, 0x001FF: 0xA5}


def test_hmmv_outside_in_text2_does_the_same() -> None:
    vdp = _hmmv_outside(_TEXT2, dx=0, dy=0, nx=16, ny=4)
    assert _written(vdp) == {0x000FF: 0xA5, 0x001FF: 0xA5}


def test_lmmv_outside_still_writes_nothing() -> None:
    vdp = _make_vdp(r0=_GRAPHIC1[0], r1=_GRAPHIC1[1])
    _dispatch(vdp, cmd_code=0x8, dx=0, dy=0, nx=1, ny=1, clr=0xFF)
    assert _written(vdp) == {}
    assert vdp._status2 & _CE == 0


def test_leaving_bitmap_mode_keeps_the_command_running() -> None:
    # Real HB-F1XD (2026-10-07): CE still 1 right after R#0 selects GRAPHIC 1.
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=128, ny=200, clr=0x00)
    assert vdp._status2 & _CE
    _write_reg(vdp, 0, 0x00)  # leave GRAPHIC 4 for GRAPHIC 1
    assert vdp._status2 & _CE
    assert vdp._cmd_active
    vdp.tick(10_000_000)  # past the command's duration
    assert vdp._status2 & _CE == 0


def test_cpu_transfer_survives_a_switch_out_of_the_bitmap_modes() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xF, dx=0, dy=1, nx=4, ny=1, clr=0x12)  # HMMC, 2 bytes
    _write_reg(vdp, 0, 0x00)  # GRAPHIC 1
    _write_data(vdp, 0x34)
    _write_reg(vdp, 0, _G4)
    assert (vdp.vram[128], vdp.vram[129]) == (0x12, 0x34)  # GRAPHIC 4 row 1
    assert not vdp._cmd_active


def test_switching_between_bitmap_modes_keeps_the_command_running() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=128, ny=200, clr=0x00)
    _write_reg(vdp, 0, _G7)  # GRAPHIC 4 -> GRAPHIC 7: still a command mode
    assert vdp._status2 & _CE
    assert vdp._cmd_active



def test_lmcm_keeps_its_dispatch_geometry_after_a_mode_switch() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB
    vdp.vram[1] = 0xCD
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)  # LMCM in GRAPHIC 4
    _write_reg(vdp, 0, _G7)  # 8-bit dots from here on, but not for this LMCM
    assert [_read_status(vdp, 7) for _ in range(4)] == [0xA, 0xB, 0xC, 0xD]

# ---------------------------------------------------------------------------
# TR is never cleared by completion (openMSX commandDone)
# ---------------------------------------------------------------------------

def test_abort_leaves_transfer_ready_set() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)  # LMCM sets TR
    assert vdp._status2 & _TR
    _dispatch(vdp, cmd_code=0x0)  # STOP
    assert vdp._status2 & _CE == 0
    assert vdp._status2 & _TR


def test_block_command_timeout_leaves_transfer_ready_set() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)  # LMCM sets TR
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=8, ny=1, clr=0x00)  # HMMV
    vdp.tick(10_000_000)
    assert vdp._status2 & _CE == 0
    assert vdp._status2 & _TR


def test_reading_s7_with_no_command_running_clears_transfer_ready() -> None:
    """openMSX VDP.cc readStatusReg case 7 -> resetColor(), which clears TR
    when no command is running."""
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1)
    _dispatch(vdp, cmd_code=0x0)  # STOP, TR still set
    assert vdp._status2 & _TR
    _read_status(vdp, 7)
    assert vdp._status2 & _TR == 0


# ---------------------------------------------------------------------------
# LINE: Bresenham error term and edge termination
# ---------------------------------------------------------------------------

def test_line_error_term_matches_hardware() -> None:
    vdp = _make_vdp()
    # NX=4 (major X), NY=1: with the hardware error term ((NX-1)/2) the minor
    # axis steps before the third dot, so dot 2 lands on row 1, not row 0.
    _dispatch(vdp, cmd_code=0x7, dx=0, dy=0, nx=4, ny=1, clr=0xF)
    assert vdp.vram[128 + 1] >> 4 == 0xF  # dot (2,1) drawn
    assert vdp.vram[1] >> 4 == 0x0        # dot (2,0) not drawn


def test_line_terminates_at_the_right_edge() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0x7, dx=254, dy=0, nx=8, ny=0, clr=0xF)
    assert vdp.vram[127] == 0xFF  # dots 254, 255
    assert vdp.vram[0] == 0x00    # the line stopped instead of wrapping


def test_line_terminates_above_the_top_border() -> None:
    vdp = _make_vdp()
    # Upward vertical line from row 1: two dots, then Y would go negative.
    _dispatch(vdp, cmd_code=0x7, dx=0, dy=1, nx=8, ny=0, clr=0xF,
              arg=_ARG_MAJ | _ARG_DIY)
    assert vdp.vram[128] >> 4 == 0xF  # row 1
    assert vdp.vram[0] >> 4 == 0xF    # row 0
    assert vdp.vram[1023 * 128] == 0x00  # did not wrap to the bottom


# ---------------------------------------------------------------------------
# MXS / MXD select expansion RAM, which this machine does not have
# ---------------------------------------------------------------------------

def test_mxd_write_is_discarded() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xC, dx=0, dy=0, nx=2, ny=1, clr=0xAB, arg=_ARG_MXD)
    assert vdp.vram[0] == 0x00


def test_mxs_read_returns_all_ones_dot() -> None:
    # Real HB-F1XD (2026-10-07): POINT with MXS after R#44 = 0x55 read 0x0F.
    vdp = _make_vdp()
    vdp.vram[0] = 0x7F
    _dispatch(vdp, cmd_code=0x4, sx=0, sy=0, clr=0x55, arg=_ARG_MXS)  # POINT
    assert _read_status(vdp, 7) == 0x0F


@pytest.mark.parametrize(("r0", "expected"), [(_G5, 0x03), (_G6, 0x0F), (_G7, 0xFF)])
def test_mxs_point_is_masked_to_the_dot_width(r0: int, expected: int) -> None:
    vdp = _make_vdp(r0)
    _dispatch(vdp, cmd_code=0x4, sx=0, sy=0, clr=0x55, arg=_ARG_MXS)  # POINT
    assert _read_status(vdp, 7) == expected


def test_mxs_lmcm_reads_all_ones_dots() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x12
    _dispatch(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1, clr=0x55, arg=_ARG_MXS)
    assert [_read_status(vdp, 7) for _ in range(2)] == [0x0F, 0x0F]


def test_mxd_transfer_write_is_discarded() -> None:
    vdp = _make_vdp()
    _dispatch(vdp, cmd_code=0xF, dx=0, dy=0, nx=4, ny=1, arg=_ARG_MXD)
    _write_data(vdp, 0xAB)
    assert vdp.vram[0] == 0x00


# ---------------------------------------------------------------------------
# Port decoding: the chip has four ports and a coarser machine decode mirrors
# them (Handbook Table 4.3; openMSX VDP::readIO/writeIO use `port & 0x03`)
# ---------------------------------------------------------------------------

def test_ports_mirror_every_four_addresses() -> None:
    vdp = _make_vdp()
    vdp.write_port(0x99, 0x00)   # address setup: low byte
    vdp.write_port(0x99, 0x40)   # write mode, high byte
    vdp.write_port(0x9C, 0x5A)   # mirrors 0x98 -> VRAM data
    assert vdp.vram[0] == 0x5A


def test_mirrored_read_returns_vram_data() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x33
    vdp.vram[1] = 0x44
    vdp.write_port(0x99, 0x00)
    vdp.write_port(0x99, 0x00)   # read mode: preloads vram[0], address -> 1
    assert vdp.read_port(0x9C) == 0x33  # mirrors 0x98
    assert vdp.read_port(0x98) == 0x44


def test_write_only_ports_read_as_ones() -> None:
    """Ports 2 and 3 (palette, indirect register) are write-only."""
    vdp = _make_vdp()
    assert vdp.read_port(0x9A) == 0xFF
    assert vdp.read_port(0x9B) == 0xFF


# The one remaining deviation from hardware is documented in the spec:
# GRAPHIC 6/7 planar VRAM interleave is not modelled, but the linear address
# model is applied consistently across the command engine, the renderer and
# port 0x98, so no software-visible behaviour differs.
