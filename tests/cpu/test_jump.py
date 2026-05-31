from msx.memory import Memory
from msx.cpu.z80 import Z80
from msx.cpu import flags as F


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), cartridge=None)
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_jp_nn() -> None:
    cpu = make_cpu([0xC3, 0x34, 0x12])  # JP 0x1234
    cpu.step()
    assert cpu.registers.PC == 0x1234


def test_jp_hl() -> None:
    cpu = make_cpu([0xE9])
    cpu.registers.HL = 0x4000
    cpu.step()
    assert cpu.registers.PC == 0x4000


def test_jp_cc_taken() -> None:
    cpu = make_cpu([0xCA, 0x00, 0x10])  # JP Z, 0x1000
    cpu.registers.F = F.FLAG_Z
    cpu.step()
    assert cpu.registers.PC == 0x1000


def test_jp_cc_not_taken() -> None:
    cpu = make_cpu([0xCA, 0x00, 0x10])
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.PC == 3


def test_jr_forward() -> None:
    cpu = make_cpu([0x18, 0x05])  # JR +5
    cpu.step()
    assert cpu.registers.PC == 7  # 2 + 5


def test_jr_backward() -> None:
    cpu = make_cpu([0x18, 0xFE])  # JR -2 (loops back to self)
    cpu.step()
    assert cpu.registers.PC == 0x0000


def test_djnz_branches() -> None:
    cpu = make_cpu([0x10, 0xFE])  # DJNZ -2
    cpu.registers.B = 2
    cycles = cpu.step()
    assert cpu.registers.B == 1
    assert cpu.registers.PC == 0x0000
    assert cycles == 13


def test_djnz_no_branch() -> None:
    cpu = make_cpu([0x10, 0x05])
    cpu.registers.B = 1
    cycles = cpu.step()
    assert cpu.registers.B == 0
    assert cpu.registers.PC == 2
    assert cycles == 8


def test_call_ret() -> None:
    rom = bytes([0xCD, 0x06, 0x00] + [0x00] * 3 + [0xC9] + [0] * 32761)
    ram = bytearray(16384)
    mem = Memory(rom=rom, ram=ram, cartridge=None)
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFF
    cpu.step()  # CALL 0x0006
    assert cpu.registers.PC == 0x0006
    cpu.step()  # RET
    assert cpu.registers.PC == 0x0003


def test_rst() -> None:
    cpu = make_cpu([0xFF])  # RST 0x38
    cpu.registers.SP = 0xFFFF
    cpu.step()
    assert cpu.registers.PC == 0x0038


def test_jr_cc_nz_taken() -> None:
    cpu = make_cpu([0x20, 0x02])  # JR NZ, +2
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.PC == 4


def test_jr_cc_nz_not_taken() -> None:
    cpu = make_cpu([0x20, 0x02])
    cpu.registers.F = F.FLAG_Z
    cpu.step()
    assert cpu.registers.PC == 2
