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


def make_cpu_mem(rom: list[int]) -> tuple[Z80, Memory]:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    return Z80(read_byte=mem.read, write_byte=mem.write), mem


def test_ld_ix_nn() -> None:
    cpu = make_cpu([0xDD, 0x21, 0x34, 0x12])  # LD IX, 0x1234
    cpu.step()
    assert cpu.registers.IX == 0x1234


def test_ld_a_ix_d() -> None:
    rom = bytes([0xDD, 0x7E, 0x02] + [0] * 32765)  # LD A, (IX+2)
    ram = bytearray(32768)
    ram[0x4002] = 0x55  # (0xC000 + 2): addr - 0x8000 = 0x4002
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert cpu.registers.A == 0x55


def test_ld_ix_d_n() -> None:
    rom = bytes([0xDD, 0x36, 0x01, 0xAB] + [0] * 32764)  # LD (IX+1), 0xAB
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert mem.read(0xC001) == 0xAB


def test_add_ix_bc() -> None:
    cpu = make_cpu([0xDD, 0x09])  # ADD IX, BC
    cpu.registers.IX = 0x1000
    cpu.registers.BC = 0x0234
    cpu.step()
    assert cpu.registers.IX == 0x1234


def test_inc_ix() -> None:
    cpu = make_cpu([0xDD, 0x23])  # INC IX
    cpu.registers.IX = 0x00FF
    cpu.step()
    assert cpu.registers.IX == 0x0100


def test_push_pop_ix() -> None:
    cpu = make_cpu([0xDD, 0xE5, 0xDD, 0xE1])  # PUSH IX; POP IX
    cpu.registers.IX = 0xBEEF
    cpu.registers.SP = 0xFFFF
    cpu.step()
    cpu.registers.IX = 0
    cpu.step()
    assert cpu.registers.IX == 0xBEEF


def test_jp_ix() -> None:
    cpu = make_cpu([0xDD, 0xE9])  # JP (IX)
    cpu.registers.IX = 0x4000
    cpu.step()
    assert cpu.registers.PC == 0x4000


# ---------------------------------------------------------------------------
# Undocumented opcodes: IXH / IXL register access
# ---------------------------------------------------------------------------

def test_ld_ixh_n() -> None:
    cpu = make_cpu([0xDD, 0x26, 0x42])  # LD IXH, 0x42
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0x42
    assert cpu.registers.IXL == 0x00
    assert t == 11


def test_ld_ixl_n() -> None:
    cpu = make_cpu([0xDD, 0x2E, 0xAB])  # LD IXL, 0xAB
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0xAB
    assert cpu.registers.IXH == 0x00
    assert t == 11


def test_ld_a_ixh() -> None:
    cpu = make_cpu([0xDD, 0x7C])  # LD A, IXH
    cpu.registers.IX = 0x1234
    t = cpu.step()
    assert cpu.registers.A == 0x12
    assert t == 8


def test_ld_a_ixl() -> None:
    cpu = make_cpu([0xDD, 0x7D])  # LD A, IXL
    cpu.registers.IX = 0x1234
    t = cpu.step()
    assert cpu.registers.A == 0x34
    assert t == 8


def test_ld_b_ixh() -> None:
    cpu = make_cpu([0xDD, 0x44])  # LD B, IXH
    cpu.registers.IX = 0xAB00
    t = cpu.step()
    assert cpu.registers.B == 0xAB
    assert t == 8


def test_ld_c_ixh() -> None:
    cpu = make_cpu([0xDD, 0x4C])  # LD C, IXH
    cpu.registers.IX = 0x5500
    t = cpu.step()
    assert cpu.registers.C == 0x55
    assert t == 8


def test_ld_d_ixh() -> None:
    cpu = make_cpu([0xDD, 0x54])  # LD D, IXH
    cpu.registers.IX = 0x7F00
    t = cpu.step()
    assert cpu.registers.D == 0x7F
    assert t == 8


def test_ld_e_ixh() -> None:
    cpu = make_cpu([0xDD, 0x5C])  # LD E, IXH
    cpu.registers.IX = 0x0100
    t = cpu.step()
    assert cpu.registers.E == 0x01
    assert t == 8


def test_ld_b_ixl() -> None:
    cpu = make_cpu([0xDD, 0x45])  # LD B, IXL
    cpu.registers.IX = 0x00CD
    t = cpu.step()
    assert cpu.registers.B == 0xCD
    assert t == 8


def test_ld_c_ixl() -> None:
    cpu = make_cpu([0xDD, 0x4D])  # LD C, IXL
    cpu.registers.IX = 0x0055
    t = cpu.step()
    assert cpu.registers.C == 0x55
    assert t == 8


def test_ld_d_ixl() -> None:
    cpu = make_cpu([0xDD, 0x55])  # LD D, IXL
    cpu.registers.IX = 0x007F
    t = cpu.step()
    assert cpu.registers.D == 0x7F
    assert t == 8


def test_ld_e_ixl() -> None:
    cpu = make_cpu([0xDD, 0x5D])  # LD E, IXL
    cpu.registers.IX = 0x00EE
    t = cpu.step()
    assert cpu.registers.E == 0xEE
    assert t == 8


def test_ld_ixh_a() -> None:
    cpu = make_cpu([0xDD, 0x67])  # LD IXH, A
    cpu.registers.A = 0x55
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0x55
    assert cpu.registers.IXL == 0x00
    assert t == 8


def test_ld_ixh_b() -> None:
    cpu = make_cpu([0xDD, 0x60])  # LD IXH, B
    cpu.registers.BC = 0x1200
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0x12
    assert t == 8


def test_ld_ixh_c() -> None:
    cpu = make_cpu([0xDD, 0x61])  # LD IXH, C
    cpu.registers.BC = 0x00AB
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0xAB
    assert t == 8


def test_ld_ixh_d() -> None:
    cpu = make_cpu([0xDD, 0x62])  # LD IXH, D
    cpu.registers.DE = 0x3400
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0x34
    assert t == 8


def test_ld_ixh_e() -> None:
    cpu = make_cpu([0xDD, 0x63])  # LD IXH, E
    cpu.registers.DE = 0x0056
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXH == 0x56
    assert t == 8


def test_ld_ixh_ixl() -> None:
    cpu = make_cpu([0xDD, 0x65])  # LD IXH, IXL
    cpu.registers.IX = 0x1234
    t = cpu.step()
    assert cpu.registers.IXH == 0x34
    assert cpu.registers.IXL == 0x34
    assert t == 8


def test_ld_ixh_ixh_selfcopy() -> None:
    cpu = make_cpu([0xDD, 0x64])  # LD IXH, IXH (self-copy)
    cpu.registers.IX = 0xAB00
    t = cpu.step()
    assert cpu.registers.IXH == 0xAB
    assert t == 8


def test_ld_ixl_a() -> None:
    cpu = make_cpu([0xDD, 0x6F])  # LD IXL, A
    cpu.registers.A = 0x77
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0x77
    assert cpu.registers.IXH == 0x00
    assert t == 8


def test_ld_ixl_b() -> None:
    cpu = make_cpu([0xDD, 0x68])  # LD IXL, B
    cpu.registers.BC = 0x9900
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0x99
    assert t == 8


def test_ld_ixl_c() -> None:
    cpu = make_cpu([0xDD, 0x69])  # LD IXL, C
    cpu.registers.BC = 0x00CC
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0xCC
    assert t == 8


def test_ld_ixl_d() -> None:
    cpu = make_cpu([0xDD, 0x6A])  # LD IXL, D
    cpu.registers.DE = 0xDD00
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0xDD
    assert t == 8


def test_ld_ixl_e() -> None:
    cpu = make_cpu([0xDD, 0x6B])  # LD IXL, E
    cpu.registers.DE = 0x00EE
    cpu.registers.IX = 0x0000
    t = cpu.step()
    assert cpu.registers.IXL == 0xEE
    assert t == 8


def test_ld_ixl_ixh() -> None:
    cpu = make_cpu([0xDD, 0x6C])  # LD IXL, IXH
    cpu.registers.IX = 0x1234
    t = cpu.step()
    assert cpu.registers.IXL == 0x12
    assert cpu.registers.IXH == 0x12
    assert t == 8


def test_ld_ixl_ixl_selfcopy() -> None:
    cpu = make_cpu([0xDD, 0x6D])  # LD IXL, IXL (self-copy)
    cpu.registers.IX = 0x00AB
    t = cpu.step()
    assert cpu.registers.IXL == 0xAB
    assert t == 8


def test_add_a_ixh() -> None:
    cpu = make_cpu([0xDD, 0x84])  # ADD A, IXH
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0500  # IXH=0x05
    t = cpu.step()
    assert cpu.registers.A == 0x15
    assert not (cpu.registers.F & 0x01)  # C clear
    assert t == 8


def test_add_a_ixh_carry() -> None:
    cpu = make_cpu([0xDD, 0x84])  # ADD A, IXH
    cpu.registers.A = 0xFF
    cpu.registers.IX = 0x0100  # IXH=0x01
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x01  # C set
    assert cpu.registers.F & 0x40  # Z set


def test_add_a_ixl() -> None:
    cpu = make_cpu([0xDD, 0x85])  # ADD A, IXL
    cpu.registers.A = 0x01
    cpu.registers.IX = 0x0002  # IXL=0x02
    cpu.step()
    assert cpu.registers.A == 0x03


def test_adc_a_ixh() -> None:
    cpu = make_cpu([0xDD, 0x8C])  # ADC A, IXH
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0400  # IXH=0x04
    cpu.registers.F = 0x01     # C=1
    cpu.step()
    assert cpu.registers.A == 0x15


def test_adc_a_ixl() -> None:
    cpu = make_cpu([0xDD, 0x8D])  # ADC A, IXL
    cpu.registers.A = 0x00
    cpu.registers.IX = 0x00FF  # IXL=0xFF
    cpu.registers.F = 0x01     # C=1
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x01  # C set


def test_sub_ixh() -> None:
    cpu = make_cpu([0xDD, 0x94])  # SUB IXH
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0300  # IXH=0x03
    t = cpu.step()
    assert cpu.registers.A == 0x0D
    assert not (cpu.registers.F & 0x01)  # C clear
    assert t == 8


def test_sub_ixl() -> None:
    cpu = make_cpu([0xDD, 0x95])  # SUB IXL
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0003  # IXL=0x03
    cpu.step()
    assert cpu.registers.A == 0x0D


def test_sbc_a_ixh() -> None:
    cpu = make_cpu([0xDD, 0x9C])  # SBC A, IXH
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0300  # IXH=0x03
    cpu.registers.F = 0x01     # C=1
    cpu.step()
    assert cpu.registers.A == 0x0C


def test_sbc_a_ixl() -> None:
    cpu = make_cpu([0xDD, 0x9D])  # SBC A, IXL
    cpu.registers.A = 0x05
    cpu.registers.IX = 0x0003  # IXL=0x03
    cpu.registers.F = 0x00     # C=0
    cpu.step()
    assert cpu.registers.A == 0x02


def test_and_ixh_zero() -> None:
    cpu = make_cpu([0xDD, 0xA4])  # AND IXH
    cpu.registers.A = 0xF0
    cpu.registers.IX = 0x0F00  # IXH=0x0F
    t = cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x40  # Z set
    assert t == 8


def test_and_ixl() -> None:
    cpu = make_cpu([0xDD, 0xA5])  # AND IXL
    cpu.registers.A = 0xFF
    cpu.registers.IX = 0x000F  # IXL=0x0F
    cpu.step()
    assert cpu.registers.A == 0x0F


def test_xor_ixh() -> None:
    cpu = make_cpu([0xDD, 0xAC])  # XOR IXH
    cpu.registers.A = 0xFF
    cpu.registers.IX = 0xFF00  # IXH=0xFF
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x40  # Z set


def test_xor_ixl() -> None:
    cpu = make_cpu([0xDD, 0xAD])  # XOR IXL
    cpu.registers.A = 0xAA
    cpu.registers.IX = 0x0055  # IXL=0x55
    cpu.step()
    assert cpu.registers.A == 0xFF


def test_or_ixh() -> None:
    cpu = make_cpu([0xDD, 0xB4])  # OR IXH
    cpu.registers.A = 0x0F
    cpu.registers.IX = 0xF000  # IXH=0xF0
    cpu.step()
    assert cpu.registers.A == 0xFF


def test_or_ixl() -> None:
    cpu = make_cpu([0xDD, 0xB5])  # OR IXL
    cpu.registers.A = 0x00
    cpu.registers.IX = 0x0000
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & 0x40  # Z set


def test_cp_ixh_equal() -> None:
    cpu = make_cpu([0xDD, 0xBC])  # CP IXH
    cpu.registers.A = 0x20
    cpu.registers.IX = 0x2000  # IXH=0x20
    t = cpu.step()
    assert cpu.registers.A == 0x20  # A unchanged
    assert cpu.registers.F & 0x40   # Z set
    assert cpu.registers.F & 0x02   # N set
    assert t == 8


def test_cp_ixl() -> None:
    cpu = make_cpu([0xDD, 0xBD])  # CP IXL
    cpu.registers.A = 0x10
    cpu.registers.IX = 0x0005  # IXL=0x05
    cpu.step()
    assert cpu.registers.A == 0x10  # A unchanged
    assert not (cpu.registers.F & 0x40)  # Z clear (not equal)


# ===========================================================================
# Characterization tests (test-coverage-hardening Phase 0): (IX+d) displacement
# addressing, ALU on (IX+d), INC/DEC (IX+d), remaining ADD IX,rr / DEC IX /
# LD (nn),IX / LD IX,(nn). Values derived from _execute_dd_fd and confirmed by
# running the opcodes; only S/Z/H/PV/N/C and register/memory state are asserted.
# ===========================================================================


# --- LD (IX+d), r / LD r, (IX+d) -------------------------------------------

def test_ld_ix_d_r_stores_register() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x70, 0x03])  # LD (IX+3), B
    cpu.registers.IX = 0xC000
    cpu.registers.B = 0x99
    cpu.step()
    assert mem.read(0xC003) == 0x99


def test_ld_r_ix_d_loads_register() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x4E, 0x04])  # LD C, (IX+4)
    mem.write(0xC004, 0x77)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert cpu.registers.C == 0x77


def test_ld_a_ix_negative_displacement() -> None:
    # IX=0xD000, d=-16 -> 0xCFF0 (still page-3 RAM)
    cpu, mem = make_cpu_mem([0xDD, 0x7E, 0xF0])  # LD A, (IX-16)
    mem.write(0xCFF0, 0x22)
    cpu.registers.IX = 0xD000
    cpu.step()
    assert cpu.registers.A == 0x22


# --- ALU on (IX+d) ----------------------------------------------------------

def _alu_ix_d(op: int, memval: int, a: int, f: int = 0) -> Z80:
    cpu, mem = make_cpu_mem([0xDD, op, 0x02])
    mem.write(0xC002, memval)
    cpu.registers.IX = 0xC000
    cpu.registers.A = a
    cpu.registers.F = f
    cpu.step()
    return cpu


def test_add_a_ix_d() -> None:
    cpu = _alu_ix_d(0x86, memval=0x05, a=0x10)
    assert cpu.registers.A == 0x15
    assert cpu.registers.F == 0x00


def test_adc_a_ix_d_with_carry() -> None:
    cpu = _alu_ix_d(0x8E, memval=0x04, a=0x10, f=F.FLAG_C)
    assert cpu.registers.A == 0x15
    assert cpu.registers.F == 0x00


def test_sub_ix_d() -> None:
    cpu = _alu_ix_d(0x96, memval=0x03, a=0x10)
    assert cpu.registers.A == 0x0D
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_N)


def test_sbc_a_ix_d_with_carry() -> None:
    cpu = _alu_ix_d(0x9E, memval=0x03, a=0x10, f=F.FLAG_C)
    assert cpu.registers.A == 0x0C
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_N)


def test_and_ix_d() -> None:
    cpu = _alu_ix_d(0xA6, memval=0x0F, a=0xFF)
    assert cpu.registers.A == 0x0F
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_PV)


def test_xor_ix_d() -> None:
    cpu = _alu_ix_d(0xAE, memval=0xFF, a=0xAA)
    assert cpu.registers.A == 0x55
    assert cpu.registers.F == F.FLAG_PV


def test_or_ix_d() -> None:
    cpu = _alu_ix_d(0xB6, memval=0xF0, a=0x0F)
    assert cpu.registers.A == 0xFF
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_PV)


def test_cp_ix_d_equal() -> None:
    cpu = _alu_ix_d(0xBE, memval=0x20, a=0x20)
    assert cpu.registers.A == 0x20  # A unchanged
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_N)


# --- INC/DEC (IX+d) ---------------------------------------------------------

def test_inc_ix_d() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x34, 0x02])  # INC (IX+2)
    mem.write(0xC002, 0x7F)
    cpu.registers.IX = 0xC000
    cpu.registers.F = 0
    cpu.step()
    assert mem.read(0xC002) == 0x80
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_H | F.FLAG_PV)  # 0x7F->0x80 overflow


def test_dec_ix_d() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x35, 0x02])  # DEC (IX+2)
    mem.write(0xC002, 0x01)
    cpu.registers.IX = 0xC000
    cpu.registers.F = 0
    cpu.step()
    assert mem.read(0xC002) == 0x00
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_N)


# --- ADD IX,rr (remaining pairs) / DEC IX -----------------------------------

def test_add_ix_de() -> None:
    cpu = make_cpu([0xDD, 0x19])  # ADD IX, DE
    cpu.registers.IX = 0x1000
    cpu.registers.DE = 0x0111
    cpu.step()
    assert cpu.registers.IX == 0x1111


def test_add_ix_ix() -> None:
    cpu = make_cpu([0xDD, 0x29])  # ADD IX, IX
    cpu.registers.IX = 0x1000
    cpu.step()
    assert cpu.registers.IX == 0x2000


def test_add_ix_sp() -> None:
    cpu = make_cpu([0xDD, 0x39])  # ADD IX, SP
    cpu.registers.IX = 0x1000
    cpu.registers.SP = 0x0011
    cpu.step()
    assert cpu.registers.IX == 0x1011


def test_dec_ix() -> None:
    cpu = make_cpu([0xDD, 0x2B])  # DEC IX
    cpu.registers.IX = 0x0100
    cpu.step()
    assert cpu.registers.IX == 0x00FF


# --- LD (nn),IX / LD IX,(nn) ------------------------------------------------

def test_ld_ind_nn_ix() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x22, 0x00, 0xC0])  # LD (0xC000), IX
    cpu.registers.IX = 0xBEEF
    cpu.step()
    assert mem.read(0xC000) == 0xEF
    assert mem.read(0xC001) == 0xBE


def test_ld_ix_ind_nn() -> None:
    cpu, mem = make_cpu_mem([0xDD, 0x2A, 0x00, 0xC0])  # LD IX, (0xC000)
    mem.write(0xC000, 0xCD)
    mem.write(0xC001, 0xAB)
    cpu.step()
    assert cpu.registers.IX == 0xABCD
