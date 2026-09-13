"""--extension memory_512k tests: expanded-overlay `ram_mapper` sub-slot
device parsing, build_machine wiring (a 512 KB RamMapper in slot 2 sub-slot
0, registered on the standard memory-mapper I/O ports), the has_ram_mapper
conflict validation, the at-most-one-ram_mapper-per-overlay validation, and
a save-state round-trip -- mirrors tests/test_cli_hbi_j1.py's structure for
the expanded-overlay case.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from msx.machine_loader import (
    MachineLoadError,
    _ExpandedExtensionOverlay,
    _ExpandedSubslotDevice,
    build_machine,
    load_device_registry,
    load_extension_overlay,
    load_machine_spec,
)
from msx.ram_mapper import RamMapper
from msx.state import load_state, save_state

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "config"

_FAKE_ROM_32K = bytes(32768)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _msx1_no_ram_mapper_yaml(main_file: str = "main.rom") -> str:
    return f"""\
    schema_version: 1
    id: test_msx1
    name: "Test MSX1 (no memory mapper)"
    generation: msx1
    rom_base: roms/fake
    cpu:
      type: z80a
      clock_mhz: 3.579545
    slots:
      primary:
        0:
          content:
            - rom:
                file: {main_file}
                size_kb: 32
                pages: [0, 1]
                sha1: null
        1: {{type: cartridge}}
        2: {{type: cartridge}}
        3:
          type: ram
          size_kb: 32
          mapper: none
    builtin_devices:
      - ref: psg_ay8910
    default_extensions: []
    """


def _msx2_with_ram_mapper_yaml(main_file: str = "main2.rom") -> str:
    return f"""\
    schema_version: 1
    id: test_msx2
    name: "Test MSX2 (has a memory mapper)"
    generation: msx2
    rom_base: roms/fake
    cpu:
      type: z80a
      clock_mhz: 3.579545
    slots:
      primary:
        0:
          content:
            - rom:
                file: {main_file}
                size_kb: 32
                pages: [0, 1]
                sha1: null
        1: {{type: cartridge}}
        2: {{type: cartridge}}
        3:
          expanded: true
          secondary:
            0:
              content:
                - rom:
                    file: sub.rom
                    size_kb: 32
                    pages: [0, 1]
                    sha1: null
            2:
              type: ram
              size_kb: 128
              mapper: standard
    builtin_devices:
      - ref: psg_ay8910
      - ref: vdp_v9938
      - ref: rtc_rp5c01
      - ref: memory_mapper_standard
    default_extensions: []
    """


def _full_registry(tmp_path: Path) -> tuple[Path, dict]:
    config_dir = tmp_path / "config"
    for dev_id, ports in [
        ("psg_ay8910", "[0xA0,0xA1,0xA2]"),
        ("vdp_v9938", "[0x98,0x99,0x9A,0x9B]"),
        ("rtc_rp5c01", "[0xB4,0xB5]"),
        ("memory_mapper_standard", "[0xFC,0xFD,0xFE,0xFF]"),
    ]:
        _write(
            config_dir / "devices" / f"{dev_id}.yaml",
            f"id: {dev_id}\ntype: io_device\nimplemented: true\nio_ports: {ports}\n",
        )
    registry = load_device_registry(config_dir)
    return config_dir, registry


def _no_mapper_spec(tmp_path: Path):
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx1.yaml", _msx1_no_ram_mapper_yaml())
    return load_machine_spec("test_msx1", config_dir, registry, tmp_path)


def _has_mapper_spec(tmp_path: Path):
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx2.yaml", _msx2_with_ram_mapper_yaml())
    return load_machine_spec("test_msx2", config_dir, registry, tmp_path)


# ---------------------------------------------------------------------------
# The real config/extensions/memory_512k.yaml resolves as expected
# ---------------------------------------------------------------------------

def test_memory_512k_yaml_resolves_ram_mapper_subslot() -> None:
    overlay = load_extension_overlay("memory_512k", _CONFIG, _ROOT)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert set(overlay.subslots) == {0}
    subslot = overlay.subslots[0]
    assert subslot.device == "ram_mapper"
    assert subslot.size_kb == 512
    assert subslot.rom_entry is None
    assert subslot.rom_base_dir is None
    assert subslot.sram_save_path is None


# ---------------------------------------------------------------------------
# Expanded overlay YAML parsing: size_kb validation (task 2.1)
# ---------------------------------------------------------------------------

def test_ram_mapper_subslot_without_size_kb_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "bad.yaml",
        """\
        schema_version: 1
        id: bad
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ram_mapper}
        """,
    )
    with pytest.raises(MachineLoadError, match="size_kb"):
        load_extension_overlay("bad", config_dir, tmp_path)


def test_ram_mapper_subslot_size_kb_not_multiple_of_16_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "bad_size.yaml",
        """\
        schema_version: 1
        id: bad_size
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ram_mapper, size_kb: 100}
        """,
    )
    with pytest.raises(MachineLoadError, match="multiple of 16"):
        load_extension_overlay("bad_size", config_dir, tmp_path)


def test_ram_mapper_subslot_with_size_kb_parses(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "custom.yaml",
        """\
        schema_version: 1
        id: custom
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ram_mapper, size_kb: 128}
        """,
    )
    overlay = load_extension_overlay("custom", config_dir, tmp_path)
    assert isinstance(overlay, _ExpandedExtensionOverlay)
    assert overlay.subslots[0].size_kb == 128


# ---------------------------------------------------------------------------
# At most one ram_mapper sub-slot per overlay (task 2.4)
# ---------------------------------------------------------------------------

def test_two_ram_mapper_subslots_in_one_overlay_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "extensions" / "dual.yaml",
        """\
        schema_version: 1
        id: dual
        overlay: true
        shape: expanded
        slot: 2
        subslots:
          0: {device: ram_mapper, size_kb: 256}
          1: {device: ram_mapper, size_kb: 256}
        """,
    )
    with pytest.raises(MachineLoadError, match="at most one 'ram_mapper'"):
        load_extension_overlay("dual", config_dir, tmp_path)


# ---------------------------------------------------------------------------
# build_machine wiring: sub-slot device, ports, open-bus sub-slots (task 2.2, 4.1-4.3)
# ---------------------------------------------------------------------------

def _ram_mapper_overlay(size_kb: int = 512) -> _ExpandedExtensionOverlay:
    return _ExpandedExtensionOverlay(
        subslots={0: _ExpandedSubslotDevice(device="ram_mapper", size_kb=size_kb)},
    )


def test_build_machine_wires_ram_mapper_subslot(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    assert machine.memory.slot2_sub_slot_enabled is True
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    assert len(sub0.ram) == 512 * 1024


def test_ram_mapper_subslot_responds_on_standard_ports(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    machine.io.write_port(0xFF, 0x05)
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    assert sub0.banks[3] == 5
    assert machine.io.read_port(0xFE) is not None  # port reachable, no exception


def test_ram_mapper_cartridge_starts_blank(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    assert all(b == 0 for b in sub0.ram)


def test_machine_reset_resets_slot2_subslot_ram_mapper_banks(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    sub0.banks = [3, 0, 0, 5]
    sub0.write(0xC000, 0x77)  # page 3 -> bank 5, physical offset 5 * 0x4000
    machine.reset()
    assert sub0.banks == [0, 0, 0, 0]
    assert sub0.ram[5 * 0x4000] == 0x77  # RAM contents retained across reset


def test_unpopulated_subslots_read_open_bus(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    assert machine.memory._mapper2_subslots[1] is None
    assert machine.memory._mapper2_subslots[2] is None
    assert machine.memory._mapper2_subslots[3] is None


# ---------------------------------------------------------------------------
# has_ram_mapper conflict validation (task 2.3)
# ---------------------------------------------------------------------------

def test_rejected_on_machine_with_existing_ram_mapper(tmp_path: Path) -> None:
    spec = _has_mapper_spec(tmp_path)
    assert spec.has_ram_mapper is True
    with pytest.raises(MachineLoadError, match="has_ram_mapper"):
        build_machine(
            spec, bios_override=_FAKE_ROM_32K, extrom_override=_FAKE_ROM_32K,
            extension_overlay=_ram_mapper_overlay(),
        )


def test_accepted_on_machine_with_no_ram_mapper(tmp_path: Path) -> None:
    spec = _no_mapper_spec(tmp_path)
    assert spec.has_ram_mapper is False
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    assert isinstance(machine.memory._mapper2_subslots[0], RamMapper)


def test_real_hb_f1xd_has_no_ram_mapper(tmp_path: Path) -> None:
    """Sanity check against the real machine catalogue: hb_f1xd (real
    hardware) accepts memory_512k, hb_f1xd_256k (hypothetical, already has a
    slot-3 memory mapper) and cbios_msx2_jp (generic MSX2) reject it."""
    registry = load_device_registry(_CONFIG)
    hb_f1xd = load_machine_spec("hb_f1xd", _CONFIG, registry, _ROOT)
    assert hb_f1xd.has_ram_mapper is False

    hb_f1xd_256k = load_machine_spec("hb_f1xd_256k", _CONFIG, registry, _ROOT)
    assert hb_f1xd_256k.has_ram_mapper is True
    with pytest.raises(MachineLoadError, match="has_ram_mapper"):
        build_machine(
            hb_f1xd_256k, bios_override=_FAKE_ROM_32K, extrom_override=_FAKE_ROM_32K,
            extension_overlay=_ram_mapper_overlay(),
        )

    cbios_msx2_jp = load_machine_spec("cbios_msx2_jp", _CONFIG, registry, _ROOT)
    assert cbios_msx2_jp.has_ram_mapper is True
    with pytest.raises(MachineLoadError, match="has_ram_mapper"):
        build_machine(
            cbios_msx2_jp, bios_override=_FAKE_ROM_32K, extrom_override=_FAKE_ROM_32K,
            extension_overlay=_ram_mapper_overlay(),
        )


# ---------------------------------------------------------------------------
# Save-state round-trip (task 4.4)
# ---------------------------------------------------------------------------

_RGB = bytearray(256 * 192 * 3)


@pytest.fixture()
def saves_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


def test_roundtrip_preserves_ram_mapper_banks_and_contents(
    saves_dir: Path, tmp_path: Path
) -> None:
    spec = _no_mapper_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extension_overlay=_ram_mapper_overlay()
    )
    sub0 = machine.memory._mapper2_subslots[0]
    assert isinstance(sub0, RamMapper)
    sub0.banks = [1, 2, 3, 4]
    sub0.write(0xC000, 0xAB)
    save_state(machine, _RGB, "test")

    sub0.banks = [0, 0, 0, 0]
    sub0.write(0xC000, 0x00)
    load_state(machine)

    assert sub0.banks == [1, 2, 3, 4]
    assert sub0.ram[4 * 0x4000] == 0xAB
