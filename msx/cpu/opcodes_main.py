"""Z80 main opcode table (256 entries) plus CB/DD/ED/FD prefix dispatch."""
from __future__ import annotations

from typing import TYPE_CHECKING

from msx.cpu import flags as F

if TYPE_CHECKING:
    from msx.cpu.z80 import Z80

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
    if idx == 0: return r.B
    if idx == 1: return r.C
    if idx == 2: return r.D
    if idx == 3: return r.E
    if idx == 4: return r.H
    if idx == 5: return r.L
    if idx == 6: return cpu.read_byte(r.HL)
    return r.A


def _set_r(cpu: Z80, idx: int, v: int) -> None:
    r = cpu.registers
    if idx == 0: r.B = v
    elif idx == 1: r.C = v
    elif idx == 2: r.D = v
    elif idx == 3: r.E = v
    elif idx == 4: r.H = v
    elif idx == 5: r.L = v
    elif idx == 6: cpu.write_byte(r.HL, v)
    else: r.A = v


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
    else:           # SET
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
        if use_iy: r.IY = nn
        else: r.IX = nn
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
        if use_iy: r.IY = val
        else: r.IX = val
        return 20
    # INC IX/IY
    if op == 0x23:
        if use_iy: r.IY = (r.IY + 1) & 0xFFFF
        else: r.IX = (r.IX + 1) & 0xFFFF
        return 10
    # DEC IX/IY
    if op == 0x2B:
        if use_iy: r.IY = (r.IY - 1) & 0xFFFF
        else: r.IX = (r.IX - 1) & 0xFFFF
        return 10
    # ADD IX/IY, rr
    if op in (0x09, 0x19, 0x29, 0x39):
        pairs = {0x09: r.BC, 0x19: r.DE, 0x29: xy, 0x39: r.SP}
        result = _add16(cpu, xy, pairs[op])
        if use_iy: r.IY = result
        else: r.IX = result
        return 15
    # PUSH IX/IY
    if op == 0xE5:
        cpu._push(xy)
        return 15
    # POP IX/IY
    if op == 0xE1:
        val = cpu._pop()
        if use_iy: r.IY = val
        else: r.IX = val
        return 14
    # EX (SP), IX/IY
    if op == 0xE3:
        lo = cpu.read_byte(r.SP)
        hi = cpu.read_byte((r.SP + 1) & 0xFFFF)
        cpu.write_byte(r.SP, xy & 0xFF)
        cpu.write_byte((r.SP + 1) & 0xFFFF, (xy >> 8) & 0xFF)
        val = (hi << 8) | lo
        if use_iy: r.IY = val
        else: r.IX = val
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
    if op in (0x34, 0x35, 0x36,
              0x46, 0x4E, 0x56, 0x5E, 0x66, 0x6E, 0x7E,
              0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x77,
              0x86, 0x8E, 0x96, 0x9E, 0xA6, 0xAE, 0xB6, 0xBE):
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
            r.A = cpu.read_byte(ea); return 19
        if op == 0x46:
            r.B = cpu.read_byte(ea); return 19
        if op == 0x4E:
            r.C = cpu.read_byte(ea); return 19
        if op == 0x56:
            r.D = cpu.read_byte(ea); return 19
        if op == 0x5E:
            r.E = cpu.read_byte(ea); return 19
        if op == 0x66:
            r.H = cpu.read_byte(ea); return 19
        if op == 0x6E:
            r.L = cpu.read_byte(ea); return 19
        if op == 0x70:
            cpu.write_byte(ea, r.B); return 19
        if op == 0x71:
            cpu.write_byte(ea, r.C); return 19
        if op == 0x72:
            cpu.write_byte(ea, r.D); return 19
        if op == 0x73:
            cpu.write_byte(ea, r.E); return 19
        if op == 0x74:
            cpu.write_byte(ea, r.H); return 19
        if op == 0x75:
            cpu.write_byte(ea, r.L); return 19
        if op == 0x77:
            cpu.write_byte(ea, r.A); return 19
        v = cpu.read_byte(ea)
        if op == 0x86:
            r.A = _add8(cpu, r.A, v); return 19
        if op == 0x8E:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _add8(cpu, r.A, v, c); return 19
        if op == 0x96:
            r.A = _sub8(cpu, r.A, v); return 19
        if op == 0x9E:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _sub8(cpu, r.A, v, c); return 19
        if op == 0xA6:
            _and8(cpu, v); return 19
        if op == 0xAE:
            _xor8(cpu, v); return 19
        if op == 0xB6:
            _or8(cpu, v); return 19
        if op == 0xBE:
            _cp8(cpu, v); return 19

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
        if use_iy: r.IYH = _inc8(cpu, r.IYH)
        else: r.IXH = _inc8(cpu, r.IXH)
        return 8
    if op == 0x25:
        if use_iy: r.IYH = _dec8(cpu, r.IYH)
        else: r.IXH = _dec8(cpu, r.IXH)
        return 8
    if op == 0x2C:
        if use_iy: r.IYL = _inc8(cpu, r.IYL)
        else: r.IXL = _inc8(cpu, r.IXL)
        return 8
    if op == 0x2D:
        if use_iy: r.IYL = _dec8(cpu, r.IYL)
        else: r.IXL = _dec8(cpu, r.IXL)
        return 8

    # prefix absorbed — delegate to normal dispatch (real Z80 behavior)
    return execute(cpu, op)


# ---------------------------------------------------------------------------
# ED prefix
# ---------------------------------------------------------------------------

def _execute_ed(cpu: Z80) -> int:
    r = cpu.registers
    op = cpu._fetch()

    # IM 0/1/2
    if op == 0x46: cpu.im = 0; return 8
    if op == 0x56: cpu.im = 1; return 8
    if op == 0x5E: cpu.im = 2; return 8

    # LD I, A / LD R, A
    if op == 0x47: r.I = r.A; return 9
    if op == 0x4F: r.R = r.A; return 9

    # LD A, I / LD A, R
    if op == 0x57:
        r.A = r.I
        f = (r.F & F.FLAG_C) | (F.FLAG_PV if cpu.iff2 else 0)
        if r.A == 0: f |= F.FLAG_Z
        if r.A & 0x80: f |= F.FLAG_S
        r.F = f; return 9
    if op == 0x5F:
        r.A = r.R
        f = (r.F & F.FLAG_C) | (F.FLAG_PV if cpu.iff2 else 0)
        if r.A == 0: f |= F.FLAG_Z
        if r.A & 0x80: f |= F.FLAG_S
        r.F = f; return 9

    # NEG
    if op == 0x44:
        r.A = _sub8(cpu, 0, r.A); return 8

    # RETN
    if op == 0x45:
        cpu.iff1 = cpu.iff2
        r.PC = cpu._pop(); return 14

    # RETI
    if op == 0x4D:
        cpu.iff1 = cpu.iff2
        r.PC = cpu._pop(); return 14

    # ADC HL, rr
    pairs16 = {0x4A: r.BC, 0x5A: r.DE, 0x6A: r.HL, 0x7A: r.SP}
    if op in pairs16:
        r.HL = _adc16(cpu, r.HL, pairs16[op]); return 15

    # SBC HL, rr
    sbc_pairs = {0x42: r.BC, 0x52: r.DE, 0x62: r.HL, 0x72: r.SP}
    if op in sbc_pairs:
        r.HL = _sbc16(cpu, r.HL, sbc_pairs[op]); return 15

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
        if op == 0x4B: r.BC = val
        elif op == 0x5B: r.DE = val
        elif op == 0x6B: r.HL = val
        else: r.SP = val
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
        if result & 0xFF == 0: f |= F.FLAG_Z
        if result & 0x80: f |= F.FLAG_S
        if (r.A ^ val ^ result) & 0x10: f |= F.FLAG_H
        if r.BC != 0: f |= F.FLAG_PV
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
    if cond == 0: return not (f & F.FLAG_Z)
    if cond == 1: return bool(f & F.FLAG_Z)
    if cond == 2: return not (f & F.FLAG_C)
    if cond == 3: return bool(f & F.FLAG_C)
    if cond == 4: return not (f & F.FLAG_PV)
    if cond == 5: return bool(f & F.FLAG_PV)
    if cond == 6: return not (f & F.FLAG_S)
    return bool(f & F.FLAG_S)


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def execute(cpu: Z80, opcode: int) -> int:
    r = cpu.registers

    # --- Prefixes ---
    if opcode == 0xCB: return _execute_cb(cpu)
    if opcode == 0xDD: return _execute_dd_fd(cpu, use_iy=False)
    if opcode == 0xFD: return _execute_dd_fd(cpu, use_iy=True)
    if opcode == 0xED: return _execute_ed(cpu)

    # --- NOP ---
    if opcode == 0x00: return 4

    # --- HALT ---
    if opcode == 0x76: cpu.halted = True; return 4

    # --- DI / EI ---
    if opcode == 0xF3: cpu.iff1 = False; cpu.iff2 = False; return 4
    if opcode == 0xFB: cpu.iff1 = True;  cpu.iff2 = True;  return 4

    # --- LD r, r' (0x40–0x7F) ---
    if 0x40 <= opcode <= 0x7F:
        dst = (opcode >> 3) & 7
        src = opcode & 7
        _set_r(cpu, dst, _get_r(cpu, src))
        return 7 if 6 in (dst, src) else 4

    # --- LD r, n ---
    if opcode in (0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E):
        n = cpu._fetch()
        dst = (opcode >> 3) & 7
        _set_r(cpu, dst, n)
        return 7
    if opcode == 0x36:  # LD (HL), n
        n = cpu._fetch()
        cpu.write_byte(r.HL, n)
        return 10

    # --- 8-bit ALU (0x80–0xBF) ---
    if 0x80 <= opcode <= 0xBF:
        src = opcode & 7
        v = _get_r(cpu, src)
        grp = (opcode >> 3) & 7
        if grp == 0: r.A = _add8(cpu, r.A, v)
        elif grp == 1:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _add8(cpu, r.A, v, c)
        elif grp == 2: r.A = _sub8(cpu, r.A, v)
        elif grp == 3:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _sub8(cpu, r.A, v, c)
        elif grp == 4: _and8(cpu, v)
        elif grp == 5: _xor8(cpu, v)
        elif grp == 6: _or8(cpu, v)
        else: _cp8(cpu, v)
        return 7 if src == 6 else 4

    # --- ALU immediate (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE) ---
    if opcode in (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE):
        n = cpu._fetch()
        grp = (opcode >> 3) & 7
        if grp == 0: r.A = _add8(cpu, r.A, n)
        elif grp == 1:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _add8(cpu, r.A, n, c)
        elif grp == 2: r.A = _sub8(cpu, r.A, n)
        elif grp == 3:
            c = 1 if (r.F & F.FLAG_C) else 0
            r.A = _sub8(cpu, r.A, n, c)
        elif grp == 4: _and8(cpu, n)
        elif grp == 5: _xor8(cpu, n)
        elif grp == 6: _or8(cpu, n)
        else: _cp8(cpu, n)
        return 7

    # --- INC r / DEC r ---
    inc_map = {0x04:0, 0x0C:1, 0x14:2, 0x1C:3, 0x24:4, 0x2C:5, 0x34:6, 0x3C:7}
    dec_map = {0x05:0, 0x0D:1, 0x15:2, 0x1D:3, 0x25:4, 0x2D:5, 0x35:6, 0x3D:7}
    if opcode in inc_map:
        idx = inc_map[opcode]
        _set_r(cpu, idx, _inc8(cpu, _get_r(cpu, idx)))
        return 11 if idx == 6 else 4
    if opcode in dec_map:
        idx = dec_map[opcode]
        _set_r(cpu, idx, _dec8(cpu, _get_r(cpu, idx)))
        return 11 if idx == 6 else 4

    # --- 16-bit LD ---
    if opcode == 0x01: r.BC = cpu._fetch_word(); return 10
    if opcode == 0x11: r.DE = cpu._fetch_word(); return 10
    if opcode == 0x21: r.HL = cpu._fetch_word(); return 10
    if opcode == 0x31: r.SP = cpu._fetch_word(); return 10

    if opcode == 0x2A:  # LD HL, (nn)
        nn = cpu._fetch_word()
        r.HL = cpu.read_byte(nn) | (cpu.read_byte((nn+1)&0xFFFF) << 8)
        return 16
    if opcode == 0x22:  # LD (nn), HL
        nn = cpu._fetch_word()
        cpu.write_byte(nn, r.L)
        cpu.write_byte((nn+1)&0xFFFF, r.H)
        return 16
    if opcode == 0xF9: r.SP = r.HL; return 6  # LD SP, HL

    # LD (BC/DE), A  /  LD A, (BC/DE)
    if opcode == 0x02: cpu.write_byte(r.BC, r.A); return 7
    if opcode == 0x12: cpu.write_byte(r.DE, r.A); return 7
    if opcode == 0x0A: r.A = cpu.read_byte(r.BC); return 7
    if opcode == 0x1A: r.A = cpu.read_byte(r.DE); return 7

    # LD (nn), A  /  LD A, (nn)
    if opcode == 0x32:
        nn = cpu._fetch_word()
        cpu.write_byte(nn, r.A); return 13
    if opcode == 0x3A:
        nn = cpu._fetch_word()
        r.A = cpu.read_byte(nn); return 13

    # --- 16-bit ADD / INC / DEC ---
    add16_map = {0x09: r.BC, 0x19: r.DE, 0x29: r.HL, 0x39: r.SP}
    if opcode in add16_map:
        r.HL = _add16(cpu, r.HL, add16_map[opcode]); return 11

    if opcode == 0x03: r.BC = (r.BC + 1) & 0xFFFF; return 6
    if opcode == 0x13: r.DE = (r.DE + 1) & 0xFFFF; return 6
    if opcode == 0x23: r.HL = (r.HL + 1) & 0xFFFF; return 6
    if opcode == 0x33: r.SP = (r.SP + 1) & 0xFFFF; return 6
    if opcode == 0x0B: r.BC = (r.BC - 1) & 0xFFFF; return 6
    if opcode == 0x1B: r.DE = (r.DE - 1) & 0xFFFF; return 6
    if opcode == 0x2B: r.HL = (r.HL - 1) & 0xFFFF; return 6
    if opcode == 0x3B: r.SP = (r.SP - 1) & 0xFFFF; return 6

    # --- Rotates ---
    if opcode == 0x07:  # RLCA
        c = (r.A >> 7) & 1
        r.A = ((r.A << 1) | c) & 0xFF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
        return 4
    if opcode == 0x0F:  # RRCA
        c = r.A & 1
        r.A = ((r.A >> 1) | (c << 7)) & 0xFF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
        return 4
    if opcode == 0x17:  # RLA
        old_c = 1 if (r.F & F.FLAG_C) else 0
        c = (r.A >> 7) & 1
        r.A = ((r.A << 1) | old_c) & 0xFF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
        return 4
    if opcode == 0x1F:  # RRA
        old_c = 1 if (r.F & F.FLAG_C) else 0
        c = r.A & 1
        r.A = ((r.A >> 1) | (old_c << 7)) & 0xFF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_C if c else 0)
        return 4

    # --- DAA ---
    if opcode == 0x27:
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
        if a == 0: new_f |= F.FLAG_Z
        if a & 0x80: new_f |= F.FLAG_S
        if F.parity(a): new_f |= F.FLAG_PV
        r.A = a
        r.F = new_f
        return 4

    # --- CPL / SCF / CCF ---
    if opcode == 0x2F:  # CPL
        r.A = (~r.A) & 0xFF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV | F.FLAG_C)) | F.FLAG_H | F.FLAG_N
        return 4
    if opcode == 0x37:  # SCF
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | F.FLAG_C
        return 4
    if opcode == 0x3F:  # CCF
        old_c = 1 if (r.F & F.FLAG_C) else 0
        r.F = (r.F & (F.FLAG_S | F.FLAG_Z | F.FLAG_PV)) | (F.FLAG_H if old_c else 0) | (0 if old_c else F.FLAG_C)
        return 4

    # --- JP / JR / DJNZ / CALL / RET / RST ---
    if opcode == 0xC3:  # JP nn
        r.PC = cpu._fetch_word(); return 10
    if opcode == 0xE9:  # JP (HL)
        r.PC = r.HL; return 4

    # JP cc, nn  (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA)
    if opcode in (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA):
        cond = (opcode >> 3) & 7
        nn = cpu._fetch_word()
        if _cc(cpu, cond): r.PC = nn
        return 10

    if opcode == 0x18:  # JR e
        e = _signed(cpu._fetch())
        r.PC = (r.PC + e) & 0xFFFF
        return 12

    # JR cc, e  (0x20, 0x28, 0x30, 0x38)
    if opcode in (0x20, 0x28, 0x30, 0x38):
        e = _signed(cpu._fetch())
        cond_map = {0x20: 0, 0x28: 1, 0x30: 2, 0x38: 3}
        if _cc(cpu, cond_map[opcode]):
            r.PC = (r.PC + e) & 0xFFFF
            return 12
        return 7

    if opcode == 0x10:  # DJNZ
        e = _signed(cpu._fetch())
        r.B = (r.B - 1) & 0xFF
        if r.B != 0:
            r.PC = (r.PC + e) & 0xFFFF
            return 13
        return 8

    if opcode == 0xCD:  # CALL nn
        nn = cpu._fetch_word()
        cpu._push(r.PC)
        r.PC = nn
        return 17

    # CALL cc, nn
    if opcode in (0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC):
        cond = (opcode >> 3) & 7
        nn = cpu._fetch_word()
        if _cc(cpu, cond):
            cpu._push(r.PC)
            r.PC = nn
            return 17
        return 10

    if opcode == 0xC9:  # RET
        r.PC = cpu._pop(); return 10

    # RET cc
    if opcode in (0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8):
        cond = (opcode >> 3) & 7
        if _cc(cpu, cond):
            r.PC = cpu._pop()
            return 11
        return 5

    # RST n
    if opcode in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
        cpu._push(r.PC)
        r.PC = opcode & 0x38
        return 11

    # --- PUSH / POP ---
    push_map = {0xC5: r.BC, 0xD5: r.DE, 0xE5: r.HL, 0xF5: r.AF}
    if opcode in push_map:
        cpu._push(push_map[opcode]); return 11
    if opcode == 0xC1: r.BC = cpu._pop(); return 10
    if opcode == 0xD1: r.DE = cpu._pop(); return 10
    if opcode == 0xE1: r.HL = cpu._pop(); return 10
    if opcode == 0xF1: r.AF = cpu._pop(); return 10

    # --- EX / EXX ---
    if opcode == 0x08:  # EX AF, AF'
        r.AF, r.AF_ = r.AF_, r.AF; return 4
    if opcode == 0xD9:  # EXX
        r.BC, r.BC_ = r.BC_, r.BC
        r.DE, r.DE_ = r.DE_, r.DE
        r.HL, r.HL_ = r.HL_, r.HL
        return 4
    if opcode == 0xEB:  # EX DE, HL
        r.DE, r.HL = r.HL, r.DE; return 4
    if opcode == 0xE3:  # EX (SP), HL
        lo = cpu.read_byte(r.SP)
        hi = cpu.read_byte((r.SP + 1) & 0xFFFF)
        cpu.write_byte(r.SP, r.L)
        cpu.write_byte((r.SP + 1) & 0xFFFF, r.H)
        r.HL = (hi << 8) | lo
        return 19

    # --- IN / OUT ---
    if opcode == 0xDB:  # IN A, (n)  — MSX decodes only low 8 bits of port
        n = cpu._fetch()
        r.A = cpu.read_port(n)
        return 11
    if opcode == 0xD3:  # OUT (n), A — MSX decodes only low 8 bits of port
        n = cpu._fetch()
        cpu.write_port(n, r.A)
        return 11

    if cpu._logger is not None:
        cpu._logger.on_undefined_opcode((r.PC - 1) & 0xFFFF, opcode)
    return 4
