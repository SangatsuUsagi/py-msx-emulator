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
    ram = bytearray(32768)
    ram[0x4000] = 0x7F  # (0xC000): addr - 0x8000 = 0x4000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    # Place opcode 0x7E in ROM
    mem2 = Memory(rom=bytes([0x7E] + [0]*32767), ram=ram, _mapper=FlatMapper(None))
    cpu2 = Z80(read_byte=mem2.read, write_byte=mem2.write)
    cpu2.registers.HL = 0xC000
    cpu2.step()
    assert cpu2.registers.A == 0x7F


def test_ld_hl_r() -> None:
    rom = bytes([0x77] + [0]*32767)  # LD (HL), A
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.A = 0x99
    cpu.step()
    assert mem.read(0xC000) == 0x99


def test_ld_hl_n() -> None:
    rom = bytes([0x36, 0xAB] + [0]*32766)  # LD (HL), 0xAB
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.step()
    assert mem.read(0xC000) == 0xAB


def test_ld_a_bc() -> None:
    rom = bytes([0x0A] + [0] * 32767)  # LD A, (BC)
    ram = bytearray(32768)
    ram[0x4000] = 0x77  # (0xC000): addr - 0x8000 = 0x4000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.BC = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x77


def test_ld_a_de() -> None:
    rom = bytes([0x1A] + [0] * 32767)  # LD A, (DE)
    ram = bytearray(32768)
    ram[0x4000] = 0x88
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.DE = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x88


def test_ld_bc_a() -> None:
    rom = bytes([0x02] + [0] * 32767)  # LD (BC), A
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.BC = 0xC000
    cpu.registers.A = 0x99
    cpu.step()
    assert mem.read(0xC000) == 0x99


def test_ld_de_a() -> None:
    rom = bytes([0x12] + [0] * 32767)  # LD (DE), A
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.DE = 0xC000
    cpu.registers.A = 0xAB
    cpu.step()
    assert mem.read(0xC000) == 0xAB


def test_ld_a_nn() -> None:
    rom = bytes([0x3A, 0x00, 0xC0] + [0] * 32765)  # LD A, (0xC000)
    ram = bytearray(32768)
    ram[0x4000] = 0x5A
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.step()
    assert cpu.registers.A == 0x5A


def test_ld_nn_a() -> None:
    rom = bytes([0x32, 0x00, 0xC0] + [0] * 32765)  # LD (0xC000), A
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.A = 0x77
    cpu.step()
    assert mem.read(0xC000) == 0x77
