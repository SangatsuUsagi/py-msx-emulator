from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(16384), _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFF
    return cpu


def test_int_mode1() -> None:
    cpu = make_cpu([0x00])  # NOP at 0x0000
    cpu.im = 1
    cpu.iff1 = True
    cpu.int_pending = True
    saved_pc = cpu.registers.PC
    cycles = cpu.step()
    assert cpu.registers.PC == 0x0038
    assert cycles == 13
    assert cpu.iff1 is False
    # previous PC should be on the stack
    sp = cpu.registers.SP
    lo = cpu.read_byte(sp)
    hi = cpu.read_byte((sp + 1) & 0xFFFF)
    assert (hi << 8) | lo == saved_pc


def test_int_mode1_ignored_when_di() -> None:
    cpu = make_cpu([0x00])
    cpu.im = 1
    cpu.iff1 = False
    cpu.int_pending = True
    cpu.step()
    assert cpu.registers.PC == 1  # NOP executed normally
    assert cpu.int_pending is True  # still pending


def test_int_mode2() -> None:
    rom = [0x00] * 32768
    # vector table at 0x3E00 (I=0x1F, so addr = 0x1FFF; read two bytes there)
    # Use I=0x00, addr=0x00FF, write jump target there
    rom[0x00FF] = 0x00
    rom[0x0100] = 0x20  # target PC = 0x2000
    cpu = make_cpu(rom)
    cpu.im = 2
    cpu.iff1 = True
    cpu.int_pending = True
    cpu.registers.I = 0x00
    cpu.step()
    assert cpu.registers.PC == 0x2000


def test_nmi_fires_when_di() -> None:
    cpu = make_cpu([0x00])
    cpu.iff1 = False
    cpu.nmi_pending = True
    saved_pc = cpu.registers.PC
    cycles = cpu.step()
    assert cpu.registers.PC == 0x0066
    assert cpu.iff1 is False
    assert cycles == 11


def test_nmi_fires_when_ei() -> None:
    cpu = make_cpu([0x00])
    cpu.iff1 = True
    cpu.nmi_pending = True
    cpu.step()
    assert cpu.registers.PC == 0x0066
    assert cpu.iff1 is False


def test_halt_nop_loop() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.step()
    assert cpu.halted is True
    cycles = cpu.step()
    assert cpu.halted is True
    assert cycles == 4
    assert cpu.registers.PC == 1  # PC stays after HALT
