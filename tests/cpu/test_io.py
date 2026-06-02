from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.cpu.z80 import Z80


def make_cpu(rom: list[int]) -> tuple[Z80, list[tuple[int, int]], list[int]]:
    mem = Memory(rom=bytes(rom + [0] * (32768 - len(rom))), ram=bytearray(32768), _mapper=FlatMapper(None))
    writes: list[tuple[int, int]] = []
    reads: list[int] = [0x42]

    def read_port(port: int) -> int:
        return reads.pop(0) if reads else 0xFF

    def write_port(port: int, value: int) -> None:
        writes.append((port, value))

    cpu = Z80(read_byte=mem.read, write_byte=mem.write, read_port=read_port, write_port=write_port)
    return cpu, writes, reads


def test_out_n_a() -> None:
    cpu, writes, _ = make_cpu([0xD3, 0xA1])  # OUT (0xA1), A — port is 8-bit (MSX I/O decode)
    cpu.registers.A = 0x0F
    cpu.step()
    assert (0xA1, 0x0F) in writes


def test_in_a_n() -> None:
    cpu, _, reads = make_cpu([0xDB, 0x10])  # IN A, (0x10)
    reads.clear()
    reads.append(0x55)
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.A == 0x55


def test_out_c_r() -> None:
    cpu, writes, _ = make_cpu([0xED, 0x41])  # OUT (C), B
    cpu.registers.B = 0x7F
    cpu.registers.C = 0x98
    cpu.step()
    assert (0x98, 0x7F) in writes


def test_in_r_c() -> None:
    cpu, _, reads = make_cpu([0xED, 0x40])  # IN B, (C)
    reads.clear()
    reads.append(0xAB)
    cpu.registers.C = 0x99
    cpu.step()
    assert cpu.registers.B == 0xAB
