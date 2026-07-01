"""Z80 disassembler — pure Python, no external dependencies.

disassemble(read, addr) -> tuple[str, int]
    Returns (mnemonic_string, bytes_consumed).
    Covers all documented Z80 opcodes including CB/DD/ED/FD/DDCB/FDCB prefixes.
    Unknown bytes are rendered as "DB XXh".
"""

from __future__ import annotations

from typing import Callable

# ---------------------------------------------------------------------------
# Template format
# ---------------------------------------------------------------------------
# Each entry: (template_string, total_byte_count_including_prefix)
# Placeholders in template:
#   {b0} = byte at offset 1 (after opcode), formatted as 02Xh
#   {b1} = byte at offset 2 (after opcode+b0), formatted as 02Xh
#   {w}  = 16-bit word at offset 1 (little-endian), formatted as 04Xh
#   {d}  = signed displacement at offset 1, formatted as +dd/-dd (decimal)
#   {r}  = PC-relative jump target: the signed offset at offset 1 resolved to
#          the absolute destination address, formatted as 04Xh
#
# For DD/FD prefixed ops with displacement:
#   {id} = signed displacement for (IX+d)/(IY+d)
#   {ib} = immediate byte after displacement
#   {iw} = 16-bit word after prefix

# ---------------------------------------------------------------------------
# Main opcode table (0x00–0xFF)
# ---------------------------------------------------------------------------
_MAIN_OPS: dict[int, tuple[str, int]] = {
    0x00: ("NOP", 1),
    0x01: ("LD BC, {w}h", 3),
    0x02: ("LD (BC), A", 1),
    0x03: ("INC BC", 1),
    0x04: ("INC B", 1),
    0x05: ("DEC B", 1),
    0x06: ("LD B, {b0}h", 2),
    0x07: ("RLCA", 1),
    0x08: ("EX AF, AF'", 1),
    0x09: ("ADD HL, BC", 1),
    0x0A: ("LD A, (BC)", 1),
    0x0B: ("DEC BC", 1),
    0x0C: ("INC C", 1),
    0x0D: ("DEC C", 1),
    0x0E: ("LD C, {b0}h", 2),
    0x0F: ("RRCA", 1),
    0x10: ("DJNZ {r}", 2),
    0x11: ("LD DE, {w}h", 3),
    0x12: ("LD (DE), A", 1),
    0x13: ("INC DE", 1),
    0x14: ("INC D", 1),
    0x15: ("DEC D", 1),
    0x16: ("LD D, {b0}h", 2),
    0x17: ("RLA", 1),
    0x18: ("JR {r}", 2),
    0x19: ("ADD HL, DE", 1),
    0x1A: ("LD A, (DE)", 1),
    0x1B: ("DEC DE", 1),
    0x1C: ("INC E", 1),
    0x1D: ("DEC E", 1),
    0x1E: ("LD E, {b0}h", 2),
    0x1F: ("RRA", 1),
    0x20: ("JR NZ, {r}", 2),
    0x21: ("LD HL, {w}h", 3),
    0x22: ("LD ({w}h), HL", 3),
    0x23: ("INC HL", 1),
    0x24: ("INC H", 1),
    0x25: ("DEC H", 1),
    0x26: ("LD H, {b0}h", 2),
    0x27: ("DAA", 1),
    0x28: ("JR Z, {r}", 2),
    0x29: ("ADD HL, HL", 1),
    0x2A: ("LD HL, ({w}h)", 3),
    0x2B: ("DEC HL", 1),
    0x2C: ("INC L", 1),
    0x2D: ("DEC L", 1),
    0x2E: ("LD L, {b0}h", 2),
    0x2F: ("CPL", 1),
    0x30: ("JR NC, {r}", 2),
    0x31: ("LD SP, {w}h", 3),
    0x32: ("LD ({w}h), A", 3),
    0x33: ("INC SP", 1),
    0x34: ("INC (HL)", 1),
    0x35: ("DEC (HL)", 1),
    0x36: ("LD (HL), {b0}h", 2),
    0x37: ("SCF", 1),
    0x38: ("JR C, {r}", 2),
    0x39: ("ADD HL, SP", 1),
    0x3A: ("LD A, ({w}h)", 3),
    0x3B: ("DEC SP", 1),
    0x3C: ("INC A", 1),
    0x3D: ("DEC A", 1),
    0x3E: ("LD A, {b0}h", 2),
    0x3F: ("CCF", 1),
    0x40: ("LD B, B", 1), 0x41: ("LD B, C", 1), 0x42: ("LD B, D", 1),
    0x43: ("LD B, E", 1), 0x44: ("LD B, H", 1), 0x45: ("LD B, L", 1),
    0x46: ("LD B, (HL)", 1), 0x47: ("LD B, A", 1),
    0x48: ("LD C, B", 1), 0x49: ("LD C, C", 1), 0x4A: ("LD C, D", 1),
    0x4B: ("LD C, E", 1), 0x4C: ("LD C, H", 1), 0x4D: ("LD C, L", 1),
    0x4E: ("LD C, (HL)", 1), 0x4F: ("LD C, A", 1),
    0x50: ("LD D, B", 1), 0x51: ("LD D, C", 1), 0x52: ("LD D, D", 1),
    0x53: ("LD D, E", 1), 0x54: ("LD D, H", 1), 0x55: ("LD D, L", 1),
    0x56: ("LD D, (HL)", 1), 0x57: ("LD D, A", 1),
    0x58: ("LD E, B", 1), 0x59: ("LD E, C", 1), 0x5A: ("LD E, D", 1),
    0x5B: ("LD E, E", 1), 0x5C: ("LD E, H", 1), 0x5D: ("LD E, L", 1),
    0x5E: ("LD E, (HL)", 1), 0x5F: ("LD E, A", 1),
    0x60: ("LD H, B", 1), 0x61: ("LD H, C", 1), 0x62: ("LD H, D", 1),
    0x63: ("LD H, E", 1), 0x64: ("LD H, H", 1), 0x65: ("LD H, L", 1),
    0x66: ("LD H, (HL)", 1), 0x67: ("LD H, A", 1),
    0x68: ("LD L, B", 1), 0x69: ("LD L, C", 1), 0x6A: ("LD L, D", 1),
    0x6B: ("LD L, E", 1), 0x6C: ("LD L, H", 1), 0x6D: ("LD L, L", 1),
    0x6E: ("LD L, (HL)", 1), 0x6F: ("LD L, A", 1),
    0x70: ("LD (HL), B", 1), 0x71: ("LD (HL), C", 1), 0x72: ("LD (HL), D", 1),
    0x73: ("LD (HL), E", 1), 0x74: ("LD (HL), H", 1), 0x75: ("LD (HL), L", 1),
    0x76: ("HALT", 1), 0x77: ("LD (HL), A", 1),
    0x78: ("LD A, B", 1), 0x79: ("LD A, C", 1), 0x7A: ("LD A, D", 1),
    0x7B: ("LD A, E", 1), 0x7C: ("LD A, H", 1), 0x7D: ("LD A, L", 1),
    0x7E: ("LD A, (HL)", 1), 0x7F: ("LD A, A", 1),
    0x80: ("ADD A, B", 1), 0x81: ("ADD A, C", 1), 0x82: ("ADD A, D", 1),
    0x83: ("ADD A, E", 1), 0x84: ("ADD A, H", 1), 0x85: ("ADD A, L", 1),
    0x86: ("ADD A, (HL)", 1), 0x87: ("ADD A, A", 1),
    0x88: ("ADC A, B", 1), 0x89: ("ADC A, C", 1), 0x8A: ("ADC A, D", 1),
    0x8B: ("ADC A, E", 1), 0x8C: ("ADC A, H", 1), 0x8D: ("ADC A, L", 1),
    0x8E: ("ADC A, (HL)", 1), 0x8F: ("ADC A, A", 1),
    0x90: ("SUB B", 1), 0x91: ("SUB C", 1), 0x92: ("SUB D", 1),
    0x93: ("SUB E", 1), 0x94: ("SUB H", 1), 0x95: ("SUB L", 1),
    0x96: ("SUB (HL)", 1), 0x97: ("SUB A", 1),
    0x98: ("SBC A, B", 1), 0x99: ("SBC A, C", 1), 0x9A: ("SBC A, D", 1),
    0x9B: ("SBC A, E", 1), 0x9C: ("SBC A, H", 1), 0x9D: ("SBC A, L", 1),
    0x9E: ("SBC A, (HL)", 1), 0x9F: ("SBC A, A", 1),
    0xA0: ("AND B", 1), 0xA1: ("AND C", 1), 0xA2: ("AND D", 1),
    0xA3: ("AND E", 1), 0xA4: ("AND H", 1), 0xA5: ("AND L", 1),
    0xA6: ("AND (HL)", 1), 0xA7: ("AND A", 1),
    0xA8: ("XOR B", 1), 0xA9: ("XOR C", 1), 0xAA: ("XOR D", 1),
    0xAB: ("XOR E", 1), 0xAC: ("XOR H", 1), 0xAD: ("XOR L", 1),
    0xAE: ("XOR (HL)", 1), 0xAF: ("XOR A", 1),
    0xB0: ("OR B", 1), 0xB1: ("OR C", 1), 0xB2: ("OR D", 1),
    0xB3: ("OR E", 1), 0xB4: ("OR H", 1), 0xB5: ("OR L", 1),
    0xB6: ("OR (HL)", 1), 0xB7: ("OR A", 1),
    0xB8: ("CP B", 1), 0xB9: ("CP C", 1), 0xBA: ("CP D", 1),
    0xBB: ("CP E", 1), 0xBC: ("CP H", 1), 0xBD: ("CP L", 1),
    0xBE: ("CP (HL)", 1), 0xBF: ("CP A", 1),
    0xC0: ("RET NZ", 1), 0xC1: ("POP BC", 1), 0xC2: ("JP NZ, {w}h", 3),
    0xC3: ("JP {w}h", 3), 0xC4: ("CALL NZ, {w}h", 3), 0xC5: ("PUSH BC", 1),
    0xC6: ("ADD A, {b0}h", 2), 0xC7: ("RST 00h", 1),
    0xC8: ("RET Z", 1), 0xC9: ("RET", 1), 0xCA: ("JP Z, {w}h", 3),
    # 0xCB: CB prefix — handled separately
    0xCC: ("CALL Z, {w}h", 3), 0xCD: ("CALL {w}h", 3),
    0xCE: ("ADC A, {b0}h", 2), 0xCF: ("RST 08h", 1),
    0xD0: ("RET NC", 1), 0xD1: ("POP DE", 1), 0xD2: ("JP NC, {w}h", 3),
    0xD3: ("OUT ({b0}h), A", 2), 0xD4: ("CALL NC, {w}h", 3), 0xD5: ("PUSH DE", 1),
    0xD6: ("SUB {b0}h", 2), 0xD7: ("RST 10h", 1),
    0xD8: ("RET C", 1), 0xD9: ("EXX", 1), 0xDA: ("JP C, {w}h", 3),
    0xDB: ("IN A, ({b0}h)", 2), 0xDC: ("CALL C, {w}h", 3),
    # 0xDD: DD prefix — handled separately
    0xDE: ("SBC A, {b0}h", 2), 0xDF: ("RST 18h", 1),
    0xE0: ("RET PO", 1), 0xE1: ("POP HL", 1), 0xE2: ("JP PO, {w}h", 3),
    0xE3: ("EX (SP), HL", 1), 0xE4: ("CALL PO, {w}h", 3), 0xE5: ("PUSH HL", 1),
    0xE6: ("AND {b0}h", 2), 0xE7: ("RST 20h", 1),
    0xE8: ("RET PE", 1), 0xE9: ("JP (HL)", 1), 0xEA: ("JP PE, {w}h", 3),
    0xEB: ("EX DE, HL", 1), 0xEC: ("CALL PE, {w}h", 3),
    # 0xED: ED prefix — handled separately
    0xEE: ("XOR {b0}h", 2), 0xEF: ("RST 28h", 1),
    0xF0: ("RET P", 1), 0xF1: ("POP AF", 1), 0xF2: ("JP P, {w}h", 3),
    0xF3: ("DI", 1), 0xF4: ("CALL P, {w}h", 3), 0xF5: ("PUSH AF", 1),
    0xF6: ("OR {b0}h", 2), 0xF7: ("RST 30h", 1),
    0xF8: ("RET M", 1), 0xF9: ("LD SP, HL", 1), 0xFA: ("JP M, {w}h", 3),
    0xFB: ("EI", 1), 0xFC: ("CALL M, {w}h", 3),
    # 0xFD: FD prefix — handled separately
    0xFE: ("CP {b0}h", 2), 0xFF: ("RST 38h", 1),
}

# ---------------------------------------------------------------------------
# CB prefix table (0xCB 0xXX)
# ---------------------------------------------------------------------------
_CB_OPS: dict[int, tuple[str, int]] = {}

def _build_cb() -> None:
    regs = ["B", "C", "D", "E", "H", "L", "(HL)", "A"]
    for i, r in enumerate(regs):
        _CB_OPS[0x00 + i] = (f"RLC {r}", 2)
        _CB_OPS[0x08 + i] = (f"RRC {r}", 2)
        _CB_OPS[0x10 + i] = (f"RL {r}", 2)
        _CB_OPS[0x18 + i] = (f"RR {r}", 2)
        _CB_OPS[0x20 + i] = (f"SLA {r}", 2)
        _CB_OPS[0x28 + i] = (f"SRA {r}", 2)
        _CB_OPS[0x30 + i] = (f"SLL {r}", 2)   # undocumented but common
        _CB_OPS[0x38 + i] = (f"SRL {r}", 2)
        for bit in range(8):
            _CB_OPS[0x40 + bit * 8 + i] = (f"BIT {bit}, {r}", 2)
            _CB_OPS[0x80 + bit * 8 + i] = (f"RES {bit}, {r}", 2)
            _CB_OPS[0xC0 + bit * 8 + i] = (f"SET {bit}, {r}", 2)

_build_cb()

# ---------------------------------------------------------------------------
# ED prefix table
# ---------------------------------------------------------------------------
_ED_OPS: dict[int, tuple[str, int]] = {
    0x40: ("IN B, (C)", 2), 0x41: ("OUT (C), B", 2),
    0x42: ("SBC HL, BC", 2), 0x43: ("LD ({w}h), BC", 4),
    0x44: ("NEG", 2), 0x45: ("RETN", 2), 0x46: ("IM 0", 2), 0x47: ("LD I, A", 2),
    0x48: ("IN C, (C)", 2), 0x49: ("OUT (C), C", 2),
    0x4A: ("ADC HL, BC", 2), 0x4B: ("LD BC, ({w}h)", 4),
    0x4C: ("NEG", 2), 0x4D: ("RETI", 2), 0x4E: ("IM 0", 2), 0x4F: ("LD R, A", 2),
    0x50: ("IN D, (C)", 2), 0x51: ("OUT (C), D", 2),
    0x52: ("SBC HL, DE", 2), 0x53: ("LD ({w}h), DE", 4),
    0x54: ("NEG", 2), 0x55: ("RETN", 2), 0x56: ("IM 1", 2), 0x57: ("LD A, I", 2),
    0x58: ("IN E, (C)", 2), 0x59: ("OUT (C), E", 2),
    0x5A: ("ADC HL, DE", 2), 0x5B: ("LD DE, ({w}h)", 4),
    0x5C: ("NEG", 2), 0x5D: ("RETN", 2), 0x5E: ("IM 2", 2), 0x5F: ("LD A, R", 2),
    0x60: ("IN H, (C)", 2), 0x61: ("OUT (C), H", 2),
    0x62: ("SBC HL, HL", 2), 0x63: ("LD ({w}h), HL", 4),
    0x64: ("NEG", 2), 0x65: ("RETN", 2), 0x66: ("IM 0", 2), 0x67: ("RRD", 2),
    0x68: ("IN L, (C)", 2), 0x69: ("OUT (C), L", 2),
    0x6A: ("ADC HL, HL", 2), 0x6B: ("LD HL, ({w}h)", 4),
    0x6C: ("NEG", 2), 0x6D: ("RETN", 2), 0x6E: ("IM 0", 2), 0x6F: ("RLD", 2),
    0x70: ("IN F, (C)", 2), 0x71: ("OUT (C), 0", 2),
    0x72: ("SBC HL, SP", 2), 0x73: ("LD ({w}h), SP", 4),
    0x74: ("NEG", 2), 0x75: ("RETN", 2), 0x76: ("IM 1", 2),
    0x78: ("IN A, (C)", 2), 0x79: ("OUT (C), A", 2),
    0x7A: ("ADC HL, SP", 2), 0x7B: ("LD SP, ({w}h)", 4),
    0x7C: ("NEG", 2), 0x7D: ("RETN", 2), 0x7E: ("IM 2", 2),
    0xA0: ("LDI", 2), 0xA1: ("CPI", 2), 0xA2: ("INI", 2), 0xA3: ("OUTI", 2),
    0xA8: ("LDD", 2), 0xA9: ("CPD", 2), 0xAA: ("IND", 2), 0xAB: ("OUTD", 2),
    0xB0: ("LDIR", 2), 0xB1: ("CPIR", 2), 0xB2: ("INIR", 2), 0xB3: ("OTIR", 2),
    0xB8: ("LDDR", 2), 0xB9: ("CPDR", 2), 0xBA: ("INDR", 2), 0xBB: ("OTDR", 2),
}

# ---------------------------------------------------------------------------
# DD/FD prefix tables (IX/IY variants)
# {id} = displacement, {ib} = immediate byte after displacement
# ---------------------------------------------------------------------------
def _build_xy(xy: str) -> dict[int, tuple[str, int]]:
    """Build opcode table for DD (IX) or FD (IY) prefix."""
    t: dict[int, tuple[str, int]] = {
        0x09: (f"ADD {xy}, BC", 2),
        0x19: (f"ADD {xy}, DE", 2),
        0x21: (f"LD {xy}, {{w}}h", 4),
        0x22: (f"LD ({{w}}h), {xy}", 4),
        0x23: (f"INC {xy}", 2),
        0x24: (f"INC {xy}H", 2),
        0x25: (f"DEC {xy}H", 2),
        0x26: (f"LD {xy}H, {{b0}}h", 3),
        0x29: (f"ADD {xy}, {xy}", 2),
        0x2A: (f"LD {xy}, ({{w}}h)", 4),
        0x2B: (f"DEC {xy}", 2),
        0x2C: (f"INC {xy}L", 2),
        0x2D: (f"DEC {xy}L", 2),
        0x2E: (f"LD {xy}L, {{b0}}h", 3),
        0x34: (f"INC ({xy}+{{id}})", 3),
        0x35: (f"DEC ({xy}+{{id}})", 3),
        0x36: (f"LD ({xy}+{{id}}), {{ib}}h", 4),
        0x39: (f"ADD {xy}, SP", 2),
        0x44: (f"LD B, {xy}H", 2),
        0x45: (f"LD B, {xy}L", 2),
        0x46: (f"LD B, ({xy}+{{id}})", 3),
        0x4C: (f"LD C, {xy}H", 2),
        0x4D: (f"LD C, {xy}L", 2),
        0x4E: (f"LD C, ({xy}+{{id}})", 3),
        0x54: (f"LD D, {xy}H", 2),
        0x55: (f"LD D, {xy}L", 2),
        0x56: (f"LD D, ({xy}+{{id}})", 3),
        0x5C: (f"LD E, {xy}H", 2),
        0x5D: (f"LD E, {xy}L", 2),
        0x5E: (f"LD E, ({xy}+{{id}})", 3),
        0x60: (f"LD {xy}H, B", 2),
        0x61: (f"LD {xy}H, C", 2),
        0x62: (f"LD {xy}H, D", 2),
        0x63: (f"LD {xy}H, E", 2),
        0x64: (f"LD {xy}H, {xy}H", 2),
        0x65: (f"LD {xy}H, {xy}L", 2),
        0x66: (f"LD H, ({xy}+{{id}})", 3),
        0x67: (f"LD {xy}H, A", 2),
        0x68: (f"LD {xy}L, B", 2),
        0x69: (f"LD {xy}L, C", 2),
        0x6A: (f"LD {xy}L, D", 2),
        0x6B: (f"LD {xy}L, E", 2),
        0x6C: (f"LD {xy}L, {xy}H", 2),
        0x6D: (f"LD {xy}L, {xy}L", 2),
        0x6E: (f"LD L, ({xy}+{{id}})", 3),
        0x6F: (f"LD {xy}L, A", 2),
        0x70: (f"LD ({xy}+{{id}}), B", 3),
        0x71: (f"LD ({xy}+{{id}}), C", 3),
        0x72: (f"LD ({xy}+{{id}}), D", 3),
        0x73: (f"LD ({xy}+{{id}}), E", 3),
        0x74: (f"LD ({xy}+{{id}}), H", 3),
        0x75: (f"LD ({xy}+{{id}}), L", 3),
        0x77: (f"LD ({xy}+{{id}}), A", 3),
        0x7C: (f"LD A, {xy}H", 2),
        0x7D: (f"LD A, {xy}L", 2),
        0x7E: (f"LD A, ({xy}+{{id}})", 3),
        0x84: (f"ADD A, {xy}H", 2),
        0x85: (f"ADD A, {xy}L", 2),
        0x86: (f"ADD A, ({xy}+{{id}})", 3),
        0x8C: (f"ADC A, {xy}H", 2),
        0x8D: (f"ADC A, {xy}L", 2),
        0x8E: (f"ADC A, ({xy}+{{id}})", 3),
        0x94: (f"SUB {xy}H", 2),
        0x95: (f"SUB {xy}L", 2),
        0x96: (f"SUB ({xy}+{{id}})", 3),
        0x9C: (f"SBC A, {xy}H", 2),
        0x9D: (f"SBC A, {xy}L", 2),
        0x9E: (f"SBC A, ({xy}+{{id}})", 3),
        0xA4: (f"AND {xy}H", 2),
        0xA5: (f"AND {xy}L", 2),
        0xA6: (f"AND ({xy}+{{id}})", 3),
        0xAC: (f"XOR {xy}H", 2),
        0xAD: (f"XOR {xy}L", 2),
        0xAE: (f"XOR ({xy}+{{id}})", 3),
        0xB4: (f"OR {xy}H", 2),
        0xB5: (f"OR {xy}L", 2),
        0xB6: (f"OR ({xy}+{{id}})", 3),
        0xBC: (f"CP {xy}H", 2),
        0xBD: (f"CP {xy}L", 2),
        0xBE: (f"CP ({xy}+{{id}})", 3),
        0xC1: (f"POP {xy}", 2),
        0xC5: (f"PUSH {xy}", 2),
        0xE1: (f"POP {xy}", 2),
        0xE3: (f"EX (SP), {xy}", 2),
        0xE5: (f"PUSH {xy}", 2),
        0xE9: (f"JP ({xy})", 2),
        0xF9: (f"LD SP, {xy}", 2),
    }
    return t

_DD_OPS: dict[int, tuple[str, int]] = _build_xy("IX")
_FD_OPS: dict[int, tuple[str, int]] = _build_xy("IY")

# ---------------------------------------------------------------------------
# DDCB / FDCB — 4-byte encoding: prefix prefix disp opcode
# Key = opcode byte (4th byte); template already has xy placeholder
# ---------------------------------------------------------------------------
def _build_xycb(xy: str) -> dict[int, tuple[str, int]]:
    t: dict[int, tuple[str, int]] = {}
    for bit in range(8):
        t[0x46 + bit * 8] = (f"BIT {bit}, ({xy}+{{id}})", 4)
        t[0x86 + bit * 8] = (f"RES {bit}, ({xy}+{{id}})", 4)
        t[0xC6 + bit * 8] = (f"SET {bit}, ({xy}+{{id}})", 4)
    # Rotate/shift group. Index 6 is the documented standalone (IX+d)/(IY+d)
    # form (no register copy); the other indices are the undocumented variants
    # that also copy the result into a register. 0x30 is SLL (undocumented).
    regs = ["B", "C", "D", "E", "H", "L", None, "A"]
    shift_ops = [
        (0x00, "RLC"), (0x08, "RRC"), (0x10, "RL"), (0x18, "RR"),
        (0x20, "SLA"), (0x28, "SRA"), (0x30, "SLL"), (0x38, "SRL"),
    ]
    for base, mnem in shift_ops:
        for i, r in enumerate(regs):
            suffix = "" if r is None else f", {r}"
            t[base + i] = (f"{mnem} ({xy}+{{id}}){suffix}", 4)
    return t

_DDCB_OPS: dict[int, tuple[str, int]] = _build_xycb("IX")
_FDCB_OPS: dict[int, tuple[str, int]] = _build_xycb("IY")


# ---------------------------------------------------------------------------
# Main decode function
# ---------------------------------------------------------------------------
def _signed8(b: int) -> int:
    return b if b < 128 else b - 256


def _fmt_disp(d: int) -> str:
    s = _signed8(d)
    return f"+{s:02X}h" if s >= 0 else f"-{(-s):02X}h"


def _apply_template(tmpl: str, read: Callable[[int], int], addr: int, prefix_len: int) -> str:
    """Substitute placeholders in a template string.

    Args:
        tmpl: Template string with placeholders.
        read: Memory read callback.
        addr: Base address of the instruction (before prefix).
        prefix_len: Number of prefix bytes consumed before the opcode byte.
    """
    # Offset within instruction bytes:
    #   prefix_len prefix bytes + 1 opcode byte already consumed
    off = addr + prefix_len + 1

    if "{w}h" in tmpl:
        lo = read(off & 0xFFFF)
        hi = read((off + 1) & 0xFFFF)
        return tmpl.replace("{w}h", f"{(hi << 8) | lo:04X}h")

    if "{w}" in tmpl:
        lo = read(off & 0xFFFF)
        hi = read((off + 1) & 0xFFFF)
        return tmpl.replace("{w}", f"{(hi << 8) | lo:04X}h")

    if "{r}" in tmpl:
        raw = read(off & 0xFFFF)
        # Relative jump: target = addr + total_size + signed_offset
        # Show signed offset for simplicity (consistent with common Z80 debuggers)
        s = _signed8(raw)
        target = (addr + prefix_len + 2 + s) & 0xFFFF
        return tmpl.replace("{r}", f"{target:04X}h")

    if "{b0}h" in tmpl:
        b0 = read(off & 0xFFFF)
        return tmpl.replace("{b0}h", f"{b0:02X}h")

    if "{b0}" in tmpl:
        b0 = read(off & 0xFFFF)
        return tmpl.replace("{b0}", f"{b0:02X}h")

    return tmpl


def _apply_xy_template(tmpl: str, read: Callable[[int], int], addr: int) -> str:
    """Substitute IX/IY displacement templates.

    DD/FD instructions:
        addr+0 = prefix (DD or FD)
        addr+1 = opcode
        addr+2 = displacement (for {id} templates)
        addr+3 = immediate byte (for {ib} templates)
    """
    off_disp = (addr + 2) & 0xFFFF

    if "{id}" in tmpl and "{ib}h" in tmpl:
        d = read(off_disp)
        ib = read((off_disp + 1) & 0xFFFF)
        tmpl = tmpl.replace("{id}", _fmt_disp(d))
        tmpl = tmpl.replace("{ib}h", f"{ib:02X}h")
        return tmpl

    if "{id}" in tmpl:
        d = read(off_disp)
        return tmpl.replace("{id}", _fmt_disp(d))

    if "{w}h" in tmpl or "{w}" in tmpl:
        lo = read((addr + 2) & 0xFFFF)
        hi = read((addr + 3) & 0xFFFF)
        val = f"{(hi << 8) | lo:04X}h"
        return tmpl.replace("{w}h", val).replace("{w}", val)

    if "{b0}h" in tmpl or "{b0}" in tmpl:
        b0 = read((addr + 2) & 0xFFFF)
        val = f"{b0:02X}h"
        return tmpl.replace("{b0}h", val).replace("{b0}", val)

    return tmpl


def _apply_xycb_template(tmpl: str, read: Callable[[int], int], addr: int) -> str:
    """Substitute DDCB/FDCB displacement templates.

    4-byte encoding: prefix prefix disp opcode
        addr+0 = DD or FD
        addr+1 = CB
        addr+2 = displacement
        addr+3 = opcode (key into _DDCB_OPS / _FDCB_OPS)
    """
    d = read((addr + 2) & 0xFFFF)
    return tmpl.replace("{id}", _fmt_disp(d))


def disassemble(read: Callable[[int], int], addr: int) -> tuple[str, int]:
    """Decode one Z80 instruction.

    Args:
        read: Callable(address) -> byte value (0x00–0xFF).
        addr: Address of the first byte of the instruction.

    Returns:
        Tuple of (mnemonic_string, bytes_consumed).
        Unknown bytes are returned as ("DB XXh", 1).
    """
    b0 = read(addr & 0xFFFF)

    if b0 == 0xCB:
        b1 = read((addr + 1) & 0xFFFF)
        entry = _CB_OPS.get(b1)
        if entry:
            return entry[0], entry[1]
        return f"DB {b1:02X}h", 2

    if b0 == 0xED:
        b1 = read((addr + 1) & 0xFFFF)
        entry = _ED_OPS.get(b1)
        if entry:
            tmpl, size = entry
            mnemonic = _apply_template(tmpl, read, addr, prefix_len=1)
            return mnemonic, size
        return f"DB {b1:02X}h", 2

    if b0 in (0xDD, 0xFD):
        xy_ops = _DD_OPS if b0 == 0xDD else _FD_OPS
        xycb_ops = _DDCB_OPS if b0 == 0xDD else _FDCB_OPS

        b1 = read((addr + 1) & 0xFFFF)

        if b1 == 0xCB:
            b3 = read((addr + 3) & 0xFFFF)
            entry = xycb_ops.get(b3)
            if entry:
                tmpl, size = entry
                return _apply_xycb_template(tmpl, read, addr), size
            return f"DB {b3:02X}h", 4

        entry = xy_ops.get(b1)
        if entry:
            tmpl, size = entry
            return _apply_xy_template(tmpl, read, addr), size

        # Unrecognised DD/FD sub-opcode: fall back to main table for b1
        main_entry = _MAIN_OPS.get(b1)
        if main_entry:
            tmpl, size = main_entry
            mnemonic = _apply_template(tmpl, read, addr + 1, prefix_len=0)
            return mnemonic, size + 1  # +1 for the prefix byte
        return f"DB {b0:02X}h", 1

    # Main table
    entry = _MAIN_OPS.get(b0)
    if entry:
        tmpl, size = entry
        mnemonic = _apply_template(tmpl, read, addr, prefix_len=0)
        return mnemonic, size

    return f"DB {b0:02X}h", 1
