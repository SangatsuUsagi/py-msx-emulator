import pytest
from msx.cpu.z80 import Z80
from msx.input import InputState
from msx.io import IOBus
from msx.machine import CYCLES_PER_FRAME, Machine, make_machine
from msx.mapper import Ascii8Mapper, Ascii16Mapper, FlatMapper, KonamiMapper
from msx.memory import Memory
from msx.vdp.vdp import VDP

# 32 KB BIOS ROM filled with NOP (0x00), then HALT (0x76) at offset 0
_NOP_ROM = bytes(32768)
_HALT_ROM = bytes([0x76] + [0x00] * 32767)


def _make_machine(rom: bytes = _NOP_ROM) -> Machine:
    return make_machine(rom=rom)


def test_memory_connected_to_cpu() -> None:
    rom = bytes([0x3E, 0xAB] + [0x00] * 32766)  # LD A, 0xAB at 0x0000
    m = _make_machine(rom=rom)
    assert m.cpu.read_byte(0x0000) == 0x3E
    assert m.memory.read(0x0001) == 0xAB


def test_step_returns_positive_t_states() -> None:
    rom = bytes([0x00] * 32768)  # NOP
    m = _make_machine(rom=rom)
    t = m.step()
    assert t > 0


def test_step_nop_returns_4() -> None:
    rom = bytes([0x00] * 32768)  # NOP = 4 T-states
    m = _make_machine(rom=rom)
    assert m.step() == 4


def test_run_frame_returns_correct_size() -> None:
    m = _make_machine()
    buf = m.run_frame()
    assert len(buf) == 256 * 192


def test_run_frame_skip_render_returns_empty_buffer() -> None:
    m = _make_machine()
    buf = m.run_frame(skip_render=True)
    assert len(buf) == 0


def test_run_frame_skip_render_still_fires_vblank() -> None:
    rom = bytes([0xFB] + [0x00] * 32767)  # EI then NOP — enable interrupts
    m = _make_machine(rom=rom)
    m.vdp.regs[1] = 0x60  # BL=1, IE=1
    m.run_frame(skip_render=True)
    assert m.cpu.int_pending


def test_run_frame_executes_at_least_cycles_per_frame() -> None:
    # NOP ROM: each instruction is 4 T-states, so at least CYCLES_PER_FRAME // 4 NOPs
    # must execute, advancing PC by that many positions.
    m = _make_machine(rom=_NOP_ROM)
    m.run_frame()
    assert m.cpu.registers.PC >= CYCLES_PER_FRAME // 4


def test_vblank_sets_int_pending_when_ie_set() -> None:
    m = _make_machine()
    m.vdp.regs[1] = 0x60  # BL=1, IE=1
    m.run_frame()
    assert m.cpu.int_pending


def test_reset_clears_cpu_state() -> None:
    m = _make_machine()
    m.cpu.iff1 = True
    m.cpu.registers.PC = 0x1234
    m.reset()
    assert m.cpu.registers.PC == 0x0000
    assert not m.cpu.iff1


def test_reset_clears_vdp_status() -> None:
    m = _make_machine()
    m.vdp.status = 0xFF
    m.reset()
    assert m.vdp.status == 0


def test_make_machine_io_routes_vdp_write() -> None:
    m = _make_machine()
    m.io.write_port(0x99, 0x00)  # VDP address latch byte 1
    m.io.write_port(0x99, 0x40)  # VDP address = 0x0000, write mode
    m.io.write_port(0x98, 0xAB)  # write to VRAM
    assert m.vdp.vram[0x0000] == 0xAB


def test_make_machine_io_routes_psg() -> None:
    m = _make_machine()
    m.io.write_port(0xA0, 0x07)   # latch reg 7
    m.io.write_port(0xA1, 0x38)   # write 0x38 to reg 7
    assert m.io.read_port(0xA2) == 0x38


def test_make_machine_exposes_input_state() -> None:
    m = _make_machine()
    assert isinstance(m.input, InputState)
    assert all(row == 0xFF for row in m.input.matrix)
    assert m.input.joy1 == 0x3F
    assert m.input.joy2 == 0x3F


def test_make_machine_default_mapper_is_flat() -> None:
    # Explicit Mirrored (no auto-detection needed)
    cart = bytes([0xAB] + [0] * 32767)
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="Mirrored")
    assert isinstance(m.memory._mapper, FlatMapper)


def test_make_machine_mapper_ascii8() -> None:
    m = make_machine(rom=_NOP_ROM, mapper="ASCII8")
    assert isinstance(m.memory._mapper, Ascii8Mapper)


def test_make_machine_mapper_ascii16() -> None:
    m = make_machine(rom=_NOP_ROM, mapper="ASCII16")
    assert isinstance(m.memory._mapper, Ascii16Mapper)


def test_make_machine_mapper_konami() -> None:
    m = make_machine(rom=_NOP_ROM, mapper="Konami")
    assert isinstance(m.memory._mapper, KonamiMapper)


# ---------------------------------------------------------------------------
# auto-detection
# ---------------------------------------------------------------------------

def test_auto_no_cartridge_uses_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "_db", {})
    m = make_machine(rom=_NOP_ROM, mapper="auto")
    assert isinstance(m.memory._mapper, FlatMapper)
    assert m.scc is None


def test_auto_unknown_cartridge_falls_back_to_mirrored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import msx.romdb as romdb
    monkeypatch.setattr(romdb, "_db", {})  # empty DB → always miss
    cart = bytes([0x01, 0x02])
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, FlatMapper)
    assert m.scc is None
    assert "not found in ROM database" in capsys.readouterr().err


def test_auto_unsupported_db_mapper_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib
    import msx.romdb as romdb
    cart = b"\xDE\xAD"
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "Page2"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, FlatMapper)
    stderr = capsys.readouterr().err
    assert "Page2" in stderr


def test_auto_known_konamisco_selects_scc_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import msx.romdb as romdb
    from msx.mapper import KonamiSCCMapper
    cart = bytes(65536)  # dummy 64 KB
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "KonamiSCC"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, KonamiSCCMapper)
    assert m.scc is not None


def test_auto_known_konami_selects_konami_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import msx.romdb as romdb
    cart = bytes(65536)
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "Konami"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, KonamiMapper)
    assert m.scc is None


# ---------------------------------------------------------------------------
# Slot 2 cartridge wiring
# ---------------------------------------------------------------------------

def test_make_machine_no_cartridge2_slot2_open_bus() -> None:
    m = make_machine(rom=_NOP_ROM)
    # page 1 → slot 2: slot_register bits 3:2 = 0b10 → 0x08
    m.memory.slot_register = 0x08
    assert m.memory.read(0x4000) == 0xFF


def test_make_machine_cartridge2_wired_to_slot2() -> None:
    cart2 = b"\xBB" + b"\x00" * 32767
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2="Mirrored")
    # page 1 → slot 2
    m.memory.slot_register = 0x08
    assert m.memory.read(0x4000) == 0xBB


def test_make_machine_mapper2_konamisco_falls_back_to_konami(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib
    import msx.romdb as romdb
    cart2 = bytes(65536)
    sha1 = hashlib.sha1(cart2).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "KonamiSCC"}})
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2="auto")
    assert isinstance(m.memory._mapper2, KonamiMapper)
    assert "KonamiSCC" in capsys.readouterr().err
