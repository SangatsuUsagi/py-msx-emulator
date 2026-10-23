"""Tests for TMS9918A run_frame() scanline splitting at vblank_start_line.

Mirrors tests/test_machine_scanline.py's V9938 coverage: the TMS9918A path now
splits its scanline loop the same way (see openspec/specs/machine-core/spec.md
"V9938 frame is rendered at the start of vertical blanking" and
openspec/specs/vdp-interrupts/spec.md "VBlank flag" / "IRQ line —
level-based VBlank IRQ (TMS9918A)").
"""
from pathlib import Path

import pytest

from host.state_files import load_state, save_state
from msx.cpu.z80 import Z80
from msx.diagnostics.logger import DebugLogger
from msx.machine import Machine
from msx.vdp._geometry import ACTIVE_H, OUTPUT_H, VideoStandard, pad_rows
from msx.vdp.vdp import VDP
from tests.factories import make_machine

NTSC_CYCLES = VideoStandard.NTSC.cycles_per_frame

_DUMMY_ROM = bytes(32768)
_MID_DISPLAY_PC = 0x1000  # well inside the pre-render segment (all-NOP ROM)


def _make_msx1() -> Machine:
    return make_machine(_DUMMY_ROM)


# ---------------------------------------------------------------------------
# Z80 interrupt acceptance, observed at the IM 1 vector
# ---------------------------------------------------------------------------

# Power-on leaves every page on slot 0 (this ROM), so the stack needs page 3
# switched to the RAM slot first: LD A,C0h; OUT (A8h),A; LD SP,F000h.
_PROLOGUE = bytes([0x3E, 0xC0, 0xD3, 0xA8, 0x31, 0x00, 0xF0])
_MAIN = len(_PROLOGUE)  # address `main` code starts at
_EI_LOOP = bytes([0xFB, 0x18, 0xFE])                    # EI; JR $
_ACK_AND_RETURN = bytes([0xDB, 0x99, 0xFB, 0xC9])        # IN A,(99h); EI; RET


def _rom(main: bytes, handler: bytes) -> bytes:
    rom = bytearray(32768)
    code = _PROLOGUE + main
    rom[0:len(code)] = code
    rom[0x38:0x38 + len(handler)] = handler
    return bytes(rom)


class _Acceptances:
    """Record every arrival at the 0038h vector: the frame-relative T-state it
    happened at (exact, per instruction), the stacked return address, and S#0's
    F bit at that moment. Z80 is a slots dataclass, so `step` is patched at the
    class level and restored on exit."""

    def __init__(self, machine: Machine) -> None:
        self.machine = machine
        self.hits: list[tuple[int, int, int]] = []
        self._t = 0
        self._orig = Z80.step

    def __enter__(self) -> "_Acceptances":
        orig = self._orig
        machine = self.machine

        def step(cpu: Z80) -> int:
            n = orig(cpu)
            self._t += n
            if cpu.registers.PC == 0x0038:
                sp = cpu.registers.SP
                ret = machine.memory.read(sp) | (machine.memory.read((sp + 1) & 0xFFFF) << 8)
                self.hits.append((self._t, ret, machine.vdp.status & 0x80))
            return n

        Z80.step = step  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        Z80.step = self._orig  # type: ignore[method-assign]


def _set_r1(vdp: VDP, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x81)


# ---------------------------------------------------------------------------
# VBlank IRQ: raised at the display_height boundary, not at the end
# ---------------------------------------------------------------------------

def test_vblank_irq_taken_before_post_display_height_segment() -> None:
    """With IE0 set, the interrupt is accepted at the start of vertical
    blanking (~192/262 into the frame), with F visible to the handler, while
    the remaining scanlines of the same run_frame() call are still running."""
    machine = make_machine(_rom(_EI_LOOP, _ACK_AND_RETURN))
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    _set_r1(vdp, 0x60)  # BL=1, IE0=1

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    assert len(acc.hits) == 1
    t, _ret, f = acc.hits[0]
    assert f == 0x80
    assert 0.70 < t / NTSC_CYCLES < 0.78


def test_vblank_isr_vram_write_lands_on_the_next_frame() -> None:
    """VRAM written by the interrupt handler must not appear in the frame that
    was being displayed when the interrupt fired -- same guarantee the V9938
    path already has (tests/test_machine_scanline.py)."""
    # Handler: VRAM 0x0800 := 0x80 (write address 0x4800), ack S#0, EI; RET.
    handler = bytes([
        0x3E, 0x00, 0xD3, 0x99, 0x3E, 0x48, 0xD3, 0x99,
        0x3E, 0x80, 0xD3, 0x98,
    ]) + _ACK_AND_RETURN
    machine = make_machine(_rom(_EI_LOOP, handler))
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    # GRAPHIC1 (SCREEN 1): name table @0, pattern table @0x800, colour table
    # @0x40 -- distinct, non-overlapping addresses.
    vdp.regs[3] = 1  # colour table base = 1 << 6 = 0x40
    vdp.regs[4] = 1  # pattern table base = (1 & 7) << 11 = 0x800
    _set_r1(vdp, 0x60)  # BL=1, IE0=1 (M1=M2=0 -> GRAPHIC1)
    vdp.vram[0x40] = 0xF1  # tile-group 0 colour: fg=15, bg=1

    first = machine.run_frame()
    second = machine.run_frame()

    top_left = pad_rows(ACTIVE_H) * 256
    assert len(first) == 256 * OUTPUT_H
    assert first[top_left] == 1
    assert second[top_left] == 15


# ---------------------------------------------------------------------------
# Level-based IRQ line (openspec vdp-interrupts "IRQ line — level-based
# VBlank IRQ (TMS9918A)")
# ---------------------------------------------------------------------------

def test_setting_ie0_while_f_pending_is_taken_immediately() -> None:
    """IE0 0->1 with F already set raises the line on the spot: the interrupt
    is accepted right after the OUT that completes the R#1 write, not at the
    next vertical blanking (the case openMSX's VDP.cc R#1 handler calls out)."""
    main = bytes([
        0xFB,              # +0: EI
        0x3E, 0x60,        # +1: LD A,60h
        0xD3, 0x99,        # +3: OUT (99h),A
        0x3E, 0x81,        # +5: LD A,81h
        0xD3, 0x99,        # +7: OUT (99h),A   -> R#1 := 60h (IE0=1)
        0x18, 0xFE,        # +9: JR $
    ])
    machine = make_machine(_rom(main, _ACK_AND_RETURN))
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x40   # BL=1, IE0=0
    vdp.status = 0x80    # F pending from an earlier VBlank

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    t, ret, f = acc.hits[0]
    assert ret == _MAIN + 9
    assert f == 0x80
    assert t < 100


def test_status_read_before_ei_drops_the_pending_irq() -> None:
    """F && IE0 asserted, but S#0 is read while interrupts are still disabled:
    the line drops, so EI does not take a stale interrupt -- the next one is
    the following vertical blanking."""
    main = bytes([
        0xDB, 0x99,        # +0: IN A,(99h)   (DI state from reset)
        0xFB,              # +2: EI
        0x18, 0xFE,        # +3: JR $
    ])
    machine = make_machine(_rom(main, _ACK_AND_RETURN))
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60
    vdp.status = 0x80
    vdp.update_irq()
    assert vdp.irq is True

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    assert len(acc.hits) == 1
    assert acc.hits[0][0] / NTSC_CYCLES > 0.70


def test_line_still_asserted_after_acceptance_retriggers() -> None:
    """A handler that re-enables interrupts without acknowledging S#0 leaves
    the line high, so the interrupt is taken again straight after EI; RET."""
    machine = make_machine(_rom(_EI_LOOP, bytes([0xFB, 0xC9])))  # EI; RET
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60
    vdp.status = 0x80
    vdp.update_irq()

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    assert len(acc.hits) > 1
    assert acc.hits[1][0] - acc.hits[0][0] < 50


def test_state_restore_rederives_irq_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = _make_msx1()
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60
    vdp.status = 0x80
    path = save_state(machine, bytes(256 * OUTPUT_H * 3), "irq")

    vdp.irq = False  # stale pre-restore value
    load_state(machine, path)

    assert vdp.irq is True


def test_state_restore_drops_stale_irq_when_f_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = _make_msx1()
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60
    vdp.status = 0x00
    path = save_state(machine, bytes(256 * OUTPUT_H * 3), "irq-low")

    vdp.irq = True  # stale pre-restore value
    load_state(machine, path)

    assert vdp.irq is False


# ---------------------------------------------------------------------------
# Every scanline-loop arm mirrors vdp.irq onto the CPU line (debug and logged
# arms; the tests above run on the fast arm)
# ---------------------------------------------------------------------------

_UNREACHED_PC = 0x7FFF  # breakpoint address these test ROMs never execute


def _machine_on_arm(rom: bytes, arm: str) -> Machine:
    if arm == "logged":
        return make_machine(rom, logger=DebugLogger())
    machine = make_machine(rom)
    machine.set_pause_hook(lambda reason, pc: None)
    machine.set_breakpoints([_UNREACHED_PC])
    return machine


@pytest.mark.parametrize("arm", ["debug", "logged"])
def test_vblank_irq_taken_on_debug_and_logged_arms(arm: str) -> None:
    machine = _machine_on_arm(_rom(_EI_LOOP, _ACK_AND_RETURN), arm)
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    _set_r1(vdp, 0x60)

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    assert not machine.is_paused
    assert len(acc.hits) == 1
    t, _ret, f = acc.hits[0]
    assert f == 0x80
    assert 0.70 < t / NTSC_CYCLES < 0.78


@pytest.mark.parametrize("arm", ["debug", "logged"])
def test_status_read_drops_irq_on_debug_and_logged_arms(arm: str) -> None:
    main = bytes([0xDB, 0x99, 0xFB, 0x18, 0xFE])  # IN A,(99h); EI; JR $
    machine = _machine_on_arm(_rom(main, _ACK_AND_RETURN), arm)
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x60
    vdp.status = 0x80
    vdp.update_irq()

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    assert len(acc.hits) == 1
    assert acc.hits[0][0] / NTSC_CYCLES > 0.70


@pytest.mark.parametrize("arm", ["debug", "logged"])
def test_setting_ie0_while_f_pending_on_debug_and_logged_arms(arm: str) -> None:
    main = bytes([
        0xFB, 0x3E, 0x60, 0xD3, 0x99, 0x3E, 0x81, 0xD3, 0x99,  # EI; R#1 := 60h
        0x18, 0xFE,                                            # JR $
    ])
    machine = _machine_on_arm(_rom(main, _ACK_AND_RETURN), arm)
    vdp = machine.vdp
    assert isinstance(vdp, VDP)
    vdp.regs[1] = 0x40
    vdp.status = 0x80

    with _Acceptances(machine) as acc:
        machine.run_frame(skip_render=True)

    t, ret, f = acc.hits[0]
    assert ret == _MAIN + 9
    assert f == 0x80
    assert t < 100


# ---------------------------------------------------------------------------
# Pause / Ctrl-C mid-display still stops the frame (TMS9918A now splits too)
# ---------------------------------------------------------------------------

def test_pause_during_active_display_skips_the_rest_of_the_frame() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x40  # BL=1

    machine.set_pause_hook(lambda reason, pc: None)
    machine.set_breakpoints([_MID_DISPLAY_PC])
    machine.run_frame(skip_render=True)

    assert machine.is_paused
    assert machine.cycle_count < machine.cycles_per_frame


def test_ctrl_c_during_active_display_skips_the_vblank_segment() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x40  # BL=1

    calls: list[int] = []
    # Z80 is a slots dataclass (no per-instance __dict__), so `step` can only
    # be overridden at the class level -- restored in `finally`, mirroring
    # how this repo's own investigation tooling patches VDP.write_port.
    orig_step = Z80.step

    def patched_step(self: Z80) -> int:
        if self.registers.PC == _MID_DISPLAY_PC:
            raise KeyboardInterrupt
        return orig_step(self)

    Z80.step = patched_step
    try:
        # A pause hook is what makes _on_frame_interrupt handle Ctrl-C instead
        # of re-raising; no breakpoints, so this runs on the fast loop.
        machine.set_pause_hook(lambda reason, pc: calls.append(pc))
        machine.run_frame(skip_render=True)
    finally:
        Z80.step = orig_step

    assert calls
    assert machine.cycle_count < machine.cycles_per_frame
def test_break_during_display_does_not_raise_vblank() -> None:
    # A frame cut short by a breakpoint never reached vertical blanking, so
    # F and the /INT level stay clear -- the same as on the V9938.
    machine = make_machine(b"\x00" * 0x8000)
    machine.vdp.regs[1] = 0x60             # BL + IE0
    machine.vdp.status = 0x00
    machine.set_pause_hook(lambda *_: None)
    machine.set_breakpoints([0x1000])      # reached mid-frame by the NOP sled

    machine.run_frame()

    assert machine.cpu.registers.PC == 0x1000
    assert not (machine.vdp.status & 0x80)
    assert machine.vdp.irq is False


def test_complete_frame_still_raises_vblank() -> None:
    machine = make_machine(b"\x00" * 0x8000)
    machine.vdp.regs[1] = 0x40
    machine.vdp.status = 0x00

    machine.run_frame()

    assert machine.vdp.status & 0x80


def test_break_with_skip_render_does_not_raise_vblank() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x60             # BL + IE0
    machine.vdp.status = 0x00
    machine.set_pause_hook(lambda reason, pc: None)
    machine.set_breakpoints([_MID_DISPLAY_PC])

    machine.run_frame(skip_render=True)

    assert machine.is_paused
    assert not (machine.vdp.status & 0x80)
    assert machine.vdp.irq is False


def test_ctrl_c_during_active_display_does_not_raise_vblank() -> None:
    machine = _make_msx1()
    machine.vdp.regs[1] = 0x60             # BL + IE0
    machine.vdp.status = 0x00
    orig_step = Z80.step

    def patched_step(self: Z80) -> int:
        if self.registers.PC == _MID_DISPLAY_PC:
            raise KeyboardInterrupt
        return orig_step(self)

    Z80.step = patched_step  # type: ignore[method-assign]
    try:
        machine.set_pause_hook(lambda reason, pc: None)
        buf = machine.run_frame()
    finally:
        Z80.step = orig_step  # type: ignore[method-assign]

    assert len(buf) == 256 * OUTPUT_H      # the cut-short frame is still rendered
    assert machine.cycle_count < machine.cycles_per_frame
    assert not (machine.vdp.status & 0x80)
    assert machine.vdp.irq is False
