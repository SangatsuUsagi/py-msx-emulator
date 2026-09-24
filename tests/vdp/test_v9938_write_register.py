"""V9938._write_register: the one register store behind ports 0x99 and 0x9B.

allium/v9938.allium WriteControlPort / WriteIndirectRegister: R#0-R#27 are
stored ANDed with their implemented-bit mask (R#24-R#27, not V9938
registers, as written), with the same side effects and logger value on both
ports; R#32-R#46 go to the command engine; other numbers are ignored.
"""
from __future__ import annotations

import pytest

from msx.diagnostics.logger import DebugLogger
from msx.vdp.v9938 import V9938


def _via_99(vdp: V9938, reg: int, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


def _via_9b(vdp: V9938, reg: int, value: int) -> None:
    vdp.regs[17] = 0x80 | reg           # AII set: no auto-increment
    vdp.write_port(0x9B, value)


def _recording_logger(seen: list[tuple[int, int]]) -> DebugLogger:
    class _Rec(DebugLogger):
        def on_vdp_reg_write(self, reg: int, value: int, frame: int) -> None:
            seen.append((reg, value))

    return _Rec()


# R#14 relocates the pointer and R#17 is the 0x9B pointer itself; both are
# covered elsewhere, and writing R#17 through 0x9B would compare a different
# thing.
_PLAIN_REGS = [r for r in range(28) if r not in (14, 17)]


@pytest.mark.parametrize("reg", _PLAIN_REGS)
@pytest.mark.parametrize("value", [0xFF, 0x5A])
def test_both_ports_store_the_same_masked_value(reg: int, value: int) -> None:
    a = V9938()
    b = V9938()
    _via_99(a, reg, value)
    _via_9b(b, reg, value)
    assert a.regs[reg] == b.regs[reg]


@pytest.mark.parametrize("reg", [24, 25, 26, 27])
def test_non_v9938_registers_24_to_27_are_stored_as_written(reg: int) -> None:
    for write in (_via_99, _via_9b):
        vdp = V9938()
        write(vdp, reg, 0xA5)
        assert vdp.regs[reg] == 0xA5


@pytest.mark.parametrize("reg", [28, 29, 30, 31, 47, 63])
def test_unassigned_register_numbers_are_ignored(reg: int) -> None:
    for write in (_via_99, _via_9b):
        vdp = V9938()
        regs_before = list(vdp.regs)
        cmd_before = list(vdp.cmd_regs)
        write(vdp, reg, 0xA5)
        assert vdp.regs[:17] == regs_before[:17]
        assert vdp.regs[18:] == regs_before[18:]
        assert vdp.cmd_regs == cmd_before


@pytest.mark.parametrize("write", [_via_99, _via_9b])
def test_command_registers_reach_the_engine_from_both_ports(write) -> None:
    vdp = V9938()
    write(vdp, 36, 0x12)                # DX low
    assert vdp.cmd_regs[36 - 32] == 0x12


@pytest.mark.parametrize("write", [_via_99, _via_9b])
def test_logger_sees_the_masked_value_on_both_ports(write) -> None:
    seen: list[tuple[int, int]] = []
    vdp = V9938(_logger=_recording_logger(seen))
    write(vdp, 10, 0xFF)                # R#10 mask 0x07
    assert (10, 0x07) in seen


@pytest.mark.parametrize("write", [_via_99, _via_9b])
def test_ie0_write_rederives_irq_on_both_ports(write) -> None:
    vdp = V9938()
    vdp.status = 0x80                   # F pending
    write(vdp, 1, 0x60)                 # IE0 on
    assert vdp.irq is True
    write(vdp, 1, 0x40)                 # IE0 off
    assert vdp.irq is False


def test_indirect_auto_increment_after_the_store() -> None:
    vdp = V9938()
    vdp.regs[17] = 9                    # R#9, auto-increment on
    vdp.write_port(0x9B, 0xFF)          # R#9 mask 0xBF
    vdp.write_port(0x9B, 0xFF)          # R#10 mask 0x07
    assert (vdp.regs[9], vdp.regs[10]) == (0xBF, 0x07)
    assert vdp.regs[17] & 0x3F == 11


def test_indirect_pointer_wraps_from_63_to_0() -> None:
    vdp = V9938()
    vdp.regs[17] = 63
    vdp.write_port(0x9B, 0x00)          # R#63 ignored
    assert vdp.regs[17] & 0x3F == 0
