"""YM2413 (OPLL) rhythm mode: register 0x0E enable + 5 percussion voices.

Ground truth for the register map: openMSX YM2413Okazaki. Synthesis is
simplified (noise-based HH/SD/TC, single-tone TOM, full 2-op FM BD) — see
msx/opll.py module docstring. These tests check that each instrument bit
independently contributes audible PCM and that rhythm-off restores ordinary
melody behaviour on channels 6-8, not sample-exact timbre.
"""
from __future__ import annotations

from array import array

from msx.opll import Opll

_RHYTHM_ENABLE = 0x20
_BD_BIT = 0x10
_SD_BIT = 0x08
_TOM_BIT = 0x04
_TC_BIT = 0x02
_HH_BIT = 0x01

_LOUD_VOL_NIBBLE = 0x00  # 0 = loudest in this register's 4-bit scale


def _max_abs(buf: bytes) -> int:
    arr = array("h")
    arr.frombytes(buf)
    return max(abs(x) for x in arr) if len(arr) else 0


# ---------------------------------------------------------------------------
# Rhythm-enable bit and instrument key bits
# ---------------------------------------------------------------------------

def test_rhythm_disabled_by_default() -> None:
    o = Opll()
    assert (o.read_reg(0x0E) & _RHYTHM_ENABLE) == 0


def test_rhythm_enable_bit_set() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE)
    assert (o.read_reg(0x0E) & _RHYTHM_ENABLE) != 0


# ---------------------------------------------------------------------------
# Each rhythm instrument contributes audible PCM when keyed
# ---------------------------------------------------------------------------

def test_hi_hat_contributes_pcm() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE | _HH_BIT)
    o.write_reg(0x37, _LOUD_VOL_NIBBLE << 4)
    assert _max_abs(o.generate_samples(735)) > 0


def test_snare_contributes_pcm() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE | _SD_BIT)
    o.write_reg(0x37, _LOUD_VOL_NIBBLE)
    assert _max_abs(o.generate_samples(735)) > 0


def test_tom_contributes_pcm() -> None:
    o = Opll()
    o.write_reg(0x18, 0x50)
    o.write_reg(0x28, 0x06)  # block=3
    o.write_reg(0x0E, _RHYTHM_ENABLE | _TOM_BIT)
    o.write_reg(0x38, _LOUD_VOL_NIBBLE << 4)
    assert _max_abs(o.generate_samples(735)) > 0


def test_top_cymbal_contributes_pcm() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE | _TC_BIT)
    o.write_reg(0x38, _LOUD_VOL_NIBBLE)
    assert _max_abs(o.generate_samples(735)) > 0


def test_bass_drum_contributes_pcm() -> None:
    o = Opll()
    o.write_reg(0x16, 0x50)
    o.write_reg(0x26, 0x06)  # block=3
    o.write_reg(0x36, 0x10)  # instrument=1, vol=0
    o.write_reg(0x0E, _RHYTHM_ENABLE | _BD_BIT)
    assert _max_abs(o.generate_samples(735)) > 0


def test_no_instrument_bit_is_silent() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE)  # rhythm on, no instrument keyed
    o.write_reg(0x37, 0x00)
    o.write_reg(0x38, 0x00)
    assert _max_abs(o.generate_samples(735)) == 0


# ---------------------------------------------------------------------------
# Rhythm disabled leaves channels 6-8 as ordinary melody channels
# ---------------------------------------------------------------------------

def test_rhythm_disabled_channel7_plays_melody() -> None:
    o = Opll()
    assert (o.read_reg(0x0E) & _RHYTHM_ENABLE) == 0
    o.write_reg(0x37, 0x10)  # instrument=1, vol=0
    o.write_reg(0x17, 0x50)
    o.write_reg(0x27, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_rhythm_disabled_channel8_plays_melody() -> None:
    o = Opll()
    o.write_reg(0x38, 0x10)
    o.write_reg(0x18, 0x50)
    o.write_reg(0x28, 0x16)  # block=3, KON=1
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0


def test_rhythm_off_after_being_on_restores_channel7_melody() -> None:
    o = Opll()
    o.write_reg(0x0E, _RHYTHM_ENABLE | _HH_BIT)
    o.generate_samples(100)
    o.write_reg(0x0E, 0x00)  # rhythm off again
    o.write_reg(0x37, 0x10)
    o.write_reg(0x17, 0x50)
    o.write_reg(0x27, 0x16)
    for _ in range(4):
        buf = o.generate_samples(735)
    assert _max_abs(buf) > 0
