"""--tape, --tape-fast and --no-tape-fast (app-cli), and the `tape` key of
py_emulator.yaml (app-config-file)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from host.app_config import load_app_config
from host.cas_image import CAS_HEADER, parse_cas
from msx.boundary import FrameOutput, TapeInfo
from msx.machine import Machine
from tests.test_cli_trace_flags import _FRAME_DONE, _run_main


def _cas(tmp_path: Path, name: str = "game.cas") -> Path:
    path = tmp_path / name
    path.write_bytes(CAS_HEADER + bytes([0xD0] * 10) + b"GAME  " + CAS_HEADER + bytes(20))
    return path


def _run(argv: list[str]) -> tuple[int, str, list[TapeInfo]]:
    """Run one headless frame; returns the exit code, stderr and the tape
    state the frame saw."""
    seen: list[TapeInfo] = []

    def step_frame(self: Machine, *_args: object, **_kwargs: object) -> FrameOutput:
        seen.append(self.tape_info())
        return _FRAME_DONE

    with patch.object(Machine, "step_frame", step_frame):
        code, _out, err = _run_main(["--count-frame", "1", *argv], machine="hb_10")
    return code, err, seen


def test_a_tape_is_mounted(tmp_path: Path) -> None:
    path = _cas(tmp_path)
    code, _err, seen = _run(["--tape", str(path)])
    assert code == 0
    length = parse_cas(path.read_bytes()).length
    assert seen == [TapeInfo(mounted=1, motor=0, position=0, length=length)]


def test_a_wav_file_is_refused(tmp_path: Path) -> None:
    code, err, seen = _run(["--tape", str(tmp_path / "game.wav")])
    assert code != 0 and seen == []
    assert "only CAS" in err and "WAV" in err and "external tool" in err


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    code, err, seen = _run(["--tape", str(tmp_path / "missing.cas")])
    assert code != 0 and seen == []
    assert "--tape" in err


def test_a_file_with_no_header_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.cas"
    bad.write_bytes(bytes(64))
    code, err, seen = _run(["--tape", str(bad)])
    assert code != 0 and seen == []
    assert "--tape" in err and "no block header" in err


def test_a_machine_with_no_port(tmp_path: Path) -> None:
    import host.machine_config as machine_config

    real = machine_config.load_machine_config

    def format_2(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        loaded = real(*args, **kwargs)  # type: ignore[arg-type]
        document = json.loads(loaded.config.document)
        document["format_version"] = 2
        del document["machine"]["cassette_port"]
        del document["machine"]["ram_mapper_subslot"]
        del document["machine"]["mapper_io"]
        loaded.config.document = json.dumps(document).encode("utf-8")
        return loaded

    with patch.object(machine_config, "load_machine_config", format_2):
        code, err, seen = _run(["--tape", str(_cas(tmp_path))])
    assert code != 0 and seen == []
    assert "no cassette port" in err


def _sdl_run_kwargs(argv: list[str]) -> dict[str, object]:
    """The keyword arguments main() passes to the SDL2 frontend's run()."""
    import importlib.util
    import sys
    from unittest.mock import MagicMock

    from tests.test_cli_trace_flags import _MAIN_PATH

    run = MagicMock()
    with patch.object(sys, "argv", [".", "--machine", "hb_10", *argv]), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", lambda self: bytes(32768)), \
         patch("frontend.sdl2_frontend.run", run), \
         patch("host.debugger.prompt.Debugger"):
        spec = importlib.util.spec_from_file_location("_emulator_main_tape", _MAIN_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    assert run.call_count == 1
    return dict(run.call_args.kwargs)


def test_tape_fast_is_on_by_default(tmp_path: Path) -> None:
    assert _sdl_run_kwargs(["--tape", str(_cas(tmp_path))])["tape_fast"] is True


def test_no_tape_fast_turns_it_off(tmp_path: Path) -> None:
    kwargs = _sdl_run_kwargs(["--tape", str(_cas(tmp_path)), "--no-tape-fast"])
    assert kwargs["tape_fast"] is False


# --- app-config-file "tape key parsed" ---

def test_the_tape_key_is_parsed(tmp_path: Path) -> None:
    (tmp_path / "tapes").mkdir()
    _cas(tmp_path / "tapes")
    (tmp_path / "py_emulator.yaml").write_text("tape: tapes/game.cas\n", encoding="utf-8")
    assert load_app_config(tmp_path).tape == "tapes/game.cas"


def test_a_missing_tape_key_file_is_an_error(tmp_path: Path) -> None:
    import pytest

    from host.app_config import AppConfigError

    (tmp_path / "py_emulator.yaml").write_text("tape: nope.cas\n", encoding="utf-8")
    with pytest.raises(AppConfigError, match="tape"):
        load_app_config(tmp_path)


def test_the_config_tape_is_mounted_and_tape_overrides_it(tmp_path: Path) -> None:
    from host.app_config import AppConfig

    first = _cas(tmp_path, "first.cas")
    second = tmp_path / "second.cas"
    second.write_bytes(CAS_HEADER + bytes([0xD0] * 10) + b"OTHER " + CAS_HEADER + bytes(500))
    with patch("host.app_config.load_app_config", return_value=AppConfig(tape=str(first))):
        code, _err, seen = _run([])
        assert code == 0
        assert seen[0].length == parse_cas(first.read_bytes()).length
        code, _err, seen = _run(["--tape", str(second)])
        assert code == 0
        assert seen[0].length == parse_cas(second.read_bytes()).length
