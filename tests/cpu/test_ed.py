from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80
from msx.cpu import flags as F


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(32768), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ldi() -> None:
    rom = bytes([0xED, 0xA0] + [0] * 32766)
    ram = bytearray(32768)
    ram[0x4000] = 0xAB  # source at 0xC000: addr - 0x8000 = 0x4000
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
    ram = bytearray(32768)
    ram[0x4000] = 0x01  # 0xC000: addr - 0x8000
    ram[0x4001] = 0x02
    ram[0x4002] = 0x03
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
    ram = bytearray(32768)
    ram[0x4005] = 0x55  # 0xC005: addr - 0x8000
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
    ram = bytearray(32768)
    ram[0x7FFE] = 0x00  # 0xFFFE: addr - 0x8000
    ram[0x7FFF] = 0x10
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFE
    cpu.iff2 = True
    cpu.iff1 = False
    cpu.step()
    assert cpu.registers.PC == 0x1000
    assert cpu.iff1 is True


def test_ini_sets_carry_half_and_n() -> None:
    cpu = make_cpu([0xED, 0xA2])  # INI
    cpu.read_port = lambda port: 0xFF
    cpu.registers.B = 0x10
    cpu.registers.C = 0x01
    cpu.registers.HL = 0xC000
    cpu.step()
    assert cpu.registers.B == 0x0F
    assert cpu.registers.HL == 0xC001
    f = cpu.registers.F
    # k = value + ((C + 1) & 0xFF) = 0xFF + 0x02 = 0x101 > 255
    assert f & F.FLAG_C
    assert f & F.FLAG_H
    assert f & F.FLAG_N  # N = bit 7 of the transferred value (0xFF)


def test_outi_decrements_b_sets_zero_and_drives_port() -> None:
    writes: list[tuple[int, int]] = []
    cpu = make_cpu([0xED, 0xA3])  # OUTI
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.B = 0x01
    cpu.registers.C = 0x99
    cpu.registers.HL = 0xC000  # (HL) reads back 0x00 from zeroed RAM
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F & F.FLAG_Z  # B decremented to zero
    assert writes == [(0x0199, 0x00)]  # port = (B_before << 8) | C
