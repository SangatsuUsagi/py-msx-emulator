"""tools/wav2cas.py: a WAV from tools/bas2wav.py converts back to the same
tape blocks, with the normal and the inverted signal."""
from __future__ import annotations

import array
import importlib.util
import sys
import wave
from pathlib import Path
from types import ModuleType

import pytest

from host.cas_image import CAS_HEADER, parse_cas


def _load_tool(name: str) -> ModuleType:
    path = Path(__file__).parent.parent / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bas2wav = _load_tool("bas2wav")
wav2cas = _load_tool("wav2cas")

_PROGRAM = b'10 PRINT "HELLO"\n20 GOTO 10\n'


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(bas2wav.SAMPLE_RATE)
        out.writeframes(pcm)


@pytest.mark.parametrize("level", [26000, -26000], ids=["normal", "inverted"])
def test_round_trip(level: int) -> None:
    text = bas2wav.program_text(_PROGRAM)
    name = bas2wav.tape_name("HELLO")
    pcm = bas2wav.build_pcm(text, name, level, 0.2)
    samples = array.array("h", pcm).tolist()
    blocks = wav2cas.convert(samples, bas2wav.SAMPLE_RATE)
    assert blocks == [bas2wav.ASCII_FILE_ID + name, text]


def test_main_writes_a_cas_the_emulator_reads(tmp_path: Path) -> None:
    text = bas2wav.program_text(_PROGRAM)
    name = bas2wav.tape_name("HELLO")
    source = tmp_path / "tape.wav"
    output = tmp_path / "tape.cas"
    _write_wav(source, bas2wav.build_pcm(text, name, 26000, 0.2))
    assert wav2cas.main([str(source), str(output)]) == 0
    image = parse_cas(output.read_bytes())
    assert [(b.type, b.name, b.length) for b in image.blocks] == [
        ("ascii", "HELLO", 16), ("data", None, len(text))]
    assert image.warnings == ()


def test_main_fails_when_no_block_is_found(tmp_path: Path) -> None:
    source = tmp_path / "silence.wav"
    output = tmp_path / "silence.cas"
    _write_wav(source, bytes(2 * bas2wav.SAMPLE_RATE))
    assert wav2cas.main([str(source), str(output)]) == 1
    assert not output.exists()


def test_cas_bytes_puts_each_header_at_an_8_byte_boundary() -> None:
    data = wav2cas.cas_bytes([bytes(3), bytes(5)])
    assert data == CAS_HEADER + bytes(3) + bytes(5) + CAS_HEADER + bytes(5)
