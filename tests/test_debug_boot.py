"""diag-debug-logger "Log VDP display-enable milestone" and "Log VDP screen
mode change": the VDPs record the stored R#0/R#1 values, and the host
writes the [BOOT] lines."""
from __future__ import annotations

import pytest

from host.debug_log import DebugLogFormatter
from msx.boundary import TRACE_DEBUG, TRACE_EVENT_VDP_MODE_REG
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.trace import TraceLog
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP


def _traced(vdp, kinds: int = TRACE_DEBUG) -> TraceLog:
    trace = TraceLog(kinds=kinds)
    vdp._trace = trace
    return trace


def _boot_lines(trace: TraceLog) -> list[str]:
    f = DebugLogFormatter()
    lines: list[str] = []
    for e in trace.drain():
        lines += f.feed(e)
    return lines


# ---------------------------------------------------------------------------
# Slot register write → production is silent (no diagnostic hook)
# ---------------------------------------------------------------------------

def test_slot_register_direct_write_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    # Production PPI writes mem.slot_register directly (msx/ppi.py:22); that
    # plain field assignment emits no diagnostic output on its own.
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    mem.set_slot_register(0xE0)
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# VDP R1 BL transition → [BOOT] log
# ---------------------------------------------------------------------------

def test_vdp_display_enable_logged() -> None:
    vdp = VDP()
    trace = _traced(vdp)
    # Write register 1 with BL=1 (0x40) via port 0x99 two-byte sequence
    vdp.write_port(0x99, 0x40)  # low byte
    vdp.write_port(0x99, 0x81)  # 0x80 | reg 1
    lines = _boot_lines(trace)
    assert any(ln.startswith("[BOOT]  VDP display enabled") for ln in lines)


def test_vdp_mode_change_logged() -> None:
    vdp = VDP()
    trace = _traced(vdp)
    # Set M1=1 (Text mode) via R1: value 0x10 = M1 bit
    vdp.write_port(0x99, 0x10)  # low byte
    vdp.write_port(0x99, 0x81)  # 0x80 | reg 1
    assert any("Text" in ln for ln in _boot_lines(trace))


def test_vdp_records_nothing_with_the_kind_off() -> None:
    vdp = VDP()
    trace = _traced(vdp, kinds=0)
    vdp.write_port(0x99, 0x40)
    vdp.write_port(0x99, 0x81)
    assert trace.drain() == ()


# ---------------------------------------------------------------------------
# V9938: the same events, from both register-write paths
# ---------------------------------------------------------------------------

def test_v9938_display_enable_via_port_99() -> None:
    vdp = V9938()
    trace = _traced(vdp)
    vdp.write_port(0x99, 0x40)  # R#1 := BL
    vdp.write_port(0x99, 0x81)
    assert any("display enabled" in ln for ln in _boot_lines(trace))


def test_v9938_display_enable_via_port_9b() -> None:
    vdp = V9938()
    trace = _traced(vdp)
    vdp.regs[17] = 0x80 | 1     # R#1, no auto-increment
    vdp.write_port(0x9B, 0x40)
    assert any("display enabled" in ln for ln in _boot_lines(trace))


def test_build_machine_wires_the_trace_into_the_v9938() -> None:
    from pathlib import Path

    from host.machine_config import (
        build_machine_from_spec,
        load_device_registry,
        load_machine_spec,
    )

    root = Path(__file__).resolve().parent.parent
    registry = load_device_registry(root / "config")
    spec = load_machine_spec("cbios_msx2_jp", root / "config", registry, root)
    machine = build_machine_from_spec(spec)
    assert isinstance(machine.vdp, V9938)
    assert machine.vdp._trace is machine._trace


def test_tms9918a_records_the_stored_register_value() -> None:
    vdp = VDP()
    trace = _traced(vdp)
    vdp.write_port(0x99, 0x06)          # R#0 := 0x06 -> stored 0x02
    vdp.write_port(0x99, 0x80)
    events = [(e.a, e.b) for e in trace.drain() if e.kind == TRACE_EVENT_VDP_MODE_REG]
    assert events == [(0, 0x02)]
