from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(32768), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_ld_iy_nn() -> None:
    cpu = make_cpu([0xFD, 0x21, 0x78, 0x56])  # LD IY, 0x5678
    cpu.step()
    assert cpu.registers.IY == 0x5678


def test_ld_a_iy_d() -> None:
    rom = bytes([0xFD, 0x7E, 0x01] + [0] * 32765)  # LD A, (IY+1)
    ram = bytearray(32768)
    ram[0x4001] = 0x77  # (0xC000 + 1) = 0xC001: addr - 0x8000 = 0x4001
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
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


# ---------------------------------------------------------------------------
# Undocumented opcodes: IYH / IYL register access (mirror of DD tests)
# ---------------------------------------------------------------------------

def test_ld_iyh_n() -> None:
    cpu = make_cpu([0xFD, 0x26, 0x42])  # LD IYH, 0x42
    cpu.registers.IY = 0x0000
    t = cpu.step()
    assert cpu.registers.IYH == 0x42
    assert cpu.registers.IYL == 0x00
    assert t == 11


def test_ld_iyl_n() -> None:
    cpu = make_cpu([0xFD, 0x2E, 0xAB])  # LD IYL, 0xAB
    cpu.registers.IY = 0x0000
    t = cpu.step()
    assert cpu.registers.IYL == 0xAB
    assert cpu.registers.IYH == 0x00
    assert t == 11


def test_ld_a_iyh() -> None:
    cpu = make_cpu([0xFD, 0x7C])  # LD A, IYH
    cpu.registers.IY = 0xABCD
    t = cpu.step()
    assert cpu.registers.A == 0xAB
    assert t == 8


def test_ld_a_iyl() -> None:
    cpu = make_cpu([0xFD, 0x7D])  # LD A, IYL
    cpu.registers.IY = 0xABCD
    t = cpu.step()
    assert cpu.registers.A == 0xCD
    assert t == 8


def test_ld_d_iyh() -> None:
    cpu = make_cpu([0xFD, 0x54])  # LD D, IYH
    cpu.registers.IY = 0xAB00
    t = cpu.step()
    assert cpu.registers.D == 0xAB
    assert t == 8


def test_ld_iyh_a() -> None:
    cpu = make_cpu([0xFD, 0x67])  # LD IYH, A
    cpu.registers.A = 0x99
    cpu.registers.IY = 0x0000
    t = cpu.step()
    assert cpu.registers.IYH == 0x99
    assert t == 8


def test_ld_iyl_iyh() -> None:
    cpu = make_cpu([0xFD, 0x6C])  # LD IYL, IYH
    cpu.registers.IY = 0x1234
    t = cpu.step()
    assert cpu.registers.IYL == 0x12
    assert cpu.registers.IYH == 0x12
    assert t == 8


def test_add_a_iyh() -> None:
    cpu = make_cpu([0xFD, 0x84])  # ADD A, IYH
    cpu.registers.A = 0x10
    cpu.registers.IY = 0x0500  # IYH=0x05
    t = cpu.step()
    assert cpu.registers.A == 0x15
    assert t == 8


def test_and_iyh_zero() -> None:
    cpu = make_cpu([0xFD, 0xA4])  # AND IYH
    cpu.registers.A = 0xF0
    cpu.registers.IY = 0x0F00  # IYH=0x0F
    t = cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x40  # Z set
    assert t == 8


def test_sub_iyl() -> None:
    cpu = make_cpu([0xFD, 0x95])  # SUB IYL
    cpu.registers.A = 0x10
    cpu.registers.IY = 0x0003  # IYL=0x03
    t = cpu.step()
    assert cpu.registers.A == 0x0D
    assert t == 8
