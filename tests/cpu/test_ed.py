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
    cpu.registers.F = F.FLAG_S | F.FLAG_C  # no flags affected
    cpu.step()
    assert cpu.registers.I == 0xAB
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_C)


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

def _run_hl16(op: int, hl: int, pair_setup, carry: bool) -> Z80:
    """Shared by ADC HL,rr and SBC HL,rr below -- the opcode byte alone
    selects which operation runs."""
    cpu = make_cpu([0xED, op])
    cpu.registers.HL = hl
    pair_setup(cpu.registers)
    cpu.registers.F = F.FLAG_C if carry else 0
    cpu.step()
    return cpu


_adc_hl = _run_hl16
_sbc_hl = _run_hl16


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


def test_fetch_operand_byte_preserves_r_bit7() -> None:
    """The `_fetch()` path (used for operand/displacement/immediate bytes,
    not just opcode M1 fetches) must also treat bit 7 as sticky."""
    cpu = make_cpu([0xED, 0x5E, 0x00])  # LD A, R ; NOP (any 2+ byte stream)
    cpu.registers.R = 0xC8  # bit 7 set
    cpu._fetch()
    assert cpu.registers.R == 0xC9  # bit 7 preserved, low 7 bits +1


def test_ld_r_a_sets_bit7_and_next_fetch_preserves_it() -> None:
    """R's auto-increment on fetch only touches bits 0-6; bit 7 is sticky
    and changes only via a full-register write like LD R,A. Regression
    guard for the sticky-bit-7 fix in Z80._fetch()/step()."""
    cpu = make_cpu([0xED, 0x4F, 0x00])  # LD R, A ; NOP
    cpu.registers.A = 0xC8  # bit 7 set
    cpu.registers.F = F.FLAG_Z | F.FLAG_C  # LD R,A affects no flags
    cpu.step()  # LD R,A: R = 0xC8 (2 fetches auto-incremented R first, then overwritten)
    assert cpu.registers.R == 0xC8
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_C)
    cpu.step()  # NOP: one more fetch, auto-increment only bumps bits 0-6
    assert cpu.registers.R == 0xC9  # bit 7 still set, low 7 bits incremented by 1


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


# ===========================================================================
# allium:propagate reconciliation (cpu_z80_ed.allium) — gaps not already
# covered above or in tests/cpu/test_alu16.py / tests/cpu/test_io.py.
# ===========================================================================


# --- NEG edge cases: 0 - 0 and the 0x80 self-negation special case ---------

def test_neg_zero_clears_carry() -> None:
    # 0 - 0 has no borrow either way -- NEG must NOT set carry here, unlike
    # the general "NEG always sets carry unless A was 0" folk description.
    cpu = make_cpu([0xED, 0x44])
    cpu.registers.A = 0x00
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_N)
    assert not (cpu.registers.F & F.FLAG_C)


def test_neg_0x80_sets_carry_and_overflow() -> None:
    # 0 - 0x80 = -128, which wraps back to 0x80 -- the one input where NEG's
    # result equals its operand. Carry and P/V (signed overflow) both set.
    cpu = make_cpu([0xED, 0x44])
    cpu.registers.A = 0x80
    cpu.step()
    assert cpu.registers.A == 0x80
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_PV | F.FLAG_N | F.FLAG_C)


# --- LD A,I / LD A,R with IFF2=0: P/V must clear, not just "not parity" ----

def test_ld_a_i_iff2_false_clears_pv() -> None:
    cpu = make_cpu([0xED, 0x57])  # LD A, I
    cpu.registers.I = 0x80
    cpu.iff2 = False
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0x80
    assert cpu.registers.F & F.FLAG_S
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_PV)  # PV <- IFF2, which is False
    assert cpu.registers.F & F.FLAG_C  # carry preserved
    assert not (cpu.registers.F & F.FLAG_H)
    assert not (cpu.registers.F & F.FLAG_N)


def test_ld_a_r_iff2_false_clears_pv() -> None:
    cpu = make_cpu([0xED, 0x5F])  # LD A, R
    cpu.registers.R = 0x01
    cpu.iff2 = False
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0x03  # R incremented twice (ED + 5F fetches)
    assert not (cpu.registers.F & F.FLAG_PV)
    assert cpu.registers.F & F.FLAG_C
    assert not (cpu.registers.F & F.FLAG_S)
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_H)
    assert not (cpu.registers.F & F.FLAG_N)


# --- LD (nn),rr / LD rr,(nn): all four register pairs, little-endian ------

def _store_pair_direct(op: int, nn: int, pair_setup) -> tuple[Z80, Memory]:
    rom = bytes([0xED, op, nn & 0xFF, (nn >> 8) & 0xFF] + [0] * 32764)
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    pair_setup(cpu.registers)
    cpu.step()
    return cpu, mem


def test_ld_ind_nn_bc() -> None:
    _cpu, mem = _store_pair_direct(0x43, 0xC000, lambda r: setattr(r, "BC", 0x1234))
    assert mem.read(0xC000) == 0x34  # low byte first
    assert mem.read(0xC001) == 0x12


def test_ld_ind_nn_de() -> None:
    _cpu, mem = _store_pair_direct(0x53, 0xC000, lambda r: setattr(r, "DE", 0x5678))
    assert mem.read(0xC000) == 0x78
    assert mem.read(0xC001) == 0x56


def test_ld_ind_nn_hl() -> None:
    # 0xED 0x63 -- the redundant duplicate of main-table 0x22 (StoreHLDirect).
    _cpu, mem = _store_pair_direct(0x63, 0xC000, lambda r: setattr(r, "HL", 0x9ABC))
    assert mem.read(0xC000) == 0xBC
    assert mem.read(0xC001) == 0x9A


def test_ld_ind_nn_sp() -> None:
    _cpu, mem = _store_pair_direct(0x73, 0xC000, lambda r: setattr(r, "SP", 0xDEF0))
    assert mem.read(0xC000) == 0xF0
    assert mem.read(0xC001) == 0xDE


def _load_pair_direct(op: int, nn: int, lo: int, hi: int) -> Z80:
    rom = bytes([0xED, op, nn & 0xFF, (nn >> 8) & 0xFF] + [0] * 32764)
    ram = bytearray(32768)
    ram[(nn - 0x8000) & 0x7FFF] = lo
    ram[(nn - 0x8000 + 1) & 0x7FFF] = hi
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.step()
    return cpu


def test_ld_rr_ind_nn_bc() -> None:
    cpu = _load_pair_direct(0x4B, 0xC000, 0x34, 0x12)
    assert cpu.registers.BC == 0x1234


def test_ld_rr_ind_nn_de() -> None:
    cpu = _load_pair_direct(0x5B, 0xC000, 0x78, 0x56)
    assert cpu.registers.DE == 0x5678


def test_ld_rr_ind_nn_hl() -> None:
    # 0xED 0x6B -- the redundant duplicate of main-table 0x2A (LoadHLDirect).
    cpu = _load_pair_direct(0x6B, 0xC000, 0xBC, 0x9A)
    assert cpu.registers.HL == 0x9ABC


def test_ld_rr_ind_nn_sp() -> None:
    cpu = _load_pair_direct(0x7B, 0xC000, 0xF0, 0xDE)
    assert cpu.registers.SP == 0xDEF0


# --- Undocumented IN F,(C) / OUT (C),0 --------------------------------------

def test_in_f_c_sets_flags_and_discards_value() -> None:
    cpu = make_cpu([0xED, 0x70])  # IN F, (C)
    cpu.read_port = lambda port: 0x81  # S and P/V (even parity) both set
    cpu.registers.A = 0x55  # sentinel: must survive unchanged
    cpu.registers.BC = 0x1234
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.A == 0x55  # value discarded, not stored anywhere
    assert cpu.registers.F & F.FLAG_S
    assert cpu.registers.F & F.FLAG_PV
    assert cpu.registers.F & F.FLAG_C  # carry preserved
    assert not (cpu.registers.F & F.FLAG_Z)


def test_out_c_0_writes_literal_zero() -> None:
    writes: list[tuple[int, int]] = []
    cpu = make_cpu([0xED, 0x71])  # OUT (C), 0
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.BC = 0x0304
    cpu.step()
    assert writes == [(0x0304, 0x00)]


# --- Block I/O P/V flag (openspec "Block I/O flag computation") -----------

def test_ini_sets_pv_alongside_carry_half_and_zero() -> None:
    # B=1,C=0,value=0xFF: b_after=0 (Z), k=0xFF+1=0x100>0xFF (H+C), value bit7
    # set (N), and parity((k&7)^b_after) = parity(0) = even -> PV set too.
    # test_ini_sets_carry_half_and_n above never asserts PV; this pins it.
    cpu = make_cpu([0xED, 0xA2])  # INI
    cpu.read_port = lambda port: 0xFF
    cpu.registers.B = 0x01
    cpu.registers.C = 0x00
    cpu.registers.HL = 0xC000
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F == (F.FLAG_Z | F.FLAG_H | F.FLAG_PV | F.FLAG_N | F.FLAG_C)


def test_outi_sets_pv_without_carry_or_half() -> None:
    # B=2,C=0,value=0x80 (at HL, read before increment): new_hl=HL+1 so
    # l_after=low_byte(new_hl); k=0x80+l_after=0x81 (<=0xFF, so H/C clear);
    # b_after=1 (not zero, not signed); parity((k&7)^b_after)=parity(0)=even
    # -> PV set on its own, distinct from the carry/half-set case above.
    rom = bytes([0xED, 0xA3] + [0] * 32766)  # OUTI
    ram = bytearray(32768)
    ram[0x4000] = 0x80  # (0xC000)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    writes: list[tuple[int, int]] = []
    cpu.write_port = lambda port, value: writes.append((port, value))
    cpu.registers.B = 0x02
    cpu.registers.C = 0x00
    cpu.registers.HL = 0xC000
    cpu.step()
    assert writes == [(0x0200, 0x80)]
    assert cpu.registers.HL == 0xC001
    assert cpu.registers.B == 0x01
    assert cpu.registers.F == (F.FLAG_PV | F.FLAG_N)


# --- Undocumented IM-mirror opcodes fall through to undefined -------------

def test_im_mirror_opcodes_do_not_set_interrupt_mode() -> None:
    # Documented hardware treats 0x4E/0x66/0x6E/0x76/0x7E as duplicates of
    # 0x46/0x56/0x5E, but opcodes_main.py's dispatch table only registers
    # the canonical three -- these five fall through to _ed_undefined
    # instead. Regression guard for that narrow, deliberate divergence.
    for op in (0x4E, 0x66, 0x6E, 0x76, 0x7E):
        cpu = make_cpu([0xED, op])
        cpu.im = 9  # sentinel unreachable via SetInterruptMode (0/1/2 only)
        cpu.step()
        assert cpu.im == 9, f"opcode 0x{op:02X} unexpectedly touched cpu.im"
