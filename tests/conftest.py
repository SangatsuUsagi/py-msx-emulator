import pytest

from msx.memory import Memory
from msx.cpu.z80 import Z80


@pytest.fixture
def bare_memory() -> Memory:
    return Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None)


@pytest.fixture
def bare_cpu(bare_memory: Memory) -> Z80:
    return Z80(
        read_byte=bare_memory.read,
        write_byte=bare_memory.write,
    )
