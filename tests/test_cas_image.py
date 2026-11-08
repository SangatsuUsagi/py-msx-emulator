"""io-cassette-image: the CAS parse, the 1200-baud synthesis and the block list."""
from __future__ import annotations

import struct

import pytest

from host.cas_image import (
    CAS_HEADER,
    CPU_HZ,
    SAMPLE_HZ,
    CasImageError,
    _Signal,
    parse_cas,
    sample_time,
)

_BINARY = bytes([0xD0] * 10)
_ASCII = bytes([0xEA] * 10)
_SAMPLE_T = CPU_HZ / SAMPLE_HZ  # about 745.7 T-states


def _intervals(edges: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(edges) // 4}I", edges))


def _binary_file(name: bytes, data: bytes) -> bytes:
    return CAS_HEADER + _BINARY + name + CAS_HEADER + data


def _runs(edges: bytes) -> list[tuple[int, int]]:
    """(level, length in samples) runs of the signal, rounded from T-states."""
    runs: list[tuple[int, int]] = []
    level = 1
    for interval in _intervals(edges):
        runs.append((level, round(interval / _SAMPLE_T)))
        level ^= 1
    return runs


def _decode_bytes(edges: bytes) -> list[int]:
    """Decode a signal that holds only bytes (no silence, no sync) by the
    length of each low half-cycle: 2 samples for a 0 bit, 1 for a 1 bit."""
    lows = [length for level, length in _runs(edges) if level == 0]
    bits: list[int] = []
    i = 0
    while i < len(lows):
        if lows[i] == 2:
            bits.append(0)
            i += 1
        else:
            assert lows[i] == 1 and lows[i + 1] == 1
            bits.append(1)
            i += 2
    values: list[int] = []
    for start in range(0, len(bits), 11):
        frame = bits[start:start + 11]
        assert frame[0] == 0 and frame[9:] == [1, 1]
        value = 0
        for i, bit in enumerate(frame[1:9]):
            value |= bit << i
        values.append(value)
    return values


# --- The CAS file is split at block headers ---

def test_no_header_is_rejected() -> None:
    with pytest.raises(CasImageError, match="not a valid CAS image"):
        parse_cas(bytes(64))


def test_leading_bytes_are_skipped() -> None:
    body = _binary_file(b"GAME  ", b"\x01\x02")
    plain = parse_cas(body)
    image = parse_cas(b"\x55\x66\x77" + body)
    assert image.warnings
    assert image.edges == plain.edges
    assert image.blocks == plain.blocks


def test_padding_is_played() -> None:
    padded = CAS_HEADER + _BINARY + b"GAME  " + bytes(5) + CAS_HEADER + b"\x01"
    image = parse_cas(padded)
    assert image.blocks[0].length == 10 + 6 + 5


# --- The file type decides the block layout ---

def test_a_binary_file_has_two_blocks() -> None:
    image = parse_cas(_binary_file(b"GAME  ", bytes(1000)))
    assert [(b.type, b.name, b.length) for b in image.blocks] == [
        ("binary", "GAME", 16),
        ("data", None, 1000),
    ]


def test_an_ascii_file_ends_at_1a() -> None:
    data = (CAS_HEADER + _ASCII + b"TEXT  "
            + CAS_HEADER + b"A" * 8
            + CAS_HEADER + b"B" * 7 + b"\x1a"
            + CAS_HEADER + b"C" * 8)
    image = parse_cas(data)
    assert [b.type for b in image.blocks] == ["ascii", "data", "data", "unknown"]
    # The block after the 0x1A block starts a new group with a long lead.
    runs = _runs(image.edges)
    data_sample = -(-image.blocks[3].start * SAMPLE_HZ // CPU_HZ)
    position = 0
    index = 0
    while position < data_sample:
        position += runs[index][1]
        index += 1
    assert position == data_sample
    assert _lead(runs, index) == (SAMPLE_HZ * 2 + 1, 16000)


# --- The signal is synthesized at 1200 baud ---

def test_a_byte_round_trips() -> None:
    signal = _Signal()
    for value in (0x00, 0x55, 0xA5, 0xFF):
        signal.byte(value)
    assert _decode_bytes(signal.edge_bytes()) == [0x00, 0x55, 0xA5, 0xFF]


def test_bits_are_1200_and_2400_hz_cycles() -> None:
    signal = _Signal()
    signal.bit(0)
    signal.bit(1)
    assert _runs(signal.edge_bytes()) == [(1, 2), (0, 2), (1, 1), (0, 1), (1, 1), (0, 1)]


# --- Each block starts with silence and a sync ---

def _lead(runs: list[tuple[int, int]], first: int) -> tuple[int, int]:
    """The silence-and-first-half run at index `first`, and the number of
    1-sample low halves (sync cycles) after it, up to the first 0 bit."""
    level, silence = runs[first]
    assert level == 1
    cycles = 0
    for level, length in runs[first + 1:]:
        if level == 0:
            if length != 1:
                break
            cycles += 1
    return silence, cycles


def test_the_sync_lengths() -> None:
    image = parse_cas(_binary_file(b"GAME  ", b"\x00"))
    runs = _runs(image.edges)
    # 2 s of level 1 merge with the first half of the first sync cycle;
    # the long sync is 8000 one bits = 16000 cycles of 2400 Hz.
    assert _lead(runs, 0) == (SAMPLE_HZ * 2 + 1, 16000)
    # The data block starts with 1 s of level 1 and 4000 cycles.
    data_sample = -(-image.blocks[1].start * SAMPLE_HZ // CPU_HZ)
    position = 0
    index = 0
    while position < data_sample:
        position += runs[index][1]
        index += 1
    assert position == data_sample
    assert _lead(runs, index) == (SAMPLE_HZ + 1, 4000)


def test_the_sync_cycle_counts() -> None:
    signal = _Signal()
    signal.silence(SAMPLE_HZ)
    signal.sync(2000)
    lows = [length for level, length in _runs(signal.edge_bytes()) if level == 0]
    assert lows == [1] * 4000


# --- The edge times come from integer arithmetic ---

def test_the_same_file_gives_the_same_bytes() -> None:
    data = _binary_file(b"GAME  ", bytes(range(256)) * 4)
    assert parse_cas(data).edges == parse_cas(data).edges


def test_no_drift_over_a_long_tape() -> None:
    signal = _Signal()
    for i in range(100_000):
        signal.bit(i % 3 == 0)
    total = 0
    times = []
    for interval in _intervals(signal.edge_bytes()):
        total += interval
        times.append(total)
    expected = [sample_time(sample) for sample in signal.change_samples]
    expected.append(sample_time(signal.sample_count))
    assert times == expected
    assert times[-1] == 100_000 * 4 * CPU_HZ // SAMPLE_HZ


def test_the_edge_list_starts_at_level_1_with_no_zero_interval() -> None:
    image = parse_cas(_binary_file(b"GAME  ", bytes(100)))
    intervals = _intervals(image.edges)
    assert len(image.edges) % 4 == 0 and intervals
    assert all(interval > 0 for interval in intervals)
    assert sum(intervals) == image.length


# --- The host lists the blocks ---

def test_the_list_of_a_binary_file() -> None:
    image = parse_cas(_binary_file(b"GAME  ", bytes(10)))
    first, second = image.blocks
    assert (first.number, first.type, first.name, first.start) == (0, "binary", "GAME", 0)
    signal = _Signal()
    signal.silence(SAMPLE_HZ * 2)
    signal.sync(8000)
    for _ in range(16):
        signal.byte(0)
    assert (second.number, second.type) == (1, "data")
    assert second.start == sample_time(signal.sample_count)


def test_a_binary_header_with_no_data_block_still_plays_a_data_sync() -> None:
    image = parse_cas(CAS_HEADER + _BINARY + b"GAME  ")
    assert [(b.type, b.length) for b in image.blocks] == [("binary", 16), ("data", 0)]
