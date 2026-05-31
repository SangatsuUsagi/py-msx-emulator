from msx.memory import Memory
from msx.cpu.z80 import Z80
from msx.cpu import flags as F


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), cartridge=None)
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_add_a_b() -> None:
    cpu = make_cpu([0x80])  # ADD A, B
    cpu.registers.A = 0x10
    cpu.registers.B = 0x20
    cpu.step()
    assert cpu.registers.A == 0x30
    assert not (cpu.registers.F & F.FLAG_C)
    assert not (cpu.registers.F & F.FLAG_Z)


def test_add_a_overflow() -> None:
    cpu = make_cpu([0x87])  # ADD A, A
    cpu.registers.A = 0xFF
    cpu.step()
    assert cpu.registers.A == 0xFE
    assert cpu.registers.F & F.FLAG_C


def test_xor_a_zero() -> None:
    cpu = make_cpu([0xAF])  # XOR A
    cpu.registers.A = 0x42
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & F.FLAG_Z
    assert not (cpu.registers.F & F.FLAG_S)
    assert not (cpu.registers.F & F.FLAG_C)


def test_sub_result() -> None:
    cpu = make_cpu([0x90])  # SUB B
    cpu.registers.A = 0x10
    cpu.registers.B = 0x05
    cpu.step()
    assert cpu.registers.A == 0x0B
    assert cpu.registers.F & F.FLAG_N


def test_sub_borrow() -> None:
    cpu = make_cpu([0x97])  # SUB A
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & F.FLAG_Z


def test_inc_r() -> None:
    cpu = make_cpu([0x04])  # INC B
    cpu.registers.B = 0x0F
    cpu.step()
    assert cpu.registers.B == 0x10
    assert cpu.registers.F & F.FLAG_H


def test_dec_r() -> None:
    cpu = make_cpu([0x05])  # DEC B
    cpu.registers.B = 0x01
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F & F.FLAG_Z
    assert cpu.registers.F & F.FLAG_N


def test_and_r() -> None:
    cpu = make_cpu([0xA0])  # AND B
    cpu.registers.A = 0xFF
    cpu.registers.B = 0x0F
    cpu.step()
    assert cpu.registers.A == 0x0F
    assert cpu.registers.F & F.FLAG_H


def test_or_r() -> None:
    cpu = make_cpu([0xB0])  # OR B
    cpu.registers.A = 0xF0
    cpu.registers.B = 0x0F
    cpu.step()
    assert cpu.registers.A == 0xFF
    assert cpu.registers.F & F.FLAG_S


def test_cp_equal() -> None:
    cpu = make_cpu([0xB8])  # CP B
    cpu.registers.A = 0x42
    cpu.registers.B = 0x42
    cpu.step()
    assert cpu.registers.A == 0x42  # A unchanged
    assert cpu.registers.F & F.FLAG_Z


def test_adc_with_carry() -> None:
    cpu = make_cpu([0x88])  # ADC A, B
    cpu.registers.A = 0x01
    cpu.registers.B = 0x01
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0x03


def test_sbc_with_borrow() -> None:
    cpu = make_cpu([0x98])  # SBC A, B
    cpu.registers.A = 0x05
    cpu.registers.B = 0x02
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0x02
