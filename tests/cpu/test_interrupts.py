from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory


def make_cpu(rom: list[int]) -> Z80:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.registers.SP = 0xFFFF
    return cpu


def test_int_mode1() -> None:
    cpu = make_cpu([0x00])  # NOP at 0x0000
    cpu.im = 1
    cpu.iff1 = True
    cpu.int_pending = True
    saved_pc = cpu.registers.PC
    cycles = cpu.step()
    assert cpu.registers.PC == 0x0038
    assert cycles == 13
    assert cpu.iff1 is False
    # previous PC should be on the stack
    sp = cpu.registers.SP
    lo = cpu.read_byte(sp)
    hi = cpu.read_byte((sp + 1) & 0xFFFF)
    assert (hi << 8) | lo == saved_pc


def test_int_mode1_ignored_when_di() -> None:
    cpu = make_cpu([0x00])
    cpu.im = 1
    cpu.iff1 = False
    cpu.int_pending = True
    cpu.step()
    assert cpu.registers.PC == 1  # NOP executed normally
    assert cpu.int_pending is True  # still pending


def test_int_mode2() -> None:
    rom = [0x00] * 32768
    # vector table at 0x3E00 (I=0x1F, so addr = 0x1FFF; read two bytes there)
    # Use I=0x00, addr=0x00FF, write jump target there
    rom[0x00FF] = 0x00
    rom[0x0100] = 0x20  # target PC = 0x2000
    cpu = make_cpu(rom)
    cpu.im = 2
    cpu.iff1 = True
    cpu.int_pending = True
    cpu.registers.I = 0x00
    cpu.step()
    assert cpu.registers.PC == 0x2000


def test_nmi_fires_when_di() -> None:
    cpu = make_cpu([0x00])
    cpu.iff1 = False
    cpu.nmi_pending = True
    cycles = cpu.step()
    assert cpu.registers.PC == 0x0066
    assert cpu.iff1 is False
    assert cycles == 11


def test_nmi_fires_when_ei() -> None:
    cpu = make_cpu([0x00])
    cpu.iff1 = True
    cpu.nmi_pending = True
    cpu.step()
    assert cpu.registers.PC == 0x0066
    assert cpu.iff1 is False


def test_nmi_saves_iff1_into_iff2_and_retn_restores() -> None:
    # NMI acceptance copies IFF1 into IFF2 and clears IFF1; RETN (IFF1<-IFF2)
    # at the end of the handler must restore the pre-NMI enable state.
    rom = [0x00] * 0x0068
    rom[0x0066] = 0xED
    rom[0x0067] = 0x45  # RETN at the NMI vector
    cpu = make_cpu(rom)
    cpu.iff1 = True
    cpu.iff2 = False
    cpu.nmi_pending = True
    cpu.step()  # accept NMI: IFF2 <- IFF1 (True), IFF1 <- False, PC <- 0x0066
    assert cpu.iff1 is False
    assert cpu.iff2 is True
    cpu.step()  # RETN: IFF1 <- IFF2
    assert cpu.iff1 is True


def test_halt_nop_loop() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.step()
    assert cpu.halted is True
    cycles = cpu.step()
    assert cpu.halted is True
    assert cycles == 4
    assert cpu.registers.PC == 1  # PC stays after HALT


def test_nmi_wakes_cpu_from_halt() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.step()
    assert cpu.halted is True
    cpu.nmi_pending = True
    cpu.step()
    assert cpu.halted is False
    assert cpu.registers.PC == 0x0066


def test_maskable_interrupt_wakes_cpu_from_halt() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.im = 1
    cpu.iff1 = True
    cpu.step()  # HALT
    assert cpu.halted is True
    cpu.int_pending = True
    cpu.step()
    assert cpu.halted is False
    assert cpu.registers.PC == 0x0038


def test_di_clears_iff1_and_iff2() -> None:
    cpu = make_cpu([0xF3])  # DI
    cpu.iff1 = True
    cpu.iff2 = True
    cpu.step()
    assert cpu.iff1 is False
    assert cpu.iff2 is False
    assert cpu.registers.PC == 1


def test_ei_sets_iff1_iff2_and_ei_pending() -> None:
    cpu = make_cpu([0xFB])  # EI
    cpu.iff1 = False
    cpu.iff2 = False
    cpu.step()
    assert cpu.iff1 is True
    assert cpu.iff2 is True
    assert cpu.ei_pending is True
    assert cpu.registers.PC == 1


# ===========================================================================
# EI one-instruction interrupt-enable delay (Z80 User Manual — EI: "the
# maskable interrupt is not accepted until after the instruction following
# EI is executed"; see openspec/specs/cpu-z80/spec.md, "EI interrupt-enable
# delay").
# ===========================================================================


def test_interrupt_after_ei_is_delayed_one_instruction() -> None:
    cpu = make_cpu([0xFB, 0x00])  # EI; NOP
    cpu.im = 1
    cpu.int_pending = True
    cpu.step()  # EI: iff1/iff2 -> True, ei_pending -> True
    assert cpu.registers.PC == 1
    assert cpu.int_pending is True  # not yet taken
    cpu.step()  # NOP: interrupt gated by ei_pending, executes normally
    assert cpu.registers.PC == 2
    assert cpu.int_pending is True  # still not taken
    assert cpu.ei_pending is False  # cleared once this step elapsed
    cpu.step()  # interrupt accepted on the next boundary
    assert cpu.registers.PC == 0x0038
    assert cpu.int_pending is False


def test_interrupt_not_taken_during_ei_itself() -> None:
    cpu = make_cpu([0xFB])  # EI
    cpu.im = 1
    cpu.iff1 = False
    cpu.int_pending = True
    cpu.step()  # EI executes normally; not vectored to 0x0038
    assert cpu.registers.PC == 1
    assert cpu.iff1 is True
    assert cpu.int_pending is True  # still pending, not consumed


# --- R refresh on interrupt acceptance and HALT (openMSX incR per M1) -------

def test_maskable_interrupt_acceptance_increments_r() -> None:
    for im in (1, 2):
        cpu = make_cpu([0x00])
        cpu.im = im
        cpu.iff1 = True
        cpu.int_pending = True
        cpu.registers.R = 0x7F
        cpu.step()
        assert cpu.registers.R == 0x00, f"IM {im}"  # bit 7 kept, low 7 bits wrap


def test_nmi_acceptance_increments_r() -> None:
    cpu = make_cpu([0x00])
    cpu.nmi_pending = True
    cpu.registers.R = 0x80
    cpu.step()
    assert cpu.registers.R == 0x81


def test_each_halted_nop_increments_r() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.step()
    cpu.registers.R = 0x10
    for _ in range(3):
        cpu.step()
    assert cpu.registers.R == 0x13


# --- step_halted_batch: equivalent to unrolled step() calls while halted ----
# (Machine._run_lines_fast's HALT-batching optimization; see openspec cpu-z80
# "Batched HALT execution is externally transparent".)


def _run_unrolled(cpu: Z80, budget: int) -> int:
    """Reference behavior step_halted_batch must match: keep calling step()
    (already halted, no interrupt pending) until at least `budget` T-states
    have elapsed, exactly as Machine's old un-batched loop did."""
    total = 0
    while total < budget:
        total += cpu.step()
    return total


def test_batch_matches_unrolled_steps_exact_multiple() -> None:
    cpu = make_cpu([0x76])  # HALT
    cpu.step()
    cpu.registers.R = 0x10
    budget = 4 * 5  # exactly 5 HALT-NOPs at stride 4 (m1_wait_states=0)
    n = cpu.step_halted_batch(budget)
    assert n == budget
    assert cpu.registers.R == 0x15


def test_batch_matches_unrolled_steps_non_multiple_budget() -> None:
    # budget not a multiple of the stride: batch must overshoot by the same
    # amount an unrolled `while total < budget: total += step()` loop would.
    reference = make_cpu([0x76])
    reference.step()
    reference.registers.R = 0x20
    budget = 4 * 3 + 1
    expected_total = _run_unrolled(reference, budget)

    batched = make_cpu([0x76])
    batched.step()
    batched.registers.R = 0x20
    n = batched.step_halted_batch(budget)

    assert n == expected_total
    assert batched.registers.R == reference.registers.R


def test_batch_respects_m1_wait_states() -> None:
    cpu = make_cpu([0x76])
    cpu.m1_wait_states = 1
    cpu.step()
    cpu.registers.R = 0x00
    budget = 7  # stride is 5 (4 + 1); ceil(7/5) = 2 steps
    n = cpu.step_halted_batch(budget)
    assert n == 10
    assert cpu.registers.R == 0x02


def test_batch_preserves_r_bit7_across_wraparound() -> None:
    cpu = make_cpu([0x76])
    cpu.step()
    cpu.registers.R = 0x80 | 0x7E  # bit 7 set, low bits near wraparound
    n = cpu.step_halted_batch(4 * 4)  # 4 HALT-NOPs: 0x7E -> 0x02 (mod 128)
    assert n == 16
    assert cpu.registers.R == 0x82


# --- MEMPTR (WZ) ---------------------------------------------------------


def test_interrupt_acceptance_sets_memptr_to_handler_address() -> None:
    # Spec "Interrupt acceptance sets MEMPTR to the handler address".
    cpu = make_cpu([0x00])
    cpu.im = 1
    cpu.iff1 = True
    cpu.int_pending = True
    cpu.step()
    assert cpu.registers.WZ == 0x0038


def test_im2_acceptance_sets_memptr_to_vector_target() -> None:
    rom = [0x00] * 32768
    rom[0x00FF] = 0x34
    rom[0x0100] = 0x12
    cpu = make_cpu(rom)
    cpu.im = 2
    cpu.iff1 = True
    cpu.int_pending = True
    cpu.registers.I = 0x00
    cpu.step()
    assert cpu.registers.WZ == 0x1234


def test_nmi_acceptance_sets_memptr() -> None:
    cpu = make_cpu([0x00])
    cpu.nmi_pending = True
    cpu.step()
    assert cpu.registers.WZ == 0x0066
