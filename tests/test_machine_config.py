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
from msx.boundary import STATUS_NO_TAPE_PORT, MediaId, MediaImage, RomImage, RtcTime
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
    assert type(machine.memory.mapper1).__name__ == "KonamiMapper"


def test_build_reads_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_machine_config(
        _msx2_spec(), bios_override=_ROM, extrom_override=bytes(16 * 1024)
    ).config

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
    machine = build_machine(load_machine_config(spec, **_overrides(spec)).config)
    assert isinstance(machine.video_standard, int)
    assert isinstance(machine.keyboard_layout, int)
    assert machine.video_standard == VideoStandard.NTSC
    assert machine.keyboard_layout == KeyboardLayout.JAPANESE


# The canonical documents' SHA-256, recorded at format version 2. The video
# standard and the keyboard layout stay text in the document.
_DOCUMENT_SHA256 = {
    "cbios_msx1_eu": "a599559d7ab11bd0d2da0a16896f151ceffdabf2fd20d2cf89bee549811ef24c",
    "cbios_msx1_jp": "ac71696b425bc0b22209283ebbd16d0dde02f27371abc58666eeaaa79dbb3cac",
    "hb_f1xd": "f14b9a7d9a74241afd71b81511acf00c6b1c96db63e054c3ee159714eefd51d6",
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
    assert parsed.printer_port.value == machine["printer_port"]
    assert parsed.cassette_port == machine["cassette_port"]


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
    return load_machine_config(spec, **args).config.document


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
    assert json.loads(first)["format_version"] == 8


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
    ).config
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
    config = load_machine_config(_spec(machine_id), **_overrides(_spec(machine_id))).config
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

def format_1_config(machine_id: str = "hb_10", keep_printer_port: bool = False):
    """A machine's config with its document written as format version 1."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 1
    del document["machine"]["mapper_io"]
    del document["machine"]["psg_read_back"]
    del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    del document["machine"]["cassette_port"]
    del document["machine"]["ram_mapper_subslot"]
    if not keep_printer_port:
        del document["machine"]["printer_port"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def format_2_config(machine_id: str = "hb_10", keep_cassette_port: bool = False):
    """A machine's config with its document written as format version 2."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 2
    del document["machine"]["mapper_io"]
    del document["machine"]["psg_read_back"]
    del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    del document["machine"]["ram_mapper_subslot"]
    if not keep_cassette_port:
        del document["machine"]["cassette_port"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def format_3_config(machine_id: str = "cbios_msx2", keep_subslot: bool = False):
    """A machine's config with its document written as format version 3."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 3
    del document["machine"]["mapper_io"]
    del document["machine"]["psg_read_back"]
    del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    if not keep_subslot:
        del document["machine"]["ram_mapper_subslot"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def test_a_format_3_documents_mapper_has_no_subslot() -> None:
    # machine-loader "A format 3 document's mapper has no sub-slot".
    machine = build_machine(format_3_config())
    assert machine.memory.ram_mapper is not None
    assert machine.memory.ram_mapper_subslot is None


def test_a_format_3_document_with_ram_mapper_subslot_is_rejected() -> None:
    # machine-loader "A format 3 document with ram_mapper_subslot is rejected".
    with pytest.raises(MachineLoadError, match="ram_mapper_subslot"):
        build_machine(format_3_config(keep_subslot=True))


def format_4_config(machine_id: str = "hb_f1xd", keep_mapper_io: bool = False):
    """A machine's config with its document written as format version 4."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 4
    del document["machine"]["psg_read_back"]
    del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    if not keep_mapper_io:
        del document["machine"]["mapper_io"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def test_a_format_4_document_has_the_plain_mapper_port_form() -> None:
    # machine-loader "A format 4 document has the plain mapper port form".
    machine = build_machine(format_4_config())
    assert machine.mapper_latch is None
    assert machine.io.read_port(0xFC) == 0xFF


def test_a_format_4_document_with_mapper_io_is_rejected() -> None:
    # machine-loader "A format 4 document with mapper_io is rejected".
    with pytest.raises(MachineLoadError, match="mapper_io"):
        build_machine(format_4_config(keep_mapper_io=True))


def test_the_mapper_port_form_round_trips_through_the_document() -> None:
    # machine-loader "The mapper port form round-trips through the document".
    from msx.machine_config import parse_machine_config
    from msx.machine_spec import MapperPortForm

    parsed = parse_machine_config(_document("hb_f1xd"))
    assert parsed.spec.mapper_io is MapperPortForm.S1985


def format_5_config(machine_id: str = "hb_f1xd", keep_psg_read_back: bool = False):
    """A machine's config with its document written as format version 5."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 5
    del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    if not keep_psg_read_back:
        del document["machine"]["psg_read_back"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def test_a_format_5_document_has_the_ym2149_read_back_form() -> None:
    # machine-loader "A format 5 document has the YM2149 read-back form".
    from msx.machine_spec import PsgReadBack

    assert parse_machine_config(format_5_config().document).spec.psg_read_back is (
        PsgReadBack.YM2149
    )


def test_a_format_5_document_with_psg_read_back_is_rejected() -> None:
    # machine-loader "A format 5 document with psg_read_back is rejected".
    with pytest.raises(MachineLoadError, match="psg_read_back"):
        build_machine(format_5_config(keep_psg_read_back=True))


def test_the_psg_read_back_form_round_trips_through_the_document() -> None:
    # machine-loader "The PSG read-back form round-trips through the document".
    from msx.machine_spec import PsgReadBack

    parsed = parse_machine_config(_document("hb_f1xd"))
    assert parsed.spec.psg_read_back is PsgReadBack.AY8910


def format_6_config(machine_id: str = "hb_f1xd", keep_rtc_read_back: bool = False):
    """A machine's config with its document written as format version 6."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 6
    if not keep_rtc_read_back:
        del document["machine"]["rtc_read_back"]
    del document["machine"]["psg_pin67_read"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def test_a_format_6_document_has_the_plain_rtc_read_back_form() -> None:
    # machine-loader "A format 6 document has the plain RTC read-back form".
    from msx.machine_spec import RtcReadBack

    assert parse_machine_config(format_6_config().document).spec.rtc_read_back is (
        RtcReadBack.PLAIN
    )


def test_a_format_6_document_with_rtc_read_back_is_rejected() -> None:
    # machine-loader "A format 6 document with rtc_read_back is rejected".
    with pytest.raises(MachineLoadError, match="rtc_read_back"):
        build_machine(format_6_config(keep_rtc_read_back=True))


def test_the_rtc_read_back_form_round_trips_through_the_document() -> None:
    # machine-loader "The RTC read-back form round-trips through the document".
    from msx.machine_spec import RtcReadBack

    parsed = parse_machine_config(_document("hb_f1xd"))
    assert parsed.spec.rtc_read_back is RtcReadBack.S1985


def format_7_config(machine_id: str = "hb_f1xd", keep_psg_pin67_read: bool = False):
    """A machine's config with its document written as format version 7."""
    spec = _spec(machine_id)
    config = load_machine_config(spec, **_overrides(spec)).config
    document = json.loads(config.document)
    document["format_version"] = 7
    if not keep_psg_pin67_read:
        del document["machine"]["psg_pin67_read"]
    config.document = json.dumps(document).encode("utf-8")
    return config


def test_a_format_7_document_has_the_plain_psg_pin67_read_form() -> None:
    # machine-loader "A format 7 document has the plain PSG pin 6/7 read form".
    from msx.machine_spec import PsgPin67Read

    assert parse_machine_config(format_7_config().document).spec.psg_pin67_read is (
        PsgPin67Read.PLAIN
    )


def test_a_format_7_document_with_psg_pin67_read_is_rejected() -> None:
    # machine-loader "A format 7 document with psg_pin67_read is rejected".
    with pytest.raises(MachineLoadError, match="psg_pin67_read"):
        build_machine(format_7_config(keep_psg_pin67_read=True))


def test_the_pin67_read_form_round_trips_through_the_document() -> None:
    # machine-loader "The pin 6/7 read form round-trips through the document".
    from msx.machine_spec import PsgPin67Read

    parsed = parse_machine_config(_document("hb_f1xd"))
    assert parsed.spec.psg_pin67_read is PsgPin67Read.GATED


def test_a_ram_mapper_subslot_without_a_mapper_is_rejected() -> None:
    config = format_3_config("hb_f1xd", keep_subslot=True)
    document = json.loads(config.document)
    document["format_version"] = 4
    document["machine"]["ram_mapper_subslot"] = 3
    config.document = json.dumps(document).encode("utf-8")
    with pytest.raises(MachineLoadError, match="ram_mapper_subslot: set without has_ram_mapper"):
        build_machine(config)


def test_a_format_2_document_has_no_cassette_port() -> None:
    # machine-loader "A format 2 document has no cassette port".
    machine = build_machine(format_2_config())
    assert machine.cassette is None
    assert machine.printer is not None
    assert machine.mount_tape(b"\x10\x00\x00\x00") == STATUS_NO_TAPE_PORT


def test_a_format_2_document_with_cassette_port_is_rejected() -> None:
    # machine-loader "A format 2 document with cassette_port is rejected".
    with pytest.raises(MachineLoadError, match="cassette_port"):
        build_machine(format_2_config(keep_cassette_port=True))


def test_a_format_1_document_has_no_printer_port() -> None:
    # machine-loader "A format 1 document has no printer port".
    machine = build_machine(format_1_config())
    assert machine.printer is None
    assert machine.cassette is None


def test_a_format_1_document_with_printer_port_is_rejected() -> None:
    # machine-loader "A format 1 document with printer_port is rejected".
    with pytest.raises(MachineLoadError, match="printer_port"):
        build_machine(format_1_config(keep_printer_port=True))


@pytest.mark.parametrize("version", [0, 9])
def test_an_unsupported_format_version_is_rejected(version: int) -> None:
    # machine-loader "An unsupported format version is rejected".
    config = format_1_config()
    document = json.loads(config.document)
    document["format_version"] = version
    config.document = json.dumps(document).encode("utf-8")
    with pytest.raises(MachineLoadError, match=f"format_version {version} "):
        build_machine(config)


@pytest.mark.parametrize(("version", "message"), [(9, "version 9, expected 1 to 8"),
                                                  (1, "not valid JSON")])
def test_a_document_that_is_not_strict_json_names_an_unsupported_version(
    version: int, message: str
) -> None:
    config = format_1_config()
    config.document = config.document.replace(
        b'"format_version": 1', f'"format_version": {version}, "x": 1.5'.encode()
    )
    with pytest.raises(MachineLoadError, match=message):
        build_machine(config)

def _hb_f1xd_config(tmp_path: Path):
    spec = _spec("hb_f1xd")
    return load_machine_config(spec, **_overrides(spec)).config


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
    config = load_machine_config(spec, **_overrides(spec), extension_overlay2=overlay).config
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
        load_machine_config(spec, **_overrides(spec)).config


def test_a_machine_without_a_rom_base_dir_cannot_read_its_rom_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # machine-config-loader RejectRomLoadWithoutRomBaseDir, machine level.
    monkeypatch.chdir(tmp_path)
    spec = dataclasses.replace(_spec("hb_f1xd"), rom_base_dir=None)
    with pytest.raises(MachineLoadError, match="has no rom_base_dir to read"):
        load_machine_config(spec)


def test_a_missing_optional_logo_rom_is_skipped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # machine-config-loader SkipMissingOptionalRom.
    monkeypatch.chdir(tmp_path)
    spec = dataclasses.replace(
        _spec("hb_f1xd"), rom_base_dir=tmp_path,
        logo_rom_entry=RomEntry(file="logo.rom", size_kb=16, pages=[]),
    )
    overrides = _overrides(spec)
    del overrides["logo_override"]
    config = load_machine_config(spec, **overrides).config
    assert json.loads(config.document)["machine"]["logo_rom"] is None
    assert "optional ROM file not found, skipping" in capsys.readouterr().err


def test_a_saved_sram_of_the_wrong_size_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # machine-config-loader DiscardMismatchedSram, host side.
    monkeypatch.chdir(tmp_path)
    save = tmp_path / "fmpac.sram"
    save.write_bytes(bytes(100))
    overlay = dataclasses.replace(_overlay("fmpac"), sram_save_path=save)
    spec = _spec("hb_f1xd")
    config = load_machine_config(spec, **_overrides(spec), extension_overlay2=overlay).config
    assert all(image.media_id != MediaId.FMPAC_SRAM for image in config.media)
    assert "has wrong size (100 != 8192), starting fresh" in capsys.readouterr().err


def test_an_fmpac_rom_of_the_wrong_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fmpac.rom").write_bytes(bytes(16 * 1024))
    overlay = dataclasses.replace(
        _overlay("fmpac"), rom_base_dir=tmp_path,
        rom_entry=RomEntry(file="fmpac.rom", size_kb=16, pages=[]),
    )
    spec = _spec("hb_f1xd")
    config = load_machine_config(spec, **_overrides(spec), extension_overlay2=overlay).config
    with pytest.raises(MachineLoadError, match="the FM-PAC ROM is 16384 bytes, not 65536"):
        build_machine(config)


def test_a_rom_image_of_the_wrong_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The SHA1 matches; only the size the reference records differs.
    monkeypatch.chdir(tmp_path)
    config = _hb_f1xd_config(tmp_path)

    def edit(decoded: dict) -> None:
        decoded["machine"]["main_rom"]["size"] += 1

    with pytest.raises(MachineLoadError, match="expected 32769 bytes"):
        build_machine(_edited(config, edit))


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
    # Block 0 BCD digits: seconds, minutes, hours (0-5), day (7, 8),
    # month (9, 10), years since 1980 (11, 12).
    regs = list(machine.rtc.cmos.data())
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


# --- machine-loader "A machine declares its cassette port" ---

_ALL_MACHINES = sorted(p.stem for p in (_CONFIG / "machines").glob("*.yaml"))


@pytest.mark.parametrize("machine_id", _ALL_MACHINES)
def test_every_shipped_machine_has_a_cassette_port(machine_id: str) -> None:
    assert _spec(machine_id).cassette_port is True


def test_a_machine_without_the_entry_has_no_cassette_port(tmp_path: Path) -> None:
    import shutil

    from host.machine_config import load_device_registry, load_machine_spec

    config = tmp_path / "config"
    shutil.copytree(_CONFIG, config)
    path = config / "machines" / "hb_10.yaml"
    lines = path.read_text().splitlines(keepends=True)
    start = lines.index("  - ref: cassette_port\n")
    end = start + 1
    while lines[end].startswith("    #"):
        end += 1
    path.write_text("".join(lines[:start] + lines[end:]))
    spec = load_machine_spec("hb_10", config, load_device_registry(config), _PROJECT)
    assert spec.cassette_port is False


def test_the_cassette_port_round_trips_through_the_document() -> None:
    document = _document("hb_10")
    assert json.loads(document)["machine"]["cassette_port"] is True
    assert parse_machine_config(document).spec.cassette_port is True
