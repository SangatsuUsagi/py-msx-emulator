"""machine-state "Snapshot encoding is separate from storage" and the format-17
byte encoding (canonical JSON, strict decoding, base64 blobs)."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from host import state_files
from host.machine_config import build_machine_from_spec
from host.state_files import save_state as save_state_file
from msx import state
from msx.boundary import RgbFrame
from msx.canonical_json import encode_canonical
from msx.input import InputFrame
from msx.machine import Machine
from msx.machine_loader import build_machine
from msx.machine_spec import MachineSpec, _FdcDef, _RomEntry
from msx.snapshot_schema import SnapshotMismatchError
from tests.factories import make_machine, make_machine_msx2

_ROM = bytes(32 * 1024)


def test_core_save_returns_bytes_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    machine.step_frame(InputFrame(), render_video=False)
    data = state.save_state(machine)
    assert isinstance(data, bytes)
    assert json.loads(data)["format_version"] == state.CURRENT_FORMAT_VERSION
    assert list(tmp_path.iterdir()) == []


def test_core_load_restores_from_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    saved = make_machine(rom=_ROM)
    for _ in range(2):
        saved.step_frame(InputFrame(), render_video=False)
    data = state.save_state(saved)
    restored = make_machine(rom=_ROM)
    state.load_state(restored, data)
    assert state._snapshot_from_machine(restored) == state._snapshot_from_machine(saved)


def test_host_save_writes_the_same_files_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    path = save_state_file(machine, RgbFrame(256, 192, bytes(256 * 192 * 3)), "My Title")
    states = tmp_path / "saves" / "states"
    assert path.parent == Path("saves") / "states"
    assert path.name.startswith("My_Title_") and path.suffix == ".state"
    assert (states / path.name).with_suffix(".png").exists()
    assert (states / "latest.state").resolve() == (states / path.name).resolve()
    assert (states / "latest.png").resolve() == (states / path.name).with_suffix(".png").resolve()


def test_host_save_scales_the_thumbnail_to_256x192(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PIL import Image

    monkeypatch.chdir(tmp_path)
    machine = make_machine(rom=_ROM)
    path = save_state_file(machine, RgbFrame(512, 212, bytes(512 * 212 * 3)), "wide")
    with Image.open(path.with_suffix(".png")) as img:
        assert img.size == (256, 192)


@pytest.mark.parametrize(("resume", "expected"), [("", None), ("a/b.state", Path("a/b.state"))])
def test_resume_state_maps_empty_to_the_latest_state(
    monkeypatch: pytest.MonkeyPatch, resume: str, expected: Path | None
) -> None:
    calls: list[Path | None] = []
    monkeypatch.setattr(state_files, "load_state", lambda machine, path=None: calls.append(path))
    state_files.resume_state(make_machine(rom=_ROM), resume)
    assert calls == [expected]


# ---------------------------------------------------------------------------
# machine-state "Snapshot bytes are canonical JSON", "Snapshot decoding is
# strict" and "Byte blobs are base64 strings"
# ---------------------------------------------------------------------------

def _canonical(data: bytes) -> bytes:
    text = json.dumps(
        json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return text.encode("utf-8")


def _ran(frames: int) -> Machine:
    machine = make_machine(rom=_ROM)
    for _ in range(frames):
        machine.step_frame(InputFrame(), render_video=False)
    return machine


def test_saved_bytes_are_already_canonical() -> None:
    data = state.save_state(_ran(3))
    assert _canonical(data) == data
    assert b" " not in data and b"\n" not in data


def test_a_non_ascii_disk_path_is_written_as_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for name, size in (("main.rom", 32768), ("sub.rom", 16384), ("disk.rom", 16384)):
        (tmp_path / name).write_bytes(bytes(size))
    disk = tmp_path / "ゲーム.dsk"
    disk.write_bytes(bytes(737280))
    spec = MachineSpec(
        name="non_ascii_disk", generation="msx2", rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=False, ram_size_kb=32, has_v9938=True, has_rtc=False,
        flat_ram_subslot=3, flat_ram_size_kb=64,
        fdc=_FdcDef(
            disk_rom_entry=_RomEntry(file="disk.rom", size_kb=16, pages=[1]),
            controller="wd2793", connection_style="sony", drives=1,
        ),
    )
    data = state.save_state(build_machine_from_spec(spec, fdd1=disk))
    assert "ゲーム.dsk".encode("utf-8") in data
    assert b"\\u" not in data


def test_the_same_state_gives_the_same_bytes() -> None:
    assert state.save_state(_ran(5)) == state.save_state(_ran(5))


def _load_edited(edit: Callable[[str], str]) -> None:
    """Load an edited save into another machine: it must be refused as
    corrupt, and the machine left as it was."""
    text = state.save_state(_ran(3)).decode("utf-8")
    edited = edit(text)
    assert edited != text
    target = _ran(1)
    before = state.save_state(target)
    with pytest.raises(ValueError, match="corrupt or unsupported save state"):
        state.load_state(target, edited.encode("utf-8"))
    assert state.save_state(target) == before


def test_a_float_value_is_rejected() -> None:
    _load_edited(lambda text: re.sub(r'"im":\d+', '"im":0.0', text, count=1))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_are_rejected(constant: str) -> None:
    _load_edited(lambda text: re.sub(r'"im":\d+', f'"im":{constant}', text, count=1))


def test_a_duplicate_key_is_rejected() -> None:
    # "im" is a key of the "cpu" object only.
    _load_edited(lambda text: text.replace('"im":', '"im":0,"im":', 1))


def test_a_pretty_printed_file_still_loads() -> None:
    saved = _ran(3)
    data = state.save_state(saved)
    decoded = json.loads(data)
    reordered = dict(reversed(list(decoded.items())))
    pretty = json.dumps(reordered, indent=2).encode("utf-8")
    target = _ran(1)
    state.load_state(target, pretty)
    assert state.save_state(target) == data


def test_an_integer_beyond_2_to_the_53_is_not_written() -> None:
    machine = _ran(1)
    machine.vdp.frame_count = 2**53
    with pytest.raises(ValueError, match="outside"):
        state.save_state(machine)


def test_vram_is_a_plain_base64_string() -> None:
    machine = make_machine_msx2(_ROM, bytes(0x4000))
    machine.vdp.vram[0x1FFFF] = 0x5A
    decoded = json.loads(state.save_state(machine))
    vram = decoded["vdp"]["vram"]
    assert isinstance(vram, str)
    raw = base64.b64decode(vram, validate=True)
    assert len(raw) == 131072 and raw[-1] == 0x5A


def test_invalid_base64_is_rejected_naming_the_field() -> None:
    machine = _ran(1)
    decoded = json.loads(state.save_state(machine))
    decoded["memory"]["ram"] = "*" + decoded["memory"]["ram"][1:]
    with pytest.raises(state.StateLoadError, match="memory.ram"):
        state.load_state(_ran(1), json.dumps(decoded).encode("utf-8"))


def _keys(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(key)
            found.extend(_keys(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_keys(value))
    return found


def test_no_private_names_in_the_file() -> None:
    machine = make_machine(rom=_ROM, cartridge=bytes(0x20000), mapper="KonamiSCC")
    assert machine.scc is not None
    for _ in range(2):
        machine.step_frame(InputFrame(), render_video=False)
    keys = _keys(json.loads(state.save_state(machine)))
    assert "lfsr" in keys and "waves" in keys
    assert [key for key in keys if key.startswith("_")] == []


def test_an_msx1_machine_without_extras() -> None:
    decoded = json.loads(state.save_state(_ran(1)))
    assert set(decoded) == {
        "format_version", "machine_type", "machine_config_sha256", "cpu", "memory", "slots",
        "vdp", "psg", "ppi", "scc", "fdc", "rtc", "mouse",
    }
    assert isinstance(decoded["ppi"], dict)
    for key in ("scc", "fdc", "rtc", "mouse"):
        assert decoded[key] is None
    assert decoded["vdp"]["v9938"] is None
    assert decoded["memory"]["ram_mapper"] is None
    assert decoded["slots"]["slot3"]["secondary_slot_register"] is None


# ---------------------------------------------------------------------------
# machine-state "Save, load and save again gives the same bytes"
# ---------------------------------------------------------------------------

def _loaded_machine(tmp_path: Path) -> Machine:
    """An MSX2 machine with an RTC, a RAM mapper, a WD2793 FDC with a disk,
    a Konami SCC cartridge in slot 1, an FM-PAC in slot 2 and a mouse."""
    from msx.boundary import RtcTime
    from msx.machine_spec import ExtensionOverlay
    from msx.mouse import MouseDevice
    from msx.psg import JoystickPort

    for name, size in (("main.rom", 32768), ("sub.rom", 16384), ("disk.rom", 16384),
                       ("fmpac.rom", 65536)):
        path = tmp_path / name
        if not path.exists():
            path.write_bytes(bytes(size))
    disk = tmp_path / "disk.dsk"
    if not disk.exists():
        disk.write_bytes(bytes(737280))
    spec = MachineSpec(
        name="round_trip", generation="msx2", rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="sub.rom", size_kb=16, pages=[0]),
        has_ram_mapper=True, ram_size_kb=128, has_v9938=True, has_rtc=True,
        fdc=_FdcDef(
            disk_rom_entry=_RomEntry(file="disk.rom", size_kb=16, pages=[1]),
            controller="wd2793", connection_style="sony", drives=1,
        ),
    )
    fmpac = ExtensionOverlay(
        device="fmpac", rom_base_dir=tmp_path,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        sram_save_path=tmp_path / "fmpac.sram",
    )
    machine = build_machine_from_spec(
        spec, cartridge1=bytes(0x20000), mapper1="KonamiSCC", fdd1=disk,
        extension_overlay2=fmpac, rtc_initial_time=RtcTime(2026, 10, 24, 12, 0, 0),
    )
    machine.attach_mouse(MouseDevice(), JoystickPort.JOY2)
    return machine


def test_round_trip_is_byte_identical(tmp_path: Path) -> None:
    saved = _loaded_machine(tmp_path)
    assert saved.scc is not None and saved.fmpac is not None and saved.fdc is not None
    assert saved.rtc is not None and saved.psg.mouse is not None
    for reg, value in ((8, 0x0F), (0, 0x40)):          # PSG channel A audible
        saved.io.write_port(0xA0, reg)
        saved.io.write_port(0xA1, value)
    saved.memory._mapper.write(0x9000, 0x3F)           # SCC on, then a wave and a tone
    saved.memory._mapper.write(0x9800, 0x7F)
    saved.memory._mapper.write(0x9880, 0x20)
    saved.memory._mapper.write(0x988A, 0x0F)
    saved.memory._mapper.write(0x988F, 0x01)
    saved.fmpac.write(0x7FF4, 0x30)                    # OPLL channel 0: key on
    saved.fmpac.write(0x7FF5, 0x10)
    saved.fmpac.write(0x7FF4, 0x10)
    saved.fmpac.write(0x7FF5, 0x50)
    saved.fmpac.write(0x7FF4, 0x20)
    saved.fmpac.write(0x7FF5, 0x17)
    saved.psg.mouse.add_motion(5, -3)
    for _ in range(10):
        saved.step_frame(InputFrame(), render_video=False)
    first = state.save_state(saved)
    decoded = json.loads(first)
    for key in ("scc", "fdc", "rtc", "mouse"):
        assert decoded[key] is not None
    assert decoded["slots"]["slot2"]["mapper"]["kind"] == "fmpac"

    fresh = _loaded_machine(tmp_path)
    state.load_state(fresh, first)
    assert state.save_state(fresh) == first


def test_an_earlier_version_with_a_float_is_rejected_as_a_version_mismatch() -> None:
    # Format 16 saved the OPLL resampler phase as a float; such a file must be
    # refused for its version, not reported as a corrupt file.
    decoded = json.loads(state.save_state(_ran(1)))
    decoded["format_version"] = 16
    decoded["out_time"] = 22458.33333141287   # an older format with a float
    target = _ran(1)
    before = state.save_state(target)
    with pytest.raises(ValueError, match="incompatible state file: version 16, expected 20"):
        state.load_state(target, json.dumps(decoded).encode("utf-8"))
    assert state.save_state(target) == before


# ---------------------------------------------------------------------------
# machine-state "A snapshot is bound to its machine configuration"
# ---------------------------------------------------------------------------

def _konami_machine(cartridge: bytes) -> Machine:
    return make_machine(rom=_ROM, cartridge=cartridge, mapper="Konami")


def test_the_same_configuration_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    from tests.factories import make_config

    config = make_config(_ROM, cartridge=bytes(0x20000), mapper="Konami")
    saved = _konami_machine(bytes(0x20000))
    data = state.save_state(saved)
    canonical = encode_canonical(json.loads(config.document))
    assert json.loads(data)["machine_config_sha256"] == hashlib.sha256(canonical).hexdigest()
    state.load_state(_konami_machine(bytes(0x20000)), data)


def test_a_reformatted_document_loads_the_same_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    from tests.factories import make_config, reformat_document

    config = make_config(_ROM, cartridge=bytes(0x20000), mapper="Konami")
    data = state.save_state(build_machine(config))
    reformatted = dataclasses.replace(config, document=reformat_document(config.document))
    target = build_machine(reformatted)
    state.load_state(target, data)
    assert state.save_state(target) == data


def test_a_different_cartridge_rom_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    data = state.save_state(_konami_machine(bytes(0x20000)))
    other = _konami_machine(bytes([0xFF]) * 0x20000)
    before = state.save_state(other)
    with pytest.raises(SnapshotMismatchError, match="machine configuration mismatch") as exc_info:
        state.load_state(other, data)
    assert json.loads(data)["machine_config_sha256"] in str(exc_info.value)
    assert other.machine_config_sha256 in str(exc_info.value)
    assert state.save_state(other) == before


def test_a_difference_the_specific_checks_see_is_named_by_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    from msx.machine_spec import ExtensionOverlay

    fmpac_dir = tmp_path / "fmpac"
    fmpac_dir.mkdir()
    (fmpac_dir / "fmpac.rom").write_bytes(bytes(65536))
    fmpac = ExtensionOverlay(
        device="fmpac", rom_base_dir=fmpac_dir,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        sram_save_path=fmpac_dir / "fmpac.sram",
    )
    data = state.save_state(make_machine(rom=_ROM, extension_overlay=fmpac))
    scc_plus = make_machine(rom=_ROM, extension_overlay=ExtensionOverlay(device="scc_i_cart"))
    with pytest.raises(SnapshotMismatchError, match="slot 2 mapper mismatch") as exc_info:
        state.load_state(scc_plus, data)
    assert "fmpac" in str(exc_info.value) and "scc_i_cart" in str(exc_info.value)
