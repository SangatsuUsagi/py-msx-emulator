"""A CAS tape file, converted into the edge list the core plays.

A CAS file holds the bytes of the tape blocks, each block after the 8-byte
header 1F A6 DE BA CC 13 7D 74. This module finds the headers, groups the
blocks into files by their type bytes, and synthesizes the tape signal at
1200 baud, the MSX BIOS default (io-cassette-image).

The parse rules and the silence and sync lengths match openMSX's CasImage
(references/openMSX/src/cassette/CasImage.cc): a header is found at any
offset, the bytes before the first header are skipped with a warning, and all
bytes from one header to the next are played. openMSX plays at 3744 baud;
this module plays the same lengths at 1200 baud.

The signal is 4 samples per bit at 4800 Hz. A 0 bit is the levels 1 1 0 0 and
a 1 bit is 1 0 1 0, so a 0 bit is one cycle of 1200 Hz and a 1 bit two cycles
of 2400 Hz. Silence is level 1. The level change at sample k is at T-state
k * 3579545 // 4800, and the edge list holds the differences of those times
as unsigned 32-bit little-endian integers, the first interval at level 1
(io-cassette, "The edge list describes the tape signal").
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

CAS_HEADER = bytes((0x1F, 0xA6, 0xDE, 0xBA, 0xCC, 0x13, 0x7D, 0x74))
_TYPE_LENGTH = 10
_NAME_LENGTH = 6
_FILE_TYPES = {0xD3: "basic", 0xEA: "ascii", 0xD0: "binary"}

CPU_HZ = 3579545
SAMPLE_HZ = 4800
_SHORT_SILENCE = SAMPLE_HZ * 1  # samples
_LONG_SILENCE = SAMPLE_HZ * 2
_SHORT_SYNC = 2000  # one bits
_LONG_SYNC = 8000


class CasImageError(ValueError):
    """The file is not a CAS image this module can play."""


@dataclass(frozen=True)
class CasBlock:
    """One block of the tape. `start` is the T-state where the silence
    before its sync starts. `name` is set for the first block of a file."""

    number: int
    start: int
    type: str
    name: str | None
    length: int


@dataclass(frozen=True)
class CasImage:
    """The converted tape: the block list, the warnings, the edge list and
    the tape length in T-states."""

    blocks: tuple[CasBlock, ...]
    warnings: tuple[str, ...]
    edges: bytes
    length: int


def sample_time(sample: int) -> int:
    """The T-state of sample `sample`."""
    return sample * CPU_HZ // SAMPLE_HZ


class _Signal:
    """The tape signal as level runs; it keeps the sample of each change."""

    def __init__(self) -> None:
        self.samples = 0
        self.level = 1
        self.changes: list[int] = []

    def run(self, level: int, count: int) -> None:
        if level != self.level:
            self.changes.append(self.samples)
            self.level = level
        self.samples += count

    def silence(self, count: int) -> None:
        self.run(1, count)

    def bit(self, value: int) -> None:
        if value:
            self.run(1, 1)
            self.run(0, 1)
            self.run(1, 1)
            self.run(0, 1)
        else:
            self.run(1, 2)
            self.run(0, 2)

    def sync(self, ones: int) -> None:
        for _ in range(ones):
            self.bit(1)

    def byte(self, value: int) -> None:
        self.bit(0)
        for i in range(8):
            self.bit((value >> i) & 1)
        self.bit(1)
        self.bit(1)

    def edge_bytes(self) -> bytes:
        """The intervals between the changes, and from the last change to
        the end of the signal."""
        intervals: list[int] = []
        previous = 0
        for sample in self.changes:
            now = sample_time(sample)
            intervals.append(now - previous)
            previous = now
        intervals.append(sample_time(self.samples) - previous)
        return struct.pack(f"<{len(intervals)}I", *intervals)


class _Parser:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.signal = _Signal()
        self.blocks: list[CasBlock] = []

    def at_header(self) -> bool:
        end = self.pos + len(CAS_HEADER)
        return end <= len(self.data) and self.data[self.pos:end] == CAS_HEADER

    def block(self, silence: int, sync: int, kind: str, name: str | None) -> bool:
        """Play one block whose header is at `pos`: the silence, the sync
        and the bytes up to the next header. Returns whether the bytes hold
        0x1A, which ends an ASCII file. As in openMSX, a block is played also
        when no header is left, and then it has no bytes."""
        start = sample_time(self.signal.samples)
        self.pos += len(CAS_HEADER)
        self.signal.silence(silence)
        self.signal.sync(sync)
        first = self.pos
        eof = False
        while self.pos < len(self.data) and not self.at_header():
            value = self.data[self.pos]
            self.signal.byte(value)
            if value == 0x1A:
                eof = True
            self.pos += 1
        length = max(0, self.pos - first)
        self.blocks.append(CasBlock(len(self.blocks), start, kind, name, length))
        return eof

    def file_type(self) -> str:
        """The type of the block after the header at `pos`."""
        first = self.pos + len(CAS_HEADER)
        marker = self.data[first:first + _TYPE_LENGTH]
        if len(marker) == _TYPE_LENGTH and marker.count(marker[0]) == _TYPE_LENGTH:
            return _FILE_TYPES.get(marker[0], "unknown")
        return "unknown"

    def file_name(self) -> str:
        first = self.pos + len(CAS_HEADER) + _TYPE_LENGTH
        raw = self.data[first:first + _NAME_LENGTH]
        return raw.decode("ascii", errors="replace").rstrip(" ")

    def file(self) -> None:
        """Play the file whose first header is at `pos`."""
        kind = self.file_type()
        if kind == "unknown":
            self.block(_LONG_SILENCE, _LONG_SYNC, kind, None)
            return
        self.block(_LONG_SILENCE, _LONG_SYNC, kind, self.file_name())
        if kind == "ascii":
            while True:
                eof = self.block(_SHORT_SILENCE, _SHORT_SYNC, "data", None)
                if eof or not self.at_header():
                    return
        else:
            self.block(_SHORT_SILENCE, _SHORT_SYNC, "data", None)


def parse_cas(data: bytes) -> CasImage:
    """Convert a CAS file. Raises CasImageError when it holds no header."""
    parser = _Parser(data)
    skipped = 0
    found = False
    while parser.pos + len(CAS_HEADER) <= len(data):
        if parser.at_header():
            found = True
            parser.file()
        else:
            parser.pos += 1
            skipped += 1
    if not found:
        raise CasImageError("not a valid CAS image: no block header found")
    warnings: list[str] = []
    if skipped:
        warnings.append(f"skipped {skipped} bytes that are not in a block")
    return CasImage(
        blocks=tuple(parser.blocks),
        warnings=tuple(warnings),
        edges=parser.signal.edge_bytes(),
        length=sample_time(parser.signal.samples),
    )
