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


def test_rlc_b() -> None:
    cpu = make_cpu([0xCB, 0x00])  # RLC B
    cpu.registers.B = 0x85
    cpu.step()
    assert cpu.registers.B == 0x0B
    assert cpu.registers.F & F.FLAG_C


def test_rrc_c() -> None:
    cpu = make_cpu([0xCB, 0x09])  # RRC C
    cpu.registers.C = 0x01
    cpu.step()
    assert cpu.registers.C == 0x80
    assert cpu.registers.F & F.FLAG_C


def test_rl_d() -> None:
    cpu = make_cpu([0xCB, 0x12])  # RL D
    cpu.registers.D = 0x76
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert cpu.registers.D == 0xED
    assert not (cpu.registers.F & F.FLAG_C)


def test_sla_e() -> None:
    cpu = make_cpu([0xCB, 0x23])  # SLA E
    cpu.registers.E = 0x81
    cpu.step()
    assert cpu.registers.E == 0x02
    assert cpu.registers.F & F.FLAG_C


def test_srl_a() -> None:
    cpu = make_cpu([0xCB, 0x3F])  # SRL A
    cpu.registers.A = 0x01
    cpu.step()
    assert cpu.registers.A == 0x00
    assert cpu.registers.F & F.FLAG_C
    assert cpu.registers.F & F.FLAG_Z


def test_bit_3_b_set() -> None:
    cpu = make_cpu([0xCB, 0x58])  # BIT 3, B
    cpu.registers.B = 0x08
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_Z)


def test_bit_3_b_clear() -> None:
    cpu = make_cpu([0xCB, 0x58])
    cpu.registers.B = 0x00
    cpu.step()
    assert cpu.registers.F & F.FLAG_Z


def test_set_5_c() -> None:
    cpu = make_cpu([0xCB, 0xE9])  # SET 5, C
    cpu.registers.C = 0x00
    cpu.step()
    assert cpu.registers.C == 0x20


def test_res_7_a() -> None:
    cpu = make_cpu([0xCB, 0xBF])  # RES 7, A
    cpu.registers.A = 0xFF
    cpu.step()
    assert cpu.registers.A == 0x7F


def test_bit7_ix_with_bit_set_sets_sign_flag() -> None:
    rom = bytes([0xDD, 0xCB, 0x02, 0x7E] + [0] * 32764)  # BIT 7,(IX+2)
    ram = bytearray(32768)
    ram[0x4002] = 0x80  # 0xC002 -> ram[addr - 0x8000]; bit 7 set
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    f = cpu.registers.F
    assert f & F.FLAG_S
    assert not (f & F.FLAG_Z)
    assert f & F.FLAG_H
    assert not (f & F.FLAG_N)


def test_bit7_ix_with_bit_clear_clears_sign_sets_zero() -> None:
    rom = bytes([0xDD, 0xCB, 0x02, 0x7E] + [0] * 32764)  # BIT 7,(IX+2)
    ram = bytearray(32768)  # 0xC002 reads back 0x00
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    f = cpu.registers.F
    assert not (f & F.FLAG_S)
    assert f & F.FLAG_Z


def test_ddcb_rlc_echoes_into_named_register() -> None:
    rom = bytes([0xDD, 0xCB, 0x03, 0x00] + [0] * 32764)  # RLC (IX+3),B
    ram = bytearray(32768)
    ram[0x4003] = 0x81  # 0xC003 -> ram[addr - 0x8000]
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert mem.read(0xC003) == 0x03
    assert cpu.registers.B == 0x03


def test_fdcb_res_echoes_into_named_register() -> None:
    rom = bytes([0xFD, 0xCB, 0x05, 0x9A] + [0] * 32764)  # RES 3,(IY+5),D
    ram = bytearray(32768)
    ram[0x4005] = 0xFF  # 0xC005 -> ram[addr - 0x8000]
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IY = 0xC000
    cpu.step()
    assert mem.read(0xC005) == 0xF7
    assert cpu.registers.D == 0xF7


def test_ddcb_set_with_register_field_6_writes_memory_only() -> None:
    rom = bytes([0xDD, 0xCB, 0x02, 0xC6] + [0] * 32764)  # SET 0,(IX+2)
    ram = bytearray(32768)
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.registers.B = cpu.registers.C = cpu.registers.D = 0x00
    cpu.registers.E = cpu.registers.H = cpu.registers.L = cpu.registers.A = 0x00
    cpu.step()
    assert mem.read(0xC002) == 0x01
    assert (cpu.registers.B, cpu.registers.C, cpu.registers.D) == (0, 0, 0)
    assert (cpu.registers.E, cpu.registers.H, cpu.registers.L, cpu.registers.A) == (
        0, 0, 0, 0,
    )


def test_ddcb_bit_does_not_echo_or_write_memory() -> None:
    rom = bytes([0xDD, 0xCB, 0x00, 0x69] + [0] * 32764)  # BIT 5,(IX+0),C
    ram = bytearray(32768)
    ram[0x4000] = 0x20  # bit 5 set
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.registers.C = 0x77
    cpu.step()
    assert mem.read(0xC000) == 0x20  # unchanged
    assert cpu.registers.C == 0x77  # unchanged


# ===========================================================================
# allium:propagate reconciliation (cpu_z80_cb.allium) — gaps not already
# covered above.
# ===========================================================================


# --- Row 0 (RLC/RRC/RL/RR/SLA/SRA/SLL/SRL): shared flag shape --------------


def test_rotate_shift_flags_are_fresh_every_op() -> None:
    # All eight row-0 ops share one flag shape: S/Z/PV recomputed fresh from
    # the result, H/N always cleared, C = the bit rotated/shifted out (never
    # preserved). F starts as 0xFF (every flag set, including carry-in=1 for
    # RL/RR) to prove every bit gets overwritten, not just the ones the
    # tests above happen to check. A=0xC3 (1100 0011) exercises a left-hand
    # and a right-hand carry alongside the fresh S/PV recompute.
    cases = [
        (0x07, 0x87, 0x85),  # RLC A
        (0x0F, 0xE1, 0x85),  # RRC A
        (0x17, 0x87, 0x85),  # RL A  (carry-in=1 from stale F)
        (0x1F, 0xE1, 0x85),  # RR A  (carry-in=1 from stale F)
        (0x27, 0x86, 0x81),  # SLA A
        (0x2F, 0xE1, 0x85),  # SRA A
        (0x37, 0x87, 0x85),  # SLL A
        (0x3F, 0x61, 0x01),  # SRL A
    ]
    for opcode, expected_result, expected_flags in cases:
        cpu = make_cpu([0xCB, opcode])
        cpu.registers.A = 0xC3
        cpu.registers.F = 0xFF
        cpu.step()
        assert cpu.registers.A == expected_result, f"opcode 0x{opcode:02X}"
        assert cpu.registers.F == expected_flags, f"opcode 0x{opcode:02X}"


def test_rlc_hl_reads_and_writes_memory() -> None:
    # Row 0's (HL) form must read the operand from memory and write the
    # result back to memory -- distinct from the plain-register write-back
    # path every other rotate/shift test in this file exercises.
    rom = bytes([0xCB, 0x06] + [0] * 32766)  # RLC (HL)
    ram = bytearray(32768)
    ram[0x4000] = 0x85  # 0xC000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    cpu.step()
    assert mem.read(0xC000) == 0x0B
    assert cpu.registers.F & F.FLAG_C


# --- RR: rotate-through-carry (distinct from RL's own existing test) ------


def test_rr_b_rotates_through_carry_not_via_bit7() -> None:
    # RR rotates through carry: the incoming carry becomes the new bit 7,
    # and the OLD bit 0 -- not bit 7 -- becomes the new carry. B=0x80 (bit 7
    # set, bit 0 clear) with carry-in=1 pulls the two apart: if carry-out
    # were mistakenly read from bit 7 (which is 1) instead of bit 0 (which
    # is 0), this test would see carry set instead of clear.
    cpu = make_cpu([0xCB, 0x18])  # RR B
    cpu.registers.B = 0x80
    cpu.registers.F = 0xFF  # carry-in=1, plus every other flag stale
    cpu.step()
    assert cpu.registers.B == 0xC0  # bit 7 <- old carry-in (1)
    assert cpu.registers.F == (F.FLAG_S | F.FLAG_PV)  # carry-out <- old bit 0 (0)


# --- SRA: arithmetic shift preserves sign bit, unlike SRL -------------------


def test_sra_l_preserves_sign_bit() -> None:
    # SRA is an arithmetic shift: bit 7 is preserved (sign-extended), unlike
    # SRL which always fills bit 7 with 0. L=0x81 (bit 7 set) makes the two
    # diverge: SRA must leave bit 7 set in the result.
    cpu = make_cpu([0xCB, 0x2D])  # SRA L
    cpu.registers.L = 0x81
    cpu.step()
    assert cpu.registers.L == 0xC0
    assert cpu.registers.F & F.FLAG_C  # old bit 0 (1) shifted out


# --- SLL (undocumented): bit 0 forced to 1 regardless of the shift --------


def test_sll_h_forces_bit0_set_unlike_sla() -> None:
    # SLL (undocumented) shifts left like SLA but forces the incoming bit 0
    # to 1 rather than 0, regardless of what would otherwise have shifted
    # in. H=0x00 makes the divergence unambiguous: SLA on the same input
    # would leave the whole result at 0x00 with Z set; SLL must produce
    # 0x01 with Z clear.
    cpu = make_cpu([0xCB, 0x34])  # SLL H
    cpu.registers.H = 0x00
    cpu.step()
    assert cpu.registers.H == 0x01
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_C)  # bit 7 of input was 0


# --- BIT n,r: the 5-flag algorithm, independently per flag ------------------


def test_bit_preserves_carry_when_set() -> None:
    cpu = make_cpu([0xCB, 0x47])  # BIT 0, A
    cpu.registers.A = 0x01
    cpu.registers.F = F.FLAG_C
    cpu.step()
    f = cpu.registers.F
    assert f & F.FLAG_H
    assert not (f & F.FLAG_N)
    assert f & F.FLAG_C  # carry preserved, not touched by BIT


def test_bit_preserves_carry_when_clear() -> None:
    cpu = make_cpu([0xCB, 0x47])  # BIT 0, A
    cpu.registers.A = 0x00
    cpu.registers.F = 0
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_C)


def test_bit_clear_sets_z_and_pv_together() -> None:
    cpu = make_cpu([0xCB, 0x47])  # BIT 0, A
    cpu.registers.A = 0x00
    cpu.step()
    f = cpu.registers.F
    assert f & F.FLAG_Z
    assert f & F.FLAG_PV


def test_bit_7_set_sets_sign() -> None:
    cpu = make_cpu([0xCB, 0x7F])  # BIT 7, A
    cpu.registers.A = 0x80
    cpu.step()
    assert cpu.registers.F & F.FLAG_S


def test_bit_7_clear_does_not_set_sign() -> None:
    cpu = make_cpu([0xCB, 0x7F])  # BIT 7, A
    cpu.registers.A = 0x00
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_S)


def test_bit_nonseven_bit_set_does_not_set_sign() -> None:
    # S depends on bit_index = 7, not merely on the tested bit's value --
    # bit 0 set here must NOT set S, even though a set bit is present.
    cpu = make_cpu([0xCB, 0x47])  # BIT 0, A
    cpu.registers.A = 0x01
    cpu.step()
    assert not (cpu.registers.F & F.FLAG_S)


# --- BIT (HL) 12-T-state vs. the other rows' 15-T-state (HL) form ---------


def test_bit_hl_takes_12_t_states() -> None:
    # BIT (HL) never writes its operand back, so it skips the write cycle
    # every other row's (HL) form pays -- 12 T-states here, not the general
    # 15 (see test_rotate_hl_takes_15_t_states below).
    cpu = make_cpu([0xCB, 0x46])  # BIT 0, (HL)
    cpu.registers.HL = 0xC000
    cycles = cpu.step()
    assert cycles == 12


def test_rotate_hl_takes_15_t_states() -> None:
    cpu = make_cpu([0xCB, 0x06])  # RLC (HL)
    cpu.registers.HL = 0xC000
    cycles = cpu.step()
    assert cycles == 15


# --- RES/SET n,r: no flags affected, edge bit indices, register and (HL) --


def test_res_0_b_clears_bit_no_flags_affected() -> None:
    cpu = make_cpu([0xCB, 0x80])  # RES 0, B
    cpu.registers.B = 0x01
    stale = F.FLAG_S | F.FLAG_Z | F.FLAG_H | F.FLAG_PV | F.FLAG_N | F.FLAG_C
    cpu.registers.F = stale
    cpu.step()
    assert cpu.registers.B == 0x00
    assert cpu.registers.F == stale


def test_set_7_hl_sets_bit_no_flags_affected() -> None:
    rom = bytes([0xCB, 0xFE] + [0] * 32766)  # SET 7, (HL)
    ram = bytearray(32768)
    ram[0x4000] = 0x00  # 0xC000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.HL = 0xC000
    stale = F.FLAG_S | F.FLAG_Z | F.FLAG_H | F.FLAG_PV | F.FLAG_N | F.FLAG_C
    cpu.registers.F = stale
    cpu.step()
    assert mem.read(0xC000) == 0x80
    assert cpu.registers.F == stale


# ===========================================================================
# allium:propagate reconciliation (cpu_z80_dd_fd.allium) — DDCB/FDCB row-0
# op-selection gaps: only RLC had ever been exercised in indexed (DDCB/FDCB)
# form; SRA/SLL/RL confirm the `bit` field selects the right rotate/shift
# function and that carry threads through correctly, not just RLC.
# ===========================================================================


def test_ddcb_sra_preserves_sign_bit() -> None:
    # SRA (IX+d): arithmetic shift preserves the sign bit -- distinguishes it
    # from SLA/SRL and confirms the DDCB row-0 dispatch isn't hardcoded to
    # RLC (the only row-0 op exercised elsewhere in DDCB/FDCB form).
    rom = bytes([0xDD, 0xCB, 0x02, 0x2E] + [0] * 32764)  # SRA (IX+2)
    ram = bytearray(32768)
    ram[0x4002] = 0x81  # 0xC002
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.step()
    assert mem.read(0xC002) == 0xC0
    assert cpu.registers.F & F.FLAG_C


def test_fdcb_sll_forces_bit0_set() -> None:
    # SLL (IY+d) (undocumented): forces bit 0 to 1 regardless of shift-in --
    # also gives an FD-prefixed spot check for the same DDCB row-0 dispatch.
    rom = bytes([0xFD, 0xCB, 0x01, 0x36] + [0] * 32764)  # SLL (IY+1)
    ram = bytearray(32768)
    ram[0x4001] = 0x00  # 0xC001
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IY = 0xC000
    cpu.step()
    assert mem.read(0xC001) == 0x01
    assert not (cpu.registers.F & F.FLAG_Z)
    assert not (cpu.registers.F & F.FLAG_C)


def test_ddcb_rl_rotates_through_carry() -> None:
    # RL (IX+d): rotates through carry (carry-in becomes new bit 0, old bit
    # 7 becomes carry-out) -- confirms the carry flag is correctly threaded
    # into the DDCB row-0 path, unlike the carry-independent RLC already
    # tested elsewhere.
    rom = bytes([0xDD, 0xCB, 0x00, 0x16] + [0] * 32764)  # RL (IX+0)
    ram = bytearray(32768)
    ram[0x4000] = 0x76  # 0xC000
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.registers.F = F.FLAG_C
    cpu.step()
    assert mem.read(0xC000) == 0xED
    assert not (cpu.registers.F & F.FLAG_C)


def test_ddcb_bit_nonseven_set_does_not_set_sign() -> None:
    # BIT 5,(IX+d): S depends on bit_index == 7, not merely on whether the
    # tested bit is set -- the existing DDCB BIT tests only cover bit 7 and
    # (separately) bit 5 without checking flags at all.
    rom = bytes([0xDD, 0xCB, 0x00, 0x6E] + [0] * 32764)  # BIT 5,(IX+0)
    ram = bytearray(32768)
    ram[0x4000] = 0x20  # bit 5 set
    mem = Memory(rom=rom, ram=ram, _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.IX = 0xC000
    cpu.registers.F = 0
    cpu.step()
    f = cpu.registers.F
    assert not (f & F.FLAG_S)
    assert not (f & F.FLAG_Z)
    assert f & F.FLAG_H
