from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), cartridge=None)
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ld_iy_nn() -> None:
    cpu = make_cpu([0xFD, 0x21, 0x78, 0x56])  # LD IY, 0x5678
    cpu.step()
    assert cpu.registers.IY == 0x5678


def test_ld_a_iy_d() -> None:
    rom = bytes([0xFD, 0x7E, 0x01] + [0] * 32765)  # LD A, (IY+1)
    ram = bytearray(16384)
    ram[1] = 0x77  # (0xC000 + 1) = 0xC001 maps to ram[1]
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IY = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x77


def test_add_iy_de() -> None:
    cpu = make_cpu([0xFD, 0x19])  # ADD IY, DE
    cpu.registers.IY = 0x1000
    cpu.registers.DE = 0x0500
    cpu.step()
    assert cpu.registers.IY == 0x1500


def test_inc_iy() -> None:
    cpu = make_cpu([0xFD, 0x23])
    cpu.registers.IY = 0xFFFE
    cpu.step()
    assert cpu.registers.IY == 0xFFFF


def test_ld_sp_iy() -> None:
    cpu = make_cpu([0xFD, 0xF9])  # LD SP, IY
    cpu.registers.IY = 0xD000
    cpu.step()
    assert cpu.registers.SP == 0xD000
