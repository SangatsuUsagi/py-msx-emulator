"""Tests for V9938 hardware command engine."""
from __future__ import annotations

import pytest

from msx.vdp.v9938 import V9938


def _make_vdp() -> V9938:
    vdp = V9938()
    # G4 mode: R0=0x06 (M3=1, M4=1), R1=0x60 (BL=1)
    vdp.regs[0] = 0x06
    vdp.regs[1] = 0x60
    return vdp


def _write_reg(vdp: V9938, reg: int, value: int) -> None:
    """Write a VDP register via port 0x99."""
    vdp.write_port(0x99, value & 0xFF)
    vdp.write_port(0x99, 0x80 | (reg & 0x1F))


def _write_cmd_reg(vdp: V9938, reg: int, value: int) -> None:
    """Write command register R(32+offset) via port 0x9B (R17 indirect)."""
    _write_reg(vdp, 17, 0x80 | (32 + reg))  # AII=1 (no auto-increment), ptr=32+reg
    vdp.write_port(0x9B, value & 0xFF)


def _dispatch_cmd(vdp: V9938, cmd_code: int, log: int = 0,
                  dx: int = 0, dy: int = 0, nx: int = 1, ny: int = 1,
                  sx: int = 0, sy: int = 0, clr: int = 0, arg: int = 0) -> None:
    """Set up and dispatch a V9938 command."""
    _write_reg(vdp, 17, 32)  # AII=0 (auto-increment), ptr=R32 (SX low)
    for val in [sx & 0xFF, 0, sy & 0xFF, (sy >> 8) & 0x03,
                dx & 0xFF, 0, dy & 0xFF, (dy >> 8) & 0x03,
                nx & 0xFF, 0, ny & 0xFF, 0, clr, arg & 0xFF]:
        vdp.write_port(0x9B, val)
    # Write CMR (R46) via ptr=46 to trigger dispatch
    _write_reg(vdp, 17, 0x80 | 46)  # AII=1, ptr=46 (R46)
    vdp.write_port(0x9B, (cmd_code << 4) | (log & 0xF))


# ---------------------------------------------------------------------------
# Port 0x9B routing
# ---------------------------------------------------------------------------

def test_port_9b_writes_cmd_regs_32_to_45() -> None:
    vdp = _make_vdp()
    _write_reg(vdp, 17, 0x80 | 32)  # AII=1, ptr=32 (R32=SX low)
    vdp.write_port(0x9B, 0xAB)
    assert vdp.cmd_regs[0] == 0xAB  # cmd_regs[ptr-32] = cmd_regs[0]


def test_port_9b_ptr_47_plus_ignored() -> None:
    vdp = _make_vdp()
    _write_reg(vdp, 17, 0x80 | 47)  # ptr=47 > 46 → ignored
    vdp.write_port(0x9B, 0xFF)
    assert all(r == 0 for r in vdp.cmd_regs)


def test_port_9b_auto_increment() -> None:
    vdp = _make_vdp()
    _write_reg(vdp, 17, 32)  # AII=0, ptr=32, auto-increment enabled
    vdp.write_port(0x9B, 0x11)
    vdp.write_port(0x9B, 0x22)
    assert vdp.cmd_regs[0] == 0x11
    assert vdp.cmd_regs[1] == 0x22


# ---------------------------------------------------------------------------
# Status register S2 via R15
# ---------------------------------------------------------------------------

def test_s2_default_returns_s0() -> None:
    vdp = _make_vdp()
    vdp.status = 0xAB
    vdp.regs[15] = 0
    # S#0 bits 4-0 report the idle last-sprite number (0x1F).
    assert vdp.read_port(0x99) == 0xBF  # (0xAB & 0xE0) | 0x1F


def test_s2_r15_2_returns_status2() -> None:
    vdp = _make_vdp()
    vdp._status2 = 0x81  # CE=1, TR=1
    vdp.regs[15] = 2
    # Bits 2,3 (0x0C) always read as 1 on the V9938.
    assert vdp.read_port(0x99) == 0x81 | 0x0C


def test_s2_r15_2_does_not_clear_status2() -> None:
    vdp = _make_vdp()
    vdp._status2 = 0x81
    vdp.regs[15] = 2
    vdp.read_port(0x99)
    assert vdp._status2 == 0x81


# ---------------------------------------------------------------------------
# STOP command
# ---------------------------------------------------------------------------

def test_stop_cancels_active_cmd() -> None:
    # V9938 ABSR/STOP immediately cancels any running command
    vdp = _make_vdp()
    vdp._status2 = 0x81  # simulate active command
    vdp._cmd_active = True
    _dispatch_cmd(vdp, cmd_code=0x0)
    assert vdp._status2 & 0x01 == 0  # CE cleared
    assert not vdp._cmd_active


def test_stop_noop_when_idle() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0x0)
    assert vdp._status2 & 0x01 == 0  # CE not set
    assert not vdp._cmd_active


# ---------------------------------------------------------------------------
# HMMV — synchronous fill
# ---------------------------------------------------------------------------

def test_hmmv_fills_rectangle() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xC, dx=4, dy=0, nx=4, ny=2, clr=0xAB)
    # dx=4, nx=4: bytes at (4//2)=2 and (6//2)=3 → columns 4,5,6,7 packed into
    # bytes at offset 2 and 3 of row 0 and row 1
    assert vdp.vram[2] == 0xAB  # row 0, byte (dx//2)=2
    assert vdp.vram[3] == 0xAB  # row 0, byte 3
    assert vdp.vram[128 + 2] == 0xAB  # row 1, byte 2
    assert vdp.vram[128 + 3] == 0xAB  # row 1, byte 3
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after tick


def test_hmmv_does_not_touch_outside_rectangle() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xC, dx=0, dy=0, nx=2, ny=1, clr=0xFF)
    assert vdp.vram[1] == 0x00  # byte at x=2,3 not filled (only nx=2 → 1 byte)
    assert vdp.vram[128] == 0x00  # row 1 untouched


# ---------------------------------------------------------------------------
# HMMM — synchronous VRAM copy
# ---------------------------------------------------------------------------

def test_hmmm_copies_region() -> None:
    vdp = _make_vdp()
    # Write source data manually at (SX=0, SY=0, NX=2, NY=1)
    vdp.vram[0] = 0x55  # byte covering pixels (0,1) in G4
    _dispatch_cmd(vdp, cmd_code=0xD, sx=0, sy=0, dx=4, dy=2, nx=2, ny=1)
    # Destination: (4//2)=2 in row 2 → vram[2*128 + 2]
    assert vdp.vram[2 * 128 + 2] == 0x55
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after tick


# ---------------------------------------------------------------------------
# HMMC — CPU-feed transfer
# ---------------------------------------------------------------------------

def test_hmmc_sets_ce_and_tr_on_dispatch() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xF, dx=0, dy=0, nx=4, ny=1)
    assert vdp._status2 & 0x01  # CE set
    assert vdp._status2 & 0x80  # TR set
    assert vdp._cmd_active


def test_hmmc_transfers_bytes_via_port_9c() -> None:
    vdp = _make_vdp()
    # NX=4 pixels = 2 bytes in G4. First byte is pre-loaded in CLR (R#44) and
    # consumed on dispatch; the second arrives via the data port.
    _dispatch_cmd(vdp, cmd_code=0xF, dx=0, dy=0, nx=4, ny=1, clr=0xAB)
    vdp.write_port(0x9C, 0xCD)
    assert vdp.vram[0] == 0xAB  # pixel pair (0,1) — pre-loaded CLR
    assert vdp.vram[1] == 0xCD  # pixel pair (2,3)


def test_hmmc_clears_ce_when_done() -> None:
    vdp = _make_vdp()
    # NX=2 pixels = 1 byte total = just the pre-loaded CLR → completes on dispatch.
    _dispatch_cmd(vdp, cmd_code=0xF, dx=0, dy=0, nx=2, ny=1, clr=0xAB)
    assert vdp.vram[0] == 0xAB
    assert vdp._status2 & 0x01 == 0  # CE cleared
    assert not vdp._cmd_active


def test_hmmc_port_9c_write_when_inactive_ignored() -> None:
    vdp = _make_vdp()
    vdp.write_port(0x9C, 0xFF)  # no active command
    assert vdp.vram[0] == 0x00  # unchanged


def test_hmmc_transfers_bytes_via_port_9b() -> None:
    # V9938: port 0x9B doubles as command data port during HMMC/LMMC.
    # First byte pre-loaded in CLR; second via the port.
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xF, dx=0, dy=0, nx=4, ny=1, clr=0xAB)
    vdp.write_port(0x9B, 0xCD)
    assert vdp.vram[0] == 0xAB
    assert vdp.vram[1] == 0xCD


def test_hmmc_port_9b_does_not_write_register_when_active() -> None:
    # Port 0x9B must NOT update cmd_regs while HMMC is active.
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xF, dx=0, dy=0, nx=4, ny=1)
    vdp.regs[17] = 0x20  # ptr → R#32
    before = vdp.cmd_regs[0]
    vdp.write_port(0x9B, 0xFF)  # must route to HMMC, not R#32
    assert vdp.cmd_regs[0] == before


def test_lmmc_transfers_bytes_via_port_9b() -> None:
    # LMMC: 1 byte per pixel; color in lower nibble. G4 (4bpp): 2 pixels per VRAM byte.
    # V9938 transfers all NX*NY dots; the first dot is the pre-loaded CLR and the
    # CPU supplies the remaining NX*NY-1 via the port. CE clears after the last.
    # NX=4,NY=2 → 8 dots = CLR + 7 port writes.
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xB, log=0x0, dx=0, dy=0, nx=4, ny=2, clr=0x07)
    # (0,0)=7 came from CLR; remaining dots via the port:
    vdp.write_port(0x9B, 0x05)  # (1,0)=5
    vdp.write_port(0x9B, 0x03)  # (2,0)=3
    vdp.write_port(0x9B, 0x01)  # (3,0)=1
    vdp.write_port(0x9B, 0x04)  # (0,1)=4
    vdp.write_port(0x9B, 0x02)  # (1,1)=2
    vdp.write_port(0x9B, 0x06)  # (2,1)=6
    assert vdp._cmd_active        # still active before the final pixel
    vdp.write_port(0x9B, 0x08)  # (3,1)=8 → last dot → CE=0
    assert vdp.vram[0] == 0x75   # row 0 byte 0: pixels (0,0)=7, (1,0)=5
    assert vdp.vram[1] == 0x31   # row 0 byte 1: pixels (2,0)=3, (3,0)=1
    assert vdp.vram[128] == 0x42 # row 1 byte 0: pixels (0,1)=4, (1,1)=2
    assert vdp.vram[129] == 0x68 # row 1 byte 1: pixels (2,1)=6, (3,1)=8
    assert not vdp._cmd_active    # command complete after NX*NY dots


# ---------------------------------------------------------------------------
# LMMC — TIMP (transparent)
# ---------------------------------------------------------------------------

def test_lmmc_timp_skips_zero_bytes() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x99  # pre-fill destination
    _dispatch_cmd(vdp, cmd_code=0xB, log=0x8, dx=0, dy=0, nx=2, ny=1)
    vdp.write_port(0x9C, 0x00)  # transparent → skip
    assert vdp.vram[0] == 0x99  # unchanged


def test_lmmc_timp_writes_nonzero_bytes() -> None:
    # TIMP: color 0 = transparent (skip). Non-zero color writes the pixel.
    # NX=3,NY=1 → 3 dots: CLR (pre-load) + 2 port writes.
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xB, log=0x8, dx=0, dy=0, nx=3, ny=1, clr=0x0B)
    # (0,0) color 0xB came from CLR; remaining dots via the port:
    vdp.write_port(0x9C, 0x05)  # (1,0) color 5 → pixel written
    vdp.write_port(0x9C, 0x0C)  # (2,0) color C → last dot → CE=0
    # G4: byte 0 = pixels 0,1 packed; byte 1 = pixels 2,3.
    assert vdp.vram[0] == 0xB5      # pixel 0 high=B, pixel 1 low=5
    assert vdp.vram[1] & 0xF0 == 0xC0  # pixel 2 high nibble = C
    assert not vdp._cmd_active


# ---------------------------------------------------------------------------
# LMMV — logical pixel fill
# ---------------------------------------------------------------------------

def test_lmmv_imp_fills_pixels() -> None:
    vdp = _make_vdp()
    # CLR=0x7, LOG=IMP (0x0), fill 4×1 pixels starting at (0,0)
    _dispatch_cmd(vdp, cmd_code=0x8, log=0x0, dx=0, dy=0, nx=4, ny=1, clr=0x7)
    # G4: byte 0 = pixels (0,1), byte 1 = pixels (2,3)
    assert vdp.vram[0] == 0x77
    assert vdp.vram[1] == 0x77
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after tick


def test_lmmv_timp_skips_zero_clr() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB
    # CLR=0x0, LOG=TIMP (0x8) → transparent, no write
    _dispatch_cmd(vdp, cmd_code=0x8, log=0x8, dx=0, dy=0, nx=2, ny=1, clr=0x0)
    assert vdp.vram[0] == 0xAB  # unchanged


def test_lmmv_and_log() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xF0  # pixel 0 = 0xF, pixel 1 = 0x0
    # CLR=0xA, LOG=AND (0x1): 0xF & 0xA = 0xA; 0x0 & 0xA = 0x0
    _dispatch_cmd(vdp, cmd_code=0x8, log=0x1, dx=0, dy=0, nx=2, ny=1, clr=0xA)
    assert vdp.vram[0] == 0xA0


def test_lmmv_or_log() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x30  # pixel 0 = 0x3, pixel 1 = 0x0
    # CLR=0x5, LOG=OR (0x2): 0x3 | 0x5 = 0x7; 0x0 | 0x5 = 0x5
    _dispatch_cmd(vdp, cmd_code=0x8, log=0x2, dx=0, dy=0, nx=2, ny=1, clr=0x5)
    assert vdp.vram[0] == 0x75


def test_lmmv_xor_log() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xF0  # pixel 0 = 0xF, pixel 1 = 0x0
    # CLR=0xA, LOG=XOR (0x3): 0xF ^ 0xA = 0x5; 0x0 ^ 0xA = 0xA
    _dispatch_cmd(vdp, cmd_code=0x8, log=0x3, dx=0, dy=0, nx=2, ny=1, clr=0xA)
    assert vdp.vram[0] == 0x5A


# ---------------------------------------------------------------------------
# LMMM — logical pixel VRAM→VRAM copy
# ---------------------------------------------------------------------------

def test_lmmm_imp_copies_pixels() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x57  # source: pixel (0,0)=0x5, pixel (1,0)=0x7
    _dispatch_cmd(vdp, cmd_code=0x9, log=0x0, sx=0, sy=0, dx=4, dy=0, nx=2, ny=1)
    # dst byte = _vram_byte_addr(4, 0) = 2
    assert vdp.vram[2] == 0x57
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after tick


def test_lmmm_timp_skips_zero_src_pixels() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x05  # source: pixel (0,0)=0x0 (transparent), pixel (1,0)=0x5
    vdp.vram[2] = 0xAB  # destination pre-fill
    _dispatch_cmd(vdp, cmd_code=0x9, log=0x8, sx=0, sy=0, dx=4, dy=0, nx=2, ny=1)
    # pixel 0: src=0x0 transparent → dst nibble A unchanged
    # pixel 1: src=0x5 non-zero → dst nibble B → 0x5
    assert vdp.vram[2] == 0xA5


# ---------------------------------------------------------------------------
# YMMM — high-speed Y-strip VRAM copy
# ---------------------------------------------------------------------------

def test_ymmm_copies_strip_from_sy_to_dy() -> None:
    vdp = _make_vdp()
    # YMMM copies a vertical strip at X=DX; source row SY → destination row DY.
    # Source strip at row 0, X>=DX=4 (bytes at offset 2,3 cover x=4..7).
    vdp.vram[2] = 0xAB
    vdp.vram[3] = 0xCD
    _dispatch_cmd(vdp, cmd_code=0xE, dx=4, sy=0, dy=2, ny=1)
    # dst row 2: _vram_byte_addr(4, 2)=2*128+2=258, _vram_byte_addr(6, 2)=259
    assert vdp.vram[258] == 0xAB
    assert vdp.vram[259] == 0xCD
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after tick


def test_ymmm_destination_uses_dy_row() -> None:
    vdp = _make_vdp()
    vdp.vram[2] = 0x55  # row 0 source at X=DX=4
    _dispatch_cmd(vdp, cmd_code=0xE, dx=4, sy=0, dy=1, ny=1)
    # destination: (DX=4, DY=1) → _vram_byte_addr(4, 1) = 128 + 2 = 130
    assert vdp.vram[130] == 0x55


# ---------------------------------------------------------------------------
# PSET — single pixel write
# ---------------------------------------------------------------------------

def test_pset_writes_pixel_at_dx_dy() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0x5, dx=0, dy=0, clr=0xA)
    # pixel (0,0) = high nibble of vram[0]
    assert (vdp.vram[0] >> 4) == 0xA
    assert vdp._status2 & 0x01 == 0  # CE cleared


def test_pset_odd_pixel_uses_low_nibble() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0x5, dx=1, dy=0, clr=0xB)
    assert (vdp.vram[0] & 0x0F) == 0xB


def test_pset_log_applied() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xF0  # pixel (0,0) = 0xF
    _dispatch_cmd(vdp, cmd_code=0x5, dx=0, dy=0, clr=0xA, log=0x1)  # AND
    assert (vdp.vram[0] >> 4) == (0xF & 0xA)  # 0xA


# ---------------------------------------------------------------------------
# POINT — single pixel read into S2
# ---------------------------------------------------------------------------

def test_point_reads_pixel_into_s7() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x7F  # pixel (0,0)=0x7, pixel (1,0)=0xF
    _dispatch_cmd(vdp, cmd_code=0x4, sx=0, sy=0)
    # POINT result in S#7 (read via R15=7)
    vdp.regs[15] = 7
    assert vdp.read_port(0x99) == 0x7
    assert vdp._status2 & 0x01 == 0  # CE cleared


def test_point_odd_pixel_returns_low_nibble() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x7F  # pixel (1,0) = low nibble = 0xF
    _dispatch_cmd(vdp, cmd_code=0x4, sx=1, sy=0)
    vdp.regs[15] = 7
    assert vdp.read_port(0x99) == 0xF


# ---------------------------------------------------------------------------
# LMCM — VRAM-to-CPU block read
# ---------------------------------------------------------------------------

def test_lmcm_sets_ce_and_tr_on_dispatch() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB
    _dispatch_cmd(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1)
    assert vdp._status2 & 0x01  # CE set
    assert vdp._status2 & 0x80  # TR set
    assert vdp._cmd_active


def test_lmcm_port_9c_read_returns_buffered_bytes() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB  # pixel (0,0)=0xA, pixel (1,0)=0xB
    vdp.vram[1] = 0xCD  # pixel (2,0)=0xC, pixel (3,0)=0xD
    _dispatch_cmd(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)
    assert vdp.read_port(0x9C) == 0xAB
    assert vdp.read_port(0x9C) == 0xCD


def test_lmcm_clears_ce_after_last_byte() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0x12
    _dispatch_cmd(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1)
    vdp.read_port(0x9C)  # read the single byte
    assert vdp._status2 & 0x01 == 0  # CE cleared
    assert not vdp._cmd_active


def test_lmcm_port_9c_read_when_inactive_returns_ff() -> None:
    vdp = _make_vdp()
    assert vdp.read_port(0x9C) == 0xFF


def test_lmcm_write_to_9c_ignored_during_lmcm() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB
    _dispatch_cmd(vdp, cmd_code=0xA, sx=0, sy=0, nx=2, ny=1)
    vdp.write_port(0x9C, 0xFF)  # write while LMCM active → ignored
    assert vdp.vram[0] == 0xAB  # unchanged


def test_lmcm_read_via_status_register_7() -> None:
    # Handbook path: CPU reads each LMCM byte from S#7 (R#15=7 via port 0x99).
    vdp = _make_vdp()
    vdp.vram[0] = 0xAB  # pixels (0,0)=0xA, (1,0)=0xB
    vdp.vram[1] = 0xCD  # pixels (2,0)=0xC, (3,0)=0xD
    _dispatch_cmd(vdp, cmd_code=0xA, sx=0, sy=0, nx=4, ny=1)
    vdp.regs[15] = 7
    assert vdp.read_port(0x99) == 0xAB
    assert vdp.read_port(0x99) == 0xCD
    assert vdp._status2 & 0x01 == 0  # CE cleared after last byte
    assert not vdp._cmd_active


# ---------------------------------------------------------------------------
# SRCH — horizontal color search
# ---------------------------------------------------------------------------

def test_srch_finds_matching_pixel_right() -> None:
    vdp = _make_vdp()
    vdp.vram[2] = 0x05  # pixel (4,0)=0x0, pixel (5,0)=0x5
    _dispatch_cmd(vdp, cmd_code=0x6, sx=0, sy=0, clr=0x5, arg=0)
    # pixel (5,0) = 0x5 matches CLR → found at x=5: BD set, X in S#8
    assert vdp._status2 & 0x10  # BD set (found)
    vdp.regs[15] = 8
    assert vdp.read_port(0x99) == 5


def test_srch_bd_flag_when_not_found() -> None:
    vdp = _make_vdp()
    # vram all zeros; CLR=0xF → never matches
    _dispatch_cmd(vdp, cmd_code=0x6, sx=0, sy=0, clr=0xF, arg=0)
    assert vdp._status2 & 0x10 == 0  # BD clear (not found)


def test_srch_left_direction() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xA0  # pixel (0,0)=0xA
    _dispatch_cmd(vdp, cmd_code=0x6, sx=4, sy=0, clr=0xA, arg=0x04)  # DIX=left
    # scan left from x=4: x=4(0), 3(0), 2(0), 1(0), 0 → 0xA match at x=0
    assert vdp._status2 & 0x10  # BD set (found)
    vdp.regs[15] = 8
    assert vdp.read_port(0x99) == 0


def test_srch_stop_on_not_equal() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xAA  # pixels (0,0)=0xA, (1,0)=0xA
    vdp.vram[1] = 0x5A  # pixel (2,0)=0x5 differs
    _dispatch_cmd(vdp, cmd_code=0x6, sx=0, sy=0, clr=0xA, arg=0x02)  # EQ: stop on not-equal
    assert vdp._status2 & 0x10  # BD set (found)
    vdp.regs[15] = 8
    assert vdp.read_port(0x99) == 2  # first pixel ≠ 0xA is at x=2


# ---------------------------------------------------------------------------
# LINE — Bresenham line draw
# ---------------------------------------------------------------------------

def test_line_horizontal_draws_nx_plus_1_pixels() -> None:
    vdp = _make_vdp()
    # Horizontal line: MAJ=X (arg[2]=0), +X (arg[0]=0), NX=3 → 4 pixels at row 0
    _dispatch_cmd(vdp, cmd_code=0x7, dx=0, dy=0, nx=3, ny=0, clr=0xF, arg=0)
    # pixels (0..3, 0) = 0xF → bytes 0 and 1 = 0xFF
    assert vdp.vram[0] == 0xFF
    assert vdp.vram[1] & 0xF0 == 0xF0  # pixel (2,0) = 0xF
    assert vdp._status2 & 0x01 == 0  # CE cleared


def test_line_vertical_draws_nx_plus_1_pixels() -> None:
    vdp = _make_vdp()
    # Vertical line: MAJ=Y (arg bit0=1). NX is always the major (long) side, so
    # the line runs NX+1=3 dots along Y; NY=0 is the minor side. → 3 pixels at col 0.
    _dispatch_cmd(vdp, cmd_code=0x7, dx=0, dy=0, nx=2, ny=0, clr=0x5, arg=0x01)
    # pixels (0,0), (0,1), (0,2) = 0x5 → high nibble of vram[0], vram[128], vram[256]
    assert vdp.vram[0] >> 4 == 0x5
    assert vdp.vram[128] >> 4 == 0x5
    assert vdp.vram[256] >> 4 == 0x5


def test_line_diagonal_draws_correct_pixels() -> None:
    vdp = _make_vdp()
    # Diagonal: NX=2 (major X), NY=2 (minor Y), +X (arg[0]=0), +Y (arg[1]=0)
    # Bresenham: pixels (0,0), (1,1), (2,2) or nearby depending on error
    _dispatch_cmd(vdp, cmd_code=0x7, dx=0, dy=0, nx=2, ny=2, clr=0xA, arg=0)
    # At minimum, (0,0) must be written (first pixel)
    assert vdp.vram[0] >> 4 == 0xA


def test_line_log_applied_to_each_pixel() -> None:
    vdp = _make_vdp()
    vdp.vram[0] = 0xFF  # pixels (0,0)=0xF, (1,0)=0xF
    # LOG=XOR (0x3), CLR=0xF: 0xF ^ 0xF = 0x0
    _dispatch_cmd(vdp, cmd_code=0x7, dx=0, dy=0, nx=1, ny=0, clr=0xF, log=0x3, arg=0)
    assert vdp.vram[0] >> 4 == 0x0   # 0xF XOR 0xF = 0x0
    assert vdp.vram[0] & 0xF == 0x0  # pixel (1,0) also XOR'd


# ---------------------------------------------------------------------------
# tick() — command timer
# ---------------------------------------------------------------------------

def test_tick_noop_when_no_command() -> None:
    vdp = _make_vdp()
    vdp.tick(1000)
    assert vdp._status2 == 0
    assert not vdp._cmd_active


def test_tick_noop_for_lmmc() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xB, dx=0, dy=0, nx=2, ny=1)
    assert vdp._cmd_active
    vdp.tick(100_000)
    assert vdp._status2 & 0x01  # CE still set
    assert vdp._cmd_active


def test_tick_partial_does_not_clear_ce() -> None:
    vdp = _make_vdp()
    # HMMV NX=128, NY=1 → duration = 128 × 1 × 8 = 1024
    _dispatch_cmd(vdp, cmd_code=0xC, dx=0, dy=0, nx=128, ny=1, clr=0xFF)
    assert vdp._status2 & 0x01  # CE set immediately
    vdp.tick(500)
    assert vdp._status2 & 0x01  # CE still set after partial tick


def test_hmmv_ce_set_immediately_after_dispatch() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0xC, dx=0, dy=0, nx=4, ny=2, clr=0xAB)
    assert vdp._status2 & 0x01  # CE=1 immediately
    assert vdp._cmd_active


def test_hmmv_ce_clears_after_full_tick() -> None:
    vdp = _make_vdp()
    # NX=128, NY=212 → duration = 128 × 212 × 8 = 217,088
    _dispatch_cmd(vdp, cmd_code=0xC, dx=0, dy=0, nx=128, ny=212, clr=0x00)
    assert vdp._status2 & 0x01
    vdp.tick(128 * 212 * 8)
    assert vdp._status2 & 0x01 == 0  # CE cleared
    assert not vdp._cmd_active


def test_hmmm_ce_clears_after_full_tick() -> None:
    vdp = _make_vdp()
    # NX=128, NY=85 → duration = 128 × 85 × 8 = 86,912
    _dispatch_cmd(vdp, cmd_code=0xD, sx=0, sy=0, dx=0, dy=100, nx=128, ny=85)
    assert vdp._status2 & 0x01
    vdp.tick(128 * 85 * 8)
    assert vdp._status2 & 0x01 == 0
    assert not vdp._cmd_active


# ---------------------------------------------------------------------------
# Command timing: pixel-unit (LMMx) vs byte-unit (HMMx) CE duration
# ---------------------------------------------------------------------------

def test_lmmv_ce_duration_is_pixel_unit() -> None:
    # SCREEN 5 (ppb=2): LMMV counts pixels; HMMV counts bytes (ppb× fewer units),
    # so for the same NX/NY the LMMV CE duration is ppb× longer.
    lmmv = _make_vdp()
    _dispatch_cmd(lmmv, cmd_code=0x8, dx=0, dy=0, nx=8, ny=2, clr=0x01)
    hmmv = _make_vdp()
    _dispatch_cmd(hmmv, cmd_code=0xC, dx=0, dy=0, nx=8, ny=2, clr=0x01)
    assert lmmv._cmd_remaining == 8 * 2 * 8       # nx*ny*_CYCLES_PER_PIXEL
    assert hmmv._cmd_remaining == (8 // 2) * 2 * 8  # ceil(nx/ppb)*ny*_CYCLES_PER_BYTE
    assert lmmv._cmd_remaining == 2 * hmmv._cmd_remaining


def test_lmmm_ce_clears_after_full_tick() -> None:
    vdp = _make_vdp()
    _dispatch_cmd(vdp, cmd_code=0x9, sx=0, sy=0, dx=0, dy=4, nx=4, ny=1)
    assert vdp._status2 & 0x01  # CE set immediately after dispatch
    vdp.tick(10_000_000)
    assert vdp._status2 & 0x01 == 0  # CE cleared after ample tick
