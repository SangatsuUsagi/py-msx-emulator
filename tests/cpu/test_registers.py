from msx.cpu import flags
from msx.cpu.registers import Registers
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory
from tests.cpu.flag_helpers import pack, unpack


def _make_cpu() -> Z80:
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    return Z80(read_byte=mem.read, write_byte=mem.write)


def test_reset_values() -> None:
    r = Registers()
    r.reset()
    assert r.PC == 0x0000
    assert r.SP == 0xFFFF
    assert r.AF == 0xFFFF


def test_a_high_byte() -> None:
    r = Registers()
    r.AF = 0x1234
    assert r.A == 0x12


def test_f_low_byte() -> None:
    r = Registers()
    r.AF = 0x1234
    assert r.F == 0x34


def test_a_setter() -> None:
    r = Registers()
    r.AF = 0x0000
    r.A = 0xAB
    assert r.AF == 0xAB00
    assert r.F == 0x00


def test_f_setter() -> None:
    r = Registers()
    r.AF = 0xFF00
    r.F = 0x55
    assert r.AF == 0xFF55
    assert r.A == 0xFF


def test_bc_bytes() -> None:
    r = Registers()
    r.BC = 0x1234
    assert r.B == 0x12
    assert r.C == 0x34


def test_hl_bytes() -> None:
    r = Registers()
    r.HL = 0xABCD
    assert r.H == 0xAB
    assert r.L == 0xCD


def test_flag_pack_unpack_roundtrip() -> None:
    f = pack(s=True, z=False, h=True, pv=True, n=False, c=True)
    s, z, h, pv, n, c = unpack(f)
    assert s is True
    assert z is False
    assert h is True
    assert pv is True
    assert n is False
    assert c is True


def test_flag_all_zero() -> None:
    f = pack(s=False, z=False, h=False, pv=False, n=False, c=False)
    assert f == 0x00


def test_flag_all_set() -> None:
    f = pack(s=True, z=True, h=True, pv=True, n=True, c=True)
    assert f == (
        flags.FLAG_S | flags.FLAG_Z | flags.FLAG_H | flags.FLAG_PV | flags.FLAG_N | flags.FLAG_C
    )


def test_parity_even() -> None:
    assert flags.parity(0b00000011) is True


def test_parity_odd() -> None:
    assert flags.parity(0b00000001) is False


# ===========================================================================
# Z80.reset() — CPU-level reset (control state + registers). Distinct from
# test_reset_values above, which only exercises Registers.reset() in
# isolation; halted/iff1/iff2/im/int_pending/nmi_pending/ei_pending are set
# on Z80, not on Registers.
# ===========================================================================


def test_z80_reset_clears_control_state() -> None:
    cpu = _make_cpu()
    cpu.halted = True
    cpu.iff1 = True
    cpu.iff2 = True
    cpu.im = 2
    cpu.int_pending = True
    cpu.nmi_pending = True
    cpu.ei_pending = True
    cpu.reset()
    assert cpu.halted is False
    assert cpu.iff1 is False
    assert cpu.iff2 is False
    assert cpu.im == 0
    assert cpu.int_pending is False
    assert cpu.nmi_pending is False
    assert cpu.ei_pending is False


def test_z80_reset_restores_register_power_on_values() -> None:
    cpu = _make_cpu()
    cpu.registers.PC = 0x1234
    cpu.registers.SP = 0x0000
    cpu.registers.AF = 0x0000
    cpu.registers.BC = 0x0000
    cpu.registers.DE = 0x0000
    cpu.registers.HL = 0x0000
    cpu.registers.IX = 0x0000
    cpu.registers.IY = 0x0000
    cpu.registers.I = 0xFF
    cpu.registers.R = 0x7F
    cpu.reset()
    assert cpu.registers.PC == 0x0000
    assert cpu.registers.SP == 0xFFFF
    assert cpu.registers.AF == 0xFFFF
    assert cpu.registers.BC == 0xFFFF
    assert cpu.registers.DE == 0xFFFF
    assert cpu.registers.HL == 0xFFFF
    assert cpu.registers.IX == 0xFFFF
    assert cpu.registers.IY == 0xFFFF
    assert cpu.registers.I == 0x00
    assert cpu.registers.R == 0x00


def test_z80_reset_restores_shadow_register_power_on_values() -> None:
    # Z80.reset() resets the alternate register set to the same power-on
    # values as the primary set (Registers.reset() sets A_/F_/BC_/DE_/HL_
    # alongside A/F/BC/DE/HL).
    cpu = _make_cpu()
    cpu.registers.AF_ = 0x1234
    cpu.registers.BC_ = 0x5678
    cpu.registers.DE_ = 0x9ABC
    cpu.registers.HL_ = 0xDEF0
    cpu.reset()
    assert cpu.registers.AF_ == 0xFFFF
    assert cpu.registers.BC_ == 0xFFFF
    assert cpu.registers.DE_ == 0xFFFF
    assert cpu.registers.HL_ == 0xFFFF
