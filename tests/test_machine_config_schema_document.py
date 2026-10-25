"""msx/machine_config.schema.json against the documents the host produces
(machine-loader "The document layout is published as a JSON Schema")."""
from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest

from host.machine_config import load_device_registry, load_extension_overlay, load_machine_spec
from msx import machine_config
from msx.machine_config import encode_machine_config
from msx.machine_spec import AnyExtensionOverlay, ExpandedExtensionOverlay, MachineSpec

jsonschema = pytest.importorskip("jsonschema")

_PROJECT = Path(__file__).resolve().parent.parent
_CONFIG = _PROJECT / "config"
_SCHEMA_PATH = _PROJECT / "msx" / "machine_config.schema.json"
_MACHINES = sorted(p.stem for p in (_CONFIG / "machines").glob("*.yaml"))
_EXTENSIONS = sorted(p.stem for p in (_CONFIG / "extensions").glob("*.yaml"))


def _schema() -> dict[str, typing.Any]:
    return typing.cast(dict[str, typing.Any], json.loads(_SCHEMA_PATH.read_text()))


def _spec(machine_id: str) -> MachineSpec:
    return load_machine_spec(machine_id, _CONFIG, load_device_registry(_CONFIG), _PROJECT)


def _machine_roms(spec: MachineSpec) -> dict[str, bytes]:
    """Stand-in images for every ROM role the machine reads."""
    roms = {"main": bytes([1]) * 32768}
    if spec.logo_rom_entry is not None:
        roms["logo"] = bytes([2]) * 16384
    if spec.sub_rom_entry is not None:
        roms["sub"] = bytes([3]) * 16384
    if spec.fdc is not None:
        roms["disk"] = bytes([4]) * 16384
    if spec.io_device is not None and spec.io_device.rom_entry is not None:
        roms["io_device"] = bytes([5]) * 1024
    return roms


def _overlay_roms(slot: int, overlay: AnyExtensionOverlay) -> dict[str, bytes]:
    roms: dict[str, bytes] = {}
    if isinstance(overlay, ExpandedExtensionOverlay):
        for index, sub in overlay.subslots.items():
            if sub.rom_entry is not None:
                roms[f"slot{slot}.{index}"] = bytes([6 + index]) * 1024
        if overlay.io_device is not None and overlay.io_device.rom_entry is not None:
            roms[f"slot{slot}.io_device"] = bytes([10]) * 1024
    elif overlay.rom_entry is not None:
        roms[f"slot{slot}.ext"] = bytes([11]) * 65536
    return roms


def _documents() -> dict[str, bytes]:
    documents: dict[str, bytes] = {}
    for machine_id in _MACHINES:
        spec = _spec(machine_id)
        documents[machine_id] = encode_machine_config(
            spec, _machine_roms(spec), cartridge1=bytes(range(256)) * 64, mapper1="ASCII8"
        )[0]
    spec = _spec("hb_f1xd")
    for extension_id in _EXTENSIONS:
        overlay = load_extension_overlay(extension_id, _CONFIG, _PROJECT)
        roms = {**_machine_roms(spec), **_overlay_roms(2, overlay)}
        documents[f"hb_f1xd + {extension_id}"] = encode_machine_config(
            spec, roms, extension_overlay2=overlay
        )[0]
    return documents


def test_the_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_documents_of_every_machine_and_extension_validate() -> None:
    validator = jsonschema.Draft202012Validator(_schema())
    shapes: set[str] = set()
    for name, document in _documents().items():
        decoded = json.loads(document)
        errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(decoded)]
        assert errors == [], name
        overlay = decoded["extensions"]["slot2"]
        if overlay is not None:
            shapes.add(overlay["shape"])
    assert shapes == {"flat", "expanded"}


def test_a_document_with_an_extra_or_missing_key_does_not_validate() -> None:
    validator = jsonschema.Draft202012Validator(_schema())
    document = json.loads(_documents()["hb_f1xd"])
    document["machine"]["turbo"] = True
    assert not validator.is_valid(document)
    del document["machine"]["turbo"]
    del document["machine"]["main_rom"]["sha1"]
    assert not validator.is_valid(document)


def _properties(definition: dict[str, typing.Any]) -> set[str]:
    assert definition["additionalProperties"] is False
    assert sorted(definition["required"]) == sorted(definition["properties"])
    return set(definition["properties"])


def test_the_schema_and_the_parser_name_the_same_keys() -> None:
    schema = _schema()
    defs = schema["$defs"]
    machine = defs["Machine"]["properties"]
    assert _properties(schema) == machine_config._TOP_KEYS
    assert _properties(defs["Machine"]) == machine_config._MACHINE_KEYS
    assert _properties(defs["RomRef"]) == machine_config._ROM_KEYS
    assert machine_config._ROM_KEYS == set(machine_config.RomRef.__dataclass_fields__)
    assert _properties(machine["slot0_ram"]["anyOf"][0]) == machine_config._SLOT0_RAM_KEYS
    assert _properties(machine["fdc"]["anyOf"][0]) == machine_config._FDC_KEYS
    assert _properties(defs["Device"]) == machine_config._DEVICE_KEYS
    assert _properties(defs["Cartridge"]) == machine_config._CARTRIDGE_KEYS
    assert _properties(defs["FlatOverlay"]) == machine_config._FLAT_KEYS
    assert _properties(defs["ExpandedOverlay"]) == machine_config._EXPANDED_KEYS
    for pair in ("cartridges", "extensions"):
        assert _properties(schema["properties"][pair]) == machine_config._SLOT_PAIR_KEYS


# ---------------------------------------------------------------------------
# "The schema and the parser name the same closed sets"
# ---------------------------------------------------------------------------

def _device_enum(definition: dict[str, typing.Any]) -> set[str]:
    return set(definition["allOf"][1]["properties"]["device"]["enum"])


def _required_by(definition: dict[str, typing.Any], field: str) -> set[str]:
    """The device names whose if/then block makes `field` non-null."""
    names: set[str] = set()
    for block in definition["allOf"]:
        if "if" in block and field in block["then"]["properties"]:
            names |= set(block["if"]["properties"]["device"]["enum"])
    return names


def test_the_schema_and_the_parser_name_the_same_closed_sets() -> None:
    defs = _schema()["$defs"]
    fdc = defs["Machine"]["properties"]["fdc"]["anyOf"][0]
    assert set(defs["Cartridge"]["properties"]["mapper"]["enum"]) == machine_config.MAPPER_NAMES
    assert set(fdc["properties"]["controller"]["enum"]) == machine_config.FDC_CONTROLLERS
    assert set(fdc["properties"]["connection_style"]["enum"]) == (
        machine_config.FDC_CONNECTION_STYLES
    )
    pairs = {
        (p["properties"]["controller"]["const"], p["properties"]["connection_style"]["const"])
        for p in fdc["anyOf"]
    }
    assert pairs == machine_config.FDC_PAIRS
    flat = defs["FlatOverlay"]
    assert set(flat["properties"]["device"]["enum"]) == machine_config.EXTENSION_DEVICES
    assert _required_by(flat, "rom") == machine_config.EXTENSION_DEVICES_REQUIRING_ROM
    subslot = defs["SubslotDevice"]
    assert _device_enum(subslot) == machine_config.SUBSLOT_DEVICES
    assert _required_by(subslot, "rom") == machine_config.SUBSLOT_DEVICES_REQUIRING_ROM
    assert _required_by(subslot, "size_kb") == machine_config.SUBSLOT_DEVICES_REQUIRING_SIZE
    io = defs["IoDevice"]
    assert _device_enum(io) == machine_config.IO_DEVICES
    assert _required_by(io, "rom") == machine_config.IO_DEVICES_REQUIRING_ROM


_FLAT = {"shape": "flat", "device": "fmpac", "rom": None}


def _expanded(device: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {"shape": "expanded", "subslots": {"0": device}, "io_device": None}


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["cartridges"]["slot1"].update(mapper="Unknown"),
        lambda d: d["machine"]["fdc"].update(controller="xyz"),
        lambda d: d["machine"]["fdc"].update(connection_style="tc8566af"),
        lambda d: d["extensions"].update(slot2={**_FLAT, "device": "halnote"}),
        lambda d: d["extensions"].update(slot2=_FLAT),
        lambda d: d["extensions"].update(
            slot2=_expanded({"device": "fmpac", "rom": None, "size_kb": None})
        ),
        lambda d: d["extensions"].update(
            slot2=_expanded({"device": "ram_mapper", "rom": None, "size_kb": None})
        ),
        lambda d: d["machine"].update(
            io_device={"device": "kanji_rom", "rom": None, "size_kb": None}
        ),
    ],
    ids=[
        "mapper", "fdc-controller", "fdc-pair", "flat-device", "flat-rom",
        "subslot-device", "subslot-size", "io-device-rom",
    ],
)
def test_an_out_of_set_value_does_not_validate(edit: typing.Any) -> None:
    validator = jsonschema.Draft202012Validator(_schema())
    document = json.loads(_documents()["hb_f1xd"])
    assert validator.is_valid(document)
    edit(document)
    assert not validator.is_valid(document)
