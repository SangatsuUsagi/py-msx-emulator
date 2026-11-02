"""The golden cases of the whole core (dev-golden-state), with
tools/golden_state.py. A case whose ROM is absent is skipped."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_tool() -> ModuleType:
    path = Path(__file__).parent.parent / "tools" / "golden_state.py"
    spec = importlib.util.spec_from_file_location("golden_state", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_state"] = module
    spec.loader.exec_module(module)
    return module


golden = _load_tool()
_DATA = golden.read_vectors()
_CASES = {case["id"]: case for case in _DATA["cases"]}
_BOOT = "cbios-msx1-boot"
_MSX1_HOMEBREW = "msx1-homebrew-scroller"
_MSX2_HOMEBREW = "msx2-homebrew-card-game"


def _run_or_skip(case: dict[str, Any], **kwargs: Any) -> list[Any]:
    try:
        result: list[Any] = golden.run_case(case, **kwargs)
    except golden.MissingRom as exc:
        pytest.skip(f"{case['id']}: {exc}")
    return result


@pytest.mark.parametrize("case_id", sorted(_CASES))
def test_a_matching_case_passes(case_id: str) -> None:
    # dev-golden-state "A matching case passes".
    case = _CASES[case_id]
    lines = golden.differences(case, _run_or_skip(case))
    hint = "\nRecord an intended change with tools/golden_state.py --write"
    assert not lines, "\n".join(lines) + hint


def test_a_mismatch_names_where_it_differs() -> None:
    # dev-golden-state "A mismatch names where it differs".
    case = copy.deepcopy(_CASES[_BOOT])
    actual = _run_or_skip(case)
    case["checkpoints"][1]["state"]["vdp"] = "0" * 64
    assert golden.differences(case, actual) == [f"{_BOOT} frame 300: state section vdp differs"]


def test_a_missing_rom_skips_the_case(tmp_path: Path) -> None:
    # dev-golden-state "A missing ROM skips the case".
    case = _CASES[_MSX1_HOMEBREW]
    with pytest.raises(pytest.skip.Exception, match=case["cartridge_path"]):
        _run_or_skip(case, root=tmp_path)


def test_a_rom_with_another_hash_is_skipped(tmp_path: Path) -> None:
    # dev-golden-state "A ROM with another hash is skipped".
    case = _CASES[_MSX1_HOMEBREW]
    rom = tmp_path / case["cartridge_path"]
    rom.parent.mkdir(parents=True)
    rom.write_bytes(bytes(32768))
    other = golden.sha256_hex(bytes(32768))
    with pytest.raises(golden.MissingRom) as exc_info:
        golden.cartridge_bytes(case, tmp_path)
    assert case["cartridge_path"] in str(exc_info.value)
    assert other in str(exc_info.value)
    assert case["cartridge_sha256"] in str(exc_info.value)


def _plain_values(node: object) -> bool:
    if isinstance(node, dict):
        return all(isinstance(key, str) and _plain_values(value) for key, value in node.items())
    if isinstance(node, list):
        return all(_plain_values(value) for value in node)
    return node is None or isinstance(node, (str, int)) and not isinstance(node, bool)


def test_a_case_is_self_contained() -> None:
    # dev-golden-state "A case is self-contained".
    assert _plain_values(_DATA)
    keys = {"id", "machine", "cartridge_path", "cartridge_sha256", "mapper", "inputs",
            "checkpoints"}
    for case in _DATA["cases"]:
        assert set(case) == keys
    case = _CASES[_MSX1_HOMEBREW]
    assert golden.key_matrix(case, 399) == bytes([0xFF] * 11)
    pressed = bytearray([0xFF] * 11)
    pressed[8] = 0xFE
    assert golden.key_matrix(case, 400) == bytes(pressed)
    assert golden.key_matrix(case, 406) == bytes([0xFF] * 11)


def test_a_section_hash_is_the_hash_of_its_canonical_json() -> None:
    # dev-golden-state "A section hash is the hash of its canonical JSON".
    from msx.canonical_json import encode_canonical
    from tests.factories import make_machine_msx2

    state = make_machine_msx2(bytes(0x8000), bytes(0x4000)).save_state()
    decoded = json.loads(state)
    hashes = golden.section_hashes(state)
    assert set(hashes) == set(decoded)
    for key, value in decoded.items():
        assert hashes[key] == golden.sha256_hex(encode_canonical(value))
    assert golden.canonical_json(decoded) == state
    data = golden.read_vectors()
    assert golden.canonical_json(data) == encode_canonical(data)


def test_rendering_does_not_change_a_result() -> None:
    # dev-golden-state "Rendering does not change a result".
    case = _CASES[_BOOT]
    checkpoints = _run_or_skip(case, render_all=True)
    assert golden.differences(case, checkpoints) == []


def test_writing_twice_gives_the_same_file(tmp_path: Path) -> None:
    # dev-golden-state "Writing twice gives the same file".
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    golden.write_vectors(golden.read_vectors(), first)
    golden.write_vectors(golden.read_vectors(first), second)
    assert first.read_bytes() == second.read_bytes() == golden.DEFAULT_VECTORS.read_bytes()


def test_five_cases() -> None:
    # dev-golden-state "Five cases".
    machines = {case_id: case["machine"] for case_id, case in _CASES.items()}
    assert machines == {
        "cbios-msx1-boot": "cbios_msx1",
        "cbios-msx2-boot": "cbios_msx2",
        "cbios-msx2-pal-boot": "cbios_msx2_eu",
        _MSX1_HOMEBREW: "cbios_msx1",
        _MSX2_HOMEBREW: "cbios_msx2",
    }
    for case_id in ("cbios-msx1-boot", "cbios-msx2-boot", "cbios-msx2-pal-boot"):
        assert _CASES[case_id]["cartridge_path"] is None
        assert _CASES[case_id]["inputs"] == []


def test_the_file_pins_the_msx1_release() -> None:
    # dev-golden-state "The file pins the MSX1 release".
    case = _CASES[_MSX1_HOMEBREW]
    assert case["cartridge_path"] == "roms/homebrew/noborunoca.rom"
    assert case["cartridge_sha256"] == (
        "57a3be2fe5c3190daa3e69cf23cd262cdc8af98c60993ea941d43c815cbb25f6"
    )


def test_the_file_pins_the_msx2_release() -> None:
    # dev-golden-state "The file pins the MSX2 release".
    case = _CASES[_MSX2_HOMEBREW]
    assert case["cartridge_path"] == "roms/homebrew/uno-msx2.rom"
    assert case["cartridge_sha256"] == (
        "74d74d8720e623a7b8aadce8e0b0c9094f8a2162339a8e7c677c7732f673ddb8"
    )


def test_the_msx1_homebrew_case_plays_to_its_game_over() -> None:
    # dev-golden-state "The MSX1 homebrew case plays to its game over". The
    # game over was seen by frame 700 in screenshots; the file shows that the
    # last checkpoint comes after the start and that the PSG keeps changing.
    case = _CASES[_MSX1_HOMEBREW]
    assert case["inputs"] == [{"first": 400, "last": 405, "row": 8, "bit": 0}]
    points = case["checkpoints"]
    assert points[-1]["frame"] >= 700
    psg = [point["state"]["psg"] for point in points]
    assert len(set(psg)) == len(psg)


def test_the_msx2_homebrew_case_reaches_a_cpu_turn() -> None:
    # dev-golden-state "The MSX2 homebrew case reaches a CPU turn": U (row 5,
    # bit 2) draws a card, and the CPU players move after it.
    case = _CASES[_MSX2_HOMEBREW]
    draw = [held for held in case["inputs"] if (held["row"], held["bit"]) == (5, 2)]
    assert len(draw) == 1
    points = case["checkpoints"]
    title = points[0]
    after = [point for point in points if point["frame"] > draw[0]["last"]]
    assert after
    for point in after:
        assert point["state"]["vdp"] != title["state"]["vdp"]
