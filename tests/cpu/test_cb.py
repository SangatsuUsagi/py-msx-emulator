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


def test_rlc_b() -> None:
    cpu = make_cpu([0xCB, 0x00])  # RLC B
    cpu.registers.B = 0x85
    cpu.step()
    assert cpu.registers.B == 0x0B
    assert cpu.registers.F & F.FLAG_C


def test_rrc_c() -> None:
    cpu = make_cpu([0xCB, 0x09])  # RRC C
    cpu.registers.C = 0x01
    cpu.step()
    assert cpu.registers.C == 0x80
    assert cpu.registers.F & F.FLAG_C


def test_rl_d() -> None:
    cpu = make_cpu([0xCB, 0x12])  # RL D
    cpu.registers.D = 0x76
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.D == 0xED
    assert not (cpu.registers.F & F.FLAG_C)


def test_sla_e() -> None:
    cpu = make_cpu([0xCB, 0x23])  # SLA E
    cpu.registers.E = 0x81
    cpu.step()
    assert cpu.registers.E == 0x02
    assert cpu.registers.F & F.FLAG_C


def test_srl_a() -> None:
    cpu = make_cpu([0xCB, 0x3F])  # SRL A
    cpu.registers.A = 0x01
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & F.FLAG_C
    assert cpu.registers.F & F.FLAG_Z


def test_bit_3_b_set() -> None:
    cpu = make_cpu([0xCB, 0x58])  # BIT 3, B
    cpu.registers.B = 0x08
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_Z)


def test_bit_3_b_clear() -> None:
    cpu = make_cpu([0xCB, 0x58])
    cpu.registers.B = 0x00
    cpu.step()
    assert cpu.registers.F & F.FLAG_Z


def test_set_5_c() -> None:
    cpu = make_cpu([0xCB, 0xE9])  # SET 5, C
    cpu.registers.C = 0x00
    cpu.step()
    assert cpu.registers.C == 0x20


def test_res_7_a() -> None:
    cpu = make_cpu([0xCB, 0xBF])  # RES 7, A
    cpu.registers.A = 0xFF
    cpu.step()
    assert cpu.registers.A == 0x7F


def test_bit7_ix_with_bit_set_sets_sign_flag() -> None:
    rom = bytes([0xDD, 0xCB, 0x02, 0x7E] + [0] * 32764)  # BIT 7,(IX+2)
    ram = bytearray(32768)
    ram[0x4002] = 0x80  # 0xC002 -> ram[addr - 0x8000]; bit 7 set
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    f = cpu.registers.F
    assert f & F.FLAG_S
    assert not (f & F.FLAG_Z)
    assert f & F.FLAG_H
    assert not (f & F.FLAG_N)


def test_bit7_ix_with_bit_clear_clears_sign_sets_zero() -> None:
    rom = bytes([0xDD, 0xCB, 0x02, 0x7E] + [0] * 32764)  # BIT 7,(IX+2)
    ram = bytearray(32768)  # 0xC002 reads back 0x00
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    f = cpu.registers.F
    assert not (f & F.FLAG_S)
    assert f & F.FLAG_Z
