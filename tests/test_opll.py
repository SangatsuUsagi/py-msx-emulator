"""YM2413 (OPLL) tests: register decode, melody FM synthesis, and ADSR shape.

The chip is a faithful port of emu2413 v1.5.9 (see msx/opll.py). These tests
check register/behaviour correctness (key-on produces sound, key-off decays to
silence, preset vs. user tone selection, note pitch) and lock the ported
instrument ROM and exp/log-sin tables to emu2413's reference values.
"""
from __future__ import annotations

from array import array

import pytest

from msx.opll import (
    _DEFAULT_INST,
    _EXP_TABLE,
    _FULLSIN_TABLE,
    SAMPLE_RATE,
    Opll,
    note_frequency,
)

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


# ---------------------------------------------------------------------------
# Ported-data locks: instrument ROM and generated tables must match emu2413.
# ---------------------------------------------------------------------------

def test_instrument_rom_matches_emu2413() -> None:
    # emu2413 v1.5.9 default_inst[0] (YM2413 set): 16 melody + 3 rhythm patches.
    emu = (
        (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
        (0x71, 0x61, 0x1E, 0x17, 0xD0, 0x78, 0x00, 0x17),
        (0x13, 0x41, 0x1A, 0x0D, 0xD8, 0xF7, 0x23, 0x13),
        (0x13, 0x01, 0x99, 0x00, 0xF2, 0xC4, 0x21, 0x23),
        (0x11, 0x61, 0x0E, 0x07, 0x8D, 0x64, 0x70, 0x27),
        (0x32, 0x21, 0x1E, 0x06, 0xE1, 0x76, 0x01, 0x28),
        (0x31, 0x22, 0x16, 0x05, 0xE0, 0x71, 0x00, 0x18),
        (0x21, 0x61, 0x1D, 0x07, 0x82, 0x81, 0x11, 0x07),
        (0x33, 0x21, 0x2D, 0x13, 0xB0, 0x70, 0x00, 0x07),
        (0x61, 0x61, 0x1B, 0x06, 0x64, 0x65, 0x10, 0x17),
        (0x41, 0x61, 0x0B, 0x18, 0x85, 0xF0, 0x81, 0x07),
        (0x33, 0x01, 0x83, 0x11, 0xEA, 0xEF, 0x10, 0x04),
        (0x17, 0xC1, 0x24, 0x07, 0xF8, 0xF8, 0x22, 0x12),
        (0x61, 0x50, 0x0C, 0x05, 0xD2, 0xF5, 0x40, 0x42),
        (0x01, 0x01, 0x55, 0x03, 0xE9, 0x90, 0x03, 0x02),
        (0x41, 0x41, 0x89, 0x03, 0xF1, 0xE4, 0xC0, 0x13),
        (0x01, 0x01, 0x18, 0x0F, 0xDF, 0xF8, 0x6A, 0x6D),
        (0x01, 0x01, 0x00, 0x00, 0xC8, 0xD8, 0xA7, 0x68),
        (0x05, 0x01, 0x00, 0x00, 0xF8, 0xAA, 0x59, 0x55),
    )
    assert _DEFAULT_INST == emu


def test_exp_table_matches_emu2413_formula() -> None:
    # exp_table[x] = round((2^(x/256) - 1) * 1024)
    assert _EXP_TABLE[0] == 0
    assert _EXP_TABLE[1] == 3
    assert _EXP_TABLE[255] == 1018
    for x in (5, 64, 128, 200):
        assert _EXP_TABLE[x] == int((2 ** (x / 256.0) - 1) * 1024 + 0.5)


def test_fullsin_table_matches_emu2413_formula() -> None:
    # First quarter is -log2(sin(...)) * 256; the rest is mirrored/sign-extended.
    assert _FULLSIN_TABLE[0] == 2137
    assert _FULLSIN_TABLE[255] == 0
    # Second quarter mirrors the first (makeSinTable).
    assert _FULLSIN_TABLE[256] == _FULLSIN_TABLE[255]
    assert _FULLSIN_TABLE[511] == _FULLSIN_TABLE[0]
    # Negative half carries the 0x8000 sign bit.
    assert _FULLSIN_TABLE[512] == (0x8000 | _FULLSIN_TABLE[0])


def test_clarinet_pitch_is_accurate() -> None:
    # A harmonically simple preset lets a coarse autocorrelation recover the
    # fundamental; the emu2413 phase generator must land on the note pitch.
    o = Opll()
    o.write_reg(0x30, 0x50)  # clarinet, vol 0
    fnum, block = 290, 4
    o.write_reg(0x10, fnum & 0xFF)
    o.write_reg(0x20, ((fnum >> 8) & 1) | (block << 1) | 0x10)
    for _ in range(8):
        o.generate_samples(735)
    buf = bytearray()
    for _ in range(6):
        buf += o.generate_samples(735)
    arr = array("h")
    arr.frombytes(bytes(buf))
    samples = list(arr)

    sr = 44100
    best_lag, best = 0, -1e18
    for lag in range(int(sr / 2000), int(sr / 100)):
        s = sum(samples[i] * samples[i + lag] for i in range(len(samples) - lag))
        if s > best:
            best, best_lag = s, lag
    measured = sr / best_lag
    assert measured == pytest.approx(note_frequency(fnum, block), rel=0.03)
