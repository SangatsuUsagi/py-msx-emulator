"""Tests for scanline-based run_frame() and level-based IRQ."""
from msx.machine import CYCLES_PER_FRAME, LINES_PER_FRAME, make_machine_msx2
from msx.vdp.v9938 import V9938
from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory

_DUMMY_ROM = bytes(32768)
_DUMMY_EXTROM = bytes(16384)


def _make_msx2() -> object:
    return make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)


# ---------------------------------------------------------------------------
# run_frame buffer size
# ---------------------------------------------------------------------------

def test_run_frame_returns_192_line_buffer() -> None:
    machine = _make_msx2()
    machine.vdp.regs[1] |= 0x40  # BL
    buf = machine.run_frame()
    assert len(buf) == 256 * 192


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


def test_irq_taken_on_instruction_after_ei() -> None:
    cpu = _make_cpu_with_rom([0xFB, 0x00])  # EI, NOP
    cpu.iff1 = False
    cpu.iff2 = False
    cpu.int_pending = False
    cpu.im = 1

    # EI: sets iff1, but int_pending=False → not taken
    cpu.step()
    assert cpu.registers.PC == 1

    # Set int_pending AFTER EI (as scanline loop would do)
    cpu.int_pending = True
    cpu.step()  # NOP — but interrupt taken first
    assert cpu.registers.PC == 0x0038  # vectored to IM1 ISR


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


def test_no_line_interrupt_when_r19_exceeds_display_height() -> None:
    machine = _make_msx2()
    vdp = machine.vdp
    assert isinstance(vdp, V9938)

    vdp.regs[0] |= 0x10   # IE1
    vdp.regs[1] |= 0x40   # BL
    vdp.regs[19] = 220    # > display_height (192)
    vdp.regs[23] = 0

    machine.run_frame(skip_render=True)

    # FH must NOT be set (220 > 192)
    assert not (vdp._status1 & 0x01)
