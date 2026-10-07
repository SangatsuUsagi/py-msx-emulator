"""Undocumented F bits 5 (Y) and 3 (X): the per-family source value.

Real-hardware basis: extras/HB-10/YXFLAG.BAS on a SONY HB-F1XD (2026-10-07).
"""
import pytest

from msx.cpu import flags as F
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory

_YX = F.FLAG_Y | F.FLAG_X


def make_cpu(code: list[int]) -> Z80:
    mem = Memory(
        rom=bytes(code + [0] * (32768 - len(code))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    return Z80(read_byte=mem.read, write_byte=mem.write)


def _run(cpu: Z80, steps: int) -> None:
    for _ in range(steps):
        cpu.step()


def test_yxflag_bas_sequence() -> None:
    # The YXFLAG.BAS routine without its PUSH AF/POP BC/LD (nn),A bookkeeping.
    steps = [
        ([0xAF, 0xC6, 0x28], 2, 0x28),        # XOR A; ADD A,0x28
        ([0xAF, 0xFE, 0x28], 2, 0xBB),        # XOR A; CP 0x28
        ([0x3E, 0x28, 0xE6, 0xFF], 2, 0x3C),  # LD A,0x28; AND 0xFF
        ([0xAF, 0x3E, 0x28, 0x37], 3, 0x6D),  # XOR A; LD A,0x28; SCF
    ]
    for code, n, expected in steps:
        cpu = make_cpu(code)
        _run(cpu, n)
        assert cpu.registers.F == expected, code
    # BIT 0,A and RLCA follow the SCF, keeping its C and S/Z/PV as on hardware.
    cpu = make_cpu([0xAF, 0x3E, 0x28, 0x37, 0x3E, 0x28, 0xCB, 0x47, 0x3E, 0x14, 0x07])
    _run(cpu, 5)
    assert cpu.registers.F == 0x7D
    _run(cpu, 2)
    assert cpu.registers.F == 0x6C


def test_cp_takes_yx_from_the_operand() -> None:
    cpu = make_cpu([0xFE, 0x28])  # CP 0x28, A=0 -> result 0xD8 has bit 5 clear
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_add16_takes_yx_from_the_high_byte() -> None:
    cpu = make_cpu([0x19])  # ADD HL,DE
    cpu.registers.HL = 0x2000
    cpu.registers.DE = 0x0800
    cpu.registers.F = 0  # the old Y/X must not survive
    cpu.step()
    assert cpu.registers.HL == 0x2800
    assert cpu.registers.F & _YX == _YX


@pytest.mark.parametrize(("code", "a", "expected_yx"), [
    ([0x3C], 0x27, _YX),          # INC A -> 0x28
    ([0x3D], 0x29, _YX),          # DEC A -> 0x28
    ([0x2F], 0xD7, _YX),          # CPL -> 0x28
    ([0x3F], 0x28, _YX),          # CCF: from A
    ([0xED, 0x44], 0xD8, _YX),    # NEG -> 0x28
    ([0xCB, 0x07], 0x14, _YX),    # RLC A -> 0x28
])
def test_yx_from_result_or_a(code: list[int], a: int, expected_yx: int) -> None:
    cpu = make_cpu(code)
    cpu.registers.A = a
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.F & _YX == expected_yx


def test_bit_n_r_takes_yx_from_the_register() -> None:
    cpu = make_cpu([0xCB, 0x40])  # BIT 0,B
    cpu.registers.B = 0x28
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_bit_n_ix_d_takes_yx_from_the_address_high_byte() -> None:
    cpu = make_cpu([0xDD, 0xCB, 0x05, 0x46])  # BIT 0,(IX+5)
    cpu.registers.IX = 0xA800  # IX+5 = 0xA805: high byte 0xA8 has bits 5 and 3
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_bit_n_hl_clears_yx() -> None:
    cpu = make_cpu([0xCB, 0x46])  # BIT 0,(HL): MEMPTR not modelled
    cpu.registers.HL = 0xC000
    cpu.write_byte(0xC000, 0xFF)
    cpu.registers.F = _YX
    cpu.step()
    assert cpu.registers.F & _YX == 0


def test_ldi_takes_yx_from_a_plus_byte() -> None:
    cpu = make_cpu([0xED, 0xA0])  # LDI
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xD000
    cpu.registers.BC = 0x0002
    cpu.registers.A = 0x00
    cpu.registers.F = 0
    cpu.write_byte(0xC000, 0x0A)  # A + byte = 0x0A: bit 1 -> Y, bit 3 -> X
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_cpi_takes_yx_from_a_minus_byte_minus_h() -> None:
    cpu = make_cpu([0xED, 0xA1])  # CPI
    cpu.registers.HL = 0xC000
    cpu.registers.BC = 0x0002
    cpu.registers.A = 0x10
    cpu.write_byte(0xC000, 0x01)  # 0x10 - 0x01 = 0x0F with H set -> n = 0x0E
    cpu.step()
    assert cpu.registers.F & F.FLAG_H
    assert cpu.registers.F & _YX == _YX
