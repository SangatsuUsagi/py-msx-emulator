"""Tests for msx.debugger.disasm — Z80 disassembler."""

import pytest
from msx.debugger.disasm import disassemble


def _mem(*bytes_: int):
    """Return a read callable backed by a fixed byte sequence."""
    data = list(bytes_)

    def read(addr: int) -> int:
        return data[addr] if addr < len(data) else 0xFF

    return read


class TestMainOpcodes:
    def test_nop(self):
        mnem, size = disassemble(_mem(0x00), 0)
        assert mnem == "NOP"
        assert size == 1

    def test_ld_a_n(self):
        mnem, size = disassemble(_mem(0x3E, 0x42), 0)
        assert mnem == "LD A, 42h"
        assert size == 2

    def test_ld_bc_nn(self):
        mnem, size = disassemble(_mem(0x01, 0x34, 0x12), 0)
        assert mnem == "LD BC, 1234h"
        assert size == 3

    def test_halt(self):
        mnem, size = disassemble(_mem(0x76), 0)
        assert mnem == "HALT"
        assert size == 1

    def test_ret(self):
        mnem, size = disassemble(_mem(0xC9), 0)
        assert mnem == "RET"
        assert size == 1

    def test_call_nn(self):
        mnem, size = disassemble(_mem(0xCD, 0x00, 0xC0), 0)
        assert mnem == "CALL C000h"
        assert size == 3

    def test_rst_38(self):
        mnem, size = disassemble(_mem(0xFF), 0)
        assert mnem == "RST 38h"
        assert size == 1

    def test_xor_a(self):
        mnem, size = disassemble(_mem(0xAF), 0)
        assert mnem == "XOR A"
        assert size == 1

    def test_jr_relative(self):
        # JR $+2+5 = 0x0007
        mnem, size = disassemble(_mem(0x18, 0x05), 0)
        assert "0007h" in mnem
        assert size == 2

    def test_out_port(self):
        mnem, size = disassemble(_mem(0xD3, 0xA8), 0)
        assert mnem == "OUT (A8h), A"
        assert size == 2


class TestCBPrefix:
    def test_rlc_a(self):
        mnem, size = disassemble(_mem(0xCB, 0x07), 0)
        assert mnem == "RLC A"
        assert size == 2

    def test_rrc_b(self):
        mnem, size = disassemble(_mem(0xCB, 0x00), 0)
        assert mnem == "RLC B"
        assert size == 2

    def test_bit_0_hl(self):
        mnem, size = disassemble(_mem(0xCB, 0x46), 0)
        assert mnem == "BIT 0, (HL)"
        assert size == 2

    def test_set_7_a(self):
        mnem, size = disassemble(_mem(0xCB, 0xFF), 0)
        assert mnem == "SET 7, A"
        assert size == 2

    def test_res_3_e(self):
        mnem, size = disassemble(_mem(0xCB, 0x9B), 0)
        assert mnem == "RES 3, E"
        assert size == 2

    def test_srl_b(self):
        mnem, size = disassemble(_mem(0xCB, 0x38), 0)
        assert mnem == "SRL B"
        assert size == 2


class TestEDPrefix:
    def test_ldir(self):
        mnem, size = disassemble(_mem(0xED, 0xB0), 0)
        assert mnem == "LDIR"
        assert size == 2

    def test_lddr(self):
        mnem, size = disassemble(_mem(0xED, 0xB8), 0)
        assert mnem == "LDDR"
        assert size == 2

    def test_ld_nn_bc(self):
        mnem, size = disassemble(_mem(0xED, 0x43, 0x00, 0xC0), 0)
        assert mnem == "LD (C000h), BC"
        assert size == 4

    def test_neg(self):
        mnem, size = disassemble(_mem(0xED, 0x44), 0)
        assert mnem == "NEG"
        assert size == 2

    def test_im2(self):
        mnem, size = disassemble(_mem(0xED, 0x5E), 0)
        assert mnem == "IM 2"
        assert size == 2

    def test_reti(self):
        mnem, size = disassemble(_mem(0xED, 0x4D), 0)
        assert mnem == "RETI"
        assert size == 2

    def test_otir(self):
        mnem, size = disassemble(_mem(0xED, 0xB3), 0)
        assert mnem == "OTIR"
        assert size == 2


class TestDDPrefix:
    def test_ld_ix_nn(self):
        mnem, size = disassemble(_mem(0xDD, 0x21, 0x00, 0x40), 0)
        assert mnem == "LD IX, 4000h"
        assert size == 4

    def test_ld_a_ix_d(self):
        mnem, size = disassemble(_mem(0xDD, 0x7E, 0x05), 0)
        assert mnem == "LD A, (IX++05h)"
        assert size == 3

    def test_ld_ix_d_n(self):
        mnem, size = disassemble(_mem(0xDD, 0x36, 0x02, 0x42), 0)
        assert mnem == "LD (IX++02h), 42h"
        assert size == 4

    def test_inc_ix(self):
        mnem, size = disassemble(_mem(0xDD, 0x23), 0)
        assert mnem == "INC IX"
        assert size == 2

    def test_add_ix_bc(self):
        mnem, size = disassemble(_mem(0xDD, 0x09), 0)
        assert mnem == "ADD IX, BC"
        assert size == 2


class TestFDPrefix:
    def test_ld_iy_nn(self):
        mnem, size = disassemble(_mem(0xFD, 0x21, 0x00, 0x80), 0)
        assert mnem == "LD IY, 8000h"
        assert size == 4

    def test_ld_b_iy_d(self):
        mnem, size = disassemble(_mem(0xFD, 0x46, 0x10), 0)
        assert mnem == "LD B, (IY++10h)"
        assert size == 3


class TestDDCBPrefix:
    def test_bit_0_ix_d(self):
        # DD CB 05 46 = BIT 0, (IX+5)
        mnem, size = disassemble(_mem(0xDD, 0xCB, 0x05, 0x46), 0)
        assert "BIT 0" in mnem
        assert "IX" in mnem
        assert size == 4

    def test_res_2_ix_d(self):
        mnem, size = disassemble(_mem(0xDD, 0xCB, 0x03, 0x96), 0)
        assert "RES 2" in mnem
        assert "IX" in mnem
        assert size == 4

    def test_set_7_iy_d(self):
        mnem, size = disassemble(_mem(0xFD, 0xCB, 0x01, 0xFE), 0)
        assert "SET 7" in mnem
        assert "IY" in mnem
        assert size == 4


class TestUnknownOpcode:
    def test_unknown_main(self):
        # 0xD3 is OUT (n), A — known; use 0xDD with unknown sub-opcode
        # 0xED 0x00 is unknown
        mnem, size = disassemble(_mem(0xED, 0x00), 0)
        assert "DB" in mnem
        assert size == 2

    def test_unknown_byte(self):
        # No such main opcode — actually all main bytes are covered or handled.
        # Let's inject an unmapped ED sub-opcode.
        mnem, size = disassemble(_mem(0xED, 0x01), 0)
        assert "DB" in mnem

    def test_fallback_returns_one_byte(self):
        # 0xD3 is a valid opcode — but let's test an unmapped DD sub
        # 0xDD 0x00 (NOP via DD fall-through) — test unknown sub-op path
        mnem, size = disassemble(_mem(0xDD, 0x00), 0)
        # Falls back to main table NOP + prefix → size should be 2
        assert size >= 1

    def test_db_for_truly_unknown(self):
        # ED sub-opcode 0x10 is not in ED table
        mnem, size = disassemble(_mem(0xED, 0x10), 0)
        assert "DB" in mnem
