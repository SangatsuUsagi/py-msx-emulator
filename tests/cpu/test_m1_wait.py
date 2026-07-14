"""MSX M1 wait-state timing: +m1_wait_states T-states per opcode fetch (M1).

Each instruction is run on an identical CPU with m1_wait_states=0 (pure datasheet
Z80) and =1 (MSX); the cycle delta must equal the number of M1 cycles the
instruction performs.
"""

from msx.cpu.z80 import Z80
from msx.mapper import FlatMapper
from msx.memory import Memory


def _cpu(rom: list[int], wait: int) -> Z80:
    mem = Memory(
        rom=bytes(rom + [0] * (32768 - len(rom))),
        ram=bytearray(32768),
        _mapper=FlatMapper(None),
    )
    return Z80(read_byte=mem.read, write_byte=mem.write, m1_wait_states=wait)


def _delta(rom: list[int]) -> tuple[int, int]:
    """Return (datasheet_cycles, extra_cycles_from_one_wait_per_M1)."""
    base = _cpu(rom, 0).step()
    waited = _cpu(rom, 1).step()
    return base, waited - base


def test_default_is_pure_z80() -> None:
    # No wait configured: exact datasheet timing (NOP = 4, JP nn = 10).
    assert _cpu([0x00], 0).step() == 4
    assert _cpu([0xC3, 0x00, 0x00], 0).step() == 10


def test_unprefixed_one_m1() -> None:
    for rom in ([0x00], [0xC3, 0x00, 0x00], [0xD3, 0x10], [0x3E, 0x01]):
        base, extra = _delta(rom)
        assert extra == 1, f"{rom!r} expected +1, got +{extra}"


def test_cb_prefixed_two_m1() -> None:
    base, extra = _delta([0xCB, 0x00])          # RLC B
    assert extra == 2


def test_ed_prefixed_two_m1() -> None:
    base, extra = _delta([0xED, 0x44])          # NEG
    assert extra == 2


def test_dd_prefixed_two_m1() -> None:
    base, extra = _delta([0xDD, 0x21, 0x00, 0x40])   # LD IX,0x4000
    assert extra == 2


def test_fd_prefixed_two_m1() -> None:
    base, extra = _delta([0xFD, 0x21, 0x00, 0x40])   # LD IY,0x4000
    assert extra == 2


def test_ddcb_two_m1_not_four() -> None:
    # BIT 0,(IX+0): DD CB dd 46 — DD and CB are M1s; dd and 46 are operand reads.
    base, extra = _delta([0xDD, 0xCB, 0x00, 0x46])
    assert extra == 2


def test_dd_chain_one_wait_per_prefix() -> None:
    # DD DD NOP: three M1 fetches (DD, DD, NOP).
    base, extra = _delta([0xDD, 0xDD, 0x00])
    assert extra == 3
    assert base == 4        # NOP datasheet timing; the DDs are absorbed


def test_halt_gets_one_wait() -> None:
    # HALT then a halted step: the halted internal NOP is one M1.
    c = _cpu([0x76], 1)
    c.step()                # execute HALT
    assert c.halted
    assert c.step() == 4 + 1  # halted NOP + one M1 wait
