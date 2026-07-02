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


def test_ldi() -> None:
    rom = bytes([0xED, 0xA0] + [0] * 32766)
    ram = bytearray(32768)
    ram[0x4000] = 0xAB  # source at 0xC000: addr - 0x8000 = 0x4000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0003
    cpu.step()
    assert mem.read(0xC010) == 0xAB
    assert cpu.registers.HL == 0xC001
    assert cpu.registers.DE == 0xC011
    assert cpu.registers.BC == 0x0002
    assert cpu.registers.F & F.FLAG_PV


def test_ldir() -> None:
    rom = bytes([0xED, 0xB0] + [0] * 32766)
    ram = bytearray(32768)
    ram[0x4000] = 0x01  # 0xC000: addr - 0x8000
    ram[0x4001] = 0x02
    ram[0x4002] = 0x03
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0003
    # LDIR repeats until BC == 0; PC rewinds each time
    for _ in range(3):
        cpu.step()
    assert mem.read(0xC010) == 0x01
    assert mem.read(0xC011) == 0x02
    assert mem.read(0xC012) == 0x03
    assert cpu.registers.BC == 0x0000
    assert not (cpu.registers.F & F.FLAG_PV)


def test_neg() -> None:
    cpu = make_cpu([0xED, 0x44])  # NEG
    cpu.registers.A = 0x01
    cpu.step()
    assert cpu.registers.A == 0xFF
    assert cpu.registers.F & F.FLAG_C
    assert cpu.registers.F & F.FLAG_N


def test_im1() -> None:
    cpu = make_cpu([0xED, 0x56])
    cpu.step()
    assert cpu.im == 1


def test_im2() -> None:
    cpu = make_cpu([0xED, 0x5E])
    cpu.step()
    assert cpu.im == 2


def test_ld_i_a() -> None:
    cpu = make_cpu([0xED, 0x47])  # LD I, A
    cpu.registers.A = 0xAB
    cpu.step()
    assert cpu.registers.I == 0xAB


def test_ld_a_i() -> None:
    cpu = make_cpu([0xED, 0x57])  # LD A, I
    cpu.registers.I = 0x3F
    cpu.iff2 = True
    cpu.step()
    assert cpu.registers.A == 0x3F
    assert cpu.registers.F & F.FLAG_PV


def test_ldd() -> None:
    rom = bytes([0xED, 0xA8] + [0] * 32766)
    ram = bytearray(32768)
    ram[0x4005] = 0x55  # 0xC005: addr - 0x8000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC005
    cpu.registers.DE = 0xC010
    cpu.registers.BC = 0x0002
    cpu.step()
    assert mem.read(0xC010) == 0x55
    assert cpu.registers.HL == 0xC004
    assert cpu.registers.DE == 0xC00F


def test_reti() -> None:
    rom = bytes([0xED, 0x4D] + [0] * 32766)
    ram = bytearray(32768)
    ram[0x7FFE] = 0x00  # 0xFFFE: addr - 0x8000
    ram[0x7FFF] = 0x10
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFE
    cpu.iff2 = True
    cpu.iff1 = False
    cpu.step()
    assert cpu.registers.PC == 0x1000
    assert cpu.iff1 is True


def test_ini_sets_carry_half_and_n() -> None:
    cpu = make_cpu([0xED, 0xA2])  # INI
    cpu.read_port = lambda port: 0xFF
    cpu.registers.B = 0x10
    cpu.registers.C = 0x01
    cpu.registers.HL = 0xC000
    cpu.step()
    assert cpu.registers.B == 0x0F
    assert cpu.registers.HL == 0xC001
    f = cpu.registers.F
    # k = value + ((C + 1) & 0xFF) = 0xFF + 0x02 = 0x101 > 255
    assert f & F.FLAG_C
    assert f & F.FLAG_H
    assert f & F.FLAG_N  # N = bit 7 of the transferred value (0xFF)


def test_outi_decrements_b_sets_zero_and_drives_port() -> None:
    writes: list[tuple[int, int]] = []
    cpu = make_cpu([0xED, 0xA3])  # OUTI
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.B = 0x01
    cpu.registers.C = 0x99
    cpu.registers.HL = 0xC000  # (HL) reads back 0x00 from zeroed RAM
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F & F.FLAG_Z  # B decremented to zero
    assert writes == [(0x0199, 0x00)]  # port = (B_before << 8) | C


# ===========================================================================
# Characterization tests (test-coverage-hardening Phase 0).
# Expected register/flag values were derived by reading _execute_ed / _adc16 /
# _sbc16 and confirmed by running the opcodes through the CPU. Per house style
# and this emulator's deliberate omission of the undocumented X/Y (bit3/bit5)
# flags, only S/Z/H/PV/N/C and register values are asserted.
# ===========================================================================


# --- 1.1 block instructions -------------------------------------------------

def test_lddr_copies_backward_and_clears_pv() -> None:
    rom = bytes([0xED, 0xB8] + [0] * 32766)  # LDDR
    ram = bytearray(32768)
    ram[0x4000] = 0x01  # 0xC000
    ram[0x4001] = 0x02  # 0xC001
    ram[0x4002] = 0x03  # 0xC002
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC002
    cpu.registers.DE = 0xC012
    cpu.registers.BC = 0x0003
    for _ in range(3):
        cpu.step()
    assert mem.read(0xC010) == 0x01
    assert mem.read(0xC011) == 0x02
    assert mem.read(0xC012) == 0x03
    assert cpu.registers.HL == 0xBFFF
    assert cpu.registers.DE == 0xC00F
    assert cpu.registers.BC == 0x0000
    assert not (cpu.registers.F & F.FLAG_PV)  # PV cleared at completion
    assert not (cpu.registers.F & F.FLAG_N)
    assert not (cpu.registers.F & F.FLAG_H)


def test_cpir_finds_match_sets_zero() -> None:
    rom = bytes([0xED, 0xB1] + [0] * 32766)  # CPIR
    ram = bytearray(32768)
    ram[0x4000] = 0x01
    ram[0x4001] = 0x02
    ram[0x4002] = 0x03  # match is the last byte scanned
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.A = 0x03
    cpu.registers.HL = 0xC000
    cpu.registers.BC = 0x0003
    for _ in range(3):
        cpu.step()
    assert cpu.registers.A == 0x03  # A unchanged
    assert cpu.registers.HL == 0xC003
    assert cpu.registers.BC == 0x0000
    assert cpu.registers.F & F.FLAG_Z  # match found
    assert cpu.registers.F & F.FLAG_N
    assert not (cpu.registers.F & F.FLAG_PV)  # BC reached 0


def test_cpdr_finds_match_scanning_down() -> None:
    rom = bytes([0xED, 0xB9] + [0] * 32766)  # CPDR
    ram = bytearray(32768)
    ram[0x4000] = 0x01  # match is the last byte scanned (lowest address)
    ram[0x4001] = 0x02
    ram[0x4002] = 0x03
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.A = 0x01
    cpu.registers.HL = 0xC002
    cpu.registers.BC = 0x0003
    for _ in range(3):
        cpu.step()
    assert cpu.registers.HL == 0xBFFF
    assert cpu.registers.BC == 0x0000
    assert cpu.registers.F & F.FLAG_Z
    assert cpu.registers.F & F.FLAG_N
    assert not (cpu.registers.F & F.FLAG_PV)


def test_inir_loops_until_b_zero() -> None:
    rom = bytes([0xED, 0xB2] + [0] * 32766)  # INIR
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.read_port = lambda port: 0xAA
    cpu.registers.B = 0x03
    cpu.registers.C = 0x01
    cpu.registers.HL = 0xC000
    for _ in range(3):
        cpu.step()
    assert mem.read(0xC000) == 0xAA
    assert mem.read(0xC001) == 0xAA
    assert mem.read(0xC002) == 0xAA
    assert cpu.registers.B == 0x00
    assert cpu.registers.HL == 0xC003
    assert cpu.registers.F & F.FLAG_Z  # B decremented to zero


def test_otir_loops_until_b_zero() -> None:
    writes: list[tuple[int, int]] = []
    rom = bytes([0xED, 0xB3] + [0] * 32766)  # OTIR
    ram = bytearray(32768)
    ram[0x4000] = 0x11
    ram[0x4001] = 0x22
    ram[0x4002] = 0x33
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.B = 0x03
    cpu.registers.C = 0x99
    cpu.registers.HL = 0xC000
    for _ in range(3):
        cpu.step()
    # port = (B_before << 8) | C, B counts down 3→2→1
    assert writes == [(0x0399, 0x11), (0x0299, 0x22), (0x0199, 0x33)]
    assert cpu.registers.B == 0x00
    assert cpu.registers.HL == 0xC003
    assert cpu.registers.F & F.FLAG_Z


# --- 1.2 ADC HL,rr / SBC HL,rr — the four register pairs --------------------

def _adc_hl(op: int, hl: int, pair_setup, carry: bool) -> Z80:
    cpu = make_cpu([0xED, op])
    cpu.registers.HL = hl
    pair_setup(cpu.registers)
    cpu.registers.F = F.FLAG_C if carry else 0
    cpu.step()
    return cpu


def test_adc_hl_bc_pair_with_carry_in() -> None:
    cpu = _adc_hl(0x4A, 0x1000, lambda r: setattr(r, "BC", 0x0234), carry=True)
    assert cpu.registers.HL == 0x1235  # HL + BC + 1
    assert cpu.registers.F == 0x00


def test_adc_hl_de_pair_wraps_sets_z_h_c() -> None:
    cpu = _adc_hl(0x5A, 0xFFFF, lambda r: setattr(r, "DE", 0x0000), carry=True)
    assert cpu.registers.HL == 0x0000
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_H | F.FLAG_C)


def test_adc_hl_hl_pair_doubles() -> None:
    cpu = _adc_hl(0x6A, 0x4000, lambda r: None, carry=False)
    assert cpu.registers.HL == 0x8000
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_PV)


def test_adc_hl_sp_pair() -> None:
    cpu = _adc_hl(0x7A, 0x0FFF, lambda r: setattr(r, "SP", 0x0001), carry=False)
    assert cpu.registers.HL == 0x1000
    assert cpu.registers.F == F.FLAG_H


def _sbc_hl(op: int, hl: int, pair_setup, carry: bool) -> Z80:
    cpu = make_cpu([0xED, op])
    cpu.registers.HL = hl
    pair_setup(cpu.registers)
    cpu.registers.F = F.FLAG_C if carry else 0
    cpu.step()
    return cpu


def test_sbc_hl_bc_pair() -> None:
    cpu = _sbc_hl(0x42, 0x0010, lambda r: setattr(r, "BC", 0x0005), carry=False)
    assert cpu.registers.HL == 0x000B
    assert cpu.registers.F == F.FLAG_N


def test_sbc_hl_de_pair_borrow() -> None:
    cpu = _sbc_hl(0x52, 0x0000, lambda r: setattr(r, "DE", 0x0001), carry=False)
    assert cpu.registers.HL == 0xFFFF
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_H | F.FLAG_N | F.FLAG_C)


def test_sbc_hl_hl_pair_self_zero() -> None:
    cpu = _sbc_hl(0x62, 0x1234, lambda r: None, carry=False)
    assert cpu.registers.HL == 0x0000
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_N)


def test_sbc_hl_sp_pair() -> None:
    cpu = _sbc_hl(0x72, 0x8000, lambda r: setattr(r, "SP", 0x0001), carry=False)
    assert cpu.registers.HL == 0x7FFF
    assert cpu.registers.F == (F.FLAG_H | F.FLAG_PV | F.FLAG_N)  # signed overflow


# --- 1.3 IN/OUT (C), RETN, IM 0, LD A,R, RRD/RLD ---------------------------

def test_in_r_c_sets_szp_preserves_carry() -> None:
    cpu = make_cpu([0xED, 0x40])  # IN B,(C)
    cpu.read_port = lambda port: 0x00
    cpu.registers.BC = 0x1234  # port = (B<<8)|C = 0x1234
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F & F.FLAG_Z   # value zero
    assert cpu.registers.F & F.FLAG_PV  # parity of 0x00 is even
    assert cpu.registers.F & F.FLAG_C   # carry preserved
    assert not (cpu.registers.F & F.FLAG_N)


def test_in_a_c_sets_sign() -> None:
    cpu = make_cpu([0xED, 0x78])  # IN A,(C)
    cpu.read_port = lambda port: 0x80
    cpu.registers.BC = 0x1234
    cpu.registers.F = 0
    cpu.step()
    assert cpu.registers.A == 0x80
    assert cpu.registers.F & F.FLAG_S
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_PV)


def test_out_c_r_drives_port() -> None:
    writes: list[tuple[int, int]] = []
    cpu = make_cpu([0xED, 0x79])  # OUT (C),A
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.A = 0x5A
    cpu.registers.BC = 0x1234
    cpu.step()
    assert writes == [(0x1234, 0x5A)]


def test_retn_pops_pc_and_restores_iff1() -> None:
    rom = bytes([0xED, 0x45] + [0] * 32766)  # RETN
    ram = bytearray(32768)
    ram[0x7FFE] = 0x00  # 0xFFFE
    ram[0x7FFF] = 0x20  # 0xFFFF
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFE
    cpu.iff2 = True
    cpu.iff1 = False
    cpu.step()
    assert cpu.registers.PC == 0x2000
    assert cpu.iff1 is True  # IFF1 <- IFF2


def test_im0() -> None:
    cpu = make_cpu([0xED, 0x46])
    cpu.step()
    assert cpu.im == 0


def test_ld_a_r_reads_refresh_and_sets_pv_from_iff2() -> None:
    cpu = make_cpu([0xED, 0x5F])  # LD A, R
    cpu.registers.R = 0x40
    cpu.iff2 = True
    cpu.registers.F = F.FLAG_C
    cpu.step()
    # R is incremented on each opcode fetch (ED byte + 0x5F byte), so A = 0x42.
    assert cpu.registers.A == 0x42
    assert cpu.registers.F & F.FLAG_PV  # PV <- IFF2
    assert cpu.registers.F & F.FLAG_C   # carry preserved
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_S)


def test_rrd_rotates_nibbles() -> None:
    rom = bytes([0xED, 0x67] + [0] * 32766)  # RRD
    ram = bytearray(32768)
    ram[0x4000] = 0x34  # (0xC000)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.A = 0x12
    cpu.step()
    assert cpu.registers.A == 0x14  # A low nibble <- (HL) low nibble
    assert mem.read(0xC000) == 0x23  # (HL) = (A low << 4) | (HL) high nibble
    assert cpu.registers.F & F.FLAG_PV  # parity of 0x14


def test_rld_rotates_nibbles() -> None:
    rom = bytes([0xED, 0x6F] + [0] * 32766)  # RLD
    ram = bytearray(32768)
    ram[0x4000] = 0x34  # (0xC000)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.registers.A = 0x12
    cpu.step()
    assert cpu.registers.A == 0x13
    assert mem.read(0xC000) == 0x42
