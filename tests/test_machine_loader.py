"""Tests for msx/machine_loader.py — YAML-based machine configuration loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _RomEntry,
    build_machine,
    load_device_registry,
    load_machine_spec,
)
from msx.ram_mapper import RamMapper
from msx.vdp.v9938 import V9938
from msx.vdp.vdp import VDP

_FAKE_ROM_32K = bytes(32768)
_FAKE_ROM_16K = bytes(16384)

# ---------------------------------------------------------------------------
# Helpers to build tmp config directory trees
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _make_device_dir(tmp_path: Path) -> Path:
    """Return a config dir with one valid device file."""
    config_dir = tmp_path / "config"
    _write(
        config_dir / "devices" / "psg_ay8910.yaml",
        """\
        id: psg_ay8910
        type: io_device
        implemented: true
        io_ports: [0xA0, 0xA1, 0xA2]
        """,
    )
    return config_dir


def _msx1_machine_yaml(main_file: str = "main.rom") -> str:
    return f"""\
    schema_version: 1
    id: test_msx1
    name: "Test MSX1"
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


def _msx2_machine_yaml(main_file: str = "main2.rom") -> str:
    return f"""\
    schema_version: 1
    id: test_msx2
    name: "Test MSX2"
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


# ---------------------------------------------------------------------------
# load_device_registry tests
# ---------------------------------------------------------------------------


def test_load_device_registry_returns_device(tmp_path: Path) -> None:
    config_dir = _make_device_dir(tmp_path)
    registry = load_device_registry(config_dir)
    assert "psg_ay8910" in registry
    assert registry["psg_ay8910"].type == "io_device"
    assert registry["psg_ay8910"].implemented is True


def test_load_device_registry_empty_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    (config_dir / "devices").mkdir(parents=True)
    registry = load_device_registry(config_dir)
    assert registry == {}


def test_load_device_registry_nonexistent_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    registry = load_device_registry(config_dir)
    assert registry == {}


def test_load_device_registry_id_filename_mismatch(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "devices" / "bar.yaml",
        "id: foo\ntype: io_device\nimplemented: true\n",
    )
    with pytest.raises(MachineLoadError, match="does not match filename stem"):
        load_device_registry(config_dir)


def test_load_device_registry_missing_type(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "devices" / "mydev.yaml",
        "id: mydev\nimplemented: true\n",
    )
    with pytest.raises(MachineLoadError, match="missing required field 'type'"):
        load_device_registry(config_dir)


def test_load_device_registry_missing_id(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write(
        config_dir / "devices" / "mydev.yaml",
        "type: io_device\nimplemented: true\n",
    )
    with pytest.raises(MachineLoadError, match="missing required field 'id'"):
        load_device_registry(config_dir)


# ---------------------------------------------------------------------------
# load_machine_spec tests
# ---------------------------------------------------------------------------


def _full_registry(tmp_path: Path) -> tuple[Path, dict]:
    """Config dir with all devices needed by test machines."""
    config_dir = tmp_path / "config"
    for dev_id, ports in [
        ("psg_ay8910", "[0xA0,0xA1,0xA2]"),
        ("vdp_v9938", "[0x98,0x99,0x9A,0x9B,0x9C]"),
        ("rtc_rp5c01", "[0xB4,0xB5]"),
        ("memory_mapper_standard", "[0xFC,0xFD,0xFE,0xFF]"),
    ]:
        _write(
            config_dir / "devices" / f"{dev_id}.yaml",
            f"id: {dev_id}\ntype: io_device\nimplemented: true\nio_ports: {ports}\n",
        )
    registry = load_device_registry(config_dir)
    return config_dir, registry


def test_load_machine_spec_msx1(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx1.yaml", _msx1_machine_yaml())
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.generation == "msx1"
    assert spec.main_rom_entry.file == "main.rom"
    assert spec.logo_rom_entry is None
    assert spec.sub_rom_entry is None
    assert spec.has_ram_mapper is False
    assert spec.ram_size_kb == 32


def test_load_machine_spec_msx2(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx2.yaml", _msx2_machine_yaml())
    spec = load_machine_spec("test_msx2", config_dir, registry, tmp_path)
    assert spec.generation == "msx2"
    assert spec.main_rom_entry.file == "main2.rom"
    assert spec.sub_rom_entry is not None
    assert spec.sub_rom_entry.file == "sub.rom"
    assert spec.has_ram_mapper is True
    assert spec.has_v9938 is True
    assert spec.has_rtc is True


def test_load_machine_spec_file_not_found(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    with pytest.raises(MachineLoadError, match="machine not found"):
        load_machine_spec("nonexistent", config_dir, registry, tmp_path)


def test_load_machine_spec_bad_schema_version(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    yaml_text = _msx1_machine_yaml().replace("schema_version: 1", "schema_version: 99")
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    with pytest.raises(MachineLoadError, match="unsupported schema_version"):
        load_machine_spec("test_msx1", config_dir, registry, tmp_path)


def _msx1_yaml_with_extra_ref(extra_ref: str) -> str:
    return textwrap.dedent(f"""\
    schema_version: 1
    id: test_msx1
    name: "Test MSX1"
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
                file: main.rom
                size_kb: 32
                pages: [0, 1]
                sha1: null
        3:
          type: ram
          size_kb: 32
          mapper: none
    builtin_devices:
      - ref: psg_ay8910
      - ref: {extra_ref}
    default_extensions: []
    """)


def test_load_machine_spec_unresolved_ref(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(
        config_dir / "machines" / "test_msx1.yaml",
        _msx1_yaml_with_extra_ref("totally_unknown_chip"),
    )
    with pytest.raises(MachineLoadError, match="not found in device registry"):
        load_machine_spec("test_msx1", config_dir, registry, tmp_path)


def test_load_machine_spec_unimplemented_device_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(
        config_dir / "devices" / "future_chip.yaml",
        "id: future_chip\ntype: io_device\nimplemented: false\n",
    )
    registry = load_device_registry(config_dir)
    _write(
        config_dir / "machines" / "test_msx1.yaml",
        _msx1_yaml_with_extra_ref("future_chip"),
    )
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.generation == "msx1"
    captured = capsys.readouterr()
    assert "future_chip" in captured.err
    assert "not implemented" in captured.err


def test_load_machine_spec_missing_main_rom(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    yaml_text = textwrap.dedent("""\
    schema_version: 1
    id: test_msx1
    name: "Test"
    generation: msx1
    rom_base: roms/fake
    cpu: {type: z80a, clock_mhz: 3.579545}
    slots:
      primary:
        0:
          content: []
        3: {type: ram, size_kb: 32, mapper: none}
    builtin_devices:
      - ref: psg_ay8910
    default_extensions: []
    """)
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    with pytest.raises(MachineLoadError, match="no main ROM"):
        load_machine_spec("test_msx1", config_dir, registry, tmp_path)


# ---------------------------------------------------------------------------
# build_machine tests (use bios_override to avoid disk I/O)
# ---------------------------------------------------------------------------


def _make_msx1_spec(tmp_path: Path) -> MachineSpec:
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx1.yaml", _msx1_machine_yaml())
    return load_machine_spec("test_msx1", config_dir, registry, tmp_path)


def _make_msx2_spec(tmp_path: Path) -> MachineSpec:
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx2.yaml", _msx2_machine_yaml())
    return load_machine_spec("test_msx2", config_dir, registry, tmp_path)


def test_build_machine_msx1_vdp_type(tmp_path: Path) -> None:
    spec = _make_msx1_spec(tmp_path)
    machine = build_machine(spec, bios_override=_FAKE_ROM_32K)
    assert isinstance(machine.vdp, VDP)
    assert not isinstance(machine.vdp, V9938)


def test_build_machine_msx1_no_ram_mapper(tmp_path: Path) -> None:
    spec = _make_msx1_spec(tmp_path)
    machine = build_machine(spec, bios_override=_FAKE_ROM_32K)
    assert machine.memory.ram_mapper is None


def test_build_machine_msx2_vdp_type(tmp_path: Path) -> None:
    spec = _make_msx2_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extrom_override=_FAKE_ROM_32K
    )
    assert isinstance(machine.vdp, V9938)


def test_build_machine_msx2_has_ram_mapper(tmp_path: Path) -> None:
    spec = _make_msx2_spec(tmp_path)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extrom_override=_FAKE_ROM_32K
    )
    assert isinstance(machine.memory.ram_mapper, RamMapper)


def test_build_machine_bios_override_no_disk_needed(tmp_path: Path) -> None:
    spec = _make_msx1_spec(tmp_path)
    # rom_base_dir points at a directory that has no ROM files — override must bypass disk
    machine = build_machine(spec, bios_override=_FAKE_ROM_32K)
    assert machine.memory.read(0x0000) == 0x00  # first byte of _FAKE_ROM_32K


def test_build_machine_extrom_override_msx2(tmp_path: Path) -> None:
    spec = _make_msx2_spec(tmp_path)
    custom_sub = bytes([0xAB] + [0x00] * 32767)
    machine = build_machine(
        spec, bios_override=_FAKE_ROM_32K, extrom_override=custom_sub
    )
    assert machine.memory.sub0_rom is not None
    assert machine.memory.sub0_rom[0] == 0xAB
