"""Property test for the V9938 data-port VRAM pointer step.

write_port(0x98) and read_port(0x98) inline the no-carry +1 step and fall
back to _advance_addr() only on the carry out of A13 (allium/v9938.allium
WriteVramPort / ReadVramPort: vram_address_advance with bank_carry when
A13-A0 = 0x3FFF and M4/M5 selects a V9938-only mode; MSX1 modes wrap within
the 16 KB bank). Checked for every bank, both directions, both mode kinds.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from msx.vdp.v9938 import V9938

# R#0 values: GRAPHIC1 (no M4/M5) wraps in the bank; GRAPHIC4 / GRAPHIC5 /
# GRAPHIC7 carry into R#14.
_R0_WRAP = 0x00
_R0_CARRY = (0x06, 0x08, 0x0E)


def _expected_step(bank: int, low: int, carry_mode: bool) -> tuple[int, int]:
    """(address, R#14) after one data-port access at (bank, low)."""
    if low != 0x3FFF:
        return (bank << 14) | (low + 1), bank
    if carry_mode:
        nbank = (bank + 1) & 0x07
        return nbank << 14, nbank
    return bank << 14, bank


def _vdp_at(r0: int, bank: int, low: int) -> V9938:
    vdp = V9938()
    vdp.regs[0] = r0
    vdp.regs[14] = bank
    vdp.addr = (bank << 14) | low
    return vdp


_lows = st.one_of(
    st.sampled_from([0x0000, 0x3FFE, 0x3FFF]),
    st.integers(min_value=0, max_value=0x3FFF),
)


@given(
    bank=st.integers(min_value=0, max_value=7),
    low=_lows,
    r0=st.sampled_from((_R0_WRAP,) + _R0_CARRY),
    value=st.integers(min_value=0, max_value=255),
)
@settings(max_examples=300)
def test_write_step_matches_the_spec_model(bank: int, low: int, r0: int, value: int) -> None:
    vdp = _vdp_at(r0, bank, low)
    vdp.write_port(0x98, value)
    assert vdp.vram[(bank << 14) | low] == value
    assert (vdp.addr, vdp.regs[14]) == _expected_step(bank, low, r0 != _R0_WRAP)


@given(
    bank=st.integers(min_value=0, max_value=7),
    low=_lows,
    r0=st.sampled_from((_R0_WRAP,) + _R0_CARRY),
)
@settings(max_examples=300)
def test_read_step_matches_the_spec_model(bank: int, low: int, r0: int) -> None:
    vdp = _vdp_at(r0, bank, low)
    vdp.vram[(bank << 14) | low] = 0x5A
    vdp.read_buf = 0xC3
    assert vdp.read_port(0x98) == 0xC3          # returns the read-ahead byte
    assert vdp.read_buf == 0x5A                 # refilled from the old address
    assert (vdp.addr, vdp.regs[14]) == _expected_step(bank, low, r0 != _R0_WRAP)


def test_last_byte_of_vram_wraps_to_zero_in_both_directions() -> None:
    w = _vdp_at(0x06, 7, 0x3FFF)
    w.write_port(0x98, 0xAA)
    r = _vdp_at(0x06, 7, 0x3FFF)
    r.read_port(0x98)
    for vdp in (w, r):
        assert vdp.addr == 0
        assert vdp.regs[14] == 0


def test_data_port_access_drops_only_the_control_pair() -> None:
    for access in ("write", "read"):
        vdp = V9938()
        vdp.write_port(0x9A, 0x75)
        vdp.write_port(0x99, 0x34)
        if access == "write":
            vdp.write_port(0x98, 0)
        else:
            vdp.read_port(0x98)
        assert vdp.latch is None, access
        assert vdp._pal_pending is True, access
