#!/usr/bin/env python3
"""Convert a WAV of an MSX tape in the standard format to a CAS file.

The tape is at 1200 baud: a 0 bit is one cycle of 1200 Hz and a 1 bit is two
cycles of 2400 Hz. Each byte is a 0 start bit, eight data bits (bit 0 first)
and two 1 stop bits. Each block starts after a header (a run of the 1-bit
tone) and ends at silence. The script writes each block after the CAS block
header, at an 8-byte boundary.

The script measures the cycles between rising crossings, and again between
falling crossings, and keeps the result with more bytes. Thus it also reads
a WAV with the inverted signal.

Usage:
    ./venvs/py-msx-emulator/bin/python tools/wav2cas.py tape.wav tape.cas
"""

from __future__ import annotations

import argparse
import array
import sys
import wave
from pathlib import Path

CAS_HEADER = bytes([0x1F, 0xA6, 0xDE, 0xBA, 0xCC, 0x13, 0x7D, 0x74])
MIN_PILOT = 200  # the shortest run of 1-bit cycles that is a header

# _read_byte results that are not a byte.
_SILENCE = -1  # silence, or the end of the cycles: the block ends
_NOT_START = -2  # no start bit: the next header starts


def read_wav(path: Path) -> tuple[list[int], int]:
    """Return the samples (mono, signed) and the sample rate."""
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 1:  # an 8-bit WAV is unsigned
        samples = [b - 128 for b in raw]
    elif width == 2:
        samples = array.array("h", raw).tolist()
    else:
        raise SystemExit("only 8-bit and 16-bit WAV files are supported")
    if channels > 1:  # average the channels to mono
        mono: list[int] = []
        for i in range(0, len(samples), channels):
            mono.append(sum(samples[i:i + channels]) // channels)
        samples = mono
    return samples, rate


def cycle_lengths(samples: list[int], rate: int, polarity: int) -> list[float]:
    """The intervals (in seconds) between crossings, with DC removal and
    hysteresis. Polarity 1 measures rising crossings, -1 falling crossings."""
    out: list[float] = []
    previous = -1
    high = False
    dc = 0.0
    peak = max(1, max((abs(v) for v in samples), default=0))
    hysteresis = 0.08 * peak
    for i, v in enumerate(samples):
        dc += (v - dc) * 0.001  # a slow estimate of the DC offset
        x = (v - dc) * polarity
        if not high and x > hysteresis:
            high = True
            if previous >= 0:
                out.append((i - previous) / rate)
            previous = i
        elif high and x < -hysteresis:
            high = False
    return out


def _find_pilot(cycles: list[float], i: int, min_pilot: int) -> tuple[int, float]:
    """Find a run of at least min_pilot short cycles from cycle i. Return the
    index of the long cycle after the run (the start bit) and the average
    short cycle, or 0.0 when no run is left."""
    run = 0
    total = 0.0
    while i < len(cycles):
        c = cycles[i]
        if run and c > 1.5 * (total / run):  # a long cycle is the start bit
            break
        run += 1
        total += c
        i += 1
        if run > 1 and c > 3 * (total / run):  # reset at silence
            run = 0
            total = 0.0
    if i >= len(cycles) or run < min_pilot:
        return i, 0.0
    return i, total / run


def _read_bit(cycles: list[float], i: int, short: float) -> tuple[int, int]:
    """Read the bit at cycle i. Return the bit (-1 at silence or at the end
    of the cycles) and the index of the next cycle."""
    if i >= len(cycles):
        return -1, i
    c = cycles[i]
    if c > 3 * short:  # silence ends the block
        return -1, i
    # The threshold is between short and the 0-bit cycle (about 2 * short).
    if c > 1.5 * short:
        return 0, i + 1
    return 1, i + 2  # two short cycles make one bit


def _read_byte(cycles: list[float], i: int, short: float) -> tuple[int, int]:
    """Read the byte at cycle i. Return the byte, or _SILENCE or _NOT_START,
    and the index of the next cycle."""
    bit, i = _read_bit(cycles, i, short)
    if bit == 1:
        return _NOT_START, i - 2  # go back to the first cycle of the header
    if bit < 0:
        return _SILENCE, i + 1
    value = 0
    for k in range(8):
        bit, i = _read_bit(cycles, i, short)
        if bit < 0:
            return _SILENCE, i + 1
        value |= bit << k
    for _ in range(2):  # stop bits
        bit, i = _read_bit(cycles, i, short)
        if bit < 0:
            return _SILENCE, i + 1
    return value, i


def decode(cycles: list[float], min_pilot: int = MIN_PILOT) -> list[bytes]:
    """Return the bytes of each block."""
    blocks: list[bytes] = []
    i = 0
    while i < len(cycles):
        i, short = _find_pilot(cycles, i, min_pilot)
        if short == 0.0:
            break
        data = bytearray()
        while i < len(cycles):
            value, i = _read_byte(cycles, i, short)
            if value < 0:
                break
            data.append(value)
        if data:
            blocks.append(bytes(data))
    return blocks


def convert(samples: list[int], rate: int) -> list[bytes]:
    """Decode with rising and with falling crossings, and return the blocks
    with more bytes (rising crossings on a tie)."""
    best: list[bytes] = []
    best_size = -1
    for polarity in (1, -1):
        blocks = decode(cycle_lengths(samples, rate, polarity))
        size = 0
        for block in blocks:
            size += len(block)
        if size > best_size:
            best = blocks
            best_size = size
    return best


def cas_bytes(blocks: list[bytes]) -> bytes:
    """Return the CAS file: each block after a header at an 8-byte boundary."""
    out = bytearray()
    for block in blocks:
        while len(out) % 8:
            out.append(0x00)
        out += CAS_HEADER + block
    return bytes(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="WAV file of an MSX tape")
    parser.add_argument("output", type=Path, help="CAS file to write")
    args = parser.parse_args(argv)

    samples, rate = read_wav(args.source)
    blocks = convert(samples, rate)
    if not blocks:
        print(f"{args.source}: no tape block found", file=sys.stderr)
        return 1
    for n, block in enumerate(blocks):
        print(f"block {n}: {len(block)} bytes, head={block[:16].hex(' ')}")
    args.output.write_bytes(cas_bytes(blocks))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
