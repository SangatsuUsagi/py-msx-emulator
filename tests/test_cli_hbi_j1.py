"""--extension hbi-j1 tests: CLI/config wiring (patched filesystem, no SDL
window), load_extension_overlay's expanded-shape parsing, and direct
build_machine wiring (expanded slot 2: HalnoteMapper in sub-slot 0, a flat
Kanji driver+BASIC ROM in sub-slot 1, KanjiRom on I/O ports 0xD8-0xDB) --
mirrors tests/test_cli_scc_plus.py's structure for the flat-overlay case.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.kanji import KanjiRom
from msx.machine import Machine
from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    _ExtensionOverlay,
    _RomEntry,
    build_machine,
    load_extension_overlay,
)
from msx.mapper import FixedPageMapper, HalnoteMapper
from msx.state import load_state, save_state

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"
_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"

_HALNOTE_ROM_SIZE = 1048576
_KANJIBASIC_ROM_SIZE = 32768
_KANJIFONT_ROM_SIZE = 262144


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_read_bytes(self: Path) -> bytes:
        # HalnoteMapper/KanjiRom validate ROM size strictly in __post_init__,
        # unlike fmpac/scc-plus's tests (which mock every ROM to one fixed
        # size) -- resolve the right size per filename.
        sizes = {
            "hbi-j1_msx-je.rom": _HALNOTE_ROM_SIZE,
            "hbi-j1_kanjibasic.rom": _KANJIBASIC_ROM_SIZE,
            "hbi-j1_kanjifont.rom": _KANJIFONT_ROM_SIZE,
        }
        return b"\x00" * sizes.get(self.name, 32768)

    def fake_read_text(self: Path, encoding: str | None = None) -> str:
        return ""

    with patch.object(sys, "argv", [".", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch.object(Path, "read_text", fake_read_text), \
         patch("frontend.sdl2_frontend.run"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main_hbi_j1", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# CLI-level
# ---------------------------------------------------------------------------

def test_extension_hbi_j1_alone_boots() -> None:
    code, out, _err = _run_main(["--extension", "hbi-j1", "--count-frame", "1"])
    assert code == 0
    assert "hbi-j1" in out


def test_extension_hbi_j1_and_slot2_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--extension", "hbi-j1", "--slot2", "game2.rom"])
    assert code != 0
    assert "--extension" in err and "--slot2" in err


def test_extension_hbi_j1_and_mapper2_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--extension", "hbi-j1", "--mapper2", "Konami"])
    assert code != 0
    assert "--extension" in err and "--mapper2" in err


# ---------------------------------------------------------------------------
# load_extension_overlay: expanded-shape parsing of the real hbi-j1.yaml
# ---------------------------------------------------------------------------

def test_load_hbi_j1_overlay_parses_real_yaml() -> None:
    overlay = load_extension_overlay("hbi-j1", _CONFIG, _ROOT)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert set(overlay.subslots) == {0, 1}
    assert overlay.subslots[0].device == "halnote"
    assert overlay.subslots[0].rom_entry is not None
    assert overlay.subslots[0].rom_entry.file == "hbi-j1_msx-je.rom"
    assert overlay.subslots[0].sram_save_path == Path("saves/sram/hbi-j1_msx-je.sram")
    assert overlay.subslots[1].device == "flat_rom"
    assert overlay.subslots[1].rom_entry is not None
    assert overlay.subslots[1].rom_entry.file == "hbi-j1_kanjibasic.rom"
    assert overlay.io_device is not None
    assert overlay.io_device.device == "kanji_rom"
    assert overlay.io_device.rom_entry is not None
    assert overlay.io_device.rom_entry.file == "hbi-j1_kanjifont.rom"


def test_expanded_overlay_missing_subslots_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "bogus.yaml").write_text(
        "schema_version: 1\nid: bogus\nshape: expanded\nslot: 2\n", encoding="utf-8"
    )
    with pytest.raises(MachineLoadError, match="'subslots'"):
        load_extension_overlay("bogus", tmp_path, tmp_path)


def test_expanded_overlay_subslot_index_out_of_range_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "bogus.yaml").write_text(
        "schema_version: 1\nid: bogus\nshape: expanded\nslot: 2\n"
        "subslots:\n  4:\n    device: halnote\n",
        encoding="utf-8",
    )
    with pytest.raises(MachineLoadError, match="out of range"):
        load_extension_overlay("bogus", tmp_path, tmp_path)


def test_expanded_overlay_unrecognized_subslot_device_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "bogus.yaml").write_text(
        "schema_version: 1\nid: bogus\nshape: expanded\nslot: 2\n"
        "subslots:\n  0:\n    device: fmpac\n",
        encoding="utf-8",
    )
    with pytest.raises(MachineLoadError, match="unrecognized 'device'"):
        load_extension_overlay("bogus", tmp_path, tmp_path)


def test_expanded_overlay_unrecognized_io_device_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "bogus.yaml").write_text(
        "schema_version: 1\nid: bogus\nshape: expanded\nslot: 2\n"
        "subslots:\n  0:\n    device: halnote\n    rom:\n      file: msx-je.rom\n"
        "io_device:\n  device: not_a_real_device\n",
        encoding="utf-8",
    )
    with pytest.raises(MachineLoadError, match="unrecognized 'device'"):
        load_extension_overlay("bogus", tmp_path, tmp_path)


def test_unsupported_extension_shape_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "bogus.yaml").write_text(
        "schema_version: 1\nid: bogus\nshape: bogus_shape\ndevice: fmpac\nslot: 2\n",
        encoding="utf-8",
    )
    with pytest.raises(MachineLoadError, match="unsupported extension shape"):
        load_extension_overlay("bogus", tmp_path, tmp_path)


# ---------------------------------------------------------------------------
# build_machine wiring
# ---------------------------------------------------------------------------

def _msx1_spec(main_rom_dir: Path) -> MachineSpec:
    (main_rom_dir / "main.rom").write_bytes(bytes(32768))
    return MachineSpec(
        name="test_msx1",
        generation="msx1",
        rom_base_dir=main_rom_dir,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=None,
        has_ram_mapper=False,
        ram_size_kb=64,
        has_v9938=False,
        has_rtc=False,
    )


def _hbi_j1_overlay(rom_dir: Path, sram_path: Path | None = None) -> _ExpandedExtensionOverlay:
    (rom_dir / "msx-je.rom").write_bytes(bytes(_HALNOTE_ROM_SIZE))
    (rom_dir / "kanjibasic.rom").write_bytes(bytes(_KANJIBASIC_ROM_SIZE))
    (rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    return _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="halnote", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="msx-je.rom", size_kb=1024, pages=[]),
                sram_save_path=sram_path,
            ),
            1: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="kanjibasic.rom", size_kb=32, pages=[]),
            ),
        },
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=rom_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )


def _hbi_j1_machine(tmp_path: Path, name: str = "m") -> Machine:
    main_dir = tmp_path / f"{name}_main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / f"{name}_hbi_j1_rom"
    rom_dir.mkdir()
    return build_machine(_msx1_spec(main_dir), extension_overlay=_hbi_j1_overlay(rom_dir))


def test_build_machine_expands_slot2_with_halnote_and_flat_rom(tmp_path: Path) -> None:
    machine = _hbi_j1_machine(tmp_path)
    assert machine.memory.slot2_sub_slot_enabled is True
    assert isinstance(machine.memory._mapper2_subslots[0], HalnoteMapper)
    assert isinstance(machine.memory._mapper2_subslots[1], FixedPageMapper)
    assert machine.halnote_cart is machine.memory._mapper2_subslots[0]
    assert isinstance(machine.kanji, KanjiRom)


def _msx2_spec() -> MachineSpec:
    # bios_override/extrom_override supply the main and SUB ROM bytes
    # directly (mirrors tests/factories.py's _msx2_spec), so no real slot 0
    # /slot 3 ROM files are needed -- only the extension overlay's own ROMs
    # (written by _hbi_j1_overlay) must exist on disk.
    return MachineSpec(
        name="test_msx2",
        machine_id="test_msx2",
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=True,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=False,
    )


def test_build_machine_expands_slot2_on_msx2_alongside_slot3(tmp_path: Path) -> None:
    # BuildMsx2Machine/WireMsx2Memory's slot2_sub_slot_enabled path: an
    # HBI-J1-equipped MSX2 has slot 2's sub-slot register *and* slot 3's
    # RAM-mapper sub-slot dispatch active at once (allium/
    # machine-config-loader.allium's WireMsx2Memory guidance).
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    machine = build_machine(
        _msx2_spec(),
        bios_override=bytes(32768),
        extrom_override=bytes(32768),
        extension_overlay=_hbi_j1_overlay(rom_dir),
    )
    assert machine.memory.slot2_sub_slot_enabled is True
    assert isinstance(machine.memory._mapper2_subslots[0], HalnoteMapper)
    assert isinstance(machine.memory._mapper2_subslots[1], FixedPageMapper)
    assert isinstance(machine.kanji, KanjiRom)
    # Slot 3's own sub-slot dispatch (the RAM mapper) is unaffected.
    assert machine.memory.ram_mapper is not None


def test_reset_restores_power_on_slot2_sub_slot_register(tmp_path: Path) -> None:
    # ResetSlotRegisters' third setter call (Machine.reset()'s new
    # self.memory.set_slot2_sub_slot_reg(0x00)), alongside the pre-existing
    # slot/sub-slot register resets test_reset_full_power_on_state
    # (tests/test_machine.py) covers for a machine with no expanded slot 2.
    machine = _hbi_j1_machine(tmp_path)
    machine.memory.set_slot2_sub_slot_reg(0xFF)
    machine.reset()
    assert machine.memory.slot2_sub_slot_reg == 0x00


def test_subslot0_dispatches_to_halnote_mapper(tmp_path: Path) -> None:
    machine = _hbi_j1_machine(tmp_path)
    mem = machine.memory
    mem.set_slot_register(0x08)  # page1 -> primary slot 2
    mem.set_slot2_sub_slot_reg(0b00_00_00_00)  # page1 -> sub-slot 0
    # HalnoteMapper's bank-0 window content -- the overlay's ROM is all zero.
    assert mem.read(0x4000) == 0x00


def test_subslot1_dispatches_to_flat_kanji_driver_rom(tmp_path: Path) -> None:
    rom_dir_marker = 0xAB

    def _hbi_j1_overlay_with_marker(rom_dir: Path) -> _ExpandedExtensionOverlay:
        (rom_dir / "msx-je.rom").write_bytes(bytes(_HALNOTE_ROM_SIZE))
        kanjibasic = bytearray(_KANJIBASIC_ROM_SIZE)
        kanjibasic[0] = rom_dir_marker
        (rom_dir / "kanjibasic.rom").write_bytes(bytes(kanjibasic))
        (rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
        return _ExpandedExtensionOverlay(
            subslots={
                0: _ExpandedSubslotDevice(
                    device="halnote", rom_base_dir=rom_dir,
                    rom_entry=_RomEntry(file="msx-je.rom", size_kb=1024, pages=[]),
                ),
                1: _ExpandedSubslotDevice(
                    device="flat_rom", rom_base_dir=rom_dir,
                    rom_entry=_RomEntry(file="kanjibasic.rom", size_kb=32, pages=[]),
                ),
            },
            io_device=_ExpandedSubslotDevice(
                device="kanji_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
            ),
        )

    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    machine = build_machine(
        _msx1_spec(main_dir), extension_overlay=_hbi_j1_overlay_with_marker(rom_dir)
    )
    mem = machine.memory
    # Route both page0 and page1 to primary slot 2 (bits[1:0]=2, bits[3:2]=2)
    # so both addresses go through the same sub-slot-1 FixedPageMapper.
    mem.set_slot_register(0x0A)
    mem.set_slot2_sub_slot_reg(0b00_00_01_01)  # page0, page1 -> sub-slot 1
    assert mem.read(0x4000) == rom_dir_marker
    assert mem.read(0x0000) == 0xFF  # sub-slot 1's 0x0000-0x3FFF is open bus


def test_kanji_io_ports_reachable_regardless_of_subslot(tmp_path: Path) -> None:
    machine = _hbi_j1_machine(tmp_path)
    machine.memory.set_slot_register(0x08)  # page1 -> primary slot 2
    for subreg in (0b00_00_00_00, 0b00_00_01_00):  # sub-slot 0, then sub-slot 1
        machine.memory.set_slot2_sub_slot_reg(subreg)
        machine.io.write_port(0xD8, 0x05)  # column write, JIS1
        machine.io.write_port(0xD9, 0x02)  # row write, JIS1
        value = machine.io.read_port(0xD9)  # data read, JIS1
        # All-zero ROM content at this (in-range) address -- mainly asserts
        # the ports reach KanjiRom, and keep reaching it, regardless of
        # which sub-slot slot 2 currently has selected (the device has no
        # slot location of its own).
        assert value == 0x00


def test_build_machine_loads_existing_halnote_sram(tmp_path: Path) -> None:
    # RequestExpandedDeviceRoms' conditional LoadSram branch (halnote's
    # sram_save_path present, expected_size = config.halnote_sram_size_bytes)
    # -- mirrors tests/test_fmpac.py's test_build_machine_loads_existing_fmpac_sram
    # for the flat-overlay FM-PAC precedent.
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    sram_path = tmp_path / "hbi-j1_msx-je.sram"
    saved = bytearray(16384)
    saved[0] = 0xEE
    sram_path.write_bytes(bytes(saved))

    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    overlay = _hbi_j1_overlay(rom_dir, sram_path=sram_path)
    machine = build_machine(_msx1_spec(main_dir), extension_overlay=overlay)

    cart = machine.halnote_cart
    assert cart is not None
    cart.write(0x4FFF, 0x80)  # enable SRAM at bank 0's top bit
    assert cart.read(0x0000) == 0xEE


def test_build_machine_flat_overlay_leaves_expanded_fields_unset(tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    fmpac_dir = tmp_path / "fmpac_rom"
    fmpac_dir.mkdir()
    (fmpac_dir / "fmpac.rom").write_bytes(bytes(65536))
    machine = build_machine(
        _msx1_spec(main_dir),
        extension_overlay=_ExtensionOverlay(
            device="fmpac", rom_base_dir=fmpac_dir,
            rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        ),
    )
    assert machine.memory.slot2_sub_slot_enabled is False
    assert machine.halnote_cart is None
    assert machine.kanji is None


def test_build_machine_no_overlay_leaves_expanded_fields_unset(tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    machine = build_machine(_msx1_spec(main_dir))
    assert machine.memory.slot2_sub_slot_enabled is False
    assert machine.halnote_cart is None
    assert machine.kanji is None


# ---------------------------------------------------------------------------
# Missing ROM asset: MachineLoadError naming the specific file
# ---------------------------------------------------------------------------

def test_missing_msx_je_rom_raises_naming_file(tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    overlay = _hbi_j1_overlay(rom_dir)
    (rom_dir / "msx-je.rom").unlink()
    with pytest.raises(MachineLoadError, match="msx-je.rom"):
        build_machine(_msx1_spec(main_dir), extension_overlay=overlay)


def test_missing_kanjibasic_rom_raises_naming_file(tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    overlay = _hbi_j1_overlay(rom_dir)
    (rom_dir / "kanjibasic.rom").unlink()
    with pytest.raises(MachineLoadError, match="kanjibasic.rom"):
        build_machine(_msx1_spec(main_dir), extension_overlay=overlay)


def test_missing_kanjifont_rom_raises_naming_file(tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    overlay = _hbi_j1_overlay(rom_dir)
    (rom_dir / "kanjifont.rom").unlink()
    with pytest.raises(MachineLoadError, match="kanjifont.rom"):
        build_machine(_msx1_spec(main_dir), extension_overlay=overlay)


# ---------------------------------------------------------------------------
# machine.halnote_cart save-state round-trip (mirrors
# tests/test_scc_i_cart_state.py's SCCICart precedent)
# ---------------------------------------------------------------------------

_RGB = bytearray(256 * 192 * 3)


@pytest.fixture()
def saves_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


def test_roundtrip_preserves_halnote_sram_and_banks(saves_dir: Path, tmp_path: Path) -> None:
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "hbi_j1_rom"
    rom_dir.mkdir()
    # Page-marked ROM (each 8 KB page's first byte is its page index), so a
    # bank-register change is observable through read() -- an all-zero ROM
    # cannot distinguish "page 0" from "page 5".
    msx_je_rom = bytearray(_HALNOTE_ROM_SIZE)
    for page in range(128):
        msx_je_rom[page * 8192] = page & 0xFF
    (rom_dir / "msx-je.rom").write_bytes(bytes(msx_je_rom))
    (rom_dir / "kanjibasic.rom").write_bytes(bytes(_KANJIBASIC_ROM_SIZE))
    (rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="halnote", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="msx-je.rom", size_kb=1024, pages=[]),
            ),
            1: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="kanjibasic.rom", size_kb=32, pages=[]),
            ),
        },
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=rom_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )
    machine = build_machine(_msx1_spec(main_dir), extension_overlay=overlay)
    cart = machine.halnote_cart
    assert cart is not None
    cart.write(0x4FFF, 0x85)  # SRAM enabled, bank 0 -> page 5
    cart.write(0x0000, 0xAB)  # SRAM byte
    save_state(machine, _RGB, "test")

    cart.write(0x0000, 0x00)
    cart.write(0x4FFF, 0x00)
    load_state(machine)

    assert cart.read(0x0000) == 0xAB
    assert cart.read(0x4000) == 5


def test_loading_plain_state_into_hbi_j1_machine_raises(
    saves_dir: Path, tmp_path: Path
) -> None:
    """As of generalize-slot2-mapper-state, a slot-2 expansion wiring
    mismatch (a non-expanded state loaded into a machine with slot 2
    expanded, or vice versa) raises ValueError, mirroring slot 1's
    existing strict mapper_kind check -- a deliberate behavior change from
    the prior silent-skip (msx/state.py's now-removed _restore_halnote)."""
    plain_dir = tmp_path / "plain_main_rom"
    plain_dir.mkdir()
    plain = build_machine(_msx1_spec(plain_dir))
    save_state(plain, _RGB, "test")

    hbi_machine = _hbi_j1_machine(tmp_path, name="hbi")
    with pytest.raises(ValueError, match="slot 2 expansion wiring mismatch"):
        load_state(hbi_machine)


def test_loading_hbi_j1_state_into_plain_machine_raises(
    saves_dir: Path, tmp_path: Path
) -> None:
    hbi_machine = _hbi_j1_machine(tmp_path, name="hbi")
    cart = hbi_machine.halnote_cart
    assert cart is not None
    cart.write(0x4FFF, 0x85)
    cart.write(0x0000, 0xAB)
    save_state(hbi_machine, _RGB, "test")

    plain_dir = tmp_path / "plain_main_rom"
    plain_dir.mkdir()
    plain = build_machine(_msx1_spec(plain_dir))
    with pytest.raises(ValueError, match="slot 2 expansion wiring mismatch"):
        load_state(plain)


def test_loading_state_with_mismatched_subslot_kind_raises(
    saves_dir: Path, tmp_path: Path
) -> None:
    """A per-sub-slot kind mismatch (both machines have slot 2 expanded,
    but sub-slot 0 holds a different device kind on each side) raises
    ValueError naming the sub-slot index and both kinds -- see
    openspec/changes/generalize-slot2-mapper-state."""
    hbi_machine = _hbi_j1_machine(tmp_path, name="hbi")
    save_state(hbi_machine, _RGB, "test")

    other_dir = tmp_path / "other_main_rom"
    other_dir.mkdir()
    other_rom_dir = tmp_path / "other_hbi_rom"
    other_rom_dir.mkdir()
    (other_rom_dir / "kanjibasic.rom").write_bytes(bytes(_KANJIBASIC_ROM_SIZE))
    (other_rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    # Same expanded shape as _hbi_j1_overlay, but flat_rom (not halnote) in
    # sub-slot 0 -- a genuine per-index kind mismatch, not a wiring one.
    other_overlay = _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=other_rom_dir,
                rom_entry=_RomEntry(file="kanjibasic.rom", size_kb=32, pages=[]),
            ),
        },
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=other_rom_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )
    other_machine = build_machine(_msx1_spec(other_dir), extension_overlay=other_overlay)

    with pytest.raises(ValueError, match="slot 2 sub-slot 0 mapper mismatch") as exc_info:
        load_state(other_machine)
    assert "halnote" in str(exc_info.value)
    assert "fixed_page" in str(exc_info.value)
