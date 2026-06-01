from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80
from msx.cpu import flags as F


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ldi() -> None:
    rom = bytes([0xED, 0xA0] + [0] * 32766)
    ram = bytearray(16384)
    ram[0] = 0xAB  # source at 0xC000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0003
    cpu.step()
    assert mem.read(0xC010) == 0xAB
    assert cpu.registers.HL == 0xC001
    assert cpu.registers.DE == 0xC011
    assert cpu.registers.BC == 0x0002
    assert cpu.registers.F & F.FLAG_PV


def test_ldir() -> None:
    rom = bytes([0xED, 0xB0] + [0] * 32766)
    ram = bytearray(16384)
    ram[0] = 0x01
    ram[1] = 0x02
    ram[2] = 0x03
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0003
    # LDIR repeats until BC == 0; PC rewinds each time
    for _ in range(3):
        cpu.step()
    assert mem.read(0xC010) == 0x01
    assert mem.read(0xC011) == 0x02
    assert mem.read(0xC012) == 0x03
    assert cpu.registers.BC == 0x0000
    assert not (cpu.registers.F & F.FLAG_PV)


def test_neg() -> None:
    cpu = make_cpu([0xED, 0x44])  # NEG
    cpu.registers.A = 0x01
    cpu.step()
    assert cpu.registers.A == 0xFF
    assert cpu.registers.F & F.FLAG_C
    assert cpu.registers.F & F.FLAG_N


def test_im1() -> None:
    cpu = make_cpu([0xED, 0x56])
    cpu.step()
    assert cpu.im == 1


def test_im2() -> None:
    cpu = make_cpu([0xED, 0x5E])
    cpu.step()
    assert cpu.im == 2


def test_ld_i_a() -> None:
    cpu = make_cpu([0xED, 0x47])  # LD I, A
    cpu.registers.A = 0xAB
    cpu.step()
    assert cpu.registers.I == 0xAB


def test_ld_a_i() -> None:
    cpu = make_cpu([0xED, 0x57])  # LD A, I
    cpu.registers.I = 0x3F
    cpu.iff2 = True
    cpu.step()
    assert cpu.registers.A == 0x3F
    assert cpu.registers.F & F.FLAG_PV


def test_ldd() -> None:
    rom = bytes([0xED, 0xA8] + [0] * 32766)
    ram = bytearray(16384)
    ram[5] = 0x55
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC005
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0002
    cpu.step()
    assert mem.read(0xC010) == 0x55
    assert cpu.registers.HL == 0xC004
    assert cpu.registers.DE == 0xC00F


def test_reti() -> None:
    rom = bytes([0xED, 0x4D] + [0] * 32766)
    ram = bytearray(16384)
    ram[0x3FFE] = 0x00
    ram[0x3FFF] = 0x10
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFE
    cpu.iff2 = True
    cpu.iff1 = False
    cpu.step()
    assert cpu.registers.PC == 0x1000
    assert cpu.iff1 is True
