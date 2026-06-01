from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFF
    return cpu


def test_push_pop_bc() -> None:
    cpu = make_cpu([0xC5, 0xC1])  # PUSH BC; POP BC
    cpu.registers.BC = 0x1234
    cpu.step()
    sp_after_push = cpu.registers.SP
    cpu.registers.BC = 0x0000
    cpu.step()
    assert cpu.registers.BC == 0x1234
    assert cpu.registers.SP == sp_after_push + 2


def test_push_pop_af() -> None:
    cpu = make_cpu([0xF5, 0xF1])  # PUSH AF; POP AF
    cpu.registers.AF = 0xABCD
    cpu.step()
    cpu.registers.AF = 0
    cpu.step()
    assert cpu.registers.AF == 0xABCD


def test_ex_af() -> None:
    cpu = make_cpu([0x08])  # EX AF, AF'
    cpu.registers.AF = 0x1234
    cpu.registers.AF_ = 0x5678
    cpu.step()
    assert cpu.registers.AF == 0x5678
    assert cpu.registers.AF_ == 0x1234


def test_exx() -> None:
    cpu = make_cpu([0xD9])  # EXX
    cpu.registers.BC = 0x0001
    cpu.registers.DE = 0x0002
    cpu.registers.HL = 0x0003
    cpu.registers.BC_ = 0x000A
    cpu.registers.DE_ = 0x000B
    cpu.registers.HL_ = 0x000C
    cpu.step()
    assert cpu.registers.BC == 0x000A
    assert cpu.registers.DE == 0x000B
    assert cpu.registers.HL == 0x000C


def test_ex_de_hl() -> None:
    cpu = make_cpu([0xEB])  # EX DE, HL
    cpu.registers.DE = 0xAAAA
    cpu.registers.HL = 0xBBBB
    cpu.step()
    assert cpu.registers.DE == 0xBBBB
    assert cpu.registers.HL == 0xAAAA


def test_ex_sp_hl() -> None:
    rom = bytes([0xE3] + [0] * 32767)
    ram = bytearray(16384)
    ram[0x3FFE] = 0x34
    ram[0x3FFF] = 0x12
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFE
    cpu.registers.HL = 0xABCD
    cpu.step()
    assert cpu.registers.HL == 0x1234
    assert mem.read(0xFFFE) == 0xCD
    assert mem.read(0xFFFF) == 0xAB
