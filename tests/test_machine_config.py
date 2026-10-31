"""machine-loader "Machine construction takes data, not paths" and the
MachineConfig document ("The host resolves a machine into a canonical
document", "ROMs are referenced by size and SHA1")."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest

import host.romdb as romdb
from host.machine_config import (
    load_device_registry,
    load_extension_overlay,
    load_machine_config,
    load_machine_spec,
)
from msx.boundary import MediaId, MediaImage, RomImage, RtcTime
from msx.input import InputFrame
from msx.machine_config import encode_machine_config, parse_machine_config
from msx.machine_loader import build_machine
from msx.machine_spec import (
    ExpandedSubslotDevice,
    ExtensionOverlay,
    MachineLoadError,
    RomEntry,
)
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


def test_sram_size_of_a_non_sram_id_names_it_by_name() -> None:
    from host.machine_config import _sram_size_and_label
    from msx.boundary import MediaId

    config = make_config(_ROM)
    with pytest.raises(ValueError, match="unknown SRAM media id DRIVE_A$"):
        _sram_size_and_label(config, MediaId.DRIVE_A)


def test_machine_properties_are_integers() -> None:
    # machine-core "Machine properties are integers".
    from msx.boundary import KeyboardLayout, VideoStandard

    spec = _spec("cbios_msx1_jp")
    machine = build_machine(load_machine_config(spec, **_overrides(spec)))
    assert isinstance(machine.video_standard, int)
    assert isinstance(machine.keyboard_layout, int)
    assert machine.video_standard == VideoStandard.NTSC
    assert machine.keyboard_layout == KeyboardLayout.JAPANESE


# The canonical documents' SHA-256, recorded before the video standard and
# the keyboard layout became integer enums: their text spellings did not change.
_DOCUMENT_SHA256 = {
    "cbios_msx1_eu": "5a331a17c716dc351ff29a63bb4bd9cca6d0f377a02fcf79fa1b3b944a4eb6ce",
    "cbios_msx1_jp": "cf2862c23ba6c8e1f021ce233a60d36940dbafbcd2c9f50a7201fdd128649091",
    "hb_f1xd": "ce921d1109ca7a20f43015f19e7cb63427e4e8842840a1449309c1bd7bd45cde",
}


@pytest.mark.parametrize("machine_id", sorted(_DOCUMENT_SHA256))
def test_the_canonical_document_spells_the_enums_as_text(machine_id: str) -> None:
    document = _document(machine_id)
    machine = json.loads(document)["machine"]
    assert machine["video_standard"] in ("ntsc", "pal")
    assert machine["keyboard_layout"] in ("int", "jp")
    assert hashlib.sha256(document).hexdigest() == _DOCUMENT_SHA256[machine_id]
    parsed = parse_machine_config(document).spec
    assert parsed.video_standard.spelling == machine["video_standard"]
    assert parsed.keyboard_type.spelling == machine["keyboard_layout"]


def test_machine_keyboard_layout_defaults_to_international() -> None:
    # input-keyboard "Default layout is International".
    from msx.boundary import KeyboardLayout

    machine = build_machine(make_config(_ROM))
    assert machine.keyboard_layout is KeyboardLayout.INTERNATIONAL


def test_a_jis_machine_definition_gives_the_japanese_layout() -> None:
    from host.machine_config import load_device_registry, load_machine_spec
    from msx.boundary import KeyboardLayout

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
    assert RomImage(main["sha1"], bios) in config.roms


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
    images_by_sha1 = {rom.sha1: rom.data for rom in config.roms}
    roms_by_role = {role: images_by_sha1[ref.sha1] for role, ref in parsed.roms.items()}
    again, images = encode_machine_config(
        parsed.spec, roms_by_role,
        mapper1=parsed.mapper1, mapper2=parsed.mapper2,
        extension_overlay1=parsed.extension_overlay1,
        extension_overlay2=parsed.extension_overlay2,
    )
    assert again == document
    assert images == images_by_sha1


# ---------------------------------------------------------------------------
# machine-loader "build_machine checks the document and its ROM images"
# ---------------------------------------------------------------------------

def _hb_f1xd_config(tmp_path: Path):
    spec = _spec("hb_f1xd")
    return load_machine_config(spec, **_overrides(spec))


# ---------------------------------------------------------------------------
# machine-core "The core builds a machine and reports a failure as a status"
# ---------------------------------------------------------------------------

def test_a_valid_configuration_builds_a_machine() -> None:
    from msx.boundary import STATUS_OK
    from msx.machine_loader import create_machine

    result = create_machine(make_config(_ROM))
    assert result.status == STATUS_OK
    assert result.machine is not None
    assert result.error_text == b""


def _without_main_rom(config):
    main = json.loads(config.document)["machine"]["main_rom"]["sha1"]
    config.roms = tuple(rom for rom in config.roms if rom.sha1 != main)
    return config, main


def test_a_rejected_configuration_returns_a_status_and_a_message() -> None:
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    config, main = _without_main_rom(make_config(_ROM))
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert result.machine is None
    assert main.encode() in result.error_text


def test_the_wrapper_raises_the_same_message() -> None:
    from msx.machine_loader import create_machine

    config, _main = _without_main_rom(make_config(_ROM))
    message = create_machine(config).error_text.decode()
    with pytest.raises(MachineLoadError) as exc_info:
        build_machine(config)
    assert str(exc_info.value) == message


def test_two_records_for_one_medium_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # machine-loader "Two records for one medium are rejected".
    monkeypatch.chdir(tmp_path)
    config = make_config(_ROM)
    config.media = (
        MediaImage(MediaId.SLOT1_SRAM, bytes(8192)), MediaImage(MediaId.SLOT1_SRAM, bytes(8192)),
    )
    with pytest.raises(MachineLoadError, match="two medium images for SLOT1_SRAM"):
        build_machine(config)


def test_a_drive_id_is_not_a_medium_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = make_config(_ROM)
    config.media = (MediaImage(MediaId.DRIVE_A, bytes(737280)),)
    with pytest.raises(MachineLoadError, match="DRIVE_A, a drive id"):
        build_machine(config)


def test_two_plain_int_records_for_one_medium_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # machine-loader "Two plain-int records for one medium are rejected".
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    monkeypatch.chdir(tmp_path)
    config = make_config(_ROM)
    config.media = (MediaImage(0, bytes(8192)), MediaImage(0, bytes(8192)))
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert b"two medium images for SLOT1_SRAM" in result.error_text


def test_a_plain_int_drive_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    monkeypatch.chdir(tmp_path)
    config = make_config(_ROM)
    config.media = (MediaImage(12, bytes(737280)),)
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert b"DRIVE_A, a drive id" in result.error_text


def test_an_unknown_medium_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # machine-loader "An unknown medium id is rejected".
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    monkeypatch.chdir(tmp_path)
    config = make_config(_ROM)
    config.media = (MediaImage(99, bytes(8192)),)
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert b"unknown id 99" in result.error_text


def test_a_missing_rom_image_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    sub = json.loads(config.document)["machine"]["sub_rom"]["sha1"]
    config.roms = tuple(rom for rom in config.roms if rom.sha1 != sub)
    with pytest.raises(MachineLoadError, match=sub):
        build_machine(config)


def test_a_rom_image_of_the_wrong_content_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)
    main = json.loads(config.document)["machine"]["main_rom"]["sha1"]
    config.roms = tuple(
        RomImage(rom.sha1, bytes([0xFF]) * len(rom.data)) if rom.sha1 == main else rom
        for rom in config.roms
    )
    with pytest.raises(MachineLoadError, match="does not match its reference"):
        build_machine(config)


def _edited(config, edit) -> object:
    """`config` with its document decoded, changed by `edit` and re-encoded."""
    decoded = json.loads(config.document)
    edit(decoded)
    return dataclasses.replace(config, document=json.dumps(decoded).encode("utf-8"))


@pytest.mark.parametrize(
    ("controller", "style", "field"),
    [
        ("xyz", "sony", "machine.fdc.controller"),
        ("wd2793", "xyz", "machine.fdc.connection_style"),
        ("wd2793", "tc8566af", "machine.fdc"),
    ],
)
def test_an_unsupported_fdc_controller_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, controller: str, style: str, field: str
) -> None:
    monkeypatch.chdir(tmp_path)

    def edit(decoded: dict) -> None:
        decoded["machine"]["fdc"]["controller"] = controller
        decoded["machine"]["fdc"]["connection_style"] = style

    with pytest.raises(MachineLoadError, match=re.escape(field)):
        build_machine(_edited(_hb_f1xd_config(tmp_path), edit))


_FLAT = {"shape": "flat", "device": "fmpac", "rom": None}
_EXPANDED_DEVICE = {"device": "flat_rom", "rom": None, "size_kb": None}


def _expanded(device: dict) -> dict:
    return {"shape": "expanded", "subslots": {"0": device}, "io_device": None}


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (("cartridges", "slot1", "mapper"), "Unknown", "cartridges.slot1.mapper"),
        (("extensions", "slot2"), {**_FLAT, "device": "halnote"}, "extensions.slot2.device"),
        (
            ("extensions", "slot2"), _expanded({**_EXPANDED_DEVICE, "device": "fmpac"}),
            "extensions.slot2.subslots.0.device",
        ),
        (
            ("machine", "io_device"), {**_EXPANDED_DEVICE, "device": "halnote"},
            "machine.io_device.device",
        ),
    ],
)
def test_an_unsupported_mapper_or_device_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    path: tuple[str, ...], value: object, field: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    def edit(decoded: dict) -> None:
        node = decoded
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    with pytest.raises(MachineLoadError, match=re.escape(field) + ".*must be one of"):
        build_machine(_edited(_hb_f1xd_config(tmp_path), edit))


@pytest.mark.parametrize(
    ("overlay", "field"),
    [
        (_FLAT, "extensions.slot2.rom"),
        (
            _expanded({**_EXPANDED_DEVICE, "device": "ram_mapper"}),
            "extensions.slot2.subslots.0.size_kb",
        ),
        (_expanded(_EXPANDED_DEVICE), "extensions.slot2.subslots.0.rom"),
    ],
)
def test_a_device_without_its_required_rom_or_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overlay: dict, field: str
) -> None:
    monkeypatch.chdir(tmp_path)

    def edit(decoded: dict) -> None:
        decoded["extensions"]["slot2"] = overlay

    with pytest.raises(MachineLoadError, match=re.escape(field) + " is required"):
        build_machine(_edited(_hb_f1xd_config(tmp_path), edit))


def test_the_mapper_builders_cover_exactly_the_mapper_names() -> None:
    from msx.machine_config import MAPPER_NAMES
    from msx.machine_loader import _MAPPER_BUILDERS

    assert set(_MAPPER_BUILDERS) == MAPPER_NAMES


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


def test_an_scc_i_fragment_rom_block_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The SCC-I has no ROM: a `rom:` block in its fragment is not read (so a
    # missing file is no error) and the document references no image for it.
    monkeypatch.chdir(tmp_path)
    overlay = ExtensionOverlay(
        device="scc_i_cart", rom_base_dir=tmp_path,
        rom_entry=RomEntry(file="missing_scc.rom", size_kb=64, pages=[]),
    )
    spec = _spec("hb_f1xd")
    config = load_machine_config(spec, **_overrides(spec), extension_overlay2=overlay)
    assert json.loads(config.document)["extensions"]["slot2"] == {
        "shape": "flat", "device": "scc_i_cart", "rom": None,
    }
    assert build_machine(config).scc is not None


def test_a_device_without_a_rom_file_to_read_is_a_load_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A kanji io_device parsed back from a document has no rom_base_dir.
    monkeypatch.chdir(tmp_path)
    spec = dataclasses.replace(
        _spec("hb_f1xd"), io_device=ExpandedSubslotDevice(device="kanji_rom")
    )
    with pytest.raises(MachineLoadError, match="machine io_device: no ROM file to read"):
        load_machine_config(spec, **_overrides(spec))


def test_a_ram_mapper_size_the_mapper_does_not_accept_is_rejected() -> None:
    # machine-core "A RAM mapper size the mapper does not accept is rejected".
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    config = make_config_msx2(_ROM, bytes(16 * 1024))
    document = json.loads(config.document)
    document["machine"]["ram_mapper_size_kb"] = 8
    config.document = json.dumps(document).encode("utf-8")
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert result.machine is None


@pytest.mark.parametrize(
    "start",
    [
        RtcTime(2000, 13, 1, 0, 0, 0),
        RtcTime(2000, 1, 40, 0, 0, 0),
        RtcTime(2000, 1, 1, 24, 0, 0),
        RtcTime(-1, 1, 1, 0, 0, 0),
        RtcTime(10000, 1, 1, 0, 0, 0),
    ],
)
def test_an_invalid_rtc_start_time_is_rejected(start: RtcTime) -> None:
    # machine-core "An invalid RTC start time is rejected".
    from msx.boundary import STATUS_BAD_CONFIG
    from msx.machine_loader import create_machine

    config = make_config_msx2(_ROM, bytes(16 * 1024))
    config.rtc_initial_time = start
    result = create_machine(config)
    assert result.status == STATUS_BAD_CONFIG
    assert result.machine is None


def test_a_valid_rtc_start_time_survives_a_state_round_trip() -> None:
    # machine-core "A valid RTC start time survives a state round trip".
    from msx.boundary import STATUS_OK
    from msx.machine_loader import create_machine

    config = make_config_msx2(_ROM, bytes(16 * 1024))
    config.rtc_initial_time = RtcTime(2024, 2, 29, 23, 59, 59)
    result = create_machine(config)
    assert result.status == STATUS_OK
    assert result.machine is not None
    assert result.machine.load_state(result.machine.save_state()) == STATUS_OK


def test_a_configuration_without_a_start_time_starts_at_2000() -> None:
    # machine-core "A configuration without a start time starts at 2000".
    from msx.boundary import RTC_DEFAULT_START, MachineConfig, RtcTime
    from msx.machine_loader import create_machine

    assert MachineConfig(document=b"{}").rtc_initial_time == RTC_DEFAULT_START
    assert RTC_DEFAULT_START == RtcTime(2000, 1, 1, 0, 0, 0)
    machine = create_machine(make_config_msx2(_ROM, bytes(16 * 1024))).machine
    assert machine is not None
    machine.rtc._refresh_time_regs()
    # Block 0 BCD digits: seconds, minutes, hours (0-5), day (7, 8),
    # month (9, 10), years since 1980 (11, 12).
    regs = machine.rtc._regs
    assert regs[0:6] == [0, 0, 0, 0, 0, 0]
    assert (regs[7], regs[8], regs[9], regs[10]) == (1, 0, 1, 0)
    assert (regs[11], regs[12]) == (0, 2)


def test_the_start_time_admits_no_none() -> None:
    # machine-core "The start time admits no None".
    import typing

    from msx.boundary import MachineConfig

    hint = typing.get_type_hints(MachineConfig)["rtc_initial_time"]
    assert hint is not type(None)
    assert type(None) not in typing.get_args(hint)


def _edited_document(edit) -> bytes:
    decoded = json.loads(_document())
    edit(decoded["machine"])
    return json.dumps(decoded).encode("utf-8")


@pytest.mark.parametrize(("field", "value"), [
    # machine-loader "A RAM size above the address space is rejected".
    ("ram_size_kb", 128),
    ("flat_ram_size_kb", 128),
    # machine-loader "A RAM mapper above 256 segments is rejected".
    ("ram_mapper_size_kb", 8192),
    ("ram_mapper_size_kb", 8),
])
def test_a_ram_size_outside_its_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: int
) -> None:
    monkeypatch.chdir(tmp_path)
    document = _edited_document(lambda machine: machine.__setitem__(field, value))
    with pytest.raises(MachineLoadError, match=field):
        parse_machine_config(document)


@pytest.mark.parametrize(("key", "ports"), [
    # machine-loader "An unknown I/O device is rejected".
    ("no_such_device", [0xA0, 0xA2]),
    # machine-loader "A reversed I/O range is rejected".
    ("psg_ay8910", [0xA2, 0xA0]),
])
def test_a_bad_io_range_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str, ports: list[int]
) -> None:
    monkeypatch.chdir(tmp_path)
    document = _edited_document(lambda machine: machine["device_io_ports"].__setitem__(key, ports))
    with pytest.raises(MachineLoadError, match=key):
        parse_machine_config(document)
