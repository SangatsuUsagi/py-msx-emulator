"""The VDP status views of the debugger's `v` command: text built from the core
API's VDP record. It reads no other state."""
from __future__ import annotations

from typing import TYPE_CHECKING

from host.trace import VDP_COMMAND_NAMES, VDP_LOGICAL_OP_NAMES

if TYPE_CHECKING:
    from msx.boundary import VdpInfo


def _mode_bits(r0: int, r1: int) -> tuple[int, int, int, int, int]:
    """(M1, M2, M3, M4, M5): R#1 bits 4 and 3, R#0 bits 1, 2 and 3."""
    return (r1 >> 4) & 1, (r1 >> 3) & 1, (r0 >> 1) & 1, (r0 >> 2) & 1, (r0 >> 3) & 1


def decode_screen_mode(r0: int, r1: int) -> str:
    m1, m2, m3, m4, m5 = _mode_bits(r0, r1)
    modes: dict[tuple[int, ...], str] = {
        (0, 0, 0, 0, 0): "SCREEN1 (GRAPHIC1)",
        (1, 0, 0, 0, 0): "SCREEN0 (TEXT1/40col)",
        (1, 0, 0, 1, 0): "SCREEN0 (TEXT2/80col)",
        (0, 0, 1, 0, 0): "SCREEN2 (GRAPHIC2)",
        (0, 1, 0, 0, 0): "SCREEN3 (MULTICOLOR)",
        (0, 0, 0, 1, 0): "SCREEN4 (GRAPHIC3)",
        (0, 0, 1, 1, 0): "SCREEN5 (GRAPHIC4)",
        (0, 0, 0, 0, 1): "SCREEN6 (GRAPHIC5)",
        (0, 0, 1, 0, 1): "SCREEN7 (GRAPHIC6)",
        (0, 0, 1, 1, 1): "SCREEN8 (GRAPHIC7)",
    }
    return modes.get((m1, m2, m3, m4, m5), f"UNKNOWN (M={m1}{m2}{m3}{m4}{m5})")


def decode_cmr(cmr: int) -> tuple[str, str]:
    cmd_code = (cmr >> 4) & 0xF
    log_code = cmr & 0x07
    transparent = bool(cmr & 0x08)
    cmd = VDP_COMMAND_NAMES.get(cmd_code, f"CMD{cmd_code:X}")
    log = VDP_LOGICAL_OP_NAMES.get(log_code, f"LOG{log_code}")
    if transparent:
        log += "/T"
    return cmd, log


def print_vdp_v9938(vdp: VdpInfo) -> None:
    print(f"  Screen : {decode_screen_mode(vdp.regs[0], vdp.regs[1])}")
    _print_vdp_vram_layout(vdp)
    _print_vdp_display_control(vdp)
    _print_vdp_command_state(vdp)
    _print_vdp_status(vdp)


def _print_vdp_vram_layout(vdp: VdpInfo) -> None:
    r = vdp.regs          # R#0-R#27
    m1, _m2, m3, m4, m5 = _mode_bits(r[0], r[1])
    is_text2 = bool(m4 and m1 and not m3)
    if is_text2:
        name_base = (r[2] & 0x7C) << 10  # TEXT2: 5 bits, 4 KB aligned
    elif m4 or m5:
        name_base = (r[2] & 0x60) << 10
    else:
        name_base = (r[2] & 0x0F) << 10
    color_base   = ((r[10] & 0x07) << 14) | ((r[3]  & 0xFF) << 6)
    pattern_base = (r[4]  & 0x3F) << 11
    # R#5/R#11 → SAT base (512-byte aligned; colour table sits at SAT-0x200)
    attr_reg     = (((r[11] & 0x03) << 15) | (r[5] << 7)) & 0x1FFFF
    sprite_attr  = attr_reg & ~0x1FF & 0x1FFFF
    sprite_pat   = (r[6]  & 0x3F) << 11
    print(
        f"  VRAM   : Name={name_base:05X}  Color={color_base:05X}"
        f"  Pattern={pattern_base:05X}  SprAttr={sprite_attr:05X}  SprPat={sprite_pat:05X}"
    )


def _print_vdp_display_control(vdp: VdpInfo) -> None:
    r = vdp.regs
    disp    = "ON " if r[1] & 0x40 else "OFF"
    sprites = "OFF" if r[8] & 0x02 else "ON "
    height  = 212 if r[9] & 0x80 else 192
    timing  = "PAL " if r[9] & 0x02 else "NTSC"
    ilace   = " IL" if r[9] & 0x08 else "   "
    spr_sz  = "16x16" if r[1] & 0x02 else " 8x8 "
    spr_mag = "x2" if r[1] & 0x01 else "x1"
    fg      = (r[7] >> 4) & 0xF
    bg      = r[7] & 0xF
    ie0     = "IE0" if r[1] & 0x20 else "   "
    ie1     = "IE1" if r[0] & 0x10 else "   "
    print(
        f"  Disp   : EN={disp}  SPR={sprites}  {height}L  {timing}{ilace}"
        f"  Spr={spr_sz}/mag={spr_mag}  FG={fg:X} BG={bg:X}  {ie0} {ie1}"
    )


def _print_vdp_command_state(vdp: VdpInfo) -> None:
    c = vdp.cmd_regs      # R#32-R#46 (index 0-14)
    s2 = vdp.status[2]
    sx  = c[0]  | (c[1]  & 0x01) << 8
    sy  = c[2]  | (c[3]  & 0x03) << 8
    dx  = c[4]  | (c[5]  & 0x01) << 8
    dy  = c[6]  | (c[7]  & 0x03) << 8
    nx  = c[8]  | (c[9]  & 0x01) << 8
    ny  = c[10] | (c[11] & 0x03) << 8
    clr = c[12]
    arg = c[13]
    cmr = c[14]
    cmd_name, log_op = decode_cmr(cmr)
    dix  = "←" if arg & 0x04 else "→"
    diy  = "↑" if arg & 0x08 else "↓"
    if s2 & 0x01:
        rem = vdp.cmd_remaining
        if rem > 0:
            busy = f"BUSY({rem}cyc)"
        else:
            total = (vdp.cmd_nx // 2) * vdp.cmd_ny
            done  = vdp.cmd_y * (vdp.cmd_nx // 2) + vdp.cmd_x // 2
            busy  = f"BUSY(CPU-feed rem={total - done}/{total})"
    else:
        busy = "IDLE"
    print(
        f"  CMD    : {cmd_name}  {log_op}  [{busy}]"
        f"  SRC=({sx},{sy})  DST=({dx},{dy})  SIZE=({nx},{ny})"
        f"  CLR={clr:02X}  DIR={dix}{diy}"
    )


def _print_vdp_status(vdp: VdpInfo) -> None:
    s0 = vdp.status[0]
    s2 = vdp.status[2]
    tr  = "TR" if s2 & 0x80 else "  "
    ce  = "CE" if s2 & 0x01 else "  "
    vf  = "F"  if s0 & 0x80 else " "
    sp  = f"5S={(s0 >> 6) & 1}"
    col = f"C={(s0 >> 5) & 1}"
    print(f"  Status : S#0={s0:02X} ({vf} {sp} {col})  S#2={s2:02X} ({tr} {ce})")


def print_vdp_tms(vdp: VdpInfo) -> None:
    r = vdp.regs
    s0 = vdp.status[0]

    print(f"  Screen : {decode_screen_mode(r[0], r[1])}")

    name_base    = (r[2] & 0x0F) << 10
    color_base   = r[3] << 6
    pattern_base = (r[4] & 0x07) << 11
    sprite_attr  = (r[5] & 0x7F) << 7
    sprite_pat   = (r[6] & 0x07) << 11
    print(
        f"  VRAM   : Name={name_base:04X}  Color={color_base:04X}"
        f"  Pat={pattern_base:04X}  SprAttr={sprite_attr:04X}  SprPat={sprite_pat:04X}"
    )

    en      = (r[1] >> 6) & 1
    fg      = (r[7] >> 4) & 0xF
    bg      = r[7] & 0xF
    spr_sz  = 16 if r[1] & 0x02 else 8
    spr_mag = r[1] & 0x01
    print(
        f"  Disp   : EN={en}  h=192  FG={fg:X}  BG={bg:X}  size={spr_sz}  mag={spr_mag}"
    )

    print(f"  Status : S#0={s0:02X}")
