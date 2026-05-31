from msx.cpu.registers import Registers
from msx.cpu import flags


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
    f = flags.pack(s=True, z=False, h=True, pv=True, n=False, c=True)
    s, z, h, pv, n, c = flags.unpack(f)
    assert s is True
    assert z is False
    assert h is True
    assert pv is True
    assert n is False
    assert c is True


def test_flag_all_zero() -> None:
    f = flags.pack(s=False, z=False, h=False, pv=False, n=False, c=False)
    assert f == 0x00


def test_flag_all_set() -> None:
    f = flags.pack(s=True, z=True, h=True, pv=True, n=True, c=True)
    assert f == (flags.FLAG_S | flags.FLAG_Z | flags.FLAG_H | flags.FLAG_PV | flags.FLAG_N | flags.FLAG_C)


def test_parity_even() -> None:
    assert flags.parity(0b00000011) is True


def test_parity_odd() -> None:
    assert flags.parity(0b00000001) is False
