import pytest

from msx.cpu import flags as F
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
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
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


# ===========================================================================
# Characterization tests (test-coverage-hardening Phase 0): _cc condition-code
# helper. Every condition (NZ/Z/NC/C/PO/PE/P/M) is exercised in both the taken
# and not-taken state via conditional JP (branch target / fall-through) and via
# CALL cc / RET cc (cycle counts). Confirmed by running the opcodes.
# ===========================================================================

# (name, JP-cc opcode, flags that make cc TRUE, flags that make cc FALSE)
_CONDS = [
    ("NZ", 0xC2, 0, F.FLAG_Z),
    ("Z", 0xCA, F.FLAG_Z, 0),
    ("NC", 0xD2, 0, F.FLAG_C),
    ("C", 0xDA, F.FLAG_C, 0),
    ("PO", 0xE2, 0, F.FLAG_PV),
    ("PE", 0xEA, F.FLAG_PV, 0),
    ("P", 0xF2, 0, F.FLAG_S),
    ("M", 0xFA, F.FLAG_S, 0),
]


@pytest.mark.parametrize("name,op,f_true,f_false", _CONDS)
def test_jp_cc_taken_branches(name: str, op: int, f_true: int, f_false: int) -> None:
    cpu = make_cpu([op, 0x00, 0x50])  # JP cc, 0x5000
    cpu.registers.F = f_true
    t = cpu.step()
    assert cpu.registers.PC == 0x5000  # branch taken
    assert t == 10


@pytest.mark.parametrize("name,op,f_true,f_false", _CONDS)
def test_jp_cc_not_taken_falls_through(name: str, op: int, f_true: int, f_false: int) -> None:
    cpu = make_cpu([op, 0x00, 0x50])
    cpu.registers.F = f_false
    t = cpu.step()
    assert cpu.registers.PC == 0x0003  # fell through past the 3-byte instruction
    assert t == 10


def test_call_cc_taken_pushes_and_uses_taken_cycles() -> None:
    cpu = make_cpu([0xC4, 0x00, 0x10])  # CALL NZ, 0x1000
    cpu.registers.F = 0  # NZ true
    cpu.registers.SP = 0xFFFF
    t = cpu.step()
    assert cpu.registers.PC == 0x1000
    assert cpu.registers.SP == 0xFFFD  # return address pushed
    assert t == 17


def test_call_cc_not_taken_no_push_uses_short_cycles() -> None:
    cpu = make_cpu([0xC4, 0x00, 0x10])  # CALL NZ, 0x1000
    cpu.registers.F = F.FLAG_Z  # NZ false
    cpu.registers.SP = 0xFFFF
    t = cpu.step()
    assert cpu.registers.PC == 0x0003  # fell through
    assert cpu.registers.SP == 0xFFFF  # nothing pushed
    assert t == 10


def test_ret_cc_taken_pops_and_uses_taken_cycles() -> None:
    rom = bytes([0xC8] + [0] * 32767)  # RET Z
    ram = bytearray(32768)
    ram[0x7FFE] = 0x00  # 0xFFFE
    ram[0x7FFF] = 0x30  # 0xFFFF
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.F = F.FLAG_Z  # Z true
    cpu.registers.SP = 0xFFFE
    t = cpu.step()
    assert cpu.registers.PC == 0x3000
    assert cpu.registers.SP == 0x0000  # popped two bytes (wrapped)
    assert t == 11


def test_ret_cc_not_taken_uses_short_cycles() -> None:
    cpu = make_cpu([0xC8])  # RET Z
    cpu.registers.F = 0  # Z false
    cpu.registers.SP = 0xFFFE
    t = cpu.step()
    assert cpu.registers.PC == 0x0001  # fell through
    assert cpu.registers.SP == 0xFFFE  # nothing popped
    assert t == 5
