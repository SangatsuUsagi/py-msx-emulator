"""--extension msxdos2_512k_kanjirom tests: expanded-overlay parsing of the real
config/extensions/msxdos2_512k_kanjirom.yaml (msxdos2_512k's 512 KB RamMapper
in sub-slot 0 + flat 32 KB MSX-DOS2 kernel ROM in sub-slot 1, plus hbi_j1's
own KanjiRom io_device mechanism on I/O ports 0xD8-0xDB), and build_machine
wiring for the one thing that differs from tests/test_msxdos2_512k.py's
existing coverage: the Kanji-ROM io_device. Mirrors
tests/test_msxdos2_512k.py's and tests/test_cli_hbi_j1.py's structure.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.kanji import KanjiRom
from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    _RomEntry,
    build_machine,
    load_extension_overlay,
)
from msx.mapper import FixedPageMapper
from msx.ram_mapper import RamMapper

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"
_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"

_KERNEL_ROM_SIZE = 32768
_KANJIFONT_ROM_SIZE = 262144


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_read_bytes(self: Path) -> bytes:
        sizes = {
            "msxd22s.rom": _KERNEL_ROM_SIZE,
            "BugMaruMSX-16.rom": _KANJIFONT_ROM_SIZE,
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
            spec = importlib.util.spec_from_file_location(
                "_emulator_main_msxdos2_512k_kanjirom", _MAIN_PATH
            )
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# CLI-level: msxdos2_512k_kanjirom is now an accepted --extension choice
# ---------------------------------------------------------------------------

def test_extension_msxdos2_512k_kanjirom_alone_boots() -> None:
    # hb_f1xd is MSX2 (an expanded extension overlay has no MSX1 counterpart,
    # see cart-extension-overlay's "Expanded extension overlay requires an
    # MSX2 base machine" Requirement) with no slot-3 memory mapper (unlike
    # the default cbios_msx2_jp), so it doesn't hit the has_ram_mapper
    # conflict rejection this extension's ram_mapper sub-slot also enforces.
    code, out, _err = _run_main(
        ["--machine", "hb_f1xd", "--extension", "msxdos2_512k_kanjirom", "--count-frame", "1"]
    )
    assert code == 0
    assert "msxdos2_512k_kanjirom" in out


# ---------------------------------------------------------------------------
# load_extension_overlay: expanded-shape parsing of the real
# config/extensions/msxdos2_512k_kanjirom.yaml
# ---------------------------------------------------------------------------

def test_load_msxdos2_512k_kanjirom_overlay_parses_real_yaml() -> None:
    overlay = load_extension_overlay("msxdos2_512k_kanjirom", _CONFIG, _ROOT)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert set(overlay.subslots) == {0, 1}
    assert overlay.subslots[0].device == "ram_mapper"
    assert overlay.subslots[0].size_kb == 512
    assert overlay.subslots[1].device == "flat_rom"
    assert overlay.subslots[1].rom_entry is not None
    assert overlay.subslots[1].rom_entry.file == "msxd22s.rom"
    assert overlay.subslots[1].rom_entry.size_kb == 32
    assert overlay.io_device is not None
    assert overlay.io_device.device == "kanji_rom"
    assert overlay.io_device.rom_entry is not None
    assert overlay.io_device.rom_entry.file == "../kanji/BugMaruMSX-16.rom"
    assert overlay.io_device.rom_entry.size_kb == 256


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


def _msx2_spec(has_ram_mapper: bool = False) -> MachineSpec:
    # A memory-mapper sub-slot has no MSX1-standard hardware counterpart
    # (cart-extension-overlay's "Expanded extension overlay requires an MSX2
    # base machine" Requirement), hence MSX2 here rather than _msx1_spec's
    # MSX1. bios_override/extrom_override supply the main and SUB ROM bytes
    # directly, so no real slot 0/slot 3 ROM files are needed -- only the
    # extension overlay's own ROMs (written by _msxdos2_512k_kanjirom_overlay)
    # must exist on disk. Mirrors tests/test_cli_hbi_j1.py's own _msx2_spec.
    return MachineSpec(
        name="test_msx2",
        machine_id="test_msx2",
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=has_ram_mapper,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=False,
    )


def _msxdos2_512k_kanjirom_overlay(
    rom_dir: Path, size_kb: int = 512
) -> _ExpandedExtensionOverlay:
    (rom_dir / "msxd22s.rom").write_bytes(bytes(_KERNEL_ROM_SIZE))
    (rom_dir / "kanjifont.rom").write_bytes(bytes(_KANJIFONT_ROM_SIZE))
    return _ExpandedExtensionOverlay(
        subslots={
            0: _ExpandedSubslotDevice(device="ram_mapper", size_kb=size_kb),
            1: _ExpandedSubslotDevice(
                device="flat_rom", rom_base_dir=rom_dir,
                rom_entry=_RomEntry(file="msxd22s.rom", size_kb=32, pages=[]),
            ),
        },
        io_device=_ExpandedSubslotDevice(
            device="kanji_rom", rom_base_dir=rom_dir,
            rom_entry=_RomEntry(file="kanjifont.rom", size_kb=256, pages=[]),
        ),
    )


def test_build_machine_wires_ram_mapper_kernel_rom_and_kanji_device(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=_msxdos2_512k_kanjirom_overlay(rom_dir),
    )
    assert machine.memory.slot2_sub_slot_enabled is True

    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    assert len(sub0.ram) == 512 * 1024

    sub1 = machine.memory._mapper2_subslots[1]
    assert isinstance(sub1, FixedPageMapper)

    assert isinstance(machine.kanji, KanjiRom)


def test_kernel_rom_subslot_dispatches_to_the_real_rom_bytes(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    overlay = _msxdos2_512k_kanjirom_overlay(rom_dir)
    (rom_dir / "msxd22s.rom").write_bytes(b"\x42" + bytes(_KERNEL_ROM_SIZE - 1))
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=overlay,
    )
    sub1 = machine.memory._mapper2_subslots[1]
    assert isinstance(sub1, FixedPageMapper)
    assert sub1.read(0x4000) == 0x42
    assert sub1.read(0x0000) == 0xFF
    assert sub1.read(0xC000) == 0xFF


def test_kanji_io_ports_reach_kanji_device_regardless_of_subslot(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=_msxdos2_512k_kanjirom_overlay(rom_dir),
    )
    machine.io.write_port(0xD8, 0x05)
    machine.io.write_port(0xD9, 0x02)
    assert machine.io.read_port(0xD8) == 0x00  # all-zero fixture ROM byte


def test_standard_ports_still_register_alongside_kernel_rom(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    machine = build_machine(
        _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
        extension_overlay=_msxdos2_512k_kanjirom_overlay(rom_dir),
    )
    machine.io.write_port(0xFC, 5)
    assert machine.io.read_port(0xFC) & 0x1F == 5


# ---------------------------------------------------------------------------
# has_ram_mapper conflict validation (reused, unmodified, from memory_512k)
# ---------------------------------------------------------------------------

def test_rejected_on_machine_with_existing_ram_mapper(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    spec = _msx2_spec(has_ram_mapper=True)
    with pytest.raises(MachineLoadError, match="has_ram_mapper"):
        build_machine(
            spec, bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay=_msxdos2_512k_kanjirom_overlay(rom_dir),
        )


def test_rejected_on_msx1_base_machine(tmp_path: Path) -> None:
    # A memory-mapper sub-slot has no MSX1-standard hardware counterpart --
    # see cart-extension-overlay's "Expanded extension overlay requires an
    # MSX2 base machine" Requirement.
    main_dir = tmp_path / "main_rom"
    main_dir.mkdir()
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    with pytest.raises(MachineLoadError, match="MSX1"):
        build_machine(
            _msx1_spec(main_dir),
            extension_overlay=_msxdos2_512k_kanjirom_overlay(rom_dir),
        )


# ---------------------------------------------------------------------------
# Missing ROM asset reported
# ---------------------------------------------------------------------------

def test_missing_kernel_rom_raises_naming_file(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    overlay = _msxdos2_512k_kanjirom_overlay(rom_dir)
    (rom_dir / "msxd22s.rom").unlink()
    with pytest.raises(MachineLoadError, match="msxd22s.rom"):
        build_machine(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay=overlay,
        )


def test_missing_kanji_font_rom_raises_naming_file(tmp_path: Path) -> None:
    rom_dir = tmp_path / "msxdos2_kanji_rom"
    rom_dir.mkdir()
    overlay = _msxdos2_512k_kanjirom_overlay(rom_dir)
    (rom_dir / "kanjifont.rom").unlink()
    with pytest.raises(MachineLoadError, match="kanjifont.rom"):
        build_machine(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay=overlay,
        )
