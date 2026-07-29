"""YM2413 (OPLL) rhythm mode: register 0x0E enable + 5 percussion voices.

The chip is a faithful port of emu2413 v1.5.9 (see msx/opll.py). These tests
check that each rhythm instrument bit independently contributes audible PCM,
that BD/HH/SD/TOM/CYM lock to the fixed drum patch ROM, and that rhythm-off
restores ordinary melody behaviour on channels 6-8.
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


# ---------------------------------------------------------------------------
# BD/HH/SD/TOM/CYM are locked to the fixed drum patch ROM while rhythm is
# on, independent of registers 0x36-0x38's instrument/volume nibbles — real
# hardware forces this the moment rhythm mode turns on (openMSX
# setRhythmFlags: ch6/7/8.setPatch(16/17/18, ...)).
# ---------------------------------------------------------------------------

def test_bd_ignores_instrument_register_uses_fixed_drum_patch() -> None:
    o = Opll()
    # Leave register 0x36 (and the user-tone registers 0x00-0x07) at their
    # power-on default of 0: naively treating 0x36's high nibble as an
    # instrument index would select the all-zero user tone (AR=0 on both
    # operators), which is effectively silent for many frames. The fixed
    # drum patch has a fast attack and must produce clearly audible output.
    assert o.read_reg(0x36) == 0
    o.write_reg(0x16, 0x50)
    o.write_reg(0x26, 0x06)  # block=3
    o.write_reg(0x0E, _RHYTHM_ENABLE | _BD_BIT)
    buf = o.generate_samples(735)  # fast-attack drum patch: check the first buffer
    assert _max_abs(buf) > 500


def test_tom_pitch_independent_of_tom_volume_register() -> None:
    # Register 0x38's high nibble is TOM's *volume* in rhythm mode, not an
    # instrument index — TOM's pitch multiplier must come from the fixed
    # drum patch, not from misreading that nibble as an instrument select.
    o1 = Opll()
    o1.write_reg(0x18, 0x50)
    o1.write_reg(0x28, 0x06)
    o1.write_reg(0x0E, _RHYTHM_ENABLE | _TOM_BIT)
    o1.write_reg(0x38, 0x00)  # volume nibble 0
    o1.generate_samples(100)

    o2 = Opll()
    o2.write_reg(0x18, 0x50)
    o2.write_reg(0x28, 0x06)
    o2.write_reg(0x0E, _RHYTHM_ENABLE | _TOM_BIT)
    o2.write_reg(0x38, 0xF0)  # volume nibble 15 (different volume, same pitch)
    o2.generate_samples(100)

    # TOM is MOD(8) = slot 16; its phase accumulator must be identical
    # regardless of the volume nibble (volume affects amplitude, not pitch).
    assert o1._slot[16].pg_phase == o2._slot[16].pg_phase
