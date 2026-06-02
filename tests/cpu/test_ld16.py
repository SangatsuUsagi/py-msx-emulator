from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(32768), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ld_bc_nn() -> None:
    cpu = make_cpu([0x01, 0x34, 0x12])  # LD BC, 0x1234
    cycles = cpu.step()
    assert cpu.registers.BC == 0x1234
    assert cycles == 10


def test_ld_de_nn() -> None:
    cpu = make_cpu([0x11, 0xCD, 0xAB])
    cpu.step()
    assert cpu.registers.DE == 0xABCD


def test_ld_hl_nn() -> None:
    cpu = make_cpu([0x21, 0xFF, 0x00])
    cpu.step()
    assert cpu.registers.HL == 0x00FF


def test_ld_sp_nn() -> None:
    cpu = make_cpu([0x31, 0x00, 0xFF])
    cpu.step()
    assert cpu.registers.SP == 0xFF00


def test_ld_sp_hl() -> None:
    cpu = make_cpu([0xF9])  # LD SP, HL
    cpu.registers.HL = 0x1234
    cpu.step()
    assert cpu.registers.SP == 0x1234


def test_ld_nn_hl() -> None:
    rom = bytes([0x22, 0x00, 0xC0] + [0] * 32765)  # LD (0xC000), HL
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xBEEF
    cpu.step()
    assert mem.read(0xC000) == 0xEF
    assert mem.read(0xC001) == 0xBE


def test_ld_hl_nn_indirect() -> None:
    ram = bytearray(32768)
    ram[0x4000] = 0x34  # 0xC000: addr - 0x8000 = 0x4000
    ram[0x4001] = 0x12
    rom = bytes([0x2A, 0x00, 0xC0] + [0] * 32765)  # LD HL, (0xC000)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.step()
    assert cpu.registers.HL == 0x1234
