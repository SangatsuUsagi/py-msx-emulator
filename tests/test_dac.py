"""Tests for msx/dac.py EventDac (openspec/specs/audio-dac/spec.md)."""

import struct

from msx.dac import EVENT_DAC_MAX_EVENTS, EventDac


def _samples(buf: bytearray | None) -> list[int]:
    assert buf is not None
    return [struct.unpack_from("<h", buf, i * 2)[0] for i in range(len(buf) // 2)]


def test_changed_level_is_recorded() -> None:
    dac = EventDac()
    dac.write(10, 1000)
    assert dac._events == [(10, 1000)]


def test_unchanged_level_is_not_recorded() -> None:
    dac = EventDac()
    dac.write(10, 1000)
    dac.write(20, 1000)
    assert dac._events == [(10, 1000)]


def test_events_beyond_the_bound_are_dropped() -> None:
    dac = EventDac()
    for i in range(5000):
        dac.write(i, 1000 if i % 2 == 0 else 0)
    assert EVENT_DAC_MAX_EVENTS == 4096
    assert len(dac._events) == 4096


def test_held_level_follows_last_write_after_full_buffer() -> None:
    dac = EventDac()
    # 4097 alternating writes: index 0 writes 1000, so the last (index 4096)
    # also writes 1000 and is the one the bound drops.
    for i in range(4097):
        dac.write(i, 1000 if i % 2 == 0 else 0)
    dac.generate_samples(4, 0, 100)
    assert _samples(dac.generate_samples(2, 100, 200)) == [1000, 1000]


def test_output_buffer_length() -> None:
    dac = EventDac()
    dac.write(0, 1000)
    buf = dac.generate_samples(735, 0, 59736)
    assert buf is not None
    assert len(buf) == 1470


def test_mid_frame_write_appears_at_correct_sample_position() -> None:
    dac = EventDac()
    dac.write(50, 1000)
    assert _samples(dac.generate_samples(4, 0, 100)) == [0, 0, 1000, 1000]


def test_level_persists_into_next_frame() -> None:
    dac = EventDac()
    dac.write(0, 1000)
    dac.generate_samples(4, 0, 100)
    assert _samples(dac.generate_samples(2, 100, 200)) == [1000, 1000]


def test_events_cleared_after_generation() -> None:
    dac = EventDac()
    dac.write(0, 1000)
    dac.generate_samples(4, 0, 100)
    assert dac._events == []


def test_negative_levels_are_encoded_as_signed_16_bit() -> None:
    dac = EventDac()
    dac.write(0, -32768)
    assert _samples(dac.generate_samples(2, 0, 100))[0] == -32768


def test_fresh_dac_is_silent() -> None:
    assert EventDac().generate_samples(4, 0, 100) is None


def test_held_non_zero_level_is_not_silent() -> None:
    dac = EventDac()
    dac.write(0, 1000)
    dac.generate_samples(4, 0, 100)
    assert _samples(dac.generate_samples(2, 100, 200)) == [1000, 1000]


def test_pulse_returning_to_zero_within_frame_is_not_silent() -> None:
    dac = EventDac()
    dac.write(10, 1000)
    dac.write(60, 0)
    assert _samples(dac.generate_samples(4, 0, 100)) == [0, 1000, 1000, 0]


def test_reset_drops_pending_events_and_sets_level() -> None:
    dac = EventDac()
    dac.write(10, 1000)
    dac.reset(0)
    assert dac._events == []
    assert dac.generate_samples(2, 0, 100) is None


def test_reset_to_non_zero_level_holds_that_level() -> None:
    dac = EventDac()
    dac.reset(500)
    assert _samples(dac.generate_samples(2, 0, 100)) == [500, 500]
