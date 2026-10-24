"""machine-loader "Machine construction takes data, not paths" and the
MachineConfig document ("The host resolves a machine into a canonical
document", "ROMs are referenced by size and SHA1")."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

import host.romdb as romdb
from host.machine_config import (
    load_device_registry,
    load_extension_overlay,
    load_machine_config,
    load_machine_spec,
)
from msx.boundary import RtcTime
from msx.input import InputFrame
from msx.machine_config import encode_machine_config, parse_machine_config
from msx.machine_loader import build_machine
from msx.machine_spec import MachineLoadError
from msx.state import _snapshot_from_machine
from tests.factories import _msx2_spec, make_config, make_config_msx2, reformat_document

_ROM = bytes(32 * 1024)
_CART = bytes(range(256)) * 64  # 16 KB


def test_building_twice_from_one_config_gives_equal_machines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config_msx2(_ROM, bytes(16 * 1024), cartridge=_CART, mapper="ASCII8SRAM2")
    first = build_machine(config)
    second = build_machine(config)
    for machine in (first, second):
        for _ in range(3):
            machine.step_frame(InputFrame(), render_video=False)
    assert _snapshot_from_machine(first) == _snapshot_from_machine(second)


def test_auto_mapper_is_resolved_before_building(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(romdb, "lookup", lambda cartridge: "Konami")
    config = make_config(_ROM, cartridge=_CART, mapper="auto")
    assert parse_machine_config(config.document).mapper1 == "Konami"

    def no_lookup(cartridge: bytes) -> str | None:
        raise AssertionError("build_machine looked up the ROM database")

    monkeypatch.setattr(romdb, "lookup", no_lookup)
    machine = build_machine(config)
    assert type(machine.memory._mapper).__name__ == "KonamiMapper"


def test_build_reads_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_machine_config(
        _msx2_spec(), bios_override=_ROM, extrom_override=bytes(16 * 1024)
    )

    def no_read(self: Path) -> bytes:
        raise AssertionError(f"build_machine read {self}")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail(f"checked {self}"))
    assert build_machine(config).rtc is not None


def test_sram_size_of_a_non_sram_id_names_it_by_value() -> None:
    from host.machine_config import _sram_size_and_label
    from msx.boundary import MediaId

    config = make_config(_ROM)
    with pytest.raises(ValueError, match="unknown SRAM media id 'drive_a'"):
        _sram_size_and_label(config, MediaId.DRIVE_A)


def test_machine_keyboard_layout_defaults_to_international() -> None:
    # input-keyboard "Default layout is International".
    from msx.machine_spec import KeyboardLayout

    machine = build_machine(make_config(_ROM))
    assert machine.keyboard_layout is KeyboardLayout.INTERNATIONAL
    assert machine.keyboard_layout == "int"


def test_a_jis_machine_definition_gives_the_japanese_layout() -> None:
    from host.machine_config import load_device_registry, load_machine_spec
    from msx.machine_spec import KeyboardLayout

    root = Path(__file__).resolve().parent.parent
    config_dir = root / "config"
    spec = load_machine_spec("hb_f1xd", config_dir, load_device_registry(config_dir), root)
    assert spec.keyboard_type is KeyboardLayout.JAPANESE


# ---------------------------------------------------------------------------
# The MachineConfig document
# ---------------------------------------------------------------------------

_PROJECT = Path(__file__).resolve().parent.parent
_CONFIG = _PROJECT / "config"


def _spec(machine_id: str):
    return load_machine_spec(machine_id, _CONFIG, load_device_registry(_CONFIG), _PROJECT)


def _overlay(extension_id: str):
    return load_extension_overlay(extension_id, _CONFIG, _PROJECT)


def _overrides(spec) -> dict[str, bytes]:
    """ROM bytes for a machine, so tests need no ROM files."""
    out = {"bios_override": bytes([1]) * 32768}
    if spec.generation == "msx2":
        out["extrom_override"] = bytes([2]) * 16384
        if spec.fdc is not None:
            out["disk_rom_override"] = bytes([3]) * 16384
    if spec.logo_rom_entry is not None:
        out["logo_override"] = bytes([4]) * 16384
    return out


def _document(machine_id: str = "hb_f1xd", **kwargs: object) -> bytes:
    spec = _spec(machine_id)
    args = {**_overrides(spec), **kwargs}
    return load_machine_config(spec, **args).document


def _walk(node: object) -> list[object]:
    found: list[object] = [node]
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(key)
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def test_the_same_inputs_give_the_same_document_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    first = _document(cartridge1=_CART, mapper1="ASCII8")
    second = _document(cartridge1=_CART, mapper1="ASCII8")
    assert first == second
    assert json.loads(first)["format_version"] == 1


def test_run_inputs_do_not_change_the_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    disk_a = tmp_path / "a.dsk"
    disk_b = tmp_path / "b.dsk"
    disk_a.write_bytes(bytes(737280))
    disk_b.write_bytes(bytes([0xE5]) * 737280)
    first = _document(fdd1=disk_a, rtc_initial_time=RtcTime(2026, 1, 1, 0, 0, 0))
    sram = tmp_path / "saves" / "sram"
    sram.mkdir(parents=True)
    (sram / "rtc_hb_f1xd.sram").write_bytes(bytes([0x5]) * 52)
    second = _document(fdd1=disk_b, rtc_initial_time=RtcTime(1999, 12, 31, 23, 59, 59))
    assert first == second


def test_no_host_path_is_in_the_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    document = _document(extension_overlay2=_overlay("fmpac"))
    text = document.decode("utf-8")
    for fragment in ("roms/", "saves/", str(_PROJECT), "rom_base", "save_file", "sram"):
        assert fragment not in text
    names = [
        node["name"] for node in _walk(json.loads(document))
        if isinstance(node, dict) and "sha1" in node
    ]
    assert names and all("/" not in name for name in names)


def test_an_override_rom_is_referenced_by_its_own_sha1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    bios = bytes(range(256)) * 128
    config = load_machine_config(
        _spec("cbios_msx1"), bios_override=bios, logo_override=bytes(16384)
    )
    main = json.loads(config.document)["machine"]["main_rom"]
    assert main["sha1"] == hashlib.sha1(bios).hexdigest()
    assert main["size"] == len(bios)
    assert config.roms[main["sha1"]] == bios


def test_a_cbios_rom_with_no_sha1_in_its_yaml_gets_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    document = json.loads(_document("cbios_msx2_jp"))
    refs = [node for node in _walk(document) if isinstance(node, dict) and "sha1" in node]
    assert len(refs) == 3          # main, logo and SUB ROM
    for ref in refs:
        assert len(ref["sha1"]) == 40 and ref["sha1"] == ref["sha1"].lower()


def test_an_unknown_document_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    decoded = json.loads(_document())
    decoded["machine"]["turbo"] = True
    with pytest.raises(MachineLoadError, match="turbo"):
        parse_machine_config(json.dumps(decoded).encode("utf-8"))


@pytest.mark.parametrize(
    "machine_id", sorted(p.stem for p in (_CONFIG / "machines").glob("*.yaml"))
)
def test_every_machine_round_trips_through_its_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, machine_id: str
) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_machine_config(_spec(machine_id), **_overrides(_spec(machine_id)))
    document = config.document
    parsed = parse_machine_config(document)
    roms_by_role = {role: config.roms[ref.sha1] for role, ref in parsed.roms.items()}
    again, images = encode_machine_config(
        parsed.spec, roms_by_role,
        mapper1=parsed.mapper1, mapper2=parsed.mapper2,
        extension_overlay1=parsed.extension_overlay1,
        extension_overlay2=parsed.extension_overlay2,
    )
    assert again == document
    assert images == config.roms


# ---------------------------------------------------------------------------
# machine-loader "build_machine checks the document and its ROM images"
# ---------------------------------------------------------------------------

def _hb_f1xd_config(tmp_path: Path):
    spec = _spec("hb_f1xd")
    return load_machine_config(spec, **_overrides(spec))


def test_a_missing_rom_image_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    sub = json.loads(config.document)["machine"]["sub_rom"]["sha1"]
    del config.roms[sub]
    with pytest.raises(MachineLoadError, match=sub):
        build_machine(config)


def test_a_rom_image_of_the_wrong_content_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    main = json.loads(config.document)["machine"]["main_rom"]["sha1"]
    config.roms[main] = bytes([0xFF]) * len(config.roms[main])
    with pytest.raises(MachineLoadError, match="does not match its reference"):
        build_machine(config)


def test_a_machine_keeps_the_hash_of_its_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    expected = hashlib.sha256(config.document).hexdigest()
    assert build_machine(config).machine_config_sha256 == expected


# ---------------------------------------------------------------------------
# machine-loader "The configuration identity is the hash of the canonical document"
# ---------------------------------------------------------------------------

def test_a_reformatted_document_has_the_canonical_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    reformatted = dataclasses.replace(config, document=reformat_document(config.document))
    assert reformatted.document != config.document
    canonical = build_machine(config).machine_config_sha256
    assert build_machine(reformatted).machine_config_sha256 == canonical
    assert canonical == hashlib.sha256(config.document).hexdigest()
