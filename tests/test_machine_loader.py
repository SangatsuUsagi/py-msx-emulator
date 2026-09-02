"""Tests for msx/machine_loader.py — YAML-based machine configuration loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from msx.machine_loader import (
    MachineLoadError,
    MachineSpec,
    _make_mapper,
    _parse_fdc,
    _parse_slot0,
    _parse_slot3_msx2,
    _require_scc,
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
    assert registry["psg_ay8910"].raw["type"] == "io_device"
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


# ---------------------------------------------------------------------------
# keyboard_type resolution (ppi8255 device default + per-machine override)
# ---------------------------------------------------------------------------

def test_keyboard_type_defaults_to_int_from_real_config() -> None:
    reg = load_device_registry(Path("config"))
    spec = load_machine_spec("cbios_msx2", Path("config"), reg, Path("."))
    assert spec.keyboard_type == "int"


def test_keyboard_type_override_jp_from_real_config() -> None:
    reg = load_device_registry(Path("config"))
    for mid in ("cbios_msx1_jp", "cbios_msx2_jp"):
        spec = load_machine_spec(mid, Path("config"), reg, Path("."))
        assert spec.keyboard_type == "jp", mid


def test_load_machine_spec_string_slot_keys(tmp_path: Path) -> None:
    # Slot-map keys may arrive as strings (quoted YAML / JSON). They must be
    # normalised to int so slot 0 (main ROM) and slot 3 (RAM) still resolve.
    config_dir, registry = _full_registry(tmp_path)
    yaml_text = textwrap.dedent("""\
    schema_version: 1
    id: test_msx1
    name: "Test MSX1"
    generation: msx1
    rom_base: roms/fake
    cpu: {type: z80a, clock_mhz: 3.579545}
    slots:
      primary:
        "0":
          content:
            - rom:
                file: main.rom
                size_kb: 32
                pages: [0, 1]
                sha1: null
        "3":
          type: ram
          size_kb: 64
          mapper: none
    builtin_devices:
      - ref: psg_ay8910
    default_extensions: []
    """)
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.main_rom_entry.file == "main.rom"  # slot 0 resolved
    assert spec.ram_size_kb == 64                  # slot 3 resolved


def test_m1_wait_states_absent_defaults_to_zero(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(config_dir / "machines" / "test_msx1.yaml", _msx1_machine_yaml())
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.m1_wait_states == 0


def test_m1_wait_states_read_from_cpu_block(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    yaml_text = _msx1_machine_yaml().replace(
        "clock_mhz: 3.579545",
        "clock_mhz: 3.579545\n      m1_wait_states: 1",
    )
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.m1_wait_states == 1


# ---------------------------------------------------------------------------
# Invalid keyboard_type override is rejected (allium A-2 regression guard —
# the old spec modelled the `??` default-fallback chain as swallowing an
# out-of-enum value into null; the real loader has always rejected it
# unconditionally instead)
# ---------------------------------------------------------------------------


def test_keyboard_type_invalid_override_raises(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(
        config_dir / "devices" / "ppi8255.yaml",
        "id: ppi8255\ntype: io_device\nimplemented: true\n"
        "io_ports: [0xA8]\nkeyboard_type: int\n",
    )
    registry = load_device_registry(config_dir)
    yaml_text = _msx1_yaml_with_extra_ref("ppi8255").replace(
        "- ref: ppi8255",
        "- ref: ppi8255\n    overrides: {keyboard_type: de}",
    )
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    with pytest.raises(MachineLoadError, match="must be 'int' or 'jp'"):
        load_machine_spec("test_msx1", config_dir, registry, tmp_path)


def test_keyboard_type_null_override_is_rejected_not_defaulted(tmp_path: Path) -> None:
    """An explicit `keyboard_type: null` override is a declared-but-invalid
    value, not "no override" — it must be rejected, not silently fall back
    to the device's default."""
    config_dir, registry = _full_registry(tmp_path)
    _write(
        config_dir / "devices" / "ppi8255.yaml",
        "id: ppi8255\ntype: io_device\nimplemented: true\n"
        "io_ports: [0xA8]\nkeyboard_type: int\n",
    )
    registry = load_device_registry(config_dir)
    yaml_text = _msx1_yaml_with_extra_ref("ppi8255").replace(
        "- ref: ppi8255",
        "- ref: ppi8255\n    overrides: {keyboard_type: null}",
    )
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    with pytest.raises(MachineLoadError, match="must be 'int' or 'jp'"):
        load_machine_spec("test_msx1", config_dir, registry, tmp_path)


# ---------------------------------------------------------------------------
# Resolved keyboard_type reaches the built Machine's InputState (allium B-6)
# ---------------------------------------------------------------------------


def test_build_machine_input_state_keyboard_type_reaches_machine(tmp_path: Path) -> None:
    config_dir, registry = _full_registry(tmp_path)
    _write(
        config_dir / "devices" / "ppi8255.yaml",
        "id: ppi8255\ntype: io_device\nimplemented: true\n"
        "io_ports: [0xA8]\nkeyboard_type: int\n",
    )
    registry = load_device_registry(config_dir)
    yaml_text = _msx1_yaml_with_extra_ref("ppi8255").replace(
        "- ref: ppi8255",
        "- ref: ppi8255\n    overrides: {keyboard_type: jp}",
    )
    _write(config_dir / "machines" / "test_msx1.yaml", yaml_text)
    spec = load_machine_spec("test_msx1", config_dir, registry, tmp_path)
    assert spec.keyboard_type == "jp"
    machine = build_machine(spec, bios_override=_FAKE_ROM_32K)
    assert machine.input.keyboard_type == "jp"


# ---------------------------------------------------------------------------
# _parse_slot0 role assignment: single scan, main checked before logo
# (allium A-1 — a block claiming both a main page and page 2 is main only,
# never both main and logo, and never logo-only)
# ---------------------------------------------------------------------------


def test_parse_slot0_entry_claiming_main_and_logo_pages_is_main_only() -> None:
    slot0 = {
        "content": [
            {"rom": {"file": "combo.rom", "size_kb": 48, "pages": [0, 1, 2]}},
        ],
    }
    main_rom, logo_rom = _parse_slot0(slot0, "test")
    assert main_rom is not None
    assert main_rom.file == "combo.rom"
    assert logo_rom is None


def test_parse_slot0_last_match_wins_for_each_role() -> None:
    slot0 = {
        "content": [
            {"rom": {"file": "main1.rom", "size_kb": 32, "pages": [0, 1]}},
            {"rom": {"file": "main2.rom", "size_kb": 32, "pages": [0, 1]}},
        ],
    }
    main_rom, _logo_rom = _parse_slot0(slot0, "test")
    assert main_rom is not None
    assert main_rom.file == "main2.rom"


# ---------------------------------------------------------------------------
# Flat (non-mapper) RAM sub-slot resolution: last declared sub-slot wins
# (allium/slots.allium 1.1 — unlike SUB ROM/FDC, which are first-match-wins
# via an `if result.X is None` guard, flat_ram_subslot is assigned
# unconditionally on every `type: ram` sub-slot, msx/machine_loader.py:710-712)
# ---------------------------------------------------------------------------


def test_parse_slot3_msx2_flat_ram_last_declared_subslot_wins() -> None:
    slot3 = {
        "expanded": True,
        "secondary": {
            1: {"type": "ram", "size_kb": 64},
            3: {"type": "ram", "size_kb": 64},
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.flat_ram_subslot == 3


# ---------------------------------------------------------------------------
# Malformed `content:` list items are silently skipped, not a crash
# (allium A-8 / code fix: _is_content_item_shape guard)
# ---------------------------------------------------------------------------


def test_parse_slot0_skips_non_mapping_content_item() -> None:
    slot0 = {"content": ["oops", {"rom": {"file": "main.rom", "size_kb": 32, "pages": [0, 1]}}]}
    main_rom, logo_rom = _parse_slot0(slot0, "test")
    assert main_rom is not None
    assert main_rom.file == "main.rom"
    assert logo_rom is None


def test_parse_slot3_msx2_skips_non_mapping_content_item() -> None:
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {"content": ["oops", {"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}]},
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.sub_rom is not None
    assert result.sub_rom.file == "sub.rom"


# ---------------------------------------------------------------------------
# Slot-3 validation order: sub_rom -> fdc -> flat_ram -> RAM-strategy
# exclusivity. When multiple violations coexist, the first one in this
# order is the one reported (allium A-6).
# ---------------------------------------------------------------------------


def test_slot3_multiple_violations_reports_sub_rom_out_of_range_first() -> None:
    slot3 = {
        "expanded": True,
        "secondary": {
            4: {"content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}]},
            0: {"mapper": "standard"},
            1: {"type": "ram", "size_kb": 64},
        },
    }
    with pytest.raises(MachineLoadError, match="SUB ROM sub-slot .* out of range"):
        _parse_slot3_msx2(slot3, "test")


# ---------------------------------------------------------------------------
# _parse_fdc: drives must be positive, not silently clamped
# (openspec/changes/archive/2026-09-01-machine-loader-validation-gaps)
# ---------------------------------------------------------------------------

_FDC_ROM = {"rom": {"file": "disk.rom", "size_kb": 16, "pages": [1]}}
_FDC_ROM_SUBSLOT = {"fdc": _FDC_ROM}


def test_parse_fdc_rejects_zero_drives() -> None:
    sub_val = {"fdc": {**_FDC_ROM, "drives": 0}}
    with pytest.raises(MachineLoadError, match="drives must be positive"):
        _parse_fdc(sub_val, "test", 0)


def test_parse_fdc_rejects_negative_drives() -> None:
    sub_val = {"fdc": {**_FDC_ROM, "drives": -1}}
    with pytest.raises(MachineLoadError, match="drives must be positive"):
        _parse_fdc(sub_val, "test", 0)


# ---------------------------------------------------------------------------
# flat RAM colliding with SUB ROM's or the FDC's sub-slot is rejected;
# SUB ROM and FDC sharing a sub-slot (HB-F1XD's real layout) remains allowed
# (openspec/changes/archive/2026-09-01-machine-loader-validation-gaps)
# ---------------------------------------------------------------------------


def test_parse_slot3_msx2_flat_ram_colliding_with_sub_rom_rejected() -> None:
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {
                "content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}],
                "type": "ram",
                "size_kb": 64,
            },
        },
    }
    with pytest.raises(MachineLoadError, match="flat RAM and SUB ROM both declared"):
        _parse_slot3_msx2(slot3, "test")


def test_parse_slot3_msx2_flat_ram_colliding_with_fdc_rejected() -> None:
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {**_FDC_ROM_SUBSLOT, "type": "ram", "size_kb": 64},
        },
    }
    with pytest.raises(MachineLoadError, match="flat RAM and fdc both declared"):
        _parse_slot3_msx2(slot3, "test")


def test_parse_slot3_msx2_sub_rom_and_fdc_sharing_subslot_still_allowed() -> None:
    """HB-F1XD's real shape: SUB ROM and fdc share sub-slot 0, flat RAM is
    elsewhere -- must keep loading successfully."""
    slot3 = {
        "expanded": True,
        "secondary": {
            0: {
                "content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}],
                **_FDC_ROM_SUBSLOT,
            },
            3: {"type": "ram", "size_kb": 64},
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.sub_rom_subslot == 0
    assert result.fdc_subslot == 0
    assert result.flat_ram_subslot == 3


# ---------------------------------------------------------------------------
# _make_mapper / _require_scc raise MachineLoadError, not ValueError
# (allium A-4 / code fix regression guard)
# ---------------------------------------------------------------------------


def test_make_mapper_unknown_type_raises_machine_load_error() -> None:
    with pytest.raises(MachineLoadError, match="unknown mapper type"):
        _make_mapper("NotAMapper", None)


def test_require_scc_none_raises_machine_load_error() -> None:
    with pytest.raises(MachineLoadError, match="requires an SCC instance"):
        _require_scc(None)


def test_make_mapper_konami_scc_without_scc_raises_machine_load_error() -> None:
    with pytest.raises(MachineLoadError, match="requires an SCC instance"):
        _make_mapper("KonamiSCC", bytes(0x4000), scc=None)


# ---------------------------------------------------------------------------
# logo_override bypasses disk loading entirely (allium B-1)
# ---------------------------------------------------------------------------


def test_build_machine_logo_override_bypasses_disk(tmp_path: Path) -> None:
    spec = _make_msx1_spec(tmp_path)
    # spec.logo_rom_entry is None (msx1 fixture declares no page-2 ROM) — set
    # one by hand so the override path has a file to bypass.
    spec.logo_rom_entry = _RomEntry(
        file="nonexistent_logo.rom", size_kb=32, pages=[2], sha1=None
    )
    custom_logo = bytes([0xCD] + [0x00] * 32767)
    machine = build_machine(spec, bios_override=_FAKE_ROM_32K, logo_override=custom_logo)
    assert machine.memory.extrom is not None
    assert machine.memory.extrom[0] == 0xCD
