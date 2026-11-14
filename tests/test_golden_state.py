"""The golden cases of the whole core (dev-golden-state), with
tools/golden_state.py. A case whose ROM is absent is skipped."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).parent.parent


def _load_tool(name: str = "golden_state") -> ModuleType:
    path = _ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


golden = _load_tool()
_DATA = golden.read_vectors()
_CASES = {case["id"]: case for case in _DATA["cases"]}
_BOOT = "cbios-msx1-boot"
_MSX1_HOMEBREW = "msx1-homebrew-scroller"
_MSX2_HOMEBREW = "msx2-homebrew-card-game"
_TAPE = "msx1-tape-breakout"
_DISK = "msx2-disk-breakout"
_MEDIA_KEYS = ("cartridge", "disk", "tape")
# Runs with only the checkpoint frames rendered, by case id: several
# scenarios read the same run.
_RUNS: dict[str, list[Any]] = {}


def _run_or_skip(case: dict[str, Any], **kwargs: Any) -> list[Any]:
    try:
        result: list[Any] = golden.run_case(case, **kwargs)
    except golden.MissingRom as exc:
        pytest.skip(f"{case['id']}: {exc}")
    return result


def _run_once(case_id: str) -> list[Any]:
    if case_id not in _RUNS:
        _RUNS[case_id] = _run_or_skip(_CASES[case_id])
    return _RUNS[case_id]


def _check_or_skip(case: dict[str, Any]) -> list[str]:
    """The differences of a case from its recorded hashes, or a skip when it
    has none."""
    if not golden.has_recorded_hashes(case):
        pytest.skip(f"{case['id']}: no recorded hashes")
    return golden.differences(case, _run_once(case["id"]))


def _frame_only(case: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(case)
    result["checkpoints"] = [{"frame": point["frame"]} for point in case["checkpoints"]]
    return result


@pytest.mark.parametrize("case_id", sorted(_CASES))
def test_a_matching_case_passes(case_id: str) -> None:
    # dev-golden-state "A matching case passes".
    lines = _check_or_skip(_CASES[case_id])
    hint = "\nRecord an intended change with tools/golden_state.py --write"
    assert not lines, "\n".join(lines) + hint


def test_a_mismatch_names_where_it_differs() -> None:
    # dev-golden-state "A mismatch names where it differs".
    # The run gives the expected hashes, and one section differs in a copy.
    actual = _run_once(_BOOT)
    case = copy.deepcopy(_CASES[_BOOT])
    case["checkpoints"] = copy.deepcopy([point.as_json() for point in actual])
    assert golden.differences(case, actual) == []
    case["checkpoints"][1]["state"]["vdp"] = "0" * 64
    assert golden.differences(case, actual) == [f"{_BOOT} frame 300: state section vdp differs"]


def test_an_audio_or_video_mismatch_names_where_it_differs() -> None:
    # dev-golden-state "An audio or video mismatch names where it differs".
    actual = _run_once(_BOOT)
    case = copy.deepcopy(_CASES[_BOOT])
    case["checkpoints"] = [point.as_json(with_state=False) for point in actual]
    assert golden.differences(case, actual) == []
    case["checkpoints"][1]["audio"] = "0" * 64
    assert golden.differences(case, actual) == [f"{_BOOT} frame 300: audio differs"]
    case["checkpoints"][1]["audio"] = actual[1].audio
    case["checkpoints"][0]["video"] = "0" * 64
    assert golden.differences(case, actual) == [f"{_BOOT} frame 60: video differs"]


def test_a_case_with_no_recorded_hashes_is_skipped() -> None:
    # dev-golden-state "A case with no recorded hashes is skipped".
    case = _frame_only(_CASES[_BOOT])
    with pytest.raises(pytest.skip.Exception, match=f"{_BOOT}: no recorded hashes"):
        _check_or_skip(case)


def test_a_checkpoint_may_give_its_frame_only(tmp_path: Path) -> None:
    # dev-golden-state "A checkpoint may give its frame only": the file is
    # read, and only the checkpoint with hashes is compared.
    path = tmp_path / "vectors.json"
    case = _frame_only(_CASES[_BOOT])
    golden.write_vectors({"cases": [case], "format_version": 1}, path)
    read = golden.read_vectors(path)["cases"][0]
    assert read["checkpoints"] == [{"frame": 60}, {"frame": 300}]
    assert not golden.has_recorded_hashes(read)
    actual = [golden.Checkpoint(frame=60, state={"cpu": "1" * 64}, audio="2" * 64,
                                video="3" * 64),
              golden.Checkpoint(frame=300, state={"cpu": "4" * 64}, audio="5" * 64,
                                video="6" * 64)]
    assert golden.differences(read, actual) == []
    read["checkpoints"][1] = actual[1].as_json() | {"audio": "0" * 64}
    assert golden.has_recorded_hashes(read)
    assert golden.differences(read, actual) == [f"{_BOOT} frame 300: audio differs"]


def test_a_checkpoint_may_give_audio_and_video_only(tmp_path: Path) -> None:
    # dev-golden-state "A checkpoint may give audio and video only": the file
    # is read, the checkpoint counts as recorded, and the state is not compared.
    path = tmp_path / "vectors.json"
    case = _frame_only(_CASES[_BOOT])
    case["checkpoints"] = [{"frame": 60, "audio": "1" * 64, "video": "2" * 64},
                           {"frame": 300, "audio": "3" * 64, "video": "4" * 64}]
    golden.write_vectors({"cases": [case], "format_version": 1}, path)
    read = golden.read_vectors(path)["cases"][0]
    assert read["checkpoints"] == case["checkpoints"]
    assert golden.has_recorded_hashes(read)
    actual = [golden.Checkpoint(frame=60, state={"cpu": "5" * 64}, audio="1" * 64,
                                video="2" * 64),
              golden.Checkpoint(frame=300, state={"cpu": "6" * 64}, audio="3" * 64,
                                video="4" * 64)]
    assert golden.differences(read, actual) == []


def test_the_check_does_not_fail_on_a_case_with_no_recorded_hashes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # dev-golden-state "The check does not fail on a case with no recorded hashes".
    path = tmp_path / "vectors.json"
    cases = [_frame_only(_CASES[case_id]) for case_id in sorted(_CASES)]
    golden.write_vectors({"cases": cases, "format_version": 1}, path)
    assert golden.main(["--vectors", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"{case_id}: no recorded hashes" for case_id in sorted(_CASES)]


def _write_boot_case(tmp_path: Path, argv: list[str]) -> list[dict[str, Any]]:
    """The checkpoints that `--write` records for the boot case alone."""
    _run_once(_BOOT)  # skips when C-BIOS is absent
    path = tmp_path / "vectors.json"
    golden.write_vectors({"cases": [_frame_only(_CASES[_BOOT])], "format_version": 1}, path)
    assert golden.main([*argv, "--vectors", str(path)]) == 0
    checkpoints: list[dict[str, Any]] = golden.read_vectors(path)["cases"][0]["checkpoints"]
    return checkpoints


def test_writing_records_audio_and_video_by_default(tmp_path: Path) -> None:
    # dev-golden-state "Writing records audio and video by default".
    checkpoints = _write_boot_case(tmp_path, ["--write"])
    assert [set(point) for point in checkpoints] == [{"frame", "audio", "video"}] * 2
    assert checkpoints == [point.as_json(with_state=False) for point in _run_once(_BOOT)]


def test_writing_with_state_records_every_hash(tmp_path: Path) -> None:
    # dev-golden-state "Writing with --state records every hash".
    checkpoints = _write_boot_case(tmp_path, ["--write", "--state"])
    assert [set(point) for point in checkpoints] == [{"frame", "state", "audio", "video"}] * 2
    assert checkpoints == [point.as_json() for point in _run_once(_BOOT)]


def test_the_repository_file_records_audio_and_video() -> None:
    # dev-golden-state "Every checkpoint has audio and video".
    for case in _DATA["cases"]:
        for point in case["checkpoints"]:
            assert set(point) == {"frame", "audio", "video"}, case["id"]


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
    keys = {"id", "machine", "cartridge_path", "cartridge_sha256", "mapper", "disk_path",
            "disk_sha256", "tape_path", "tape_sha256", "inputs", "checkpoints"}
    for case in _DATA["cases"]:
        assert set(case) == keys
    case = _CASES[_MSX1_HOMEBREW]
    assert golden.key_matrix(case, 399) == bytes([0xFF] * 11)
    pressed = bytearray([0xFF] * 11)
    pressed[8] = 0xFE
    assert golden.key_matrix(case, 400) == bytes(pressed)
    assert golden.key_matrix(case, 406) == bytes([0xFF] * 11)


def test_a_case_gives_a_disk_and_a_tape_or_none() -> None:
    # dev-golden-state "A case gives a disk and a tape or none".
    for case in _DATA["cases"]:
        for kind in ("disk", "tape"):
            pair = (case[f"{kind}_path"], case[f"{kind}_sha256"])
            assert pair == (None, None) or all(isinstance(value, str) for value in pair)


def test_every_medium_is_under_tests_media() -> None:
    # dev-golden-state "Every medium is under tests/media".
    for case in _DATA["cases"]:
        for kind in _MEDIA_KEYS:
            path = case[f"{kind}_path"]
            assert path is None or path.startswith("tests/media/"), (case["id"], path)


def test_the_media_directory_is_not_in_the_repository() -> None:
    # dev-golden-state "The media directory is not in the repository".
    result = subprocess.run(["git", "check-ignore", "-q", "tests/media/FREEBALL.DSK"],
                            cwd=_ROOT, check=False)
    assert result.returncode == 0


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
    rendered = _run_or_skip(_CASES[_BOOT], render_all=True)
    assert [point.state for point in rendered] == [point.state for point in _run_once(_BOOT)]
    assert [point.audio for point in rendered] == [point.audio for point in _run_once(_BOOT)]


def test_writing_twice_gives_the_same_file(tmp_path: Path) -> None:
    # dev-golden-state "Writing twice gives the same file".
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    golden.write_vectors(golden.read_vectors(), first)
    golden.write_vectors(golden.read_vectors(first), second)
    assert first.read_bytes() == second.read_bytes() == golden.DEFAULT_VECTORS.read_bytes()


def test_seven_cases() -> None:
    # dev-golden-state "Seven cases".
    machines = {case_id: case["machine"] for case_id, case in _CASES.items()}
    assert machines == {
        "cbios-msx1-boot": "cbios_msx1",
        "cbios-msx2-boot": "cbios_msx2",
        "cbios-msx2-pal-boot": "cbios_msx2_eu",
        _MSX1_HOMEBREW: "cbios_msx1",
        _MSX2_HOMEBREW: "cbios_msx2",
        _TAPE: "hb_10",
        _DISK: "hb_f1xd",
    }
    assert _CASES[_TAPE]["tape_path"] is not None and _CASES[_TAPE]["disk_path"] is None
    assert _CASES[_DISK]["disk_path"] is not None and _CASES[_DISK]["tape_path"] is None
    for case_id in ("cbios-msx1-boot", "cbios-msx2-boot", "cbios-msx2-pal-boot"):
        assert _CASES[case_id]["cartridge_path"] is None
        assert _CASES[case_id]["inputs"] == []


def test_the_file_pins_the_msx1_release() -> None:
    # dev-golden-state "The file pins the MSX1 release".
    case = _CASES[_MSX1_HOMEBREW]
    assert case["cartridge_path"] == "tests/media/noborunoca.rom"
    assert case["cartridge_sha256"] == (
        "57a3be2fe5c3190daa3e69cf23cd262cdc8af98c60993ea941d43c815cbb25f6"
    )


def test_the_file_pins_the_msx2_release() -> None:
    # dev-golden-state "The file pins the MSX2 release".
    case = _CASES[_MSX2_HOMEBREW]
    assert case["cartridge_path"] == "tests/media/uno-msx2.rom"
    assert case["cartridge_sha256"] == (
        "74d74d8720e623a7b8aadce8e0b0c9094f8a2162339a8e7c677c7732f673ddb8"
    )


def test_the_msx1_homebrew_case_plays_to_its_game_over() -> None:
    # dev-golden-state "The MSX1 homebrew case plays to its game over". The
    # game over was seen by frame 700 in screenshots; the file shows that the
    # last checkpoint comes after the start and that the PSG keeps changing.
    case = _CASES[_MSX1_HOMEBREW]
    assert case["inputs"] == [{"first": 400, "last": 405, "row": 8, "bit": 0}]
    assert case["checkpoints"][-1]["frame"] >= 700
    psg = [point.state["psg"] for point in _run_once(_MSX1_HOMEBREW)]
    assert len(set(psg)) == len(psg)


def test_the_msx2_homebrew_case_reaches_a_cpu_turn() -> None:
    # dev-golden-state "The MSX2 homebrew case reaches a CPU turn": U (row 5,
    # bit 2) draws a card, and the CPU players move after it.
    case = _CASES[_MSX2_HOMEBREW]
    draw = [held for held in case["inputs"] if (held["row"], held["bit"]) == (5, 2)]
    assert len(draw) == 1
    points = _run_once(_MSX2_HOMEBREW)
    title = points[0]
    after = [point for point in points if point.frame > draw[0]["last"]]
    assert after
    for point in after:
        assert point.state["vdp"] != title.state["vdp"]


def test_the_file_pins_the_disk() -> None:
    # dev-golden-state "The file pins the disk".
    case = _CASES[_DISK]
    assert case["disk_path"] == "tests/media/FREEBALL.DSK"
    assert case["disk_sha256"] == (
        "359115f0bf475a172d7815d53a23ee447bd2f4c38e0ebc0b93116566642bdbcf"
    )


def test_the_file_pins_the_tape() -> None:
    # dev-golden-state "The file pins the tape".
    case = _CASES[_TAPE]
    assert case["tape_path"] == "tests/media/FREEBALL.cas"
    assert case["tape_sha256"] == (
        "6fb1112f51e6013538c9686fda7811ab11563c43f76d4084dbfd591a20a6d20d"
    )


# On the JIS layout, SHIFT with the "2" key types a double quote.
_JIS_SHIFTED = {"2": '"'}
_SHIFT = (6, 0)


def _typed_text(inputs: list[dict[str, int]]) -> str:
    """The text that `inputs` type on the Japanese keyboard matrix, checking
    that each key is released before the next is pressed."""
    from frontend.sdl2_keyboard import KEY_MATRIX_JP

    names: dict[tuple[int, int], str] = {}
    for code, cell in KEY_MATRIX_JP.items():
        if 32 < code < 127 or code == 13:
            names.setdefault(cell, "\r" if code == 13 else chr(code).upper())
    presses: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for held in inputs:
        presses.setdefault((held["first"], held["last"]), []).append((held["row"], held["bit"]))
    text = ""
    last = 0
    for (first, end), cells in sorted(presses.items()):
        assert first > last
        last = end
        shifted = _SHIFT in cells
        keys = [cell for cell in cells if cell != _SHIFT]
        assert len(keys) == 1
        char = names[keys[0]]
        text += _JIS_SHIFTED[char] if shifted else char
    return text


def test_the_tape_case_types_its_load_command() -> None:
    # dev-golden-state "The tape case types its load command".
    case = _CASES[_TAPE]
    prompt = case["checkpoints"][0]["frame"]
    title = case["checkpoints"][1]["frame"]
    typing = [held for held in case["inputs"] if held["first"] < title]
    assert min(held["first"] for held in typing) > prompt
    assert _typed_text(typing) == 'RUN"CAS:"\r'


def test_the_tape_case_runs_the_game_from_tape() -> None:
    # dev-golden-state "The tape case runs the game from tape": the first
    # checkpoint is at the BASIC prompt, the second on the game's title.
    points = _run_once(_TAPE)
    assert points[1].state["vdp"] != points[0].state["vdp"]


def test_the_disk_case_boots_the_game_from_disk() -> None:
    # dev-golden-state "The disk case boots the game from disk", and the disk
    # image is only read.
    case = _CASES[_DISK]
    title = case["checkpoints"][1]["frame"]
    assert all(held["first"] > title for held in case["inputs"])
    points = _run_once(_DISK)
    assert points[1].state["vdp"] != points[0].state["vdp"]
    image = (_ROOT / case["disk_path"]).read_bytes()
    assert golden.sha256_hex(image) == case["disk_sha256"]


def _root_without_roms(tmp_path: Path) -> Path:
    os.symlink(_ROOT / "config", tmp_path / "config")
    return tmp_path


@pytest.mark.parametrize(("case_id", "kind", "rom"), [
    (_TAPE, "tape", "roms/hb_10/"),
    (_DISK, "disk", "roms/hb_f1xd/"),
])
def test_a_missing_machine_rom_skips_the_case(
    tmp_path: Path, case_id: str, kind: str, rom: str
) -> None:
    # dev-golden-state "A missing machine ROM skips the case".
    case = copy.deepcopy(_CASES[case_id])
    case[f"{kind}_path"] = None
    case[f"{kind}_sha256"] = None
    with pytest.raises(pytest.skip.Exception, match=rom):
        _run_or_skip(case, root=_root_without_roms(tmp_path))


@pytest.mark.parametrize("case_id", [_TAPE, _DISK])
def test_a_missing_disk_or_tape_image_skips_the_case(tmp_path: Path, case_id: str) -> None:
    # dev-golden-state "A missing disk or tape image skips the case".
    case = _CASES[case_id]
    path = case["tape_path"] or case["disk_path"]
    with pytest.raises(pytest.skip.Exception, match=f"{path} is absent"):
        _run_or_skip(case, root=tmp_path)


@pytest.mark.parametrize("case_id", [_TAPE, _DISK])
def test_a_disk_or_tape_image_with_another_hash_is_skipped(
    tmp_path: Path, case_id: str
) -> None:
    # dev-golden-state "A disk or tape image with another hash is skipped".
    case = _CASES[case_id]
    kind = "tape" if case["tape_path"] else "disk"
    medium = tmp_path / case[f"{kind}_path"]
    medium.parent.mkdir(parents=True)
    medium.write_bytes(bytes(16))
    with pytest.raises(golden.MissingRom) as exc_info:
        golden.medium_bytes(case, kind, tmp_path)
    assert case[f"{kind}_path"] in str(exc_info.value)
    assert golden.sha256_hex(bytes(16)) in str(exc_info.value)
    assert case[f"{kind}_sha256"] in str(exc_info.value)


def test_the_conversion_gives_the_pinned_tape(tmp_path: Path) -> None:
    # dev-golden-state "The conversion gives the pinned tape".
    source = _ROOT / "tests" / "media" / "FREEBALL.BAS"
    if not source.is_file():
        pytest.skip("tests/media/FREEBALL.BAS is absent")
    bas2wav = _load_tool("bas2wav")
    wav2cas = _load_tool("wav2cas")
    assert bas2wav.main([str(source), str(tmp_path / "FREEBALL.wav")]) == 0
    assert wav2cas.main([str(tmp_path / "FREEBALL.wav"), str(tmp_path / "FREEBALL.cas")]) == 0
    cas = (tmp_path / "FREEBALL.cas").read_bytes()
    assert golden.sha256_hex(cas) == _CASES[_TAPE]["tape_sha256"]
