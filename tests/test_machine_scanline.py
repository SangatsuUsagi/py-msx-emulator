"""Tests for scanline-based run_frame() and level-based IRQ."""
from msx.cpu.z80 import Z80
from msx.machine import CYCLES_PER_FRAME, Machine
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.vdp._geometry import ACTIVE_H, OUTPUT_H, pad_rows
from msx.vdp.v9938 import V9938
from tests.factories import make_machine_msx2

_DUMMY_ROM = bytes(32768)
_DUMMY_EXTROM = bytes(16384)


def _make_msx2() -> Machine:
    return make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)


# ---------------------------------------------------------------------------
# run_frame buffer size
# ---------------------------------------------------------------------------

def test_run_frame_returns_212_line_buffer() -> None:
    machine = _make_msx2()
    machine.vdp.regs[1] |= 0x40  # BL
    buf = machine.run_frame()
    # A 192-line frame (LN=0) is padded to the constant 212-line output height.
    assert len(buf) == 256 * 212


def test_run_frame_skip_render_returns_empty_buffer() -> None:
    machine = _make_msx2()
    buf = machine.run_frame(skip_render=True)
    assert len(buf) == 0


# ---------------------------------------------------------------------------
# VBlank via irq_pending()
# ---------------------------------------------------------------------------

def test_vblank_irq_pending_after_run_frame_with_ie0() -> None:
    machine = _make_msx2()
    machine.vdp.regs[1] |= 0x40 | 0x20  # BL + IE0
    machine.run_frame(skip_render=True)
    assert machine.vdp.irq_pending()


def test_vblank_irq_not_pending_when_ie0_clear() -> None:
    machine = _make_msx2()
    machine.vdp.regs[1] |= 0x40  # BL only
    machine.run_frame(skip_render=True)
    assert not machine.vdp.irq_pending()


# ---------------------------------------------------------------------------
# T-states per frame: approximately CYCLES_PER_FRAME
# ---------------------------------------------------------------------------

def test_total_tstates_approximately_cycles_per_frame() -> None:
    machine = _make_msx2()
    before = machine.cycle_count
    machine.run_frame(skip_render=True)
    elapsed = machine.cycle_count - before
    # Allow small overshoot due to instruction granularity (max 22 T-states)
    assert CYCLES_PER_FRAME <= elapsed <= CYCLES_PER_FRAME + 22


# ---------------------------------------------------------------------------
# EI / IFF timing: IRQ not taken on EI, taken on instruction after EI
# ---------------------------------------------------------------------------

def _make_cpu_with_rom(rom: list[int]) -> Z80:
    mem = Memory(
        rom=bytes(rom + [0x00] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFF0
    return cpu


def test_irq_not_taken_on_ei_itself() -> None:
    # ROM: [NOP, EI, NOP, ...]
    # 0x00=NOP, 0xFB=EI
    cpu = _make_cpu_with_rom([0x00, 0xFB, 0x00])
    cpu.iff1 = False
    cpu.iff2 = False
    cpu.int_pending = False
    cpu.im = 1

    # Execute NOP
    cpu.step()
    assert cpu.registers.PC == 1

    # Set IRQ pending, execute EI — interrupt must NOT be taken during EI
    cpu.int_pending = True
    cpu.step()  # EI
    assert cpu.registers.PC == 2  # EI advanced PC normally, not vectored to 0x0038
    assert cpu.iff1 is True


def test_irq_delayed_until_after_instruction_following_ei() -> None:
    # The Z80 delays interrupt enabling by one instruction: the instruction
    # immediately following EI is always executed before any pending interrupt
    # is accepted. (C-BIOS's interrupt epilogue relies on this — the RET after
    # EI must run before the next IRQ, otherwise the stack runs away.)
    cpu = _make_cpu_with_rom([0xFB, 0x00, 0x00])  # EI, NOP, NOP
    cpu.iff1 = False
    cpu.iff2 = False
    cpu.int_pending = False
    cpu.im = 1

    # EI: sets iff1, but int_pending=False → not taken
    cpu.step()
    assert cpu.registers.PC == 1
    assert cpu.iff1 is True

    # Set int_pending AFTER EI (as the scanline loop would do)
    cpu.int_pending = True
    cpu.step()  # the NOP after EI executes first — interrupt still delayed
    assert cpu.registers.PC == 2

    cpu.step()  # now the interrupt is accepted
    assert cpu.registers.PC == 0x0038  # vectored to IM1 ISR


def test_irq_withdrawn_before_acceptance_is_not_taken() -> None:
    """Machine's per-instruction `cpu.int_pending = vdp9938.irq` mirror is
    level-sensitive: it can withdraw a pending interrupt the same way it
    raises one, before the CPU ever accepts it. Regression guard for the
    allium DeassertMaskableInterrupt rule (cpu_z80.allium)."""
    cpu = _make_cpu_with_rom([0x00])  # NOP
    cpu.iff1 = True
    cpu.iff2 = True
    cpu.im = 1

    # VDP IRQ line raised (as begin_scanline() would do)...
    cpu.int_pending = True
    # ...then withdrawn again before the next step() — same instruction
    # boundary, as the scanline loop's `cpu.int_pending = vdp9938.irq`
    # mirror does every instruction, not a one-way assert.
    cpu.int_pending = False

    cpu.step()  # NOP executes; no interrupt was accepted
    assert cpu.registers.PC == 1  # not vectored to 0x0038


def test_irq_not_taken_when_iff1_false() -> None:
    cpu = _make_cpu_with_rom([0x00])
    cpu.iff1 = False
    cpu.int_pending = True
    cpu.im = 1
    cpu.step()
    assert cpu.registers.PC == 1  # NOP executed, interrupt masked


# ---------------------------------------------------------------------------
# Line interrupt: FH set for valid R#19 (machine-level)
# ---------------------------------------------------------------------------

def test_line_interrupt_fh_set_at_r19_line() -> None:
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)

    vdp.regs[0] |= 0x10   # IE1
    vdp.regs[1] |= 0x40   # BL
    vdp.regs[19] = 50
    vdp.regs[23] = 0
    machine.cpu.iff1 = True
    machine.cpu.im = 1

    initial_sp = machine.cpu.registers.SP
    machine.run_frame(skip_render=True)

    # FH must be set (begin_scanline(50) fired)
    assert vdp._status1 & 0x01
    # Z80 must have taken at least one interrupt (SP decreased from push)
    assert machine.cpu.registers.SP < initial_sp


def test_line_interrupt_fires_in_border_region() -> None:
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)

    vdp.regs[0] |= 0x10   # IE1
    vdp.regs[1] |= 0x40   # BL
    vdp.regs[19] = 220    # border/vblank region (beyond the 192-line display)
    vdp.regs[23] = 0

    machine.run_frame(skip_render=True)

    # The line interrupt counts the whole field: R#19=220 still matches raster
    # line 220 even though it is outside the active display.
    assert vdp._status1 & 0x01


# ---------------------------------------------------------------------------
# Render point: start of vertical blanking, before the VBlank ISR runs
# ---------------------------------------------------------------------------

# PC reached roughly 90 scanlines into the active display: the dummy ROM is all
# NOP, so PC advances one byte per 5 T-states and 0x1000 lands well inside the
# first (pre-render) segment without being in its first few lines.
_MID_DISPLAY_PC = 0x1000


def test_vblank_isr_vram_write_lands_on_the_next_frame() -> None:
    """VRAM written after the VBlank IRQ must not appear in the frame that was
    being displayed when the interrupt fired."""
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)
    # SCREEN 5 (G4), display enabled; every other register keeps its reset value
    # (bitmap base at VRAM 0, border index 0).
    vdp.regs[0] = 0x06  # R#0 M4|M3 -> G4
    vdp.regs[1] = 0x40  # R#1 BL: display enabled

    def on_scanline(line: int) -> None:
        if line == vdp.vblank_start_line + 1:
            # End of the first scanline after the render split — the VBlank ISR
            # has had one line to run, and its VRAM writes must not be visible
            # in the frame that was just rendered.
            vdp.vram[0] = 0x55  # G4: two pixels of palette index 5

    vdp.on_scanline = on_scanline

    first = machine.run_frame()
    second = machine.run_frame()

    # A 192-line frame is centred in the constant OUTPUT_H-line output buffer.
    top_left = pad_rows(ACTIVE_H) * 256
    assert len(first) == 256 * OUTPUT_H
    assert first[top_left] == 0
    assert second[top_left] == 5


def test_pause_during_active_display_skips_the_rest_of_the_frame() -> None:
    """A breakpoint mid-display stops the frame at the break point: the loop
    neither finishes the active display nor runs the VBlank segment."""
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)

    lines: list[int] = []
    vdp.on_scanline = lines.append

    machine.set_pause_hook(lambda reason, pc: None)
    machine.set_breakpoints([_MID_DISPLAY_PC])
    machine.run_frame(skip_render=True)

    assert machine.is_paused
    # Non-vacuous: the frame really did run into the active display, and then
    # stopped there — no scanline of the VBlank segment was begun.
    assert lines
    assert max(lines) < vdp.vblank_start_line
    assert machine.cycle_count < machine.cycles_per_frame


def test_ctrl_c_during_active_display_skips_the_vblank_segment() -> None:
    """Ctrl-C reaches the loops as an exception, not through the pause hook's
    _pause_requested flag; it must still stop the frame instead of falling
    through to the post-render VBlank segment."""
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)

    lines: list[int] = []
    interrupt_at = 100

    def on_scanline(line: int) -> None:
        lines.append(line)
        if line == interrupt_at:
            raise KeyboardInterrupt

    vdp.on_scanline = on_scanline
    # A pause hook is what makes _on_frame_interrupt handle Ctrl-C instead of
    # re-raising; no breakpoints, so this runs on the fast loop.
    machine.set_pause_hook(lambda reason, pc: None)
    machine.run_frame(skip_render=True)

    assert max(lines) == interrupt_at
