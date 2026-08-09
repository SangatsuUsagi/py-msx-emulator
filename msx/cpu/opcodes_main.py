"""Z80 main opcode table (256 entries) plus CB/DD/ED/FD prefix dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from msx.cpu import flags as F

if TYPE_CHECKING:
    from msx.cpu.z80 import Z80

# ---------------------------------------------------------------------------
# Module-level opcode dispatch constants (avoids per-call dict allocation)
# ---------------------------------------------------------------------------

# Maps INC r / DEC r opcode → register index (B=0,C=1,D=2,E=3,H=4,L=5,(HL)=6,A=7)
_INC_OPS: dict[int, int] = {
    0x04: 0,
    0x0C: 1,
    0x14: 2,
    0x1C: 3,
    0x24: 4,
    0x2C: 5,
    0x34: 6,
    0x3C: 7,
}
_DEC_OPS: dict[int, int] = {
    0x05: 0,
    0x0D: 1,
    0x15: 2,
    0x1D: 3,
    0x25: 4,
    0x2D: 5,
    0x35: 6,
    0x3D: 7,
}

# ---------------------------------------------------------------------------
# Helpers: flag computation
# ---------------------------------------------------------------------------


def _szp(v: int) -> int:
    f = 0
    if v == 0:
        f |= F.FLAG_Z
    if v & 0x80:
        f |= F.FLAG_S
    if F.parity(v):
        f |= F.FLAG_PV
    return f


# ---------------------------------------------------------------------------
# Arithmetic helpers — integer-width / overflow porting contract
#
# These rely on Python's arbitrary-precision int and MUST be widened when
# porting to Rust/C++ (fixed-width, wrapping/panicking) integers:
#   - Addition (_add8/_add16/_adc16): `result` is intentionally the UN-masked
#     sum, so it can exceed the operand width (u8 -> up to 0x1FE, u16 -> up to
#     0x1FFFF). Carry is `result > 0xFF`/`0xFFFF` and half-carry reads bit 4/12
#     of `result`. A port must compute in a WIDER accumulator (u8->u16,
#     u16->u32), test carry, then mask; it must NOT add in the result width.
#   - Subtraction (_sub8/_dec8/_sbc16, and NEG via _sub8(cpu, 0, A), plus the
#     CPI/CPD block search): `result` can be NEGATIVE. Borrow is `result < 0`
#     and the low byte relies on Python two's-complement masking of a negative
#     int (`-1 & 0xFF == 0xFF`). A port must compute in a SIGNED wider type
#     (i16/i32), test `< 0` for borrow, then cast the low bits.
# ---------------------------------------------------------------------------


def _add8(cpu: Z80, a: int, b: int, carry: int = 0) -> int:
    result = a + b + carry
    r8 = result & 0xFF
    f = 0
    if r8 == 0:
        f |= F.FLAG_Z
    if r8 & 0x80:
        f |= F.FLAG_S
    if result > 0xFF:
        f |= F.FLAG_C
    if (a ^ b ^ result) & 0x10:
        f |= F.FLAG_H
    overflow = (~(a ^ b) & (a ^ result)) & 0x80
    if overflow:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return r8


def _sub8(cpu: Z80, a: int, b: int, carry: int = 0) -> int:
    result = a - b - carry
    r8 = result & 0xFF
    f = F.FLAG_N
    if r8 == 0:
        f |= F.FLAG_Z
    if r8 & 0x80:
        f |= F.FLAG_S
    if result < 0:
        f |= F.FLAG_C
    if (a ^ b ^ result) & 0x10:
        f |= F.FLAG_H
    overflow = ((a ^ b) & (a ^ result)) & 0x80
    if overflow:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return r8


def _and8(cpu: Z80, v: int) -> None:
    r = cpu.registers
    result = r.A & v
    r.A = result
    r.F = F.FLAG_H | _szp(result)


def _or8(cpu: Z80, v: int) -> None:
    r = cpu.registers
    result = r.A | v
    r.A = result
    r.F = _szp(result)


def _xor8(cpu: Z80, v: int) -> None:
    r = cpu.registers
    result = r.A ^ v
    r.A = result
    r.F = _szp(result)


def _cp8(cpu: Z80, v: int) -> None:
    _sub8(cpu, cpu.registers.A, v)


def _inc8(cpu: Z80, v: int) -> int:
    result = (v + 1) & 0xFF
    f = cpu.registers.F & F.FLAG_C  # preserve C
    if result == 0:
        f |= F.FLAG_Z
    if result & 0x80:
        f |= F.FLAG_S
    if (v & 0x0F) == 0x0F:
        f |= F.FLAG_H
    if v == 0x7F:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return result


def _dec8(cpu: Z80, v: int) -> int:
    result = (v - 1) & 0xFF
    f = (cpu.registers.F & F.FLAG_C) | F.FLAG_N  # preserve C, set N
    if result == 0:
        f |= F.FLAG_Z
    if result & 0x80:
        f |= F.FLAG_S
    if (v & 0x0F) == 0x00:
        f |= F.FLAG_H
    if v == 0x80:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return result


def _block_io_flags(cpu: Z80, value: int, b_after: int, k: int) -> None:
    """Set flags for INI/IND/OUTI/OUTD (Sean Young, Undocumented Z80 §4.3).

    Args:
        cpu: The CPU whose F register is updated.
        value: The byte transferred to/from the port.
        b_after: The B register after its post-transfer decrement.
        k: Intermediate value; (value + (C+1)) for INI, (value + (C-1)) for IND,
            (value + L) for OUTI/OUTD.
    """
    f = 0
    if b_after == 0:
        f |= F.FLAG_Z
    if b_after & 0x80:
        f |= F.FLAG_S
    # Undocumented Y (bit 5) and X (bit 3) come from the decremented B.
    f |= b_after & (0x20 | 0x08)
    if value & 0x80:
        f |= F.FLAG_N
    if k > 0xFF:
        f |= F.FLAG_H | F.FLAG_C
    if F.parity((k & 0x07) ^ b_after):
        f |= F.FLAG_PV
    cpu.registers.F = f


def _add16(cpu: Z80, hl: int, rr: int) -> int:
    result = hl + rr
    f = cpu.registers.F & ~(F.FLAG_H | F.FLAG_N | F.FLAG_C)
    if result > 0xFFFF:
        f |= F.FLAG_C
    if (hl ^ rr ^ result) & 0x1000:
        f |= F.FLAG_H
    cpu.registers.F = f & 0xFF
    return result & 0xFFFF


def _adc16(cpu: Z80, hl: int, rr: int) -> int:
    c = 1 if (cpu.registers.F & F.FLAG_C) else 0
    result = hl + rr + c
    r16 = result & 0xFFFF
    f = 0
    if r16 == 0:
        f |= F.FLAG_Z
    if r16 & 0x8000:
        f |= F.FLAG_S
    if result > 0xFFFF:
        f |= F.FLAG_C
    if (hl ^ rr ^ result) & 0x1000:
        f |= F.FLAG_H
    overflow = (~(hl ^ rr) & (hl ^ result)) & 0x8000
    if overflow:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return r16


def _sbc16(cpu: Z80, hl: int, rr: int) -> int:
    c = 1 if (cpu.registers.F & F.FLAG_C) else 0
    result = hl - rr - c
    r16 = result & 0xFFFF
    f = F.FLAG_N
    if r16 == 0:
        f |= F.FLAG_Z
    if r16 & 0x8000:
        f |= F.FLAG_S
    if result < 0:
        f |= F.FLAG_C
    if (hl ^ rr ^ result) & 0x1000:
        f |= F.FLAG_H
    overflow = ((hl ^ rr) & (hl ^ result)) & 0x8000
    if overflow:
        f |= F.FLAG_PV
    cpu.registers.F = f
    return r16


# signed byte from unsigned
def _signed(v: int) -> int:
    return v if v < 128 else v - 256


# ---------------------------------------------------------------------------
# Register index helpers (bit pattern B=0,C=1,D=2,E=3,H=4,L=5,(HL)=6,A=7)
# ---------------------------------------------------------------------------


def _get_r(cpu: Z80, idx: int) -> int:
    r = cpu.registers
    if idx == 0:
        return r.B
    if idx == 1:
        return r.C
    if idx == 2:
        return r.D
    if idx == 3:
        return r.E
    if idx == 4:
        return r.H
    if idx == 5:
        return r.L
    if idx == 6:
        return cpu.read_byte(r.HL)
    return r.A


def _set_r(cpu: Z80, idx: int, v: int) -> None:
    r = cpu.registers
    if idx == 0:
        r.B = v
    elif idx == 1:
        r.C = v
    elif idx == 2:
        r.D = v
    elif idx == 3:
        r.E = v
    elif idx == 4:
        r.H = v
    elif idx == 5:
        r.L = v
    elif idx == 6:
        cpu.write_byte(r.HL, v)
    else:
        r.A = v


# ---------------------------------------------------------------------------
# CB prefix
# ---------------------------------------------------------------------------


def _rlc(cpu: Z80, v: int) -> int:
    c = (v >> 7) & 1
    result = ((v << 1) | c) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _rrc(cpu: Z80, v: int) -> int:
    c = v & 1
    result = ((v >> 1) | (c << 7)) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _rl(cpu: Z80, v: int) -> int:
    old_c = 1 if (cpu.registers.F & F.FLAG_C) else 0
    c = (v >> 7) & 1
    result = ((v << 1) | old_c) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _rr(cpu: Z80, v: int) -> int:
    old_c = 1 if (cpu.registers.F & F.FLAG_C) else 0
    c = v & 1
    result = ((v >> 1) | (old_c << 7)) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _sla(cpu: Z80, v: int) -> int:
    c = (v >> 7) & 1
    result = (v << 1) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _sra(cpu: Z80, v: int) -> int:
    c = v & 1
    result = ((v >> 1) | (v & 0x80)) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _sll(cpu: Z80, v: int) -> int:
    c = (v >> 7) & 1
    result = ((v << 1) | 1) & 0xFF  # undocumented: bit 0 forced to 1
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


def _srl(cpu: Z80, v: int) -> int:
    c = v & 1
    result = (v >> 1) & 0xFF
    cpu.registers.F = (F.FLAG_C if c else 0) | _szp(result)
    return result


# Portability: this tuple-of-closures dispatch (like the module-level
# _DISPATCH / _ED_DISPATCH tables) is a Python idiom. A Rust/C++ port
# expresses it as a `match` or a static function-pointer array rather than
# indexing a tuple of function objects. Hoisted to module scope (shared by
# _execute_cb and the DD/FD CB path) to avoid a per-call list allocation.
_CB_ROTATE_FNS: tuple[Callable[[Z80, int], int], ...] = (
    _rlc,
    _rrc,
    _rl,
    _rr,
    _sla,
    _sra,
    _sll,
    _srl,
)


def _execute_cb(cpu: Z80) -> int:
    op = cpu._fetch()
    row = op >> 6
    bit = (op >> 3) & 7
    reg = op & 7
    v = _get_r(cpu, reg)
    cycles = 8 if reg != 6 else 15

    if row == 0:
        result = _CB_ROTATE_FNS[bit](cpu, v)
        _set_r(cpu, reg, result)
    elif row == 1:  # BIT
        f = (cpu.registers.F & F.FLAG_C) | F.FLAG_H
        if not (v & (1 << bit)):
            f |= F.FLAG_Z | F.FLAG_PV
        if (v & (1 << bit)) and bit == 7:
            f |= F.FLAG_S
        cpu.registers.F = f
        cycles = 8 if reg != 6 else 12
    elif row == 2:  # RES
        _set_r(cpu, reg, v & ~(1 << bit))
    else:  # SET
        _set_r(cpu, reg, v | (1 << bit))

    return cycles


# ---------------------------------------------------------------------------
# DD/FD prefix helpers  (IX or IY as base)
# ---------------------------------------------------------------------------


def _dd_fd_ld_ixy_nn(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    nn = cpu._fetch_word()
    if use_iy:
        r.IY = nn
    else:
        r.IX = nn
    return 14


def _dd_fd_ld_ind_nn_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    xy = r.IY if use_iy else r.IX
    nn = cpu._fetch_word()
    cpu.write_byte(nn, xy & 0xFF)
    cpu.write_byte((nn + 1) & 0xFFFF, (xy >> 8) & 0xFF)
    return 20


def _dd_fd_ld_ixy_ind_nn(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    nn = cpu._fetch_word()
    lo = cpu.read_byte(nn)
    hi = cpu.read_byte((nn + 1) & 0xFFFF)
    val = (hi << 8) | lo
    if use_iy:
        r.IY = val
    else:
        r.IX = val
    return 20


def _dd_fd_inc_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IY = (r.IY + 1) & 0xFFFF
    else:
        r.IX = (r.IX + 1) & 0xFFFF
    return 10


def _dd_fd_dec_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IY = (r.IY - 1) & 0xFFFF
    else:
        r.IX = (r.IX - 1) & 0xFFFF
    return 10


def _dd_fd_add_ixy_rr(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    xy = r.IY if use_iy else r.IX
    if op == 0x09:
        rr = r.BC
    elif op == 0x19:
        rr = r.DE
    elif op == 0x29:
        rr = xy
    else:
        rr = r.SP
    result = _add16(cpu, xy, rr)
    if use_iy:
        r.IY = result
    else:
        r.IX = result
    return 15


def _dd_fd_push_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    cpu._push(r.IY if use_iy else r.IX)
    return 15


def _dd_fd_pop_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    val = cpu._pop()
    if use_iy:
        r.IY = val
    else:
        r.IX = val
    return 14


def _dd_fd_ex_sp_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    xy = r.IY if use_iy else r.IX
    lo = cpu.read_byte(r.SP)
    hi = cpu.read_byte((r.SP + 1) & 0xFFFF)
    cpu.write_byte(r.SP, xy & 0xFF)
    cpu.write_byte((r.SP + 1) & 0xFFFF, (xy >> 8) & 0xFF)
    val = (hi << 8) | lo
    if use_iy:
        r.IY = val
    else:
        r.IX = val
    return 23


def _dd_fd_jp_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    r.PC = r.IY if use_iy else r.IX
    return 8


def _dd_fd_ld_sp_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    r.SP = r.IY if use_iy else r.IX
    return 10


def _dd_fd_ea(cpu: Z80, use_iy: bool) -> int:
    r = cpu.registers
    xy = r.IY if use_iy else r.IX
    d = _signed(cpu._fetch())
    return (xy + d) & 0xFFFF


def _dd_fd_inc_ind_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    ea = _dd_fd_ea(cpu, use_iy)
    v = cpu.read_byte(ea)
    cpu.write_byte(ea, _inc8(cpu, v))
    return 23


def _dd_fd_dec_ind_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    ea = _dd_fd_ea(cpu, use_iy)
    v = cpu.read_byte(ea)
    cpu.write_byte(ea, _dec8(cpu, v))
    return 23


def _dd_fd_ld_ind_ixy_n(cpu: Z80, use_iy: bool, op: int) -> int:
    ea = _dd_fd_ea(cpu, use_iy)
    n = cpu._fetch()
    cpu.write_byte(ea, n)
    return 19


def _dd_fd_ld_r_ind_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD r, (IX+d)/(IY+d) — dst register index is (op >> 3) & 7 for
    # 0x46/4E/56/5E/66/6E/7E (never 6, since (HL) doesn't apply here).
    ea = _dd_fd_ea(cpu, use_iy)
    _set_r(cpu, (op >> 3) & 7, cpu.read_byte(ea))
    return 19


def _dd_fd_ld_ind_ixy_r(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD (IX+d)/(IY+d), r — src register index is op & 7 for
    # 0x70/71/72/73/74/75/77 (never 6; 0x76 is HALT, not in this group).
    ea = _dd_fd_ea(cpu, use_iy)
    cpu.write_byte(ea, _get_r(cpu, op & 7))
    return 19


def _dd_fd_alu_ind_ixy(cpu: Z80, use_iy: bool, op: int) -> int:
    # ALU A, (IX+d)/(IY+d) — group is (op >> 3) & 7, matching the
    # ADD/ADC/SUB/SBC/AND/XOR/OR/CP ordering used by _make_alu_r.
    ea = _dd_fd_ea(cpu, use_iy)
    v = cpu.read_byte(ea)
    r = cpu.registers
    grp = (op >> 3) & 7
    if grp == 0:
        r.A = _add8(cpu, r.A, v)
    elif grp == 1:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _add8(cpu, r.A, v, c)
    elif grp == 2:
        r.A = _sub8(cpu, r.A, v)
    elif grp == 3:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _sub8(cpu, r.A, v, c)
    elif grp == 4:
        _and8(cpu, v)
    elif grp == 5:
        _xor8(cpu, v)
    elif grp == 6:
        _or8(cpu, v)
    else:
        _cp8(cpu, v)
    return 19


def _dd_fd_cb(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    xy = r.IY if use_iy else r.IX
    d = _signed(cpu._fetch())
    cb_op = cpu._fetch()
    ea = (xy + d) & 0xFFFF
    v = cpu.read_byte(ea)
    row = cb_op >> 6
    bit = (cb_op >> 3) & 7
    if row == 0:
        result = _CB_ROTATE_FNS[bit](cpu, v)
        cpu.write_byte(ea, result)
    elif row == 1:
        f = (cpu.registers.F & F.FLAG_C) | F.FLAG_H
        if not (v & (1 << bit)):
            f |= F.FLAG_Z | F.FLAG_PV
        if (v & (1 << bit)) and bit == 7:
            f |= F.FLAG_S
        cpu.registers.F = f
        return 20
    elif row == 2:
        cpu.write_byte(ea, v & ~(1 << bit))
    else:
        cpu.write_byte(ea, v | (1 << bit))
    return 23


def _dd_fd_inc_ixy_h(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IYH = _inc8(cpu, r.IYH)
    else:
        r.IXH = _inc8(cpu, r.IXH)
    return 8


def _dd_fd_dec_ixy_h(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IYH = _dec8(cpu, r.IYH)
    else:
        r.IXH = _dec8(cpu, r.IXH)
    return 8


def _dd_fd_inc_ixy_l(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IYL = _inc8(cpu, r.IYL)
    else:
        r.IXL = _inc8(cpu, r.IXL)
    return 8


def _dd_fd_dec_ixy_l(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    if use_iy:
        r.IYL = _dec8(cpu, r.IYL)
    else:
        r.IXL = _dec8(cpu, r.IXL)
    return 8


def _dd_fd_ld_ixy_h_n(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    n = cpu._fetch()
    if use_iy:
        r.IYH = n
    else:
        r.IXH = n
    return 11


def _dd_fd_ld_ixy_l_n(cpu: Z80, use_iy: bool, op: int) -> int:
    r = cpu.registers
    n = cpu._fetch()
    if use_iy:
        r.IYL = n
    else:
        r.IXL = n
    return 11


def _dd_fd_ld_r_ixy_h(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD r, IXH/IYH (undocumented) — dst is (op >> 3) & 7 for 0x44/4C/54/5C/7C.
    r = cpu.registers
    xh = r.IYH if use_iy else r.IXH
    _set_r(cpu, (op >> 3) & 7, xh)
    return 8


def _dd_fd_ld_r_ixy_l(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD r, IXL/IYL (undocumented) — dst is (op >> 3) & 7 for 0x45/4D/55/5D/7D.
    r = cpu.registers
    xl = r.IYL if use_iy else r.IXL
    _set_r(cpu, (op >> 3) & 7, xl)
    return 8


def _dd_fd_xy_half_src_val(cpu: Z80, use_iy: bool, op: int) -> int:
    # Shared by "LD IXH,r" / "LD IXL,r": src is op & 7, where 4/5 mean
    # "the other half register" (self-copy included) rather than literal H/L.
    r = cpu.registers
    src = op & 7
    if src == 4:
        return r.IYH if use_iy else r.IXH
    if src == 5:
        return r.IYL if use_iy else r.IXL
    return _get_r(cpu, src)


def _dd_fd_ld_ixy_h_r(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD IXH/IYH, r (undocumented) — 0x60/61/62/63/64/65/67.
    # 0x66 = LD H,(IX+d), handled by _dd_fd_ld_r_ind_ixy instead.
    r = cpu.registers
    val = _dd_fd_xy_half_src_val(cpu, use_iy, op)
    if use_iy:
        r.IYH = val
    else:
        r.IXH = val
    return 8


def _dd_fd_ld_ixy_l_r(cpu: Z80, use_iy: bool, op: int) -> int:
    # LD IXL/IYL, r (undocumented) — 0x68/69/6A/6B/6C/6D/6F.
    # 0x6E = LD L,(IX+d), handled by _dd_fd_ld_r_ind_ixy instead.
    r = cpu.registers
    val = _dd_fd_xy_half_src_val(cpu, use_iy, op)
    if use_iy:
        r.IYL = val
    else:
        r.IXL = val
    return 8


def _dd_fd_alu_ixy_half(cpu: Z80, use_iy: bool, op: int) -> int:
    # Undocumented ADD/ADC/SUB/SBC/AND/XOR/OR/CP A, IXH/IXL/IYH/IYL.
    # grp = (op >> 3) & 7 matches the standard ALU ordering; op & 1 == 0
    # selects the high half, 1 selects the low half.
    r = cpu.registers
    if op & 1:
        half = r.IYL if use_iy else r.IXL
    else:
        half = r.IYH if use_iy else r.IXH
    grp = (op >> 3) & 7
    if grp == 0:
        r.A = _add8(cpu, r.A, half)
    elif grp == 1:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _add8(cpu, r.A, half, c)
    elif grp == 2:
        r.A = _sub8(cpu, r.A, half)
    elif grp == 3:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _sub8(cpu, r.A, half, c)
    elif grp == 4:
        _and8(cpu, half)
    elif grp == 5:
        _xor8(cpu, half)
    elif grp == 6:
        _or8(cpu, half)
    else:
        _cp8(cpu, half)
    return 8


_DD_FD_DISPATCH: dict[int, Callable[[Z80, bool, int], int]] = {}


def _build_dd_fd_dispatch() -> None:
    d = _DD_FD_DISPATCH
    d[0x21] = _dd_fd_ld_ixy_nn
    d[0x22] = _dd_fd_ld_ind_nn_ixy
    d[0x2A] = _dd_fd_ld_ixy_ind_nn
    d[0x23] = _dd_fd_inc_ixy
    d[0x2B] = _dd_fd_dec_ixy
    for op in (0x09, 0x19, 0x29, 0x39):
        d[op] = _dd_fd_add_ixy_rr
    d[0xE5] = _dd_fd_push_ixy
    d[0xE1] = _dd_fd_pop_ixy
    d[0xE3] = _dd_fd_ex_sp_ixy
    d[0xE9] = _dd_fd_jp_ixy
    d[0xF9] = _dd_fd_ld_sp_ixy

    d[0x34] = _dd_fd_inc_ind_ixy
    d[0x35] = _dd_fd_dec_ind_ixy
    d[0x36] = _dd_fd_ld_ind_ixy_n
    for op in (0x46, 0x4E, 0x56, 0x5E, 0x66, 0x6E, 0x7E):
        d[op] = _dd_fd_ld_r_ind_ixy
    for op in (0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x77):
        d[op] = _dd_fd_ld_ind_ixy_r
    for op in (0x86, 0x8E, 0x96, 0x9E, 0xA6, 0xAE, 0xB6, 0xBE):
        d[op] = _dd_fd_alu_ind_ixy

    d[0xCB] = _dd_fd_cb

    d[0x24] = _dd_fd_inc_ixy_h
    d[0x25] = _dd_fd_dec_ixy_h
    d[0x2C] = _dd_fd_inc_ixy_l
    d[0x2D] = _dd_fd_dec_ixy_l

    d[0x26] = _dd_fd_ld_ixy_h_n
    d[0x2E] = _dd_fd_ld_ixy_l_n

    for op in (0x44, 0x4C, 0x54, 0x5C, 0x7C):
        d[op] = _dd_fd_ld_r_ixy_h
    for op in (0x45, 0x4D, 0x55, 0x5D, 0x7D):
        d[op] = _dd_fd_ld_r_ixy_l
    for op in (0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x67):
        d[op] = _dd_fd_ld_ixy_h_r
    for op in (0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6F):
        d[op] = _dd_fd_ld_ixy_l_r

    for op in (
        0x84,
        0x85,
        0x8C,
        0x8D,
        0x94,
        0x95,
        0x9C,
        0x9D,
        0xA4,
        0xA5,
        0xAC,
        0xAD,
        0xB4,
        0xB5,
        0xBC,
        0xBD,
    ):
        d[op] = _dd_fd_alu_ixy_half


_build_dd_fd_dispatch()


def _execute_dd_fd(cpu: Z80, use_iy: bool) -> int:
    op = cpu._fetch()
    handler = _DD_FD_DISPATCH.get(op)
    if handler is not None:
        return handler(cpu, use_iy, op)
    # prefix absorbed — delegate to normal dispatch (real Z80 behavior)
    return _DISPATCH[op](cpu)


# ---------------------------------------------------------------------------
# ED prefix
# ---------------------------------------------------------------------------


def _ed_im(cpu: Z80, op: int) -> int:
    cpu.im = 0 if op == 0x46 else (1 if op == 0x56 else 2)
    return 8


def _ed_ld_i_a(cpu: Z80, op: int) -> int:
    cpu.registers.I = cpu.registers.A
    return 9


def _ed_ld_r_a(cpu: Z80, op: int) -> int:
    cpu.registers.R = cpu.registers.A
    return 9


def _ed_ld_a_ir(cpu: Z80, op: int) -> int:
    r = cpu.registers
    r.A = r.I if op == 0x57 else r.R
    f = (r.F & F.FLAG_C) | (F.FLAG_PV if cpu.iff2 else 0)
    if r.A == 0:
        f |= F.FLAG_Z
    if r.A & 0x80:
        f |= F.FLAG_S
    r.F = f
    return 9


def _ed_neg(cpu: Z80, op: int) -> int:
    cpu.registers.A = _sub8(cpu, 0, cpu.registers.A)
    return 8


def _ed_retn_reti(cpu: Z80, op: int) -> int:
    # RETN / RETI both copy IFF2 back into IFF1 on the real Z80; they differ
    # only in the opcode byte that external interrupt controllers observe, not
    # in their effect on the CPU's interrupt flip-flops (Zilog UM0080).
    cpu.iff1 = cpu.iff2
    cpu.registers.PC = cpu._pop()
    return 14


def _ed_adc_hl_rr(cpu: Z80, op: int) -> int:
    r = cpu.registers
    if op == 0x4A:
        rr = r.BC
    elif op == 0x5A:
        rr = r.DE
    elif op == 0x6A:
        rr = r.HL
    else:
        rr = r.SP
    r.HL = _adc16(cpu, r.HL, rr)
    return 15


def _ed_sbc_hl_rr(cpu: Z80, op: int) -> int:
    r = cpu.registers
    if op == 0x42:
        rr = r.BC
    elif op == 0x52:
        rr = r.DE
    elif op == 0x62:
        rr = r.HL
    else:
        rr = r.SP
    r.HL = _sbc16(cpu, r.HL, rr)
    return 15


def _ed_ld_ind_nn_rr(cpu: Z80, op: int) -> int:
    r = cpu.registers
    nn = cpu._fetch_word()
    if op == 0x43:
        val = r.BC
    elif op == 0x53:
        val = r.DE
    elif op == 0x63:
        val = r.HL
    else:
        val = r.SP
    cpu.write_byte(nn, val & 0xFF)
    cpu.write_byte((nn + 1) & 0xFFFF, (val >> 8) & 0xFF)
    return 20


def _ed_ld_rr_ind_nn(cpu: Z80, op: int) -> int:
    r = cpu.registers
    nn = cpu._fetch_word()
    lo = cpu.read_byte(nn)
    hi = cpu.read_byte((nn + 1) & 0xFFFF)
    val = (hi << 8) | lo
    if op == 0x4B:
        r.BC = val
    elif op == 0x5B:
        r.DE = val
    elif op == 0x6B:
        r.HL = val
    else:
        r.SP = val
    return 20


def _ed_in_r_c(cpu: Z80, op: int) -> int:
    # Port address is (B << 8) | C on the bus. Destination register index is
    # (op >> 3) & 7 for these opcodes (0x40,0x48,…,0x78).
    r = cpu.registers
    v = cpu.read_port((r.B << 8) | r.C)
    _set_r(cpu, (op >> 3) & 7, v)
    r.F = (r.F & F.FLAG_C) | _szp(v)
    return 12


def _ed_in_f_c(cpu: Z80, op: int) -> int:
    # IN F, (C) — 0x70: result discarded, flags set.
    r = cpu.registers
    v = cpu.read_port((r.B << 8) | r.C)
    r.F = (r.F & F.FLAG_C) | _szp(v)
    return 12


def _ed_out_c_r(cpu: Z80, op: int) -> int:
    # Port address is (B << 8) | C on the bus. Source register index is
    # (op >> 3) & 7 for these opcodes (0x41,0x49,…,0x79).
    r = cpu.registers
    cpu.write_port((r.B << 8) | r.C, _get_r(cpu, (op >> 3) & 7))
    return 12


def _ed_out_c_0(cpu: Z80, op: int) -> int:
    r = cpu.registers
    cpu.write_port((r.B << 8) | r.C, 0)
    return 12


def _ed_rld(cpu: Z80, op: int) -> int:
    r = cpu.registers
    mem_val = cpu.read_byte(r.HL)
    new_mem = ((mem_val << 4) | (r.A & 0x0F)) & 0xFF
    r.A = (r.A & 0xF0) | (mem_val >> 4)
    cpu.write_byte(r.HL, new_mem)
    r.F = (r.F & F.FLAG_C) | _szp(r.A)
    return 18


def _ed_rrd(cpu: Z80, op: int) -> int:
    r = cpu.registers
    mem_val = cpu.read_byte(r.HL)
    new_mem = ((r.A << 4) | (mem_val >> 4)) & 0xFF
    r.A = (r.A & 0xF0) | (mem_val & 0x0F)
    cpu.write_byte(r.HL, new_mem)
    r.F = (r.F & F.FLAG_C) | _szp(r.A)
    return 18


def _ed_block_ld(cpu: Z80, op: int) -> int:  # LDI / LDD / LDIR / LDDR
    r = cpu.registers
    val = cpu.read_byte(r.HL)
    cpu.write_byte(r.DE, val)
    inc = 1 if op in (0xA0, 0xB0) else -1
    r.HL = (r.HL + inc) & 0xFFFF
    r.DE = (r.DE + inc) & 0xFFFF
    r.BC = (r.BC - 1) & 0xFFFF
    f = r.F & ~(F.FLAG_H | F.FLAG_PV | F.FLAG_N)
    if r.BC != 0:
        f |= F.FLAG_PV
    r.F = f
    if op in (0xB0, 0xB8) and r.BC != 0:
        r.PC = (r.PC - 2) & 0xFFFF
        return 21
    return 16


def _ed_block_cp(cpu: Z80, op: int) -> int:  # CPI / CPD / CPIR / CPDR
    r = cpu.registers
    val = cpu.read_byte(r.HL)
    result = r.A - val
    inc = 1 if op in (0xA1, 0xB1) else -1
    r.HL = (r.HL + inc) & 0xFFFF
    r.BC = (r.BC - 1) & 0xFFFF
    f = (r.F & F.FLAG_C) | F.FLAG_N
    if (result & 0xFF) == 0:
        f |= F.FLAG_Z
    if result & 0x80:
        f |= F.FLAG_S
    if (r.A ^ val ^ result) & 0x10:
        f |= F.FLAG_H
    if r.BC != 0:
        f |= F.FLAG_PV
    r.F = f
    if op in (0xB1, 0xB9) and r.BC != 0 and (result & 0xFF) != 0:
        r.PC = (r.PC - 2) & 0xFFFF
        return 21
    return 16


def _ed_block_in(cpu: Z80, op: int) -> int:  # INI / IND / INIR / INDR
    r = cpu.registers
    inc = 1 if op in (0xA2, 0xB2) else -1
    val = cpu.read_port((r.B << 8) | r.C)
    cpu.write_byte(r.HL, val)
    r.HL = (r.HL + inc) & 0xFFFF
    r.B = (r.B - 1) & 0xFF
    # INI adds (C+1), IND adds (C-1); inc carries that sign.
    c_adj = (r.C + inc) & 0xFF
    _block_io_flags(cpu, val, r.B, val + c_adj)
    if op in (0xB2, 0xBA) and r.B != 0:
        r.PC = (r.PC - 2) & 0xFFFF
        return 21
    return 16


def _ed_block_out(cpu: Z80, op: int) -> int:  # OUTI / OUTD / OTIR / OTDR
    r = cpu.registers
    inc = 1 if op in (0xA3, 0xB3) else -1
    val = cpu.read_byte(r.HL)
    r.HL = (r.HL + inc) & 0xFFFF
    cpu.write_port((r.B << 8) | r.C, val)
    r.B = (r.B - 1) & 0xFF
    # OUTI/OUTD use L after HL has been incremented/decremented.
    _block_io_flags(cpu, val, r.B, val + r.L)
    if op in (0xB3, 0xBB) and r.B != 0:
        r.PC = (r.PC - 2) & 0xFFFF
        return 21
    return 16


def _ed_undefined(cpu: Z80, op: int) -> int:
    if cpu._logger is not None:
        cpu._logger.on_undefined_opcode((cpu.registers.PC - 2) & 0xFFFF, op)
    return 8


_ED_DISPATCH: dict[int, Callable[[Z80, int], int]] = {}


def _build_ed_dispatch() -> None:
    d = _ED_DISPATCH
    d[0x46] = _ed_im
    d[0x56] = _ed_im
    d[0x5E] = _ed_im
    d[0x47] = _ed_ld_i_a
    d[0x4F] = _ed_ld_r_a
    d[0x57] = _ed_ld_a_ir
    d[0x5F] = _ed_ld_a_ir
    d[0x44] = _ed_neg
    d[0x45] = _ed_retn_reti
    d[0x4D] = _ed_retn_reti
    for op in (0x4A, 0x5A, 0x6A, 0x7A):
        d[op] = _ed_adc_hl_rr
    for op in (0x42, 0x52, 0x62, 0x72):
        d[op] = _ed_sbc_hl_rr
    for op in (0x43, 0x53, 0x63, 0x73):
        d[op] = _ed_ld_ind_nn_rr
    for op in (0x4B, 0x5B, 0x6B, 0x7B):
        d[op] = _ed_ld_rr_ind_nn
    for op in (0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78):
        d[op] = _ed_in_r_c
    d[0x70] = _ed_in_f_c
    for op in (0x41, 0x49, 0x51, 0x59, 0x61, 0x69, 0x79):
        d[op] = _ed_out_c_r
    d[0x71] = _ed_out_c_0
    d[0x6F] = _ed_rld
    d[0x67] = _ed_rrd
    for op in (0xA0, 0xA8, 0xB0, 0xB8):
        d[op] = _ed_block_ld
    for op in (0xA1, 0xA9, 0xB1, 0xB9):
        d[op] = _ed_block_cp
    for op in (0xA2, 0xAA, 0xB2, 0xBA):
        d[op] = _ed_block_in
    for op in (0xA3, 0xAB, 0xB3, 0xBB):
        d[op] = _ed_block_out


_build_ed_dispatch()


def _execute_ed(cpu: Z80) -> int:
    op = cpu._fetch()
    handler = _ED_DISPATCH.get(op)
    if handler is not None:
        return handler(cpu, op)
    return _ed_undefined(cpu, op)


# ---------------------------------------------------------------------------
# Condition check helpers
# ---------------------------------------------------------------------------


def _cc(cpu: Z80, cond: int) -> bool:
    f = cpu.registers.F
    if cond == 0:
        return not (f & F.FLAG_Z)
    if cond == 1:
        return bool(f & F.FLAG_Z)
    if cond == 2:
        return not (f & F.FLAG_C)
    if cond == 3:
        return bool(f & F.FLAG_C)
    if cond == 4:
        return not (f & F.FLAG_PV)
    if cond == 5:
        return bool(f & F.FLAG_PV)
    if cond == 6:
        return not (f & F.FLAG_S)
    return bool(f & F.FLAG_S)


# ---------------------------------------------------------------------------
# Handler factory functions — build typed closures for regular opcode groups
# ---------------------------------------------------------------------------


def _make_ld_r_r(dst: int, src: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        _set_r(cpu, dst, _get_r(cpu, src))
        return 7 if 6 in (dst, src) else 4

    return _h


def _make_ld_r_n(dst: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        _set_r(cpu, dst, cpu._fetch())
        return 7

    return _h


def _make_alu_r(grp: int, src: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        r = cpu.registers
        v = _get_r(cpu, src)
        if grp == 0:
            r.A = _add8(cpu, r.A, v)
        elif grp == 1:
            r.A = _add8(cpu, r.A, v, 1 if (r.F & F.FLAG_C) else 0)
        elif grp == 2:
            r.A = _sub8(cpu, r.A, v)
        elif grp == 3:
            r.A = _sub8(cpu, r.A, v, 1 if (r.F & F.FLAG_C) else 0)
        elif grp == 4:
            _and8(cpu, v)
        elif grp == 5:
            _xor8(cpu, v)
        elif grp == 6:
            _or8(cpu, v)
        else:
            _cp8(cpu, v)
        return 7 if src == 6 else 4

    return _h


def _make_alu_imm(grp: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        r = cpu.registers
        n = cpu._fetch()
        if grp == 0:
            r.A = _add8(cpu, r.A, n)
        elif grp == 1:
            r.A = _add8(cpu, r.A, n, 1 if (r.F & F.FLAG_C) else 0)
        elif grp == 2:
            r.A = _sub8(cpu, r.A, n)
        elif grp == 3:
            r.A = _sub8(cpu, r.A, n, 1 if (r.F & F.FLAG_C) else 0)
        elif grp == 4:
            _and8(cpu, n)
        elif grp == 5:
            _xor8(cpu, n)
        elif grp == 6:
            _or8(cpu, n)
        else:
            _cp8(cpu, n)
        return 7

    return _h


def _make_inc_r(idx: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        _set_r(cpu, idx, _inc8(cpu, _get_r(cpu, idx)))
        return 11 if idx == 6 else 4

    return _h


def _make_dec_r(idx: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        _set_r(cpu, idx, _dec8(cpu, _get_r(cpu, idx)))
        return 11 if idx == 6 else 4

    return _h


def _make_jp_cc(cond: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        nn = cpu._fetch_word()
        if _cc(cpu, cond):
            cpu.registers.PC = nn
        return 10

    return _h


def _make_jr_cc(cond: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        e = _signed(cpu._fetch())
        if _cc(cpu, cond):
            cpu.registers.PC = (cpu.registers.PC + e) & 0xFFFF
            return 12
        return 7

    return _h


def _make_call_cc(cond: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        r = cpu.registers
        nn = cpu._fetch_word()
        if _cc(cpu, cond):
            cpu._push(r.PC)
            r.PC = nn
            return 17
        return 10

    return _h


def _make_ret_cc(cond: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        if _cc(cpu, cond):
            cpu.registers.PC = cpu._pop()
            return 11
        return 5

    return _h


def _make_rst(n: int) -> Callable[[Z80], int]:
    def _h(cpu: Z80) -> int:
        cpu._push(cpu.registers.PC)
        cpu.registers.PC = n
        return 11

    return _h


# ---------------------------------------------------------------------------
# Unique opcode handlers
# ---------------------------------------------------------------------------


def _op_illegal(cpu: Z80) -> int:
    if cpu._logger is not None:
        cpu._logger.on_undefined_opcode((cpu.registers.PC - 1) & 0xFFFF, 0)
    return 4


def _op_nop(cpu: Z80) -> int:
    return 4


def _op_halt(cpu: Z80) -> int:
    cpu.halted = True
    return 4


def _op_di(cpu: Z80) -> int:
    cpu.iff1 = False
    cpu.iff2 = False
    return 4


def _op_ei(cpu: Z80) -> int:
    cpu.iff1 = True
    cpu.iff2 = True
    # Interrupts are not accepted until after the instruction following EI.
    cpu.ei_pending = True
    return 4


def _op_ld_hl_n(cpu: Z80) -> int:  # LD (HL), n  0x36
    cpu.write_byte(cpu.registers.HL, cpu._fetch())
    return 10


def _op_ld_bc_nn(cpu: Z80) -> int:
    cpu.registers.BC = cpu._fetch_word()
    return 10


def _op_ld_de_nn(cpu: Z80) -> int:
    cpu.registers.DE = cpu._fetch_word()
    return 10


def _op_ld_hl_nn(cpu: Z80) -> int:
    cpu.registers.HL = cpu._fetch_word()
    return 10


def _op_ld_sp_nn(cpu: Z80) -> int:
    cpu.registers.SP = cpu._fetch_word()
    return 10


def _op_ld_hl_ind_nn(cpu: Z80) -> int:  # LD HL, (nn)  0x2A
    nn = cpu._fetch_word()
    cpu.registers.HL = cpu.read_byte(nn) | (cpu.read_byte((nn + 1) & 0xFFFF) << 8)
    return 16


def _op_ld_ind_nn_hl(cpu: Z80) -> int:  # LD (nn), HL  0x22
    r = cpu.registers
    nn = cpu._fetch_word()
    cpu.write_byte(nn, r.L)
    cpu.write_byte((nn + 1) & 0xFFFF, r.H)
    return 16


def _op_ld_sp_hl(cpu: Z80) -> int:
    cpu.registers.SP = cpu.registers.HL
    return 6


def _op_ld_ind_bc_a(cpu: Z80) -> int:
    cpu.write_byte(cpu.registers.BC, cpu.registers.A)
    return 7


def _op_ld_ind_de_a(cpu: Z80) -> int:
    cpu.write_byte(cpu.registers.DE, cpu.registers.A)
    return 7


def _op_ld_a_ind_bc(cpu: Z80) -> int:
    cpu.registers.A = cpu.read_byte(cpu.registers.BC)
    return 7


def _op_ld_a_ind_de(cpu: Z80) -> int:
    cpu.registers.A = cpu.read_byte(cpu.registers.DE)
    return 7


def _op_ld_ind_nn_a(cpu: Z80) -> int:  # LD (nn), A  0x32
    nn = cpu._fetch_word()
    cpu.write_byte(nn, cpu.registers.A)
    return 13


def _op_ld_a_ind_nn(cpu: Z80) -> int:  # LD A, (nn)  0x3A
    nn = cpu._fetch_word()
    cpu.registers.A = cpu.read_byte(nn)
    return 13


def _op_add_hl_bc(cpu: Z80) -> int:
    r = cpu.registers
    r.HL = _add16(cpu, r.HL, r.BC)
    return 11


def _op_add_hl_de(cpu: Z80) -> int:
    r = cpu.registers
    r.HL = _add16(cpu, r.HL, r.DE)
    return 11


def _op_add_hl_hl(cpu: Z80) -> int:
    r = cpu.registers
    r.HL = _add16(cpu, r.HL, r.HL)
    return 11


def _op_add_hl_sp(cpu: Z80) -> int:
    r = cpu.registers
    r.HL = _add16(cpu, r.HL, r.SP)
    return 11


def _op_inc_bc(cpu: Z80) -> int:
    cpu.registers.BC = (cpu.registers.BC + 1) & 0xFFFF
    return 6


def _op_inc_de(cpu: Z80) -> int:
    cpu.registers.DE = (cpu.registers.DE + 1) & 0xFFFF
    return 6


def _op_inc_hl(cpu: Z80) -> int:
    cpu.registers.HL = (cpu.registers.HL + 1) & 0xFFFF
    return 6


def _op_inc_sp(cpu: Z80) -> int:
    cpu.registers.SP = (cpu.registers.SP + 1) & 0xFFFF
    return 6


def _op_dec_bc(cpu: Z80) -> int:
    cpu.registers.BC = (cpu.registers.BC - 1) & 0xFFFF
    return 6


def _op_dec_de(cpu: Z80) -> int:
    cpu.registers.DE = (cpu.registers.DE - 1) & 0xFFFF
    return 6


def _op_dec_hl(cpu: Z80) -> int:
    cpu.registers.HL = (cpu.registers.HL - 1) & 0xFFFF
    return 6


def _op_dec_sp(cpu: Z80) -> int:
    cpu.registers.SP = (cpu.registers.SP - 1) & 0xFFFF
    return 6


def _op_rlca(cpu: Z80) -> int:
    r = cpu.registers
    c = (r.A >> 7) & 1
    r.A = ((r.A << 1) | c) & 0xFF
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
    return 4


def _op_rrca(cpu: Z80) -> int:
    r = cpu.registers
    c = r.A & 1
    r.A = ((r.A >> 1) | (c << 7)) & 0xFF
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
    return 4


def _op_rla(cpu: Z80) -> int:
    r = cpu.registers
    old_c = 1 if (r.F & F.FLAG_C) else 0
    c = (r.A >> 7) & 1
    r.A = ((r.A << 1) | old_c) & 0xFF
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
    return 4


def _op_rra(cpu: Z80) -> int:
    r = cpu.registers
    old_c = 1 if (r.F & F.FLAG_C) else 0
    c = r.A & 1
    r.A = ((r.A >> 1) | (old_c << 7)) & 0xFF
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
    return 4


def _op_daa(cpu: Z80) -> int:
    r = cpu.registers
    a = r.A
    a0 = a  # pre-correction value, for the half-carry (H) derivation below
    f = r.F
    correction = 0
    new_c = False
    if (f & F.FLAG_H) or ((not (f & F.FLAG_N)) and (a & 0x0F) > 9):
        correction |= 0x06
    if (f & F.FLAG_C) or ((not (f & F.FLAG_N)) and a > 0x99):
        correction |= 0x60
        new_c = True
    if f & F.FLAG_N:
        a = (a - correction) & 0xFF
    else:
        a = (a + correction) & 0xFF
    new_f = (F.FLAG_N if (f & F.FLAG_N) else 0) | (F.FLAG_C if new_c else 0)
    # Every DAA correction constant has bit 4 = 0, so any change of A's bit 4
    # can only be a carry/borrow out of bit 3 — i.e. the half-carry.
    if (a0 ^ a) & 0x10:
        new_f |= F.FLAG_H
    if a == 0:
        new_f |= F.FLAG_Z
    if a & 0x80:
        new_f |= F.FLAG_S
    if F.parity(a):
        new_f |= F.FLAG_PV
    r.A = a
    r.F = new_f
    return 4


def _op_cpl(cpu: Z80) -> int:
    r = cpu.registers
    # Portability: Python's `~` is infinite-width two's complement
    # (~x == -(x + 1)), so `(~r.A) & 0xFF` is correct only because of the mask.
    # Same pattern in the CB RES handler (`v & ~(1 << bit)`). A fixed-width port
    # applies `!` directly on a u8 with no mask needed.
    r.A = (~r.A) & 0xFF
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV | F.FLAG_C)) | F.FLAG_H | F.FLAG_N
    return 4


def _op_scf(cpu: Z80) -> int:
    r = cpu.registers
    r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | F.FLAG_C
    return 4


def _op_ccf(cpu: Z80) -> int:
    r = cpu.registers
    old_c = 1 if (r.F & F.FLAG_C) else 0
    r.F = (
        (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV))
        | (F.FLAG_H if old_c else 0)
        | (0 if old_c else F.FLAG_C)
    )
    return 4


def _op_jp_nn(cpu: Z80) -> int:
    cpu.registers.PC = cpu._fetch_word()
    return 10


def _op_jp_hl(cpu: Z80) -> int:
    cpu.registers.PC = cpu.registers.HL
    return 4


def _op_jr(cpu: Z80) -> int:
    e = _signed(cpu._fetch())
    cpu.registers.PC = (cpu.registers.PC + e) & 0xFFFF
    return 12


def _op_djnz(cpu: Z80) -> int:
    r = cpu.registers
    e = _signed(cpu._fetch())
    r.B = (r.B - 1) & 0xFF
    if r.B != 0:
        r.PC = (r.PC + e) & 0xFFFF
        return 13
    return 8


def _op_call_nn(cpu: Z80) -> int:
    r = cpu.registers
    nn = cpu._fetch_word()
    cpu._push(r.PC)
    r.PC = nn
    return 17


def _op_ret(cpu: Z80) -> int:
    cpu.registers.PC = cpu._pop()
    return 10


def _op_push_bc(cpu: Z80) -> int:
    cpu._push(cpu.registers.BC)
    return 11


def _op_push_de(cpu: Z80) -> int:
    cpu._push(cpu.registers.DE)
    return 11


def _op_push_hl(cpu: Z80) -> int:
    cpu._push(cpu.registers.HL)
    return 11


def _op_push_af(cpu: Z80) -> int:
    cpu._push(cpu.registers.AF)
    return 11


def _op_pop_bc(cpu: Z80) -> int:
    cpu.registers.BC = cpu._pop()
    return 10


def _op_pop_de(cpu: Z80) -> int:
    cpu.registers.DE = cpu._pop()
    return 10


def _op_pop_hl(cpu: Z80) -> int:
    cpu.registers.HL = cpu._pop()
    return 10


def _op_pop_af(cpu: Z80) -> int:
    cpu.registers.AF = cpu._pop()
    return 10


def _op_ex_af(cpu: Z80) -> int:
    r = cpu.registers
    r.AF, r.AF_ = r.AF_, r.AF
    return 4


def _op_exx(cpu: Z80) -> int:
    r = cpu.registers
    r.BC, r.BC_ = r.BC_, r.BC
    r.DE, r.DE_ = r.DE_, r.DE
    r.HL, r.HL_ = r.HL_, r.HL
    return 4


def _op_ex_de_hl(cpu: Z80) -> int:
    r = cpu.registers
    r.DE, r.HL = r.HL, r.DE
    return 4


def _op_ex_sp_hl(cpu: Z80) -> int:
    r = cpu.registers
    lo = cpu.read_byte(r.SP)
    hi = cpu.read_byte((r.SP + 1) & 0xFFFF)
    cpu.write_byte(r.SP, r.L)
    cpu.write_byte((r.SP + 1) & 0xFFFF, r.H)
    r.HL = (hi << 8) | lo
    return 19


def _op_in_a_n(cpu: Z80) -> int:
    # Port address is (A << 8) | n, using A before it is overwritten.
    n = cpu._fetch()
    a = cpu.registers.A
    cpu.registers.A = cpu.read_port((a << 8) | n)
    return 11


def _op_out_n_a(cpu: Z80) -> int:
    # Port address is (A << 8) | n on the bus.
    n = cpu._fetch()
    a = cpu.registers.A
    cpu.write_port((a << 8) | n, a)
    return 11


# Prefix handlers add one M1 wait for the second opcode fetch (the byte after the
# prefix). step() already charged the wait for the prefix byte itself. A DD/FD
# chain re-enters _op_prefix_dd/_op_prefix_fd via dispatch, so each prefix byte
# gets exactly one wait; for DDCB/FDCB the displacement and final opcode are
# operand reads (not M1s) and correctly get none.
def _op_prefix_cb(cpu: Z80) -> int:
    return _execute_cb(cpu) + cpu.m1_wait_states


def _op_prefix_dd(cpu: Z80) -> int:
    return _execute_dd_fd(cpu, use_iy=False) + cpu.m1_wait_states


def _op_prefix_fd(cpu: Z80) -> int:
    return _execute_dd_fd(cpu, use_iy=True) + cpu.m1_wait_states


def _op_prefix_ed(cpu: Z80) -> int:
    return _execute_ed(cpu) + cpu.m1_wait_states


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_DISPATCH: list[Callable[[Z80], int]] = [_op_illegal] * 256


def _build_dispatch() -> None:
    d = _DISPATCH

    # Prefixes
    d[0xCB] = _op_prefix_cb
    d[0xDD] = _op_prefix_dd
    d[0xFD] = _op_prefix_fd
    d[0xED] = _op_prefix_ed

    # Misc unique opcodes
    d[0x00] = _op_nop
    d[0x76] = _op_halt
    d[0xF3] = _op_di
    d[0xFB] = _op_ei

    # LD r, r'  (0x40–0x7F; 0x76 is HALT, overridden after the loop)
    for op in range(0x40, 0x80):
        dst = (op >> 3) & 7
        src = op & 7
        d[op] = _make_ld_r_r(dst, src)
    d[0x76] = _op_halt  # must follow the loop

    # 8-bit ALU r  (0x80–0xBF)
    for op in range(0x80, 0xC0):
        d[op] = _make_alu_r((op >> 3) & 7, op & 7)

    # 8-bit ALU immediate
    for op in (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE):
        d[op] = _make_alu_imm((op >> 3) & 7)

    # LD r, n
    for op in (0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E):
        d[op] = _make_ld_r_n((op >> 3) & 7)
    d[0x36] = _op_ld_hl_n

    # INC r / DEC r
    for op, idx in _INC_OPS.items():
        d[op] = _make_inc_r(idx)
    for op, idx in _DEC_OPS.items():
        d[op] = _make_dec_r(idx)

    # 16-bit loads
    d[0x01] = _op_ld_bc_nn
    d[0x11] = _op_ld_de_nn
    d[0x21] = _op_ld_hl_nn
    d[0x31] = _op_ld_sp_nn
    d[0x2A] = _op_ld_hl_ind_nn
    d[0x22] = _op_ld_ind_nn_hl
    d[0xF9] = _op_ld_sp_hl
    d[0x02] = _op_ld_ind_bc_a
    d[0x12] = _op_ld_ind_de_a
    d[0x0A] = _op_ld_a_ind_bc
    d[0x1A] = _op_ld_a_ind_de
    d[0x32] = _op_ld_ind_nn_a
    d[0x3A] = _op_ld_a_ind_nn

    # 16-bit ADD HL / INC rr / DEC rr
    d[0x09] = _op_add_hl_bc
    d[0x19] = _op_add_hl_de
    d[0x29] = _op_add_hl_hl
    d[0x39] = _op_add_hl_sp
    d[0x03] = _op_inc_bc
    d[0x13] = _op_inc_de
    d[0x23] = _op_inc_hl
    d[0x33] = _op_inc_sp
    d[0x0B] = _op_dec_bc
    d[0x1B] = _op_dec_de
    d[0x2B] = _op_dec_hl
    d[0x3B] = _op_dec_sp

    # Rotates / DAA / CPL / SCF / CCF
    d[0x07] = _op_rlca
    d[0x0F] = _op_rrca
    d[0x17] = _op_rla
    d[0x1F] = _op_rra
    d[0x27] = _op_daa
    d[0x2F] = _op_cpl
    d[0x37] = _op_scf
    d[0x3F] = _op_ccf

    # JP / JR / DJNZ
    d[0xC3] = _op_jp_nn
    d[0xE9] = _op_jp_hl
    d[0x18] = _op_jr
    d[0x10] = _op_djnz
    for op in (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA):
        d[op] = _make_jp_cc((op >> 3) & 7)
    d[0x20] = _make_jr_cc(0)  # NZ
    d[0x28] = _make_jr_cc(1)  # Z
    d[0x30] = _make_jr_cc(2)  # NC
    d[0x38] = _make_jr_cc(3)  # C

    # CALL / RET
    d[0xCD] = _op_call_nn
    d[0xC9] = _op_ret
    for op in (0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC):
        d[op] = _make_call_cc((op >> 3) & 7)
    for op in (0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8):
        d[op] = _make_ret_cc((op >> 3) & 7)

    # RST
    for op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
        d[op] = _make_rst(op & 0x38)

    # PUSH / POP
    d[0xC5] = _op_push_bc
    d[0xD5] = _op_push_de
    d[0xE5] = _op_push_hl
    d[0xF5] = _op_push_af
    d[0xC1] = _op_pop_bc
    d[0xD1] = _op_pop_de
    d[0xE1] = _op_pop_hl
    d[0xF1] = _op_pop_af

    # EX / EXX
    d[0x08] = _op_ex_af
    d[0xD9] = _op_exx
    d[0xEB] = _op_ex_de_hl
    d[0xE3] = _op_ex_sp_hl

    # IN / OUT
    d[0xDB] = _op_in_a_n
    d[0xD3] = _op_out_n_a


_build_dispatch()


# ---------------------------------------------------------------------------
