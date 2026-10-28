"""Undocumented F bits 5 (Y) and 3 (X): the per-family source value.

Real-hardware basis: extras/HB-10/YXFLAG.BAS on a SONY HB-F1XD (2026-10-07).
"""
import pytest

from msx.cpu import flags as F
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory

_YX = F.FLAG_Y | F.FLAG_X


def make_cpu(code: list[int]) -> Z80:
    mem = Memory(
        rom=bytes(code + [0] * (32768 - len(code))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    return Z80(read_byte=mem.read, write_byte=mem.write)


def _run(cpu: Z80, steps: int) -> None:
    for _ in range(steps):
        cpu.step()


def test_yxflag_bas_sequence() -> None:
    # The YXFLAG.BAS routine without its PUSH AF/POP BC/LD (nn),A bookkeeping.
    steps = [
        ([0xAF, 0xC6, 0x28], 2, 0x28),        # XOR A; ADD A,0x28
        ([0xAF, 0xFE, 0x28], 2, 0xBB),        # XOR A; CP 0x28
        ([0x3E, 0x28, 0xE6, 0xFF], 2, 0x3C),  # LD A,0x28; AND 0xFF
        ([0xAF, 0x3E, 0x28, 0x37], 3, 0x6D),  # XOR A; LD A,0x28; SCF
    ]
    for code, n, expected in steps:
        cpu = make_cpu(code)
        _run(cpu, n)
        assert cpu.registers.F == expected, code
    # BIT 0,A and RLCA follow the SCF, keeping its C and S/Z/PV as on hardware.
    cpu = make_cpu([0xAF, 0x3E, 0x28, 0x37, 0x3E, 0x28, 0xCB, 0x47, 0x3E, 0x14, 0x07])
    _run(cpu, 5)
    assert cpu.registers.F == 0x7D
    _run(cpu, 2)
    assert cpu.registers.F == 0x6C


def test_cp_takes_yx_from_the_operand() -> None:
    cpu = make_cpu([0xFE, 0x28])  # CP 0x28, A=0 -> result 0xD8 has bit 5 clear
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_add16_takes_yx_from_the_high_byte() -> None:
    cpu = make_cpu([0x19])  # ADD HL,DE
    cpu.registers.HL = 0x2000
    cpu.registers.DE = 0x0800
    cpu.registers.F = 0  # the old Y/X must not survive
    cpu.step()
    assert cpu.registers.HL == 0x2800
    assert cpu.registers.F & _YX == _YX


@pytest.mark.parametrize(("code", "a", "expected_yx"), [
    ([0x3C], 0x27, _YX),          # INC A -> 0x28
    ([0x3D], 0x29, _YX),          # DEC A -> 0x28
    ([0x2F], 0xD7, _YX),          # CPL -> 0x28
    ([0x3F], 0x28, _YX),          # CCF: from A
    ([0xED, 0x44], 0xD8, _YX),    # NEG -> 0x28
    ([0xCB, 0x07], 0x14, _YX),    # RLC A -> 0x28
])
def test_yx_from_result_or_a(code: list[int], a: int, expected_yx: int) -> None:
    cpu = make_cpu(code)
    cpu.registers.A = a
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.F & _YX == expected_yx


def test_bit_n_r_takes_yx_from_the_register() -> None:
    cpu = make_cpu([0xCB, 0x40])  # BIT 0,B
    cpu.registers.B = 0x28
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_bit_n_ix_d_takes_yx_from_the_address_high_byte() -> None:
    cpu = make_cpu([0xDD, 0xCB, 0x05, 0x46])  # BIT 0,(IX+5)
    cpu.registers.IX = 0xA800  # IX+5 = 0xA805: high byte 0xA8 has bits 5 and 3
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_bit_n_hl_takes_yx_from_memptr() -> None:
    cpu = make_cpu([0x3A, 0xFF, 0x27, 0xCB, 0x46])  # LD A,(0x27FF); BIT 0,(HL)
    cpu.registers.HL = 0xC000
    cpu.write_byte(0xC000, 0xFF)
    _run(cpu, 2)
    assert cpu.registers.WZ == 0x2800
    assert cpu.registers.F & _YX == _YX


def test_bit_n_hl_clears_yx() -> None:
    # MEMPTR = 0x0100: its high byte has bits 5 and 3 clear.
    cpu = make_cpu([0x3A, 0xFF, 0x00, 0xCB, 0x46])  # LD A,(0x00FF); BIT 0,(HL)
    cpu.registers.HL = 0xC000
    cpu.write_byte(0xC000, 0xFF)
    cpu.step()
    cpu.registers.F = _YX
    cpu.step()
    assert cpu.registers.F & _YX == 0


def test_ldi_takes_yx_from_a_plus_byte() -> None:
    cpu = make_cpu([0xED, 0xA0])  # LDI
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xD000
    cpu.registers.BC = 0x0002
    cpu.registers.A = 0x00
    cpu.registers.F = 0
    cpu.write_byte(0xC000, 0x0A)  # A + byte = 0x0A: bit 1 -> Y, bit 3 -> X
    cpu.step()
    assert cpu.registers.F & _YX == _YX


def test_cpi_takes_yx_from_a_minus_byte_minus_h() -> None:
    cpu = make_cpu([0xED, 0xA1])  # CPI
    cpu.registers.HL = 0xC000
    cpu.registers.BC = 0x0002
    cpu.registers.A = 0x10
    cpu.write_byte(0xC000, 0x01)  # 0x10 - 0x01 = 0x0F with H set -> n = 0x0E
    cpu.step()
    assert cpu.registers.F & F.FLAG_H
    assert cpu.registers.F & _YX == _YX


# --- Q latch and SCF/CCF (Zilog NMOS rule) --------------------------------


def test_q_follows_a_flag_writing_instruction() -> None:
    # Spec "Q follows a flag-writing instruction".
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x00])  # XOR A; CP 0x28; NOP
    _run(cpu, 2)
    assert cpu.registers.F == 0xBB
    assert cpu.q_next == 0xBB
    cpu.step()  # the NOP sees CP's Q
    assert cpu.q == 0xBB


def test_a_non_flag_instruction_clears_q() -> None:
    # Spec "A non-flag instruction clears Q".
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x00, 0x00])  # XOR A; CP 0x28; NOP; NOP
    _run(cpu, 4)
    assert cpu.q == 0x00
    assert cpu.q_next == 0x00


def test_interrupt_acceptance_clears_q() -> None:
    # Spec "Interrupt acceptance clears Q".
    cpu = make_cpu([0xAF, 0xFE, 0x28])  # XOR A; CP 0x28
    cpu.registers.SP = 0xF000
    _run(cpu, 2)
    cpu.im = 1
    cpu.iff1 = True
    cpu.int_pending = True
    cpu.step()  # accept: the handler's first instruction must see Q = 0
    assert cpu.q_next == 0x00


def test_scf_right_after_a_flag_writing_instruction_takes_yx_from_a() -> None:
    # Spec "SCF right after a flag-writing instruction takes Y and X from A".
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x37])  # XOR A; CP 0x28; SCF
    _run(cpu, 3)
    assert cpu.registers.F & _YX == 0


def test_scf_after_a_non_flag_instruction_ors_f_into_a() -> None:
    # Spec "SCF after a non-flag instruction ORs F into A".
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x00, 0x37])  # XOR A; CP 0x28; NOP; SCF
    _run(cpu, 4)
    assert cpu.registers.F & _YX == _YX


def test_ccf_follows_the_same_rule() -> None:
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x3F])  # XOR A; CP 0x28; CCF
    _run(cpu, 3)
    assert cpu.registers.F & _YX == 0
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0x00, 0x3F])  # ... NOP; CCF
    _run(cpu, 4)
    assert cpu.registers.F & _YX == _YX


def test_a_dd_prefix_before_ccf_clears_q() -> None:
    # Spec "A DD prefix before CCF clears Q".
    cpu = make_cpu([0xAF, 0xFE, 0x28, 0xDD, 0x3F])  # XOR A; CP 0x28; DD CCF
    _run(cpu, 3)
    assert cpu.registers.F & _YX == _YX


def test_pop_af_does_not_latch_q() -> None:
    # Design D2: POP AF loads F without the ALU (real-hardware check pending,
    # extras/HB-10/MEMPTR.BAS).
    cpu = make_cpu([0xF1, 0x37])  # POP AF; SCF
    cpu.registers.SP = 0xF000
    cpu.write_byte(0xF000, 0x28)  # F
    cpu.write_byte(0xF001, 0x00)  # A
    _run(cpu, 2)
    assert cpu.registers.F & _YX == _YX


# One instruction per flag-writing family: each must latch its new F as Q.
_FLAG_WRITERS = [
    ("ADD A,B", [0x80], {}),
    ("ADC A,n", [0xCE, 0x7F], {}),
    ("SUB n", [0xD6, 0x01], {}),
    ("SBC A,B", [0x98], {}),
    ("AND n", [0xE6, 0xF0], {}),
    ("XOR n", [0xEE, 0x03], {}),
    ("OR B", [0xB0], {}),
    ("CP n", [0xFE, 0x01], {}),
    ("INC B", [0x04], {}),
    ("DEC (HL)", [0x35], {"HL": 0xC000}),
    ("RLCA", [0x07], {}),
    ("RRCA", [0x0F], {}),
    ("RLA", [0x17], {}),
    ("RRA", [0x1F], {}),
    ("DAA", [0x27], {}),
    ("CPL", [0x2F], {}),
    ("SCF", [0x37], {}),
    ("CCF", [0x3F], {}),
    ("ADD HL,BC", [0x09], {}),
    ("RLC B", [0xCB, 0x00], {}),
    ("BIT 0,B", [0xCB, 0x40], {}),
    ("BIT 0,(HL)", [0xCB, 0x46], {"HL": 0xC000}),
    ("ADD A,(IX+0)", [0xDD, 0x86, 0x00], {"IX": 0x0000}),  # adds the 0xDD opcode byte
    ("INC (IX+0)", [0xDD, 0x34, 0x00], {"IX": 0xC000}),
    ("INC IXH", [0xDD, 0x24], {}),
    ("RLC (IX+0)", [0xDD, 0xCB, 0x00, 0x06], {"IX": 0xC000}),
    ("BIT 0,(IX+0)", [0xDD, 0xCB, 0x00, 0x46], {"IX": 0xC000}),
    ("NEG", [0xED, 0x44], {}),
    ("ADC HL,BC", [0xED, 0x4A], {}),
    ("SBC HL,BC", [0xED, 0x42], {}),
    ("LD A,I", [0xED, 0x57], {}),
    ("IN B,(C)", [0xED, 0x40], {}),
    ("IN F,(C)", [0xED, 0x70], {}),
    ("RLD", [0xED, 0x6F], {"HL": 0xC000}),
    ("RRD", [0xED, 0x67], {"HL": 0xC000}),
    ("LDI", [0xED, 0xA0], {"HL": 0xC000, "DE": 0xD000}),
    ("CPI", [0xED, 0xA1], {"HL": 0xC000}),
    ("INI", [0xED, 0xA2], {"HL": 0xC000}),
    ("OUTI", [0xED, 0xA3], {"HL": 0xC000}),
    ("LDIR repeating", [0xED, 0xB0], {"HL": 0xC000, "DE": 0xD000, "BC": 2}),
    ("CPIR repeating", [0xED, 0xB1], {"HL": 0xC000, "BC": 2}),
]


@pytest.mark.parametrize("name,code,regs", _FLAG_WRITERS, ids=[w[0] for w in _FLAG_WRITERS])
def test_every_flag_writing_family_latches_q(
    name: str, code: list[int], regs: dict[str, int]
) -> None:
    cpu = make_cpu(code)
    cpu.registers.A = 0x12
    for reg, value in regs.items():
        setattr(cpu.registers, reg, value)
    cpu.step()
    assert cpu.registers.F != 0, "pick inputs that give a non-zero F"
    assert cpu.q_next == cpu.registers.F


@pytest.mark.parametrize(
    "name,code",
    [("NOP", [0x00]), ("LD B,n", [0x06, 0x28]), ("POP AF", [0xF1]), ("EX AF,AF'", [0x08]),
     ("JP nn", [0xC3, 0x00, 0x00]), ("RES 0,B", [0xCB, 0x80]),
     ("LD A,(IX+0)", [0xDD, 0x7E, 0x00])],
)
def test_non_flag_instructions_leave_q_clear(name: str, code: list[int]) -> None:
    cpu = make_cpu(code)
    cpu.registers.SP = 0xF000
    cpu.q_next = 0xFF  # as if the previous instruction had latched flags
    cpu.step()
    assert cpu.q_next == 0x00
