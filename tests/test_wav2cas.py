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


# A recorded tape, as cycle lengths in seconds: a 1 bit is two cycles of
# 2400 Hz and a 0 bit is one cycle of 1200 Hz.
_SHORT = 1 / 2400
_SILENCE = 1.0


def _tone(data: bytes) -> list[float]:
    cycles = [_SHORT] * 1000  # the header
    for value in data:
        bits = [0] + [(value >> k) & 1 for k in range(8)] + [1, 1]
        for bit in bits:
            cycles += [_SHORT, _SHORT] if bit else [2 * _SHORT]
    return cycles


def test_decode_skips_the_noise_at_the_start_of_the_tone() -> None:
    cycles = [0.9 * _SHORT, 0.95 * _SHORT, 3 * _SHORT] + _tone(b"AB")
    assert wav2cas.decode(cycles) == [b"AB"]


def test_decode_reads_each_block_after_silence() -> None:
    cycles = [_SILENCE] + _tone(b"AB") + [_SILENCE] + _tone(b"CD")
    assert wav2cas.decode(cycles) == [b"AB", b"CD"]


@pytest.mark.parametrize(
    ("data", "split"),
    [
        # the third data bit of the second byte: one 0-bit cycle
        (b"\x00\x00", 1000 + 13 + 3),
        # the second cycle of the first data bit: a 1-bit cycle
        (b"\xff\xff", 1000 + 1 + 1),
    ],
    ids=["0-bit", "1-bit"],
)
def test_decode_joins_a_cycle_that_noise_splits(data: bytes, split: int) -> None:
    cycles = _tone(data)
    whole = cycles[split]
    cycles[split:split + 1] = [0.25 * whole, 0.75 * whole]
    assert wav2cas.decode(cycles) == [data]
