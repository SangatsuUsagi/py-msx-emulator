"""YM2413 (OPLL) tests: register decode, melody FM synthesis, and ADSR shape.

Ground truth for the register map and instrument ROM: openMSX YM2413Okazaki.
Synthesis is intentionally simplified (see msx/opll.py module docstring) —
these tests check audibly-correct behaviour (key-on produces sound, key-off
decays to silence, preset vs. user tone selection), not sample-exact output.
"""
from __future__ import annotations

from array import array

import pytest

from msx.opll import SAMPLE_RATE, Opll, note_frequency

_LOUD_INSTRUMENT_VOL = 0x10  # instrument=1 (violin), vol=0 (loudest)


def _key_on(o: Opll, ch: int, fnum: int = 0x150, block: int = 3) -> None:
    o.write_reg(0x30 + ch, _LOUD_INSTRUMENT_VOL)
    o.write_reg(0x10 + ch, fnum & 0xFF)
    o.write_reg(0x20 + ch, ((fnum >> 8) & 0x01) | (block << 1) | 0x10)


def _key_off(o: Opll, ch: int, fnum: int = 0x150, block: int = 3) -> None:
    o.write_reg(0x10 + ch, fnum & 0xFF)
    o.write_reg(0x20 + ch, ((fnum >> 8) & 0x01) | (block << 1))  # KON bit clear


def _max_abs(buf: bytes) -> int:
    arr = array("h")
    arr.frombytes(buf)
    return max(abs(x) for x in arr) if len(arr) else 0


# ---------------------------------------------------------------------------
# Register file (inherited from Phase 1, re-checked here for the synthesis path)
# ---------------------------------------------------------------------------

def test_register_write_via_latch_and_data() -> None:
    o = Opll()
    o.write_addr(0x30)
    o.write_data(0x1F)
    assert o.read_reg(0x30) == 0x1F


def test_chip_is_write_only() -> None:
    o = Opll()
    assert o.read() == 0xFF


def test_reset_clears_registers_and_silences() -> None:
    o = Opll()
    _key_on(o, 0)
    o.reset()
    assert o.read_reg(0x20) == 0
    assert o.read_reg(0x30) == 0
    buf = o.generate_samples(100)
    assert _max_abs(buf) == 0


# ---------------------------------------------------------------------------
# Buffer format
# ---------------------------------------------------------------------------

def test_generate_samples_buffer_format() -> None:
    o = Opll()
    buf = o.generate_samples(64)
    assert isinstance(buf, bytearray)
    assert len(buf) == 64 * 2


# ---------------------------------------------------------------------------
# Melody synthesis: key-on / key-off
# ---------------------------------------------------------------------------

def test_key_on_produces_nonzero_pcm() -> None:
    o = Opll()
    _key_on(o, 0)
    # Run a few buffers so the attack ramp has risen off zero.
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_silent_channel_produces_no_pcm() -> None:
    o = Opll()
    buf = o.generate_samples(735)
    assert _max_abs(buf) == 0


def test_key_off_decays_toward_silence() -> None:
    o = Opll()
    _key_on(o, 0)
    for _ in range(10):  # let the envelope reach a steady sustain
        o.generate_samples(735)
    _key_off(o, 0)
    for _ in range(60):  # long enough for release to complete
        buf = o.generate_samples(735)
    assert _max_abs(buf) == 0


# ---------------------------------------------------------------------------
# Preset vs. user tone selection
# ---------------------------------------------------------------------------

def test_preset_instrument_selected() -> None:
    o = Opll()
    o.write_reg(0x30, 0x18)  # instrument=1 (violin), vol=8 (mid-range, audible)
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_user_tone_selected() -> None:
    o = Opll()
    # User tone: instrument index 0, programmed via registers 0x00-0x07.
    o.write_reg(0x00, 0x61)  # mod multi + EG type/KR
    o.write_reg(0x01, 0x61)  # car multi + EG type/KR
    o.write_reg(0x02, 0x00)  # mod TL = 0 (loudest modulation)
    o.write_reg(0x03, 0x00)
    o.write_reg(0x04, 0xF0)  # mod AR/DR
    o.write_reg(0x05, 0xF0)  # car AR/DR
    o.write_reg(0x06, 0x00)
    o.write_reg(0x07, 0x00)
    o.write_reg(0x30, 0x00)  # instrument=0 (user), vol=0
    o.write_reg(0x10, 0x50)
    o.write_reg(0x20, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


# ---------------------------------------------------------------------------
# Multi-channel
# ---------------------------------------------------------------------------

def test_multiple_channels_sound_together() -> None:
    o = Opll()
    for ch in range(9):
        _key_on(o, ch, fnum=0x100 + ch * 20, block=3)
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


@pytest.mark.parametrize("sample_rate", [SAMPLE_RATE])
def test_sample_rate_matches_psg(sample_rate: int) -> None:
    assert sample_rate == 44_100


# ---------------------------------------------------------------------------
# Note frequency accuracy (standard YM2413 Fnum/Block formula)
# ---------------------------------------------------------------------------

def test_a4_frequency_matches_reference() -> None:
    # A4 = 440 Hz is the commonly documented reference: Fnum 290 at block 4.
    assert note_frequency(290, 4) == pytest.approx(440.0, abs=0.5)


def test_octave_doubles_frequency() -> None:
    base = note_frequency(300, 3)
    octave_up = note_frequency(300, 4)
    assert octave_up == pytest.approx(base * 2.0, rel=1e-9)
