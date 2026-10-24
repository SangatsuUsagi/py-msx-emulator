"""msx/snapshot.schema.json against the snapshots the emulator writes
(machine-state "Snapshot layout is published as a JSON Schema")."""
from __future__ import annotations

import dataclasses
import json
import typing
from collections.abc import Callable
from pathlib import Path

import pytest

from host.machine_config import build_machine_from_spec
from msx import fmpac, mapper, mouse, opll, psg, ram_mapper, rtc, scc, state
from msx.fdc import disk_drive, interface, tc8566af, wd2793
from msx.input import InputFrame
from msx.machine import Machine
from msx.machine_spec import ExtensionOverlay
from msx.mouse import MouseDevice
from msx.psg import JoystickPort
from msx.vdp import v9938
from tests.factories import make_machine, make_machine_msx2
from tests.test_extension_slot_integration import (
    _hbi_j1_overlay,
    _msx2_spec,
    _msxdos2_512k_overlay,
)
from tests.test_state import _make_msx1_machine_with_slot0_ram
from tests.test_state_bytes import _loaded_machine
from tests.test_state_fdc import _tc8566af_spec

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "msx" / "snapshot.schema.json"
_ROM = bytes(0x8000)
_EXTROM = bytes(0x4000)
_CART = bytes(0x20000)

# Every cartridge mapper name the loader accepts for slot 1 (FM-PAC, SCC-I,
# Halnote and RAM-mapper kinds come from extensions, below).
_CARTRIDGE_MAPPERS = (
    "Mirrored", "ASCII8", "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16", "ASCII16SRAM2",
    "ASCII16SRAM8", "Konami", "Majutsushi", "R-Type", "KonamiSCC", "0x4000",
    "KoeiSRAM32", "GameMaster2",
)


def _schema() -> dict[str, typing.Any]:
    return typing.cast(dict[str, typing.Any], json.loads(_SCHEMA_PATH.read_text()))


def _ran(machine: Machine) -> Machine:
    for _ in range(2):
        machine.step_frame(InputFrame(), render_video=False)
    return machine


def _with_mouse(machine: Machine) -> Machine:
    machine.attach_mouse(MouseDevice(), JoystickPort.JOY2)
    return machine


def _fmpac_overlay(rom_dir: Path) -> ExtensionOverlay:
    (rom_dir / "fmpac.rom").write_bytes(bytes(65536))
    from msx.machine_spec import _RomEntry

    return ExtensionOverlay(
        device="fmpac", rom_base_dir=rom_dir,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        sram_save_path=rom_dir / "fmpac.sram",
    )


def _machines(tmp_path: Path) -> dict[str, Callable[[], Machine]]:
    def subdir(name: str) -> Path:
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        return path

    def tc8566af_machine() -> Machine:
        disk = tmp_path / "tc.dsk"
        disk.write_bytes(bytes(737280))
        (tmp_path / "main.rom").write_bytes(bytes([0xC9]) + bytes(32768 - 1))
        (tmp_path / "sub.rom").write_bytes(bytes(16384))
        (tmp_path / "disk.rom").write_bytes(bytes(16384))
        return build_machine_from_spec(_tc8566af_spec(tmp_path), fdd1=disk)

    machines: dict[str, Callable[[], Machine]] = {
        "msx1": lambda: make_machine(_ROM),
        "msx2": lambda: make_machine_msx2(_ROM, _EXTROM),
        "msx1 slot-0 RAM": _make_msx1_machine_with_slot0_ram,
        "msx2 wd2793 fdc, ram mapper, rtc, fm-pac, scc, mouse": lambda: _loaded_machine(
            subdir("loaded")
        ),
        "msx2 tc8566af fdc": tc8566af_machine,
        "msx2 hbi-j1 slot 1, msx-dos2 512k slot 2": lambda: _with_mouse(
            build_machine_from_spec(
                _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
                extension_overlay1=_hbi_j1_overlay(subdir("hbi")),
                extension_overlay2=_msxdos2_512k_overlay(subdir("dos2")),
            )
        ),
        "msx2 scc-i slot 2, fm-pac slot 1": lambda: build_machine_from_spec(
            _msx2_spec(), bios_override=bytes(32768), extrom_override=bytes(32768),
            extension_overlay1=_fmpac_overlay(subdir("fmpac1")),
            extension_overlay2=ExtensionOverlay(device="scc_i_cart"),
        ),
    }
    for name in _CARTRIDGE_MAPPERS:
        machines[f"msx1 {name}"] = (
            lambda name=name: make_machine(_ROM, cartridge=_CART, mapper=name)
        )
    return machines


def _mapper_kinds(document: dict[str, typing.Any]) -> set[str]:
    kinds: set[str] = set()
    for slot in ("slot1", "slot2"):
        entry = document["slots"][slot]
        kinds.add(entry["mapper"]["kind"])
        for sub in entry["subslots"] or []:
            if sub is not None:
                kinds.add(sub["kind"])
    return kinds


def test_the_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_snapshots_of_every_machine_shape_validate(tmp_path: Path) -> None:
    validator = jsonschema.Draft202012Validator(_schema())
    kinds: set[str] = set()
    fdc_kinds: set[str] = set()
    for name, build in _machines(tmp_path).items():
        document = json.loads(state.save_state(_ran(build())))
        errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(document)]
        assert errors == [], name
        kinds |= _mapper_kinds(document)
        if document["fdc"] is not None:
            fdc_kinds.add(document["fdc"]["kind"])
    assert kinds == {kind.value for kind in mapper.MapperKind}
    assert fdc_kinds == {kind.value for kind in interface.FdcKind}


def test_a_snapshot_with_an_extra_or_missing_key_does_not_validate() -> None:
    validator = jsonschema.Draft202012Validator(_schema())
    document = json.loads(state.save_state(make_machine(_ROM)))
    document["cpu"]["extra"] = 0
    assert not validator.is_valid(document)
    del document["cpu"]["extra"]
    del document["psg"]["synth"]["lfsr"]
    assert not validator.is_valid(document)


def _properties(definition: dict[str, typing.Any]) -> set[str]:
    assert definition["additionalProperties"] is False
    assert sorted(definition["required"]) == sorted(definition["properties"])
    return set(definition["properties"])


def _nullable_target(schema: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return typing.cast(dict[str, typing.Any], schema["anyOf"][0])


# Each producer state TypedDict, by the $defs entry that describes it.
_TYPED_DICTS: dict[str, type] = {
    "PsgSynthState": psg.PsgSynthState,
    "SccState": scc.SccState,
    "RtcState": rtc.RtcState,
    "MouseState": mouse.MouseState,
    "V9938State": v9938.V9938State,
    "V9938CmdEngineState": v9938.V9938CmdEngineState,
    "OpllState": opll.OpllState,
    "OpllPatch": opll._PatchState,
    "OpllSlot": opll._SlotState,
    "FmPacState": fmpac.FmPacState,
    "RamMapperState": ram_mapper.RamMapperState,
    "WD2793State": wd2793.WD2793State,
    "TC8566AFState": tc8566af.TC8566AFState,
    "DiskDriveState": disk_drive.DiskDriveState,
    "SonyPhilipsConnectionState": interface.SonyPhilipsConnectionState,
    "TC8566AFConnectionState": interface.TC8566AFConnectionState,
    "NoStateMapperState": mapper.NoStateMapperState,
    "Ascii8MapperState": mapper.Ascii8MapperState,
    "Ascii16MapperState": mapper.Ascii16MapperState,
    "Ascii8Sram2MapperState": mapper.Ascii8Sram2MapperState,
    "Ascii16Sram2MapperState": mapper.Ascii16Sram2MapperState,
    "GameMaster2MapperState": mapper.GameMaster2MapperState,
    "RTypeMapperState": mapper.RTypeMapperState,
    "KonamiMapperState": mapper.KonamiMapperState,
    "MajutsushiMapperState": mapper.MajutsushiMapperState,
    "KonamiSCCMapperState": mapper.KonamiSCCMapperState,
    "SCCICartState": mapper.SCCICartState,
    "HalnoteMapperState": mapper.HalnoteMapperState,
}


@pytest.mark.parametrize("name", sorted(_TYPED_DICTS))
def test_the_schema_and_the_producers_name_the_same_keys(name: str) -> None:
    definition = _schema()["$defs"][name]
    assert _properties(definition) == set(_TYPED_DICTS[name].__annotations__)


def _field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_the_schema_and_the_snapshot_dataclasses_name_the_same_keys() -> None:
    schema = _schema()
    defs = schema["$defs"]
    memory = defs["Memory"]
    assert _properties(schema) == _field_names(state.MachineSnapshot)
    assert _properties(defs["Cpu"]) == _field_names(state.CpuSnapshot)
    assert _properties(defs["Cpu"]["properties"]["registers"]) == set(state._CPU_REG_KEYS)
    assert _properties(memory) == _field_names(state.MemorySnapshot)
    assert _properties(_nullable_target(memory["properties"]["ram_mapper"])) == _field_names(
        state.RamMapperSnapshot
    )
    assert _properties(defs["Slots"]) == _field_names(state.SlotsSnapshot)
    assert _properties(defs["Slots"]["properties"]["slot3"]) == _field_names(
        state.Slot3Snapshot
    )
    assert _properties(defs["PrimarySlot"]) == _field_names(state.SlotSnapshot)
    assert _properties(defs["MappedSlot"]) == _field_names(state.MappedSlot)
    assert _properties(defs["Vdp"]) == _field_names(state.VdpSnapshot)
    assert _properties(defs["Psg"]) == _field_names(state.PsgSnapshot)
    assert set(defs["MappedSlot"]["properties"]["kind"]["enum"]) == {
        kind.value for kind in mapper.MapperKind
    }
