from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), cartridge=None)
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ld_ix_nn() -> None:
    cpu = make_cpu([0xDD, 0x21, 0x34, 0x12])  # LD IX, 0x1234
    cpu.step()
    assert cpu.registers.IX == 0x1234


def test_ld_a_ix_d() -> None:
    rom = bytes([0xDD, 0x7E, 0x02] + [0] * 32765)  # LD A, (IX+2)
    ram = bytearray(16384)
    ram[2] = 0x55  # (0xC000 + 2)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x55


def test_ld_ix_d_n() -> None:
    rom = bytes([0xDD, 0x36, 0x01, 0xAB] + [0] * 32764)  # LD (IX+1), 0xAB
    ram = bytearray(16384)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert mem.read(0xC001) == 0xAB


def test_add_ix_bc() -> None:
    cpu = make_cpu([0xDD, 0x09])  # ADD IX, BC
    cpu.registers.IX = 0x1000
    cpu.registers.BC = 0x0234
    cpu.step()
    assert cpu.registers.IX == 0x1234


def test_inc_ix() -> None:
    cpu = make_cpu([0xDD, 0x23])  # INC IX
    cpu.registers.IX = 0x00FF
    cpu.step()
    assert cpu.registers.IX == 0x0100


def test_push_pop_ix() -> None:
    cpu = make_cpu([0xDD, 0xE5, 0xDD, 0xE1])  # PUSH IX; POP IX
    cpu.registers.IX = 0xBEEF
    cpu.registers.SP = 0xFFFF
    cpu.step()
    cpu.registers.IX = 0
    cpu.step()
    assert cpu.registers.IX == 0xBEEF


def test_jp_ix() -> None:
    cpu = make_cpu([0xDD, 0xE9])  # JP (IX)
    cpu.registers.IX = 0x4000
    cpu.step()
    assert cpu.registers.PC == 0x4000
