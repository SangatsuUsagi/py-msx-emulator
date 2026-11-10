#!/usr/bin/env python3
"""Convert an ASCII BASIC program to a WAV file that an MSX loads from tape.

Play the WAV into the EAR plug of the MSX cassette cable, after you type
LOAD"CAS:" (or RUN"CAS:") on the MSX. The file is an MSX ASCII tape file, the
same as SAVE"CAS:NAME" writes:

- a long header (16000 cycles of the 1-bit tone), then ten 0xEA bytes and the
  six-character file name;
- for each 256-byte data block, a short header (4000 cycles), then the block.
  The text ends with 0x1A, and the last block is padded with 0x1A.

Each byte is a 0 start bit, eight data bits (bit 0 first) and two 1 stop bits.
At 1200 baud a 0 is one cycle of 1200 Hz and a 1 is two cycles of 2400 Hz.
Silence comes before each header, so that BASIC has time to process a block.

Usage:
    ./venvs/py-msx-emulator/bin/python tools/bas2wav.py \\
        PROGRAM.BAS /tmp/PROGRAM.wav
"""

from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 48000
BAUD = 1200
SAMPLES_PER_BIT = SAMPLE_RATE // BAUD  # 40: one 1200 Hz cycle
LONG_HEADER_CYCLES = 16000
SHORT_HEADER_CYCLES = 4000
ASCII_FILE_ID = bytes([0xEA] * 10)
BLOCK_SIZE = 256
EOF = 0x1A
NAME_LENGTH = 6


def _square(cycles: int, samples_per_cycle: int, level: int) -> bytes:
    half = samples_per_cycle // 2
    high = struct.pack("<h", level) * half
    low = struct.pack("<h", -level) * half
    return (high + low) * cycles


class TapeWriter:
    """Builds the 16-bit mono PCM data of a tape signal."""

    def __init__(self, level: int) -> None:
        self._zero = _square(1, SAMPLES_PER_BIT, level)
        self._one = _square(2, SAMPLES_PER_BIT // 2, level)
        self._pcm = bytearray()

    def silence(self, seconds: float) -> None:
        self._pcm += bytes(2 * int(SAMPLE_RATE * seconds))

    def header(self, cycles: int) -> None:
        # A header is a run of 1 bits: two cycles of 2400 Hz each.
        self._pcm += self._one * (cycles // 2)

    def byte(self, value: int) -> None:
        self._pcm += self._zero
        for bit in range(8):
            if (value >> bit) & 1:
                self._pcm += self._one
            else:
                self._pcm += self._zero
        self._pcm += self._one
        self._pcm += self._one

    def data(self, values: bytes) -> None:
        for value in values:
            self.byte(value)

    def pcm(self) -> bytes:
        return bytes(self._pcm)


def program_text(source: bytes) -> bytes:
    """Return the program with CRLF line ends and the 0x1A end mark."""
    end = source.find(bytes([EOF]))
    if end >= 0:
        source = source[:end]
    lines = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    text = bytearray()
    for line in lines:
        if line:
            text += line + b"\r\n"
    text.append(EOF)
    while len(text) % BLOCK_SIZE != 0:
        text.append(EOF)
    return bytes(text)


def tape_name(name: str) -> bytes:
    raw = name.upper().encode("ascii")[:NAME_LENGTH]
    return raw + b" " * (NAME_LENGTH - len(raw))


def build_pcm(text: bytes, name: bytes, level: int, gap: float) -> bytes:
    tape = TapeWriter(level)
    tape.silence(2.0)
    tape.header(LONG_HEADER_CYCLES)
    tape.data(ASCII_FILE_ID + name)
    for start in range(0, len(text), BLOCK_SIZE):
        tape.silence(gap)
        tape.header(SHORT_HEADER_CYCLES)
        tape.data(text[start:start + BLOCK_SIZE])
    tape.silence(1.0)
    return tape.pcm()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="ASCII BASIC program (.BAS)")
    parser.add_argument("output", type=Path, help="WAV file to write")
    parser.add_argument("--name", help="tape file name, 6 characters (default: the file stem)")
    parser.add_argument("--invert", action="store_true",
                        help="invert the signal (try this if the MSX does not load)")
    parser.add_argument("--volume", type=float, default=0.8,
                        help="peak level, 0 to 1 (default: 0.8)")
    parser.add_argument("--gap", type=float, default=1.0,
                        help="seconds of silence before each data block (default: 1.0)")
    args = parser.parse_args(argv)

    if not 0.0 < args.volume <= 1.0:
        parser.error("--volume must be greater than 0 and at most 1")
    level = int(32767 * args.volume)
    if args.invert:
        level = -level
    name = tape_name(args.name if args.name is not None else args.source.stem)
    text = program_text(args.source.read_bytes())
    pcm = build_pcm(text, name, level, args.gap)

    with wave.open(str(args.output), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    seconds = len(pcm) / 2 / SAMPLE_RATE
    blocks = len(text) // BLOCK_SIZE
    print(f"{args.output}: {name.decode('ascii').rstrip()}, {blocks} blocks, {seconds:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
