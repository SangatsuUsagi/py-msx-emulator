from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80
from msx.cpu import flags as F


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(32768), _mapper=FlatMapper(None))
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
