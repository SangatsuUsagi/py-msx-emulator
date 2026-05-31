from tests.conftest import *
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), cartridge=None)
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_nop() -> None:
    cpu = make_cpu([0x00])
    cycles = cpu.step()
    assert cycles == 4
    assert cpu.registers.PC == 1


def test_ld_b_c() -> None:
    cpu = make_cpu([0x41])  # LD B, C
    cpu.registers.C = 0x42
    cpu.step()
    assert cpu.registers.B == 0x42


def test_ld_a_h() -> None:
    cpu = make_cpu([0x7C])  # LD A, H
    cpu.registers.H = 0xAB
    cpu.step()
    assert cpu.registers.A == 0xAB


def test_ld_r_n() -> None:
    cpu = make_cpu([0x06, 0x55])  # LD B, 0x55
    cpu.step()
    assert cpu.registers.B == 0x55
    assert cpu.registers.PC == 2


def test_ld_a_n() -> None:
    cpu = make_cpu([0x3E, 0xFF])  # LD A, 0xFF
    cpu.step()
    assert cpu.registers.A == 0xFF


def test_ld_a_hl() -> None:
    rom = bytes(32768)
    ram = bytearray(16384)
    ram[0] = 0x7F  # (0xC000)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    # Place opcode 0x7E in ROM
    mem2 = Memory(rom=bytes([0x7E] + [0]*32767), ram=ram, cartridge=None)
    cpu2 = Z80(read_byte=mem2.read, write_byte=mem2.write)
    cpu2.registers.HL = 0xC000
    cpu2.step()
    assert cpu2.registers.A == 0x7F


def test_ld_hl_r() -> None:
    rom = bytes([0x77] + [0]*32767)  # LD (HL), A
    ram = bytearray(16384)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.A = 0x99
    cpu.step()
    assert mem.read(0xC000) == 0x99


def test_ld_hl_n() -> None:
    rom = bytes([0x36, 0xAB] + [0]*32766)  # LD (HL), 0xAB
    ram = bytearray(16384)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.step()
    assert mem.read(0xC000) == 0xAB
