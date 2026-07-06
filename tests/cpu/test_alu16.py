from msx.cpu import flags as F
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_add_hl_bc() -> None:
    cpu = make_cpu([0x09])  # ADD HL, BC
    cpu.registers.HL = 0x1000
    cpu.registers.BC = 0x0234
    cpu.step()
    assert cpu.registers.HL == 0x1234
    assert not (cpu.registers.F & F.FLAG_C)


def test_add_hl_overflow() -> None:
    cpu = make_cpu([0x29])  # ADD HL, HL
    cpu.registers.HL = 0x8000
    cpu.step()
    assert cpu.registers.HL == 0x0000
    assert cpu.registers.F & F.FLAG_C


def test_inc_bc() -> None:
    cpu = make_cpu([0x03])  # INC BC
    cpu.registers.BC = 0x00FF
    cpu.step()
    assert cpu.registers.BC == 0x0100


def test_dec_de() -> None:
    cpu = make_cpu([0x1B])  # DEC DE
    cpu.registers.DE = 0x0100
    cpu.step()
    assert cpu.registers.DE == 0x00FF


def test_inc_hl() -> None:
    cpu = make_cpu([0x23])
    cpu.registers.HL = 0xFFFE
    cpu.step()
    assert cpu.registers.HL == 0xFFFF


def test_adc_hl_bc() -> None:
    cpu = make_cpu([0xED, 0x4A])  # ADC HL, BC
    cpu.registers.HL = 0x0001
    cpu.registers.BC = 0x0001
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.HL == 0x0003


def test_sbc_hl_de() -> None:
    cpu = make_cpu([0xED, 0x52])  # SBC HL, DE
    cpu.registers.HL = 0x0010
    cpu.registers.DE = 0x0005
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.HL == 0x000B
    assert cpu.registers.F & F.FLAG_N


# ===========================================================================
# Characterization tests (test-coverage-hardening Phase 0): _adc16 / _sbc16
# boundary flag paths and _make_alu_imm (AND/OR/XOR/CP n). Full S/Z/H/PV/N/C
# flag byte asserted (these paths never touch the undocumented X/Y bits).
# Values confirmed by running the opcodes through the CPU.
# ===========================================================================


def _adc16(hl: int, pair_op: int, pair_setup, carry: bool) -> Z80:
    cpu = make_cpu([0xED, pair_op])
    cpu.registers.HL = hl
    pair_setup(cpu.registers)
    cpu.registers.F = F.FLAG_C if carry else 0
    cpu.step()
    return cpu


def test_adc16_boundary_wrap_sets_z_h_c() -> None:
    # 0xFFFF + 0 + carry = 0x10000 -> 0x0000: carry out, half-carry, zero
    cpu = _adc16(0xFFFF, 0x5A, lambda r: setattr(r, "DE", 0x0000), carry=True)
    assert cpu.registers.HL == 0x0000
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_H | F.FLAG_C)


def test_adc16_half_carry_at_bit11() -> None:
    # 0x0FFF + 0x0001 = 0x1000: half-carry from bit 11 only
    cpu = _adc16(0x0FFF, 0x7A, lambda r: setattr(r, "SP", 0x0001), carry=False)
    assert cpu.registers.HL == 0x1000
    assert cpu.registers.F == F.FLAG_H


def test_adc16_signed_overflow_sets_pv() -> None:
    # 0x7FFF + 0x7FFF + 1 = 0xFFFF: positive+positive -> negative -> overflow
    cpu = _adc16(0x7FFF, 0x6A, lambda r: None, carry=True)  # ADC HL,HL
    assert cpu.registers.HL == 0xFFFF
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_H | F.FLAG_PV)


def test_adc16_carry_in_path() -> None:
    cpu = _adc16(0x1000, 0x4A, lambda r: setattr(r, "BC", 0x0234), carry=True)
    assert cpu.registers.HL == 0x1235  # HL + BC + 1
    assert cpu.registers.F == 0x00


def _sbc16(hl: int, pair_op: int, pair_setup, carry: bool) -> Z80:
    cpu = make_cpu([0xED, pair_op])
    cpu.registers.HL = hl
    pair_setup(cpu.registers)
    cpu.registers.F = F.FLAG_C if carry else 0
    cpu.step()
    return cpu


def test_sbc16_borrow_out() -> None:
    # 0x0000 - 0x0001 = -1 -> 0xFFFF: borrow, half-borrow, sign, N
    cpu = _sbc16(0x0000, 0x52, lambda r: setattr(r, "DE", 0x0001), carry=False)
    assert cpu.registers.HL == 0xFFFF
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_H | F.FLAG_N | F.FLAG_C)


def test_sbc16_half_borrow_at_bit11() -> None:
    # 0x1000 - 0x0001 = 0x0FFF: half-borrow from bit 11 only
    cpu = _sbc16(0x1000, 0x52, lambda r: setattr(r, "DE", 0x0001), carry=False)
    assert cpu.registers.HL == 0x0FFF
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_N)


def test_sbc16_signed_overflow_sets_pv() -> None:
    # 0x8000 - 0x0001 = 0x7FFF: negative-positive -> positive -> overflow
    cpu = _sbc16(0x8000, 0x72, lambda r: setattr(r, "SP", 0x0001), carry=False)
    assert cpu.registers.HL == 0x7FFF
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_PV | F.FLAG_N)


def test_sbc16_carry_in_path() -> None:
    cpu = _sbc16(0x0005, 0x42, lambda r: setattr(r, "BC", 0x0003), carry=True)
    assert cpu.registers.HL == 0x0001  # 5 - 3 - 1
    assert cpu.registers.F == F.FLAG_N


# --- _make_alu_imm: AND / OR / XOR / CP n -----------------------------------

def _alu_imm(op: int, a: int, n: int) -> Z80:
    cpu = make_cpu([op, n])
    cpu.registers.A = a
    cpu.step()
    return cpu


def test_and_n_clears_to_zero() -> None:
    cpu = _alu_imm(0xE6, 0xF0, 0x0F)  # AND 0x0F
    assert cpu.registers.A == 0x00
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_H | F.FLAG_PV)


def test_and_n_sets_half_and_parity() -> None:
    cpu = _alu_imm(0xE6, 0xFF, 0x3C)  # AND 0x3C
    assert cpu.registers.A == 0x3C
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_PV)


def test_or_n_sets_sign() -> None:
    cpu = _alu_imm(0xF6, 0x0F, 0xF0)  # OR 0xF0
    assert cpu.registers.A == 0xFF
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_PV)


def test_xor_n_sets_zero() -> None:
    cpu = _alu_imm(0xEE, 0x55, 0x55)  # XOR 0x55
    assert cpu.registers.A == 0x00
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_PV)


def test_cp_n_equal() -> None:
    cpu = _alu_imm(0xFE, 0x20, 0x20)  # CP 0x20
    assert cpu.registers.A == 0x20  # A unchanged
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_N)


def test_cp_n_less_sets_carry_and_sign() -> None:
    cpu = _alu_imm(0xFE, 0x10, 0x20)  # CP 0x20 (A < n)
    assert cpu.registers.A == 0x10  # A unchanged
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_N | F.FLAG_C)


def test_cp_n_signed_overflow() -> None:
    cpu = _alu_imm(0xFE, 0x80, 0x01)  # CP 0x01 on 0x80
    assert cpu.registers.A == 0x80  # A unchanged
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_PV | F.FLAG_N)
