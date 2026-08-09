from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory


def make_cpu(rom: list[int], ram: bytearray | None = None) -> Z80:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=ram if ram is not None else bytearray(32768),
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
    ram = bytearray(32768)
    ram[0x4000] = 0x7F  # (0xC000): addr - 0x8000 = 0x4000
    cpu = make_cpu([0x7E], ram)  # LD A, (HL)
    cpu.registers.HL = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x7F


def test_ld_hl_r() -> None:
    cpu = make_cpu([0x77])  # LD (HL), A
    cpu.registers.HL = 0xC000
    cpu.registers.A = 0x99
    cpu.step()
    assert cpu.read_byte(0xC000) == 0x99


def test_ld_hl_n() -> None:
    cpu = make_cpu([0x36, 0xAB])  # LD (HL), 0xAB
    cpu.registers.HL = 0xC000
    cpu.step()
    assert cpu.read_byte(0xC000) == 0xAB


def test_ld_a_bc() -> None:
    ram = bytearray(32768)
    ram[0x4000] = 0x77  # (0xC000): addr - 0x8000 = 0x4000
    cpu = make_cpu([0x0A], ram)  # LD A, (BC)
    cpu.registers.BC = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x77


def test_ld_a_de() -> None:
    ram = bytearray(32768)
    ram[0x4000] = 0x88
    cpu = make_cpu([0x1A], ram)  # LD A, (DE)
    cpu.registers.DE = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x88


def test_ld_bc_a() -> None:
    cpu = make_cpu([0x02])  # LD (BC), A
    cpu.registers.BC = 0xC000
    cpu.registers.A = 0x99
    cpu.step()
    assert cpu.read_byte(0xC000) == 0x99


def test_ld_de_a() -> None:
    cpu = make_cpu([0x12])  # LD (DE), A
    cpu.registers.DE = 0xC000
    cpu.registers.A = 0xAB
    cpu.step()
    assert cpu.read_byte(0xC000) == 0xAB


def test_ld_a_nn() -> None:
    ram = bytearray(32768)
    ram[0x4000] = 0x5A
    cpu = make_cpu([0x3A, 0x00, 0xC0], ram)  # LD A, (0xC000)
    cpu.step()
    assert cpu.registers.A == 0x5A


def test_ld_nn_a() -> None:
    cpu = make_cpu([0x32, 0x00, 0xC0])  # LD (0xC000), A
    cpu.registers.A = 0x77
    cpu.step()
    assert cpu.read_byte(0xC000) == 0x77
