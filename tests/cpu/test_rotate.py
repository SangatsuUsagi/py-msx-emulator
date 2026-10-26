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


def test_rlca() -> None:
    cpu = make_cpu([0x07])
    cpu.registers.A = 0x85  # 10000101
    cpu.step()
    assert cpu.registers.A == 0x0B  # 00001011
    assert cpu.registers.F & F.FLAG_C


def test_rrca() -> None:
    cpu = make_cpu([0x0F])
    cpu.registers.A = 0x01
    cpu.step()
    assert cpu.registers.A == 0x80
    assert cpu.registers.F & F.FLAG_C


def test_rla() -> None:
    cpu = make_cpu([0x17])
    cpu.registers.A = 0x76
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0xED
    assert not (cpu.registers.F & F.FLAG_C)


def test_rra() -> None:
    cpu = make_cpu([0x1F])
    cpu.registers.A = 0x01
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & F.FLAG_C


def test_cpl() -> None:
    cpu = make_cpu([0x2F])
    cpu.registers.A = 0x3C
    cpu.step()
    assert cpu.registers.A == 0xC3
    assert cpu.registers.F & F.FLAG_N
    assert cpu.registers.F & F.FLAG_H


def test_scf() -> None:
    cpu = make_cpu([0x37])
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.F & F.FLAG_C


def test_ccf_clears_carry() -> None:
    cpu = make_cpu([0x3F])
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_C)


def test_daa_after_add() -> None:
    cpu = make_cpu([0x27])
    cpu.registers.A = 0x3C  # result of 0x15 + 0x27 before DAA
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.A == 0x42  # BCD result: 15 + 27 = 42


def test_daa_bcd_add() -> None:
    cpu = make_cpu([0x3E, 0x15, 0xC6, 0x27, 0x27])  # LD A,15; ADD A,27h; DAA
    cpu.step()  # LD A, 0x15
    cpu.step()  # ADD A, 0x27 -> 0x3C
    cpu.step()  # DAA -> 0x42
    assert cpu.registers.A == 0x42


def test_daa_sets_half_carry_on_bit4_crossing() -> None:
    # A=0x0F, N=0: low nibble > 9 → add 0x06 → 0x15; the +6 crosses bit 4, so
    # DAA must set the half-carry (H) flag.
    cpu = make_cpu([0x27])
    cpu.registers.A = 0x0F
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.A == 0x15
    assert cpu.registers.F & F.FLAG_H


def test_daa_clears_half_carry_without_bit4_change() -> None:
    # A=0x12, N=0: no correction needed, A unchanged, so H must be clear.
    cpu = make_cpu([0x27])
    cpu.registers.A = 0x12
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.A == 0x12
    assert not (cpu.registers.F & F.FLAG_H)


def test_daa_after_subtract_corrects_invalid_bcd() -> None:
    # A=0x9A, N=1: the correction depends on A, H and C only, so it is 0x66
    # (low nibble > 9, A > 0x99) and is subtracted: 0x9A - 0x66 = 0x34.
    cpu = make_cpu([0x27])
    cpu.registers.A = 0x9A
    cpu.registers.F = F.FLAG_N
    cpu.step()
    assert cpu.registers.A == 0x34
    assert cpu.registers.F == 0x23  # N, C and Y (bit 5 of 0x34)


def test_daa_after_subtract_sets_carry_from_a() -> None:
    # A=0xA0, N=1: A > 0x99 alone selects 0x60 and sets C.
    cpu = make_cpu([0x27])
    cpu.registers.A = 0xA0
    cpu.registers.F = F.FLAG_N
    cpu.step()
    assert cpu.registers.A == 0x40
    assert cpu.registers.F & F.FLAG_C
