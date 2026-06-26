"""Z80 main opcode table (256 entries) plus CB/DD/ED/FD prefix dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

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


def _execute_cb(cpu: Z80) -> int:
    op = cpu._fetch()
    row = op >> 6
    bit = (op >> 3) & 7
    reg = op & 7
    v = _get_r(cpu, reg)
    cycles = 8 if reg != 6 else 15

    if row == 0:
        fn = [_rlc, _rrc, _rl, _rr, _sla, _sra, _sll, _srl][bit]
        result = fn(cpu, v)
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


def _execute_dd_fd(cpu: Z80, use_iy: bool) -> int:
    r = cpu.registers
    op = cpu._fetch()
    xy = r.IY if use_iy else r.IX

    # LD rr, nn
    if op == 0x21:
        nn = cpu._fetch_word()
        if use_iy:
            r.IY = nn
        else:
            r.IX = nn
        return 14
    # LD (nn), IX/IY
    if op == 0x22:
        nn = cpu._fetch_word()
        cpu.write_byte(nn, xy & 0xFF)
        cpu.write_byte((nn + 1) & 0xFFFF, (xy >> 8) & 0xFF)
        return 20
    # LD IX/IY, (nn)
    if op == 0x2A:
        nn = cpu._fetch_word()
        lo = cpu.read_byte(nn)
        hi = cpu.read_byte((nn + 1) & 0xFFFF)
        val = (hi << 8) | lo
        if use_iy:
            r.IY = val
        else:
            r.IX = val
        return 20
    # INC IX/IY
    if op == 0x23:
        if use_iy:
            r.IY = (r.IY + 1) & 0xFFFF
        else:
            r.IX = (r.IX + 1) & 0xFFFF
        return 10
    # DEC IX/IY
    if op == 0x2B:
        if use_iy:
            r.IY = (r.IY - 1) & 0xFFFF
        else:
            r.IX = (r.IX - 1) & 0xFFFF
        return 10
    # ADD IX/IY, rr
    if op in (0x09, 0x19, 0x29, 0x39):
        pairs = {0x09: r.BC, 0x19: r.DE, 0x29: xy, 0x39: r.SP}
        result = _add16(cpu, xy, pairs[op])
        if use_iy:
            r.IY = result
        else:
            r.IX = result
        return 15
    # PUSH IX/IY
    if op == 0xE5:
        cpu._push(xy)
        return 15
    # POP IX/IY
    if op == 0xE1:
        val = cpu._pop()
        if use_iy:
            r.IY = val
        else:
            r.IX = val
        return 14
    # EX (SP), IX/IY
    if op == 0xE3:
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
    # JP (IX/IY)
    if op == 0xE9:
        r.PC = xy
        return 8
    # LD SP, IX/IY
    if op == 0xF9:
        r.SP = xy
        return 10

    # (IX/IY + d) instructions
    if op in (
        0x34,
        0x35,
        0x36,
        0x46,
        0x4E,
        0x56,
        0x5E,
        0x66,
        0x6E,
        0x7E,
        0x70,
        0x71,
        0x72,
        0x73,
        0x74,
        0x75,
        0x77,
        0x86,
        0x8E,
        0x96,
        0x9E,
        0xA6,
        0xAE,
        0xB6,
        0xBE,
    ):
        d = _signed(cpu._fetch())
        ea = (xy + d) & 0xFFFF
        if op == 0x34:
            v = cpu.read_byte(ea)
            cpu.write_byte(ea, _inc8(cpu, v))
            return 23
        if op == 0x35:
            v = cpu.read_byte(ea)
            cpu.write_byte(ea, _dec8(cpu, v))
            return 23
        if op == 0x36:
            n = cpu._fetch()
            cpu.write_byte(ea, n)
            return 19
        if op == 0x7E:
            r.A = cpu.read_byte(ea)
            return 19
        if op == 0x46:
            r.B = cpu.read_byte(ea)
            return 19
        if op == 0x4E:
            r.C = cpu.read_byte(ea)
            return 19
        if op == 0x56:
            r.D = cpu.read_byte(ea)
            return 19
        if op == 0x5E:
            r.E = cpu.read_byte(ea)
            return 19
        if op == 0x66:
            r.H = cpu.read_byte(ea)
            return 19
        if op == 0x6E:
            r.L = cpu.read_byte(ea)
            return 19
        if op == 0x70:
            cpu.write_byte(ea, r.B)
            return 19
        if op == 0x71:
            cpu.write_byte(ea, r.C)
            return 19
        if op == 0x72:
            cpu.write_byte(ea, r.D)
            return 19
        if op == 0x73:
            cpu.write_byte(ea, r.E)
            return 19
        if op == 0x74:
            cpu.write_byte(ea, r.H)
            return 19
        if op == 0x75:
            cpu.write_byte(ea, r.L)
            return 19
        if op == 0x77:
            cpu.write_byte(ea, r.A)
            return 19
        v = cpu.read_byte(ea)
        if op == 0x86:
            r.A = _add8(cpu, r.A, v)
            return 19
        if op == 0x8E:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _add8(cpu, r.A, v, c)
            return 19
        if op == 0x96:
            r.A = _sub8(cpu, r.A, v)
            return 19
        if op == 0x9E:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _sub8(cpu, r.A, v, c)
            return 19
        if op == 0xA6:
            _and8(cpu, v)
            return 19
        if op == 0xAE:
            _xor8(cpu, v)
            return 19
        if op == 0xB6:
            _or8(cpu, v)
            return 19
        if op == 0xBE:
            _cp8(cpu, v)
            return 19

    # DD CB (bit ops on (IX+d))
    if op == 0xCB:
        d = _signed(cpu._fetch())
        cb_op = cpu._fetch()
        ea = (xy + d) & 0xFFFF
        v = cpu.read_byte(ea)
        row = cb_op >> 6
        bit = (cb_op >> 3) & 7
        if row == 0:
            fns = [_rlc, _rrc, _rl, _rr, _sla, _sra, _sll, _srl]
            result = fns[bit](cpu, v)
            cpu.write_byte(ea, result)
        elif row == 1:
            f = (cpu.registers.F & F.FLAG_C) | F.FLAG_H
            if not (v & (1 << bit)):
                f |= F.FLAG_Z | F.FLAG_PV
            cpu.registers.F = f
            return 20
        elif row == 2:
            cpu.write_byte(ea, v & ~(1 << bit))
        else:
            cpu.write_byte(ea, v | (1 << bit))
        return 23

    # INC/DEC r (high/low bytes of IX/IY)
    if op == 0x24:
        if use_iy:
            r.IYH = _inc8(cpu, r.IYH)
        else:
            r.IXH = _inc8(cpu, r.IXH)
        return 8
    if op == 0x25:
        if use_iy:
            r.IYH = _dec8(cpu, r.IYH)
        else:
            r.IXH = _dec8(cpu, r.IXH)
        return 8
    if op == 0x2C:
        if use_iy:
            r.IYL = _inc8(cpu, r.IYL)
        else:
            r.IXL = _inc8(cpu, r.IXL)
        return 8
    if op == 0x2D:
        if use_iy:
            r.IYL = _dec8(cpu, r.IYL)
        else:
            r.IXL = _dec8(cpu, r.IXL)
        return 8

    # Undocumented: LD IXH/IXL, n  (DD 26 / DD 2E) — 11 T-states
    xh = r.IYH if use_iy else r.IXH
    xl = r.IYL if use_iy else r.IXL
    if op == 0x26:
        n = cpu._fetch()
        if use_iy:
            r.IYH = n
        else:
            r.IXH = n
        return 11
    if op == 0x2E:
        n = cpu._fetch()
        if use_iy:
            r.IYL = n
        else:
            r.IXL = n
        return 11

    # Undocumented: LD r, IXH  (DD 44/4C/54/5C/7C) — 8 T-states
    if op in (0x44, 0x4C, 0x54, 0x5C, 0x7C):
        if op == 0x44:
            r.B = xh
        elif op == 0x4C:
            r.C = xh
        elif op == 0x54:
            r.D = xh
        elif op == 0x5C:
            r.E = xh
        else:
            r.A = xh
        return 8

    # Undocumented: LD r, IXL  (DD 45/4D/55/5D/7D) — 8 T-states
    if op in (0x45, 0x4D, 0x55, 0x5D, 0x7D):
        if op == 0x45:
            r.B = xl
        elif op == 0x4D:
            r.C = xl
        elif op == 0x55:
            r.D = xl
        elif op == 0x5D:
            r.E = xl
        else:
            r.A = xl
        return 8

    # Undocumented: LD IXH, r  (DD 60/61/62/63/64/65/67) — 8 T-states
    # 0x66 = LD H,(IX+d) handled above; excluded from this group
    if op in (0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x67):
        if op == 0x60:
            val = r.B
        elif op == 0x61:
            val = r.C
        elif op == 0x62:
            val = r.D
        elif op == 0x63:
            val = r.E
        elif op == 0x64:
            val = xh  # self-copy
        elif op == 0x65:
            val = xl
        else:
            val = r.A
        if use_iy:
            r.IYH = val
        else:
            r.IXH = val
        return 8

    # Undocumented: LD IXL, r  (DD 68/69/6A/6B/6C/6D/6F) — 8 T-states
    # 0x6E = LD L,(IX+d) handled above; excluded from this group
    if op in (0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6F):
        if op == 0x68:
            val = r.B
        elif op == 0x69:
            val = r.C
        elif op == 0x6A:
            val = r.D
        elif op == 0x6B:
            val = r.E
        elif op == 0x6C:
            val = xh
        elif op == 0x6D:
            val = xl  # self-copy
        else:
            val = r.A
        if use_iy:
            r.IYL = val
        else:
            r.IXL = val
        return 8

    # Undocumented: ADD/ADC A, IXH/IXL
    if op == 0x84:
        r.A = _add8(cpu, r.A, xh)
        return 8
    if op == 0x85:
        r.A = _add8(cpu, r.A, xl)
        return 8
    if op == 0x8C:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _add8(cpu, r.A, xh, c)
        return 8
    if op == 0x8D:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _add8(cpu, r.A, xl, c)
        return 8

    # Undocumented: SUB/SBC A, IXH/IXL
    if op == 0x94:
        r.A = _sub8(cpu, r.A, xh)
        return 8
    if op == 0x95:
        r.A = _sub8(cpu, r.A, xl)
        return 8
    if op == 0x9C:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _sub8(cpu, r.A, xh, c)
        return 8
    if op == 0x9D:
        c = 1 if (r.F & F.FLAG_C) else 0
        r.A = _sub8(cpu, r.A, xl, c)
        return 8

    # Undocumented: AND/XOR/OR/CP IXH/IXL
    if op == 0xA4:
        _and8(cpu, xh)
        return 8
    if op == 0xA5:
        _and8(cpu, xl)
        return 8
    if op == 0xAC:
        _xor8(cpu, xh)
        return 8
    if op == 0xAD:
        _xor8(cpu, xl)
        return 8
    if op == 0xB4:
        _or8(cpu, xh)
        return 8
    if op == 0xB5:
        _or8(cpu, xl)
        return 8
    if op == 0xBC:
        _cp8(cpu, xh)
        return 8
    if op == 0xBD:
        _cp8(cpu, xl)
        return 8

    # prefix absorbed — delegate to normal dispatch (real Z80 behavior)
    return _DISPATCH[op](cpu)


# ---------------------------------------------------------------------------
# ED prefix
# ---------------------------------------------------------------------------


def _execute_ed(cpu: Z80) -> int:
    r = cpu.registers
    op = cpu._fetch()

    # IM 0/1/2
    if op == 0x46:
        cpu.im = 0
        return 8
    if op == 0x56:
        cpu.im = 1
        return 8
    if op == 0x5E:
        cpu.im = 2
        return 8

    # LD I, A / LD R, A
    if op == 0x47:
        r.I = r.A
        return 9
    if op == 0x4F:
        r.R = r.A
        return 9

    # LD A, I / LD A, R
    if op == 0x57:
        r.A = r.I
        f = (r.F & F.FLAG_C) | (F.FLAG_PV if cpu.iff2 else 0)
        if r.A == 0:
            f |= F.FLAG_Z
        if r.A & 0x80:
            f |= F.FLAG_S
        r.F = f
        return 9
    if op == 0x5F:
        r.A = r.R
        f = (r.F & F.FLAG_C) | (F.FLAG_PV if cpu.iff2 else 0)
        if r.A == 0:
            f |= F.FLAG_Z
        if r.A & 0x80:
            f |= F.FLAG_S
        r.F = f
        return 9

    # NEG
    if op == 0x44:
        r.A = _sub8(cpu, 0, r.A)
        return 8

    # RETN
    if op == 0x45:
        cpu.iff1 = cpu.iff2
        r.PC = cpu._pop()
        return 14

    # RETI
    if op == 0x4D:
        cpu.iff1 = cpu.iff2
        r.PC = cpu._pop()
        return 14

    # ADC HL, rr
    pairs16 = {0x4A: r.BC, 0x5A: r.DE, 0x6A: r.HL, 0x7A: r.SP}
    if op in pairs16:
        r.HL = _adc16(cpu, r.HL, pairs16[op])
        return 15

    # SBC HL, rr
    sbc_pairs = {0x42: r.BC, 0x52: r.DE, 0x62: r.HL, 0x72: r.SP}
    if op in sbc_pairs:
        r.HL = _sbc16(cpu, r.HL, sbc_pairs[op])
        return 15

    # LD (nn), rr  /  LD rr, (nn)
    if op in (0x43, 0x53, 0x63, 0x73):
        nn = cpu._fetch_word()
        pairs_map = {0x43: r.BC, 0x53: r.DE, 0x63: r.HL, 0x73: r.SP}
        val = pairs_map[op]
        cpu.write_byte(nn, val & 0xFF)
        cpu.write_byte((nn + 1) & 0xFFFF, (val >> 8) & 0xFF)
        return 20
    if op in (0x4B, 0x5B, 0x6B, 0x7B):
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

    # IN r, (C)
    in_regs = {0x40: 0, 0x48: 1, 0x50: 2, 0x58: 3, 0x60: 4, 0x68: 5, 0x78: 7}
    if op in in_regs:
        v = cpu.read_port(r.C)
        _set_r(cpu, in_regs[op], v)
        r.F = (r.F & F.FLAG_C) | _szp(v)
        return 12
    # IN F, (C)  (0x70 — result discarded, flags set)
    if op == 0x70:
        v = cpu.read_port(r.C)
        r.F = (r.F & F.FLAG_C) | _szp(v)
        return 12

    # OUT (C), r
    out_regs = {0x41: 0, 0x49: 1, 0x51: 2, 0x59: 3, 0x61: 4, 0x69: 5, 0x79: 7}
    if op in out_regs:
        cpu.write_port(r.C, _get_r(cpu, out_regs[op]))
        return 12
    # OUT (C), 0
    if op == 0x71:
        cpu.write_port(r.C, 0)
        return 12

    # RLD / RRD
    if op == 0x6F:  # RLD
        mem_val = cpu.read_byte(r.HL)
        new_mem = ((mem_val << 4) | (r.A & 0x0F)) & 0xFF
        r.A = (r.A & 0xF0) | (mem_val >> 4)
        cpu.write_byte(r.HL, new_mem)
        r.F = (r.F & F.FLAG_C) | _szp(r.A)
        return 18
    if op == 0x67:  # RRD
        mem_val = cpu.read_byte(r.HL)
        new_mem = ((r.A << 4) | (mem_val >> 4)) & 0xFF
        r.A = (r.A & 0xF0) | (mem_val & 0x0F)
        cpu.write_byte(r.HL, new_mem)
        r.F = (r.F & F.FLAG_C) | _szp(r.A)
        return 18

    # Block transfer: LDI / LDD / LDIR / LDDR
    if op in (0xA0, 0xA8, 0xB0, 0xB8):
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

    # Block search: CPI / CPD / CPIR / CPDR
    if op in (0xA1, 0xA9, 0xB1, 0xB9):
        val = cpu.read_byte(r.HL)
        result = r.A - val
        inc = 1 if op in (0xA1, 0xB1) else -1
        r.HL = (r.HL + inc) & 0xFFFF
        r.BC = (r.BC - 1) & 0xFFFF
        f = (r.F & F.FLAG_C) | F.FLAG_N
        if result & 0xFF == 0:
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

    # Block I/O: INI / IND / INIR / INDR
    if op in (0xA2, 0xAA, 0xB2, 0xBA):
        val = cpu.read_port(r.C)
        cpu.write_byte(r.HL, val)
        inc = 1 if op in (0xA2, 0xB2) else -1
        r.HL = (r.HL + inc) & 0xFFFF
        r.B = _dec8(cpu, r.B)
        if op in (0xB2, 0xBA) and r.B != 0:
            r.PC = (r.PC - 2) & 0xFFFF
            return 21
        return 16

    # Block I/O: OUTI / OUTD / OTIR / OTDR
    if op in (0xA3, 0xAB, 0xB3, 0xBB):
        val = cpu.read_byte(r.HL)
        inc = 1 if op in (0xA3, 0xB3) else -1
        r.HL = (r.HL + inc) & 0xFFFF
        cpu.write_port(r.C, val)
        r.B = _dec8(cpu, r.B)
        if op in (0xB3, 0xBB) and r.B != 0:
            r.PC = (r.PC - 2) & 0xFFFF
            return 21
        return 16

    if cpu._logger is not None:
        cpu._logger.on_undefined_opcode((r.PC - 2) & 0xFFFF, op)
    return 8


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

from typing import Callable  # noqa: E402


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
    cpu.registers.A = cpu.read_port(cpu._fetch())
    return 11


def _op_out_n_a(cpu: Z80) -> int:
    cpu.write_port(cpu._fetch(), cpu.registers.A)
    return 11


def _op_prefix_cb(cpu: Z80) -> int:
    return _execute_cb(cpu)


def _op_prefix_dd(cpu: Z80) -> int:
    return _execute_dd_fd(cpu, use_iy=False)


def _op_prefix_fd(cpu: Z80) -> int:
    return _execute_dd_fd(cpu, use_iy=True)


def _op_prefix_ed(cpu: Z80) -> int:
    return _execute_ed(cpu)


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
# Main dispatch entry point
# ---------------------------------------------------------------------------


def execute(cpu: Z80, opcode: int) -> int:
    return _DISPATCH[opcode](cpu)
