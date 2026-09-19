from pathlib import Path

import pytest

from msx.input import InputState
from msx.machine import CYCLES_PER_FRAME, Machine
from msx.machine_loader import MachineLoadError
from msx.mapper import Ascii8Mapper, Ascii16Mapper, FlatMapper, KonamiMapper, KonamiSCCMapper
from msx.romdb import RomDbEntry
from tests.factories import make_machine

# 32 KB BIOS ROM filled with NOP (0x00), then HALT (0x76) at offset 0
_NOP_ROM = bytes(32768)
_HALT_ROM = bytes([0x76] + [0x00] * 32767)


def _patch_db(
    monkeypatch: pytest.MonkeyPatch, romdb: object, raw: dict[str, dict[str, str]]
) -> None:
    """Inject fake ROM-database entries by patching the _load() seam directly."""
    entries = {sha1: RomDbEntry(mapper=v.get("mapper"), system=None, title_jp=None)
               for sha1, v in raw.items()}
    monkeypatch.setattr(romdb, "_load", lambda: entries)


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
    assert len(buf) == 256 * 212  # 192-line frame padded to the constant output height


def test_run_frame_skip_render_returns_empty_buffer() -> None:
    m = _make_machine()
    buf = m.run_frame(skip_render=True)
    assert len(buf) == 0


def test_run_frame_increments_vdp_frame_count() -> None:
    # Machine.run_frame owns frame counting (both VDP variants).
    m = _make_machine()
    assert m.vdp._frame_count == 0
    m.run_frame()
    m.run_frame(skip_render=True)
    assert m.vdp._frame_count == 2


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
    _patch_db(monkeypatch, romdb, {})
    m = make_machine(rom=_NOP_ROM, mapper="auto")
    assert isinstance(m.memory._mapper, FlatMapper)
    assert m.scc is None


def test_auto_unknown_cartridge_falls_back_to_mirrored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import msx.romdb as romdb
    _patch_db(monkeypatch, romdb, {})  # empty DB → always miss
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
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "CrossBlaim"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, FlatMapper)
    stderr = capsys.readouterr().err
    assert "CrossBlaim" in stderr


def test_auto_known_konamisco_selects_scc_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import msx.romdb as romdb
    from msx.mapper import KonamiSCCMapper
    cart = bytes(65536)  # dummy 64 KB
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "KonamiSCC"}})
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
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "Konami"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, KonamiMapper)
    assert m.scc is None


@pytest.mark.parametrize(
    ("db_mapper", "expected_base"),
    [("Page2", 0x8000), ("0x4000", 0x4000), ("0x8000", 0x8000)],
    ids=["Page2", "0x4000", "0x8000"],
)
def test_auto_known_fixed_page_types_select_fixed_page_mapper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_mapper: str,
    expected_base: int,
) -> None:
    import hashlib

    import msx.romdb as romdb
    from msx.mapper import FixedPageMapper
    cart = bytes(16384)  # dummy 16 KB cartridge
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": db_mapper}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, FixedPageMapper)
    assert m.memory._mapper.base == expected_base
    assert "unsupported mapper type" not in capsys.readouterr().err


def test_auto_known_koeisram32_selects_koeisram32_mapper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hashlib

    import msx.romdb as romdb
    from msx.mapper import KoeiSRAM32Mapper
    cart = bytes(65536)  # dummy 64 KB cartridge
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "KoeiSRAM32"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    assert isinstance(m.memory._mapper, KoeiSRAM32Mapper)
    assert "unsupported mapper type" not in capsys.readouterr().err


def test_auto_known_gamemaster2_selects_gamemaster2_mapper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hashlib

    import msx.romdb as romdb
    from msx.mapper import GameMaster2Mapper
    cart = bytes(128 * 1024)  # dummy 128 KB cartridge
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "GameMaster2"}})
    m = make_machine(rom=_NOP_ROM, cartridge=cart, mapper="auto")
    mapper = m.memory._mapper
    assert isinstance(mapper, GameMaster2Mapper)
    assert mapper.sram is not None
    assert len(mapper.sram) == 8192
    assert "unsupported mapper type" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Slot 2 cartridge wiring
# ---------------------------------------------------------------------------

def test_make_machine_no_cartridge2_slot2_open_bus() -> None:
    m = make_machine(rom=_NOP_ROM)
    # page 1 → slot 2: slot_register bits 3:2 = 0b10 → 0x08
    m.memory.set_slot_register(0x08)
    assert m.memory.read(0x4000) == 0xFF


def test_make_machine_cartridge2_wired_to_slot2() -> None:
    cart2 = b"\xBB" + b"\x00" * 32767
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2="Mirrored")
    # page 1 → slot 2
    m.memory.set_slot_register(0x08)
    assert m.memory.read(0x4000) == 0xBB


@pytest.mark.parametrize(
    ("mapper2_type", "expected_base"),
    [("Page2", 0x8000), ("0x4000", 0x4000), ("0x8000", 0x8000)],
    ids=["Page2", "0x4000", "0x8000"],
)
def test_make_machine_mapper2_fixed_page_types_build_fixed_page_mapper(
    mapper2_type: str, expected_base: int,
) -> None:
    from msx.mapper import FixedPageMapper
    cart2 = bytes(16384)  # dummy 16 KB cartridge
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2=mapper2_type)
    assert isinstance(m.memory._mapper2, FixedPageMapper)
    assert m.memory._mapper2.base == expected_base


def test_make_machine_mapper2_rtype_builds_rtype_mapper() -> None:
    from msx.mapper import RTypeMapper
    cart2 = bytes(384 * 1024)  # dummy 384 KB R-Type-sized cartridge
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2="R-Type")
    assert isinstance(m.memory._mapper2, RTypeMapper)


def test_make_machine_mapper2_konamiscc_builds_real_scc_chip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import msx.romdb as romdb
    cart2 = bytes(65536)
    sha1 = hashlib.sha1(cart2).hexdigest()
    _patch_db(monkeypatch, romdb, {sha1: {"mapper": "KonamiSCC"}})
    m = make_machine(rom=_NOP_ROM, cartridge2=cart2, mapper2="auto")
    assert isinstance(m.memory._mapper2, KonamiSCCMapper)
    assert m.scc is not None
    assert m.scc is m.memory._mapper2.scc
    # The 051649 a Konami megaROM cartridge carries, not the SCC-I's 052539
    # (see SCC.is_052539's docstring) -- same chip identity slot 1's own
    # KonamiSCC resolution builds.
    assert m.scc.is_052539 is False


def test_make_machine_mapper2_konamiscc_rejected_when_slot1_also_konamiscc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import msx.romdb as romdb
    cart1 = bytes(65536)
    cart2 = bytes([0xFF]) + bytes(65535)
    sha1_1 = hashlib.sha1(cart1).hexdigest()
    sha1_2 = hashlib.sha1(cart2).hexdigest()
    _patch_db(monkeypatch, romdb, {
        sha1_1: {"mapper": "KonamiSCC"}, sha1_2: {"mapper": "KonamiSCC"},
    })
    with pytest.raises(MachineLoadError, match="scc"):
        make_machine(
            rom=_NOP_ROM, cartridge=cart1, mapper="auto", cartridge2=cart2, mapper2="auto"
        )


# ---------------------------------------------------------------------------
# Either slot's KonamiSCC mapper vs. either slot's scc_plus extension --
# the 4-way single-scc-field conflict (openspec/changes/
# slot1-slot2-symmetrization, design.md Decision D1/D5)
# ---------------------------------------------------------------------------

def test_make_machine_mapper1_konamiscc_rejected_when_extension2_is_scc_plus() -> None:
    from msx.machine_loader import _ExtensionOverlay
    cart1 = bytes(65536)
    with pytest.raises(MachineLoadError, match="scc"):
        make_machine(
            rom=_NOP_ROM, cartridge=cart1, mapper="KonamiSCC",
            extension_overlay=_ExtensionOverlay(device="scc_i_cart"),
        )


def test_make_machine_extension1_scc_plus_rejected_when_mapper2_is_konamiscc() -> None:
    from msx.machine_loader import _ExtensionOverlay
    cart2 = bytes(65536)
    with pytest.raises(MachineLoadError, match="scc"):
        make_machine(
            rom=_NOP_ROM, cartridge2=cart2, mapper2="KonamiSCC",
            extension_overlay1=_ExtensionOverlay(device="scc_i_cart"),
        )


def test_make_machine_extension1_and_extension2_both_scc_plus_rejected() -> None:
    from msx.machine_loader import _ExtensionOverlay
    with pytest.raises(MachineLoadError, match="scc"):
        make_machine(
            rom=_NOP_ROM,
            extension_overlay1=_ExtensionOverlay(device="scc_i_cart"),
            extension_overlay=_ExtensionOverlay(device="scc_i_cart"),
        )


def test_make_machine_extension1_scc_plus_alone_builds_real_scc_chip() -> None:
    from msx.machine_loader import _ExtensionOverlay
    from msx.mapper import SCCICart
    m = make_machine(rom=_NOP_ROM, extension_overlay1=_ExtensionOverlay(device="scc_i_cart"))
    assert isinstance(m.memory._mapper, SCCICart)
    assert m.scc is not None
    assert m.scc is m.memory._mapper.scc


# ---------------------------------------------------------------------------
# cycle_count

def test_cycle_count_starts_at_zero() -> None:
    m = _make_machine()
    assert m.cycle_count == 0


def test_cycle_count_increases_after_run_frame() -> None:
    m = _make_machine()
    m.run_frame()
    assert m.cycle_count > 0


def test_cycle_count_accumulates_across_frames() -> None:
    m = _make_machine()
    m.run_frame()
    after_one = m.cycle_count
    m.run_frame()
    assert m.cycle_count >= after_one * 2 - 100  # allow ±one instruction slack


def test_reset_full_power_on_state() -> None:
    # Full reset restores PSG registers, the slot register, and VDP register/
    # status state to power-on values (memory/VRAM contents are retained).
    m = _make_machine()
    m.psg.regs[7] = 0xAB
    m.psg.latch = 5
    m.memory.set_slot_register(0xFF)
    m.memory.set_sub_slot_reg(0xFF)
    m.vdp.status = 0xFF
    m.vdp.regs[1] = 0x60
    m.reset()
    assert m.psg.regs == [0] * 16
    assert m.psg.latch == 0
    assert m.memory.slot_register == 0x00
    assert m.memory.sub_slot_reg == 0x00
    assert m.vdp.status == 0
    assert all(r == 0 for r in m.vdp.regs)


def test_reset_resets_attached_fdc() -> None:
    from msx.fdc.disk_drive import DiskDrive
    from msx.fdc.interface import SonyPhilipsInterface
    from msx.fdc.wd2793 import WD2793

    m = _make_machine()
    controller = WD2793()
    drive_a, drive_b = DiskDrive(), DiskDrive()
    m.fdc = SonyPhilipsInterface(controller, [drive_a, drive_b], disk_rom=bytes(16384))
    m.fdc.write_mem(0x7FFC, 0x01)  # side 1
    m.fdc.write_mem(0x7FFD, 0x01)  # select drive B
    assert controller.drive is drive_b

    m.reset()

    assert controller.drive is drive_a  # drive A reselected
    assert drive_a.side == 0 and drive_b.side == 0  # side cleared on every drive
    assert m.fdc.side_reg == 0
    assert m.fdc.drive_reg == 0


def test_reset_with_no_fdc_attached_does_not_crash() -> None:
    m = _make_machine()
    assert m.fdc is None
    m.reset()  # must not raise


# ---------------------------------------------------------------------------
# Machine.sram_sources() -- design.md Decision D2 Option B: a generic
# (has_sram source, save path) list replacing the individually-enumerated
# _save_sram_if_present call per device __main__.py's _cleanup used to make.
# ---------------------------------------------------------------------------

def test_sram_sources_empty_when_nothing_carries_sram() -> None:
    m = _make_machine()
    assert m.sram_sources() == []


def test_sram_sources_includes_slot1_mapper_when_sram_backed() -> None:
    m = make_machine(rom=_NOP_ROM, cartridge=bytes(65536), mapper="ASCII8SRAM2")
    sources = m.sram_sources()
    assert len(sources) == 1
    source, path = sources[0]
    assert source is m.memory._mapper
    assert isinstance(path, Path)
    assert path == m.sram_save_path


def test_sram_sources_excludes_slot1_mapper_without_sram() -> None:
    # A non-SRAM mapper (ASCII8) never gets a sram_save_path in the first
    # place, but the has_sram guard is what actually excludes it here.
    m = make_machine(rom=_NOP_ROM, cartridge=bytes(65536), mapper="ASCII8")
    assert m.sram_sources() == []


def test_sram_sources_includes_both_slots_independently() -> None:
    m = make_machine(
        rom=_NOP_ROM,
        cartridge=bytes(65536), mapper="ASCII8SRAM2",
        cartridge2=bytes(range(256)) * 32, mapper2="GameMaster2",
    )
    sources = m.sram_sources()
    assert len(sources) == 2
    found = {id(source) for source, _path in sources}
    assert id(m.memory._mapper) in found
    assert id(m.memory._mapper2) in found


def test_sram_sources_includes_fmpac_and_halnote_when_present(tmp_path: Path) -> None:
    from msx.fmpac import FmPac
    from msx.mapper import HalnoteMapper
    from msx.opll import Opll

    m = _make_machine()
    m.fmpac = FmPac(rom=bytes(65536), opll=Opll())
    m.fmpac_sram_save_path = tmp_path / "fmpac.sram"
    m.halnote_cart = HalnoteMapper(rom=bytes(1048576))
    m.halnote_sram_save_path = tmp_path / "halnote.sram"

    sources = m.sram_sources()
    found = {id(source): path for source, path in sources}
    assert id(m.fmpac) in found and found[id(m.fmpac)] == m.fmpac_sram_save_path
    assert id(m.halnote_cart) in found and found[id(m.halnote_cart)] == m.halnote_sram_save_path

    m.halnote_sram_save_path = None  # unset path excludes it from the list
    sources = m.sram_sources()
    assert id(m.halnote_cart) not in {id(source) for source, _path in sources}
    assert id(m.fmpac) in {id(source) for source, _path in sources}
