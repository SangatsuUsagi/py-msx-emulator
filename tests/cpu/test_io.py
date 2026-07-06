from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory


def make_cpu(rom: list[int]) -> tuple[Z80, list[tuple[int, int]], list[int]]:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    writes: list[tuple[int, int]] = []
    reads: list[int] = [0x42]

    def read_port(port: int) -> int:
        return reads.pop(0) if reads else 0xFF

    def write_port(port: int, value: int) -> None:
        writes.append((port, value))

    cpu = Z80(read_byte=mem.read, write_byte=mem.write, read_port=read_port, write_port=write_port)
    return cpu, writes, reads


def test_out_n_a() -> None:
    # OUT (n),A drives a 16-bit port with A in the high byte: (A << 8) | n.
    cpu, writes, _ = make_cpu([0xD3, 0xA1])  # OUT (0xA1), A
    cpu.registers.A = 0x0F
    cpu.step()
    assert (0x0FA1, 0x0F) in writes


def test_in_a_n() -> None:
    cpu, _, reads = make_cpu([0xDB, 0x10])  # IN A, (0x10)
    reads.clear()
    reads.append(0x55)
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.A == 0x55


def test_out_c_r() -> None:
    # OUT (C),r drives a 16-bit port with B in the high byte: (B << 8) | C.
    cpu, writes, _ = make_cpu([0xED, 0x41])  # OUT (C), B
    cpu.registers.B = 0x7F
    cpu.registers.C = 0x98
    cpu.step()
    assert (0x7F98, 0x7F) in writes


def test_in_r_c() -> None:
    cpu, _, reads = make_cpu([0xED, 0x40])  # IN B, (C)
    reads.clear()
    reads.append(0xAB)
    cpu.registers.C = 0x99
    cpu.step()
    assert cpu.registers.B == 0xAB


def _cpu_capturing_read_port(rom: list[int]) -> tuple[Z80, list[int]]:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    ports: list[int] = []

    def read_port(port: int) -> int:
        ports.append(port)
        return 0x42

    cpu = Z80(read_byte=mem.read, write_byte=mem.write, read_port=read_port)
    return cpu, ports


def test_in_r_c_drives_b_in_high_byte() -> None:
    # IN E,(C) drives port (B << 8) | C.
    cpu, ports = _cpu_capturing_read_port([0xED, 0x58])  # IN E, (C)
    cpu.registers.B = 0x12
    cpu.registers.C = 0x34
    cpu.step()
    assert ports == [0x1234]


def test_in_a_n_drives_a_in_high_byte() -> None:
    # IN A,(n) drives port (A << 8) | n, using A before the read overwrites it.
    cpu, ports = _cpu_capturing_read_port([0xDB, 0x10])  # IN A, (0x10)
    cpu.registers.A = 0x77
    cpu.step()
    assert ports == [0x7710]
