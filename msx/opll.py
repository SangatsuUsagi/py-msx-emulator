"""YM2413 (OPLL) FM sound chip — a faithful Python port of emu2413 v1.5.9.

Source / acknowledgement
------------------------
This module is a Python port of emu2413, the reference OPLL emulator by
Mitsutaka Okazaki:

    emu2413 v1.5.9 — Copyright (c) Mitsutaka Okazaki, MIT-licensed.
    https://github.com/digital-sound-antiques/emu2413

The log-domain synthesis, the envelope-rate and instrument-ROM tables, the
AM/PM LFO, and the rhythm-mode noise taps all follow that implementation.
Grateful thanks to the author and contributors for documenting the chip so
thoroughly. This port reproduces the chip's log-domain
synthesis: a log-sin phase-generator table, an exp output table, the
hardware envelope-rate tables (with key-scale), the AM/PM LFO, the
instrument ROM, and rhythm mode with the real short-noise / LFSR taps. The
exp and log-sin tables are computed here from the same formulas the C
reference tabulates (verified to reproduce its literals exactly).

Differences from emu2413, all intentional:
- Output rate conversion uses a plain accumulate-and-average decimator from
  the chip's native clk/72 rate to 44100 Hz, not emu2413's windowed-sinc
  converter. The analog-style low-pass in the SDL frontend cleans up the
  residual imaging.
- Only the YM2413 instrument set (chip type 0) is included; the VRC7 / YMF281
  patch banks are not.
- Channel masking / stereo panning are omitted (mono mix only).

Public interface (register latch/data + generate_samples) is unchanged, so
msx/fmpac.py drives it exactly as before.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from msx.psg import SAMPLE_RATE, SAMPLES_PER_FRAME

__all__ = ["Opll", "SAMPLE_RATE", "SAMPLES_PER_FRAME", "note_frequency"]

_CLOCK = 3_579_545  # Hz — full MSX CPU clock; the chip runs at clk / 72.

# --- fixed-point / range constants (emu2413 names) ---
_DP_BITS = 19
_DP_WIDTH = 1 << _DP_BITS
_PG_BITS = 10
_PG_WIDTH = 1 << _PG_BITS
_DP_BASE_BITS = _DP_BITS - _PG_BITS  # 9

_EG_STEP = 0.375
_EG_BITS = 7
_EG_MUTE = (1 << _EG_BITS) - 1  # 127
_EG_MAX = _EG_MUTE - 4          # 123
_DAMPER_RATE = 12
# Fixed release rates (emu2413 get_parameter_rate, RELEASE branch): a note with
# the sustain flag set releases at rate 5; a percussive tone (patch EG=0) at 7.
_SUS_RELEASE_RATE = 5
_PERC_RELEASE_RATE = 7


def _tl2eg(d: int) -> int:
    return d << 1


# --- envelope states (emu2413 enum order) ---
_ATTACK = 0
_DECAY = 1
_SUSTAIN = 2
_RELEASE = 3
_DAMP = 4

# --- rhythm slot indices: MOD(ch)=2*ch, CAR(ch)=2*ch+1 ---
_SLOT_BD1 = 12  # MOD(6)
_SLOT_BD2 = 13  # CAR(6)
_SLOT_HH = 14   # MOD(7)
_SLOT_SD = 15   # CAR(7)
_SLOT_TOM = 16  # MOD(8)
_SLOT_CYM = 17  # CAR(8)


# ---------------------------------------------------------------------------
# Static tables (built once at import; formulas match emu2413's tabulations)
# ---------------------------------------------------------------------------

def _build_exp_table() -> list[int]:
    # exp_table[x] = round((2^(x/256) - 1) * 1024)
    return [int((2 ** (x / 256.0) - 1) * 1024 + 0.5) for x in range(256)]


def _build_sin_tables() -> tuple[list[int], list[int]]:
    # fullsin_table[x] = round(-log2(sin((x + 0.5) * pi / (PG_WIDTH/4) / 2)) * 256)
    # for the first quarter, then mirrored/sign-extended (makeSinTable).
    full = [0] * _PG_WIDTH
    quarter = _PG_WIDTH // 4  # 256
    for x in range(quarter):
        full[x] = int(-math.log2(math.sin((x + 0.5) * math.pi / (quarter * 2))) * 256 + 0.5)
    for x in range(quarter):
        full[quarter + x] = full[quarter - x - 1]
    for x in range(_PG_WIDTH // 2):
        full[_PG_WIDTH // 2 + x] = 0x8000 | full[x]
    half = [0] * _PG_WIDTH
    for x in range(_PG_WIDTH // 2):
        half[x] = full[x]
    for x in range(_PG_WIDTH // 2, _PG_WIDTH):
        half[x] = 0xFFF
    return full, half


_EXP_TABLE = _build_exp_table()
_FULLSIN_TABLE, _HALFSIN_TABLE = _build_sin_tables()
_WAVE_TABLE_MAP = (_FULLSIN_TABLE, _HALFSIN_TABLE)

# Pitch-modulation table (offset to fnum), indexed [ (fnum>>6)&7 ][ (pm_phase>>10)&7 ].
_PM_TABLE: tuple[tuple[int, ...], ...] = (
    (0, 0, 0, 0, 0, 0, 0, 0),
    (0, 0, 1, 0, 0, 0, -1, 0),
    (0, 1, 2, 1, 0, -1, -2, -1),
    (0, 1, 3, 1, 0, -1, -3, -1),
    (0, 2, 4, 2, 0, -2, -4, -2),
    (0, 2, 5, 2, 0, -2, -5, -2),
    (0, 3, 6, 3, 0, -3, -6, -3),
    (0, 3, 7, 3, 0, -3, -7, -3),
)

# Amplitude-modulation LFO table (each element repeats 64 cycles). Length 210.
_AM_TABLE: tuple[int, ...] = (
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1,
    2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
    4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5,
    6, 6, 6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 7,
    8, 8, 8, 8, 8, 8, 8, 8, 9, 9, 9, 9, 9, 9, 9, 9,
    10, 10, 10, 10, 10, 10, 10, 10, 11, 11, 11, 11, 11, 11, 11, 11,
    12, 12, 12, 12, 12, 12, 12, 12,
    13, 13, 13,
    12, 12, 12, 12, 12, 12, 12, 12,
    11, 11, 11, 11, 11, 11, 11, 11, 10, 10, 10, 10, 10, 10, 10, 10,
    9, 9, 9, 9, 9, 9, 9, 9, 8, 8, 8, 8, 8, 8, 8, 8,
    7, 7, 7, 7, 7, 7, 7, 7, 6, 6, 6, 6, 6, 6, 6, 6,
    5, 5, 5, 5, 5, 5, 5, 5, 4, 4, 4, 4, 4, 4, 4, 4,
    3, 3, 3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2,
    1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0,
)
_AM_TABLE_LEN = len(_AM_TABLE)  # hoisted out of the per-tick hot path

# Envelope decay increment step table (andete's research).
_EG_STEP_TABLES: tuple[tuple[int, ...], ...] = (
    (0, 1, 0, 1, 0, 1, 0, 1),
    (0, 1, 0, 1, 1, 1, 0, 1),
    (0, 1, 1, 1, 0, 1, 1, 1),
    (0, 1, 1, 1, 1, 1, 1, 1),
)

_ML_TABLE: tuple[int, ...] = (
    1, 1 * 2, 2 * 2, 3 * 2, 4 * 2, 5 * 2, 6 * 2, 7 * 2,
    8 * 2, 9 * 2, 10 * 2, 10 * 2, 12 * 2, 12 * 2, 15 * 2, 15 * 2,
)


def _db2(x: float) -> float:
    return x * 2.0


_KL_TABLE: tuple[float, ...] = (
    _db2(0.000), _db2(9.000), _db2(12.000), _db2(13.875), _db2(15.000), _db2(16.125),
    _db2(16.875), _db2(17.625), _db2(18.000), _db2(18.750), _db2(19.125), _db2(19.500),
    _db2(19.875), _db2(20.250), _db2(20.625), _db2(21.000),
)


def _build_tll_table() -> list[list[list[int]]]:
    # tll_table[(block<<4)|fnum][TL][KL]
    result = [[[0] * 4 for _ in range(64)] for _ in range(8 * 16)]
    for fnum in range(16):
        for block in range(8):
            for tl in range(64):
                for kl in range(4):
                    if kl == 0:
                        result[(block << 4) | fnum][tl][kl] = _tl2eg(tl)
                    else:
                        tmp = int(_KL_TABLE[fnum] - _db2(3.000) * (7 - block))
                        if tmp <= 0:
                            result[(block << 4) | fnum][tl][kl] = _tl2eg(tl)
                        else:
                            result[(block << 4) | fnum][tl][kl] = (
                                int((tmp >> (3 - kl)) / _EG_STEP) + _tl2eg(tl)
                            )
    return result


def _build_rks_table() -> list[list[int]]:
    result = [[0, 0] for _ in range(8 * 2)]
    for fnum8 in range(2):
        for block in range(8):
            result[(block << 1) | fnum8][1] = (block << 1) + fnum8
            result[(block << 1) | fnum8][0] = block >> 1
    return result


_TLL_TABLE = _build_tll_table()
_RKS_TABLE = _build_rks_table()

# YM2413 instrument ROM (emu2413 default_inst[0]): 16 melody tones (index 0 =
# user) + 3 rhythm patches (BD, HH/SD, TOM/CYM).
_DEFAULT_INST: tuple[tuple[int, ...], ...] = (
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),  # 0: user
    (0x71, 0x61, 0x1E, 0x17, 0xD0, 0x78, 0x00, 0x17),  # 1: violin
    (0x13, 0x41, 0x1A, 0x0D, 0xD8, 0xF7, 0x23, 0x13),  # 2: guitar
    (0x13, 0x01, 0x99, 0x00, 0xF2, 0xC4, 0x21, 0x23),  # 3: piano
    (0x11, 0x61, 0x0E, 0x07, 0x8D, 0x64, 0x70, 0x27),  # 4: flute
    (0x32, 0x21, 0x1E, 0x06, 0xE1, 0x76, 0x01, 0x28),  # 5: clarinet
    (0x31, 0x22, 0x16, 0x05, 0xE0, 0x71, 0x00, 0x18),  # 6: oboe
    (0x21, 0x61, 0x1D, 0x07, 0x82, 0x81, 0x11, 0x07),  # 7: trumpet
    (0x33, 0x21, 0x2D, 0x13, 0xB0, 0x70, 0x00, 0x07),  # 8: organ
    (0x61, 0x61, 0x1B, 0x06, 0x64, 0x65, 0x10, 0x17),  # 9: horn
    (0x41, 0x61, 0x0B, 0x18, 0x85, 0xF0, 0x81, 0x07),  # A: synthesizer
    (0x33, 0x01, 0x83, 0x11, 0xEA, 0xEF, 0x10, 0x04),  # B: harpsichord
    (0x17, 0xC1, 0x24, 0x07, 0xF8, 0xF8, 0x22, 0x12),  # C: vibraphone
    (0x61, 0x50, 0x0C, 0x05, 0xD2, 0xF5, 0x40, 0x42),  # D: synth bass
    (0x01, 0x01, 0x55, 0x03, 0xE9, 0x90, 0x03, 0x02),  # E: acoustic bass
    (0x41, 0x41, 0x89, 0x03, 0xF1, 0xE4, 0xC0, 0x13),  # F: electric guitar
    (0x01, 0x01, 0x18, 0x0F, 0xDF, 0xF8, 0x6A, 0x6D),  # R: bass drum
    (0x01, 0x01, 0x00, 0x00, 0xC8, 0xD8, 0xA7, 0x68),  # R: high-hat(M)/snare(C)
    (0x05, 0x01, 0x00, 0x00, 0xF8, 0xAA, 0x59, 0x55),  # R: tom(M)/top-cymbal(C)
)


def note_frequency(fnum: int, block: int) -> float:
    """Return the note frequency in Hz for a 9-bit F-number and 3-bit block.

    Standard YM2413 formula f = Fnum * 2^Block * clock / (2^19 * 72). Kept for
    documentation and tests; the synthesis path uses the phase accumulator
    directly (calc_phase), which is mathematically equivalent.
    """
    return fnum * (1 << block) * _CLOCK / (1 << 19) / 72.0


@dataclass
class _Patch:
    __slots__ = ("AM", "PM", "EG", "KR", "ML", "KL", "TL", "FB", "WS",
                 "AR", "DR", "SL", "RR")
    AM: int
    PM: int
    EG: int
    KR: int
    ML: int
    KL: int
    TL: int
    FB: int
    WS: int
    AR: int
    DR: int
    SL: int
    RR: int


def _null_patch() -> _Patch:
    return _Patch(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def _dump_to_patch(dump: tuple[int, ...] | bytes, off: int = 0) -> tuple[_Patch, _Patch]:
    """Decode 8 instrument bytes into (modulator, carrier) patches (OPLL_dumpToPatch)."""
    d0, d1, d2, d3, d4, d5, d6, d7 = (dump[off + i] for i in range(8))
    mod = _Patch(
        AM=(d0 >> 7) & 1, PM=(d0 >> 6) & 1, EG=(d0 >> 5) & 1, KR=(d0 >> 4) & 1,
        ML=d0 & 15, KL=(d2 >> 6) & 3, TL=d2 & 63, FB=d3 & 7, WS=(d3 >> 3) & 1,
        AR=(d4 >> 4) & 15, DR=d4 & 15, SL=(d6 >> 4) & 15, RR=d6 & 15,
    )
    car = _Patch(
        AM=(d1 >> 7) & 1, PM=(d1 >> 6) & 1, EG=(d1 >> 5) & 1, KR=(d1 >> 4) & 1,
        ML=d1 & 15, KL=(d3 >> 6) & 3, TL=0, FB=0, WS=(d3 >> 4) & 1,
        AR=(d5 >> 4) & 15, DR=d5 & 15, SL=(d7 >> 4) & 15, RR=d7 & 15,
    )
    return mod, car


def _buddy_index(number: int, slot_type: int) -> int | None:
    """The paired mod/car slot index for a normal (non-rhythm) slot, or None
    for a rhythm single-slot-mode slot (type 3) which has no FM buddy.
    """
    if slot_type == 0:
        return number + 1
    if slot_type == 1:
        return number - 1
    return None


class _Slot:
    __slots__ = (
        "number", "type", "pg_keep", "wave_table", "ws", "pg_phase", "output",
        "eg_state", "eg_shift", "eg_rate_h", "eg_rate_l", "rks", "tll",
        "key_flag", "sus_flag", "blk_fnum", "blk", "fnum", "volume",
        "pg_out", "eg_out", "patch", "update_requests", "buddy_index",
    )

    def __init__(self, number: int) -> None:
        self.number = number
        self.type = number % 2
        self.buddy_index = _buddy_index(number, self.type)
        self.pg_keep = 0
        self.ws = 0  # which of _WAVE_TABLE_MAP wave_table currently points at
        self.wave_table = _WAVE_TABLE_MAP[0]
        self.pg_phase = 0
        self.output = [0, 0]
        self.eg_state = _RELEASE
        self.eg_shift = 0
        self.eg_rate_h = 0
        self.eg_rate_l = 0
        self.rks = 0
        self.tll = 0
        self.key_flag = 0
        self.sus_flag = 0
        self.blk_fnum = 0
        self.blk = 0
        self.fnum = 0
        self.volume = 0
        self.pg_out = 0
        self.eg_out = _EG_MUTE
        self.patch: _Patch = _null_patch()
        self.update_requests = 0


_UPDATE_WS = 1
_UPDATE_TLL = 2
_UPDATE_RKS = 4
_UPDATE_EG = 8
_UPDATE_ALL = 255


@dataclass
class Opll:
    """YM2413 (OPLL) chip instance (emu2413 v1.5.9 port; see module docstring).

    Public surface: write_addr / write_data / write_reg / read_reg / read for
    register access, generate_samples(n) for PCM, reset(), and snapshot()/
    restore() for save-state. All other members mirror the emu2413 internals.
    """

    _reg: bytearray = field(default_factory=lambda: bytearray(0x40), init=False, repr=False)
    _slot: list[_Slot] = field(
        default_factory=lambda: [_Slot(i) for i in range(18)], init=False, repr=False
    )
    # 19 instruments × (mod, car). Presets rebuilt at reset; user tone (index 0)
    # tracks registers 0x00-0x07.
    _patch: list[_Patch] = field(
        default_factory=lambda: [_null_patch() for _ in range(19 * 2)],
        init=False, repr=False,
    )
    _patch_number: list[int] = field(
        default_factory=lambda: [0] * 9, init=False, repr=False
    )
    _adr: int = field(default=0, init=False, repr=False)
    _pm_phase: int = field(default=0, init=False, repr=False)
    _am_phase: int = field(default=0, init=False, repr=False)
    _lfo_am: int = field(default=0, init=False, repr=False)
    _noise: int = field(default=1, init=False, repr=False)
    _short_noise: int = field(default=0, init=False, repr=False)
    _test_flag: int = field(default=0, init=False, repr=False)
    _rhythm_mode: int = field(default=0, init=False, repr=False)
    _slot_key_status: int = field(default=0, init=False, repr=False)
    _eg_counter: int = field(default=0, init=False, repr=False)
    _ch_out: list[int] = field(default_factory=lambda: [0] * 14, init=False, repr=False)
    _out_time: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------ public I/O

    def write_addr(self, value: int) -> None:
        """Latch a register address (register-select write)."""
        self._adr = value & 0xFF

    def write_data(self, value: int) -> None:
        """Write a data byte to the currently latched register address."""
        self._write_reg(self._adr, value & 0xFF)

    def write_reg(self, index: int, value: int) -> None:
        """Write value directly to register index (bypasses the latch)."""
        self._write_reg(index & 0xFF, value & 0xFF)

    def read_reg(self, index: int) -> int:
        """Return the stored value of register index (0 if out of range)."""
        index &= 0xFF
        if index < 0x40:
            return self._reg[index]
        return 0

    def read(self) -> int:
        """OPLL is write-only; any read returns 0xFF."""
        return 0xFF

    # ------------------------------------------------------------------- reset

    def reset(self) -> None:
        """Power-on reset (OPLL_reset): clear registers, slots, and LFO/noise."""
        self._adr = 0
        self._pm_phase = 0
        self._am_phase = 0
        self._lfo_am = 0
        self._noise = 1
        self._rhythm_mode = 0
        self._slot_key_status = 0
        self._eg_counter = 0
        self._short_noise = 0
        self._test_flag = 0
        self._out_time = 0.0
        for i in range(18):
            self._slot[i] = _Slot(i)
        # Build the default YM2413 patch bank (mod/car per instrument).
        for i in range(19):
            mod, car = _dump_to_patch(_DEFAULT_INST[i])
            self._patch[i * 2 + 0] = mod
            self._patch[i * 2 + 1] = car
        self._patch_number = [0] * 9
        for i in range(9):
            self._set_patch(i, 0)
        for i in range(14):
            self._ch_out[i] = 0
        for i in range(0x40):
            self._write_reg(i, 0)

    # ------------------------------------------------------- register updates

    def _mod(self, ch: int) -> _Slot:
        return self._slot[ch << 1]

    def _car(self, ch: int) -> _Slot:
        return self._slot[(ch << 1) | 1]

    def _request_update(self, slot: _Slot, flag: int) -> None:
        slot.update_requests |= flag

    def _notify_user_patch_slots(self, select_slot: Callable[[int], _Slot], flag: int) -> None:
        """Request an update for every channel currently voiced by the user
        patch (instrument 0) -- a user-patch register write (0x00-0x07) must
        propagate live to every slot pointing at that shared _Patch object.
        """
        for i in range(9):
            if self._patch_number[i] == 0:
                self._request_update(select_slot(i), flag)

    def _get_parameter_rate(self, slot: _Slot) -> int:  # emu2413: get_parameter_rate
        if (slot.type & 1) == 0 and slot.key_flag == 0:
            return 0
        st = slot.eg_state
        if st == _ATTACK:
            return slot.patch.AR
        if st == _DECAY:
            return slot.patch.DR
        if st == _SUSTAIN:
            return 0 if slot.patch.EG else slot.patch.RR
        if st == _RELEASE:
            if slot.sus_flag:
                return _SUS_RELEASE_RATE
            if slot.patch.EG:
                return slot.patch.RR
            return _PERC_RELEASE_RATE
        if st == _DAMP:
            return _DAMPER_RATE
        return 0

    def _commit_slot_update(self, slot: _Slot) -> None:
        req = slot.update_requests
        if req & _UPDATE_WS:
            slot.ws = slot.patch.WS
            slot.wave_table = _WAVE_TABLE_MAP[slot.ws]
        if req & _UPDATE_TLL:
            idx = slot.blk_fnum >> 5
            if (slot.type & 1) == 0:
                slot.tll = _TLL_TABLE[idx][slot.patch.TL][slot.patch.KL]
            else:
                slot.tll = _TLL_TABLE[idx][slot.volume][slot.patch.KL]
        if req & _UPDATE_RKS:
            slot.rks = _RKS_TABLE[slot.blk_fnum >> 8][slot.patch.KR]
        if req & (_UPDATE_RKS | _UPDATE_EG):
            p_rate = self._get_parameter_rate(slot)
            if p_rate == 0:
                slot.eg_shift = 0
                slot.eg_rate_h = 0
                slot.eg_rate_l = 0
                slot.update_requests = 0
                return
            slot.eg_rate_h = min(15, p_rate + (slot.rks >> 2))
            slot.eg_rate_l = slot.rks & 3
            if slot.eg_state == _ATTACK:
                slot.eg_shift = (13 - slot.eg_rate_h) if 0 < slot.eg_rate_h < 12 else 0
            else:
                slot.eg_shift = (13 - slot.eg_rate_h) if slot.eg_rate_h < 13 else 0
        slot.update_requests = 0

    def _slot_on(self, i: int) -> None:
        slot = self._slot[i]
        slot.key_flag = 1
        slot.eg_state = _DAMP
        self._request_update(slot, _UPDATE_EG)

    def _slot_off(self, i: int) -> None:
        slot = self._slot[i]
        slot.key_flag = 0
        if slot.type & 1:
            slot.eg_state = _RELEASE
            self._request_update(slot, _UPDATE_EG)

    def _compute_key_status(self) -> int:
        """Decode registers 0x20-0x28's key-on bits (and, in rhythm mode,
        register 0x0E's five rhythm key bits) into an 18-bit mask, one bit
        per slot, of which operators should currently be keyed on.
        """
        r14 = self._reg[0x0E]
        rhythm = (r14 >> 5) & 1
        new_status = 0
        for ch in range(9):
            if self._reg[0x20 + ch] & 0x10:
                new_status |= 3 << (ch * 2)
        if rhythm:
            if r14 & 0x10:
                new_status |= 3 << _SLOT_BD1
            if r14 & 0x01:
                new_status |= 1 << _SLOT_HH
            if r14 & 0x08:
                new_status |= 1 << _SLOT_SD
            if r14 & 0x04:
                new_status |= 1 << _SLOT_TOM
            if r14 & 0x02:
                new_status |= 1 << _SLOT_CYM
        return new_status

    def _update_key_status(self) -> None:
        new_status = self._compute_key_status()
        updated = self._slot_key_status ^ new_status
        if updated:
            for i in range(18):
                if (updated >> i) & 1:
                    if (new_status >> i) & 1:
                        self._slot_on(i)
                    else:
                        self._slot_off(i)
        self._slot_key_status = new_status

    def _set_patch(self, ch: int, num: int) -> None:
        self._patch_number[ch] = num
        self._mod(ch).patch = self._patch[num * 2 + 0]
        self._car(ch).patch = self._patch[num * 2 + 1]
        self._request_update(self._mod(ch), _UPDATE_ALL)
        self._request_update(self._car(ch), _UPDATE_ALL)

    def _set_sus_flag(self, ch: int, flag: int) -> None:
        car = self._car(ch)
        car.sus_flag = flag
        self._request_update(car, _UPDATE_EG)
        mod = self._mod(ch)
        if mod.type & 1:
            mod.sus_flag = flag
            self._request_update(mod, _UPDATE_EG)

    def _set_volume(self, ch: int, volume: int) -> None:
        car = self._car(ch)
        car.volume = volume
        self._request_update(car, _UPDATE_TLL)

    def _set_slot_volume(self, slot: _Slot, volume: int) -> None:
        slot.volume = volume
        self._request_update(slot, _UPDATE_TLL)

    def _set_fnumber(self, ch: int, fnum: int) -> None:
        car = self._car(ch)
        mod = self._mod(ch)
        car.fnum = fnum
        car.blk_fnum = (car.blk_fnum & 0xE00) | (fnum & 0x1FF)
        mod.fnum = fnum
        mod.blk_fnum = (mod.blk_fnum & 0xE00) | (fnum & 0x1FF)
        self._request_update(car, _UPDATE_EG | _UPDATE_RKS | _UPDATE_TLL)
        self._request_update(mod, _UPDATE_EG | _UPDATE_RKS | _UPDATE_TLL)

    def _set_block(self, ch: int, blk: int) -> None:
        car = self._car(ch)
        mod = self._mod(ch)
        car.blk = blk
        car.blk_fnum = ((blk & 7) << 9) | (car.blk_fnum & 0x1FF)
        mod.blk = blk
        mod.blk_fnum = ((blk & 7) << 9) | (mod.blk_fnum & 0x1FF)
        self._request_update(car, _UPDATE_EG | _UPDATE_RKS | _UPDATE_TLL)
        self._request_update(mod, _UPDATE_EG | _UPDATE_RKS | _UPDATE_TLL)

    def _update_rhythm_mode(self) -> None:
        new_mode = (self._reg[0x0E] >> 5) & 1
        if self._rhythm_mode != new_mode:
            if new_mode:
                self._slot[_SLOT_HH].type = 3
                self._slot[_SLOT_HH].buddy_index = None
                self._slot[_SLOT_HH].pg_keep = 1
                self._slot[_SLOT_SD].type = 3
                self._slot[_SLOT_SD].buddy_index = None
                self._slot[_SLOT_TOM].type = 3
                self._slot[_SLOT_TOM].buddy_index = None
                self._slot[_SLOT_CYM].type = 3
                self._slot[_SLOT_CYM].buddy_index = None
                self._slot[_SLOT_CYM].pg_keep = 1
                self._set_patch(6, 16)
                self._set_patch(7, 17)
                self._set_patch(8, 18)
                self._set_slot_volume(self._slot[_SLOT_HH], ((self._reg[0x37] >> 4) & 15) << 2)
                self._set_slot_volume(self._slot[_SLOT_TOM], ((self._reg[0x38] >> 4) & 15) << 2)
            else:
                self._slot[_SLOT_HH].type = 0
                self._slot[_SLOT_HH].buddy_index = _buddy_index(_SLOT_HH, 0)
                self._slot[_SLOT_HH].pg_keep = 0
                self._slot[_SLOT_SD].type = 1
                self._slot[_SLOT_SD].buddy_index = _buddy_index(_SLOT_SD, 1)
                self._slot[_SLOT_TOM].type = 0
                self._slot[_SLOT_TOM].buddy_index = _buddy_index(_SLOT_TOM, 0)
                self._slot[_SLOT_CYM].type = 1
                self._slot[_SLOT_CYM].buddy_index = _buddy_index(_SLOT_CYM, 1)
                self._slot[_SLOT_CYM].pg_keep = 0
                self._set_patch(6, self._reg[0x36] >> 4)
                self._set_patch(7, self._reg[0x37] >> 4)
                self._set_patch(8, self._reg[0x38] >> 4)
        self._rhythm_mode = new_mode

    def _write_reg(self, reg: int, data: int) -> None:  # emu2413: OPLL_writeReg
        if reg >= 0x40:
            return
        # Mirror registers (0x19-0x1f, 0x29-0x2f, 0x39-0x3f) map down by 9.
        if (0x19 <= reg <= 0x1F) or (0x29 <= reg <= 0x2F) or (0x39 <= reg <= 0x3F):
            reg -= 9
        self._reg[reg] = data

        if reg == 0x00 or reg == 0x01:
            p = self._patch[reg]  # patch[0]=mod user, patch[1]=car user
            p.AM = (data >> 7) & 1
            p.PM = (data >> 6) & 1
            p.EG = (data >> 5) & 1
            p.KR = (data >> 4) & 1
            p.ML = data & 15
            select = self._mod if reg == 0x00 else self._car
            self._notify_user_patch_slots(select, _UPDATE_RKS | _UPDATE_EG)
        elif reg == 0x02:
            p = self._patch[0]
            p.KL = (data >> 6) & 3
            p.TL = data & 63
            self._notify_user_patch_slots(self._mod, _UPDATE_TLL)
        elif reg == 0x03:
            self._patch[1].KL = (data >> 6) & 3
            self._patch[1].WS = (data >> 4) & 1
            self._patch[0].WS = (data >> 3) & 1
            self._patch[0].FB = data & 7
            self._notify_user_patch_slots(self._mod, _UPDATE_WS)
            self._notify_user_patch_slots(self._car, _UPDATE_WS | _UPDATE_TLL)
        elif reg == 0x04 or reg == 0x05:
            p = self._patch[0] if reg == 0x04 else self._patch[1]
            p.AR = (data >> 4) & 15
            p.DR = data & 15
            self._notify_user_patch_slots(self._mod if reg == 0x04 else self._car, _UPDATE_EG)
        elif reg == 0x06 or reg == 0x07:
            p = self._patch[0] if reg == 0x06 else self._patch[1]
            p.SL = (data >> 4) & 15
            p.RR = data & 15
            self._notify_user_patch_slots(self._mod if reg == 0x06 else self._car, _UPDATE_EG)
        elif reg == 0x0E:
            self._update_rhythm_mode()
            self._update_key_status()
        elif reg == 0x0F:
            self._test_flag = data
        elif 0x10 <= reg <= 0x18:
            ch = reg - 0x10
            self._set_fnumber(ch, data + ((self._reg[0x20 + ch] & 1) << 8))
        elif 0x20 <= reg <= 0x28:
            ch = reg - 0x20
            self._set_fnumber(ch, ((data & 1) << 8) + self._reg[0x10 + ch])
            self._set_block(ch, (data >> 1) & 7)
            self._set_sus_flag(ch, (data >> 5) & 1)
            self._update_key_status()
        elif 0x30 <= reg <= 0x38:
            if (self._reg[0x0E] & 32) and reg >= 0x36:
                if reg == 0x37:
                    self._set_slot_volume(self._mod(7), ((data >> 4) & 15) << 2)
                elif reg == 0x38:
                    self._set_slot_volume(self._mod(8), ((data >> 4) & 15) << 2)
            else:
                self._set_patch(reg - 0x30, (data >> 4) & 15)
            self._set_volume(reg - 0x30, (data & 15) << 2)

    # ------------------------------------------------------- synthesis engine

    def _update_ampm(self) -> None:
        if self._test_flag & 2:
            self._pm_phase = 0
            self._am_phase = 0
        else:
            self._pm_phase = (self._pm_phase + (1024 if self._test_flag & 8 else 1)) & 0xFFFFFFFF
            # Mask to uint32 like emu2413's am_phase; 210 (len(_AM_TABLE)) does not
            # divide 2^32, so an unbounded accumulator would pick a different table
            # entry than hardware at the wrap boundary.
            self._am_phase = (self._am_phase + (64 if self._test_flag & 8 else 1)) & 0xFFFFFFFF
        self._lfo_am = _AM_TABLE[(self._am_phase >> 6) % _AM_TABLE_LEN]

    def _update_noise(self, cycle: int) -> None:
        noise = self._noise
        for _ in range(cycle):
            if noise & 1:
                noise ^= 0x800200
            noise >>= 1
        self._noise = noise

    def _update_short_noise(self) -> None:
        pg_hh = self._slot[_SLOT_HH].pg_out
        pg_cym = self._slot[_SLOT_CYM].pg_out
        h_bit2 = (pg_hh >> (_PG_BITS - 8)) & 1
        h_bit7 = (pg_hh >> (_PG_BITS - 3)) & 1
        h_bit3 = (pg_hh >> (_PG_BITS - 7)) & 1
        c_bit3 = (pg_cym >> (_PG_BITS - 7)) & 1
        c_bit5 = (pg_cym >> (_PG_BITS - 5)) & 1
        self._short_noise = (h_bit2 ^ h_bit7) | (h_bit3 ^ c_bit5) | (c_bit3 ^ c_bit5)

    def _calc_phase(self, slot: _Slot, reset: int) -> None:
        if slot.patch.PM:
            pm = _PM_TABLE[(slot.fnum >> 6) & 7][(self._pm_phase >> 10) & 7]
        else:
            pm = 0
        if reset:
            slot.pg_phase = 0
        slot.pg_phase = (
            slot.pg_phase
            + ((((slot.fnum & 0x1FF) * 2 + pm) * _ML_TABLE[slot.patch.ML]) << slot.blk >> 2)
        ) & (_DP_WIDTH - 1)
        slot.pg_out = slot.pg_phase >> _DP_BASE_BITS

    def _lookup_attack_step(self, slot: _Slot, counter: int) -> int:
        # rh (eg_rate_h) >= 12 clamps to the chip's fixed near-instant attack
        # timings rather than the normal per-tick step table; rh 0/15 never
        # attack at all. Mirrors emu2413's calc_envelope ATTACK switch.
        rh = slot.eg_rate_h
        if rh == 12:
            index = (counter & 0xC) >> 1
            return 4 - _EG_STEP_TABLES[slot.eg_rate_l][index]
        if rh == 13:
            index = (counter & 0xC) >> 1
            return 3 - _EG_STEP_TABLES[slot.eg_rate_l][index]
        if rh == 14:
            index = (counter & 0xC) >> 1
            return 2 - _EG_STEP_TABLES[slot.eg_rate_l][index]
        if rh == 0 or rh == 15:
            return 0
        index = counter >> slot.eg_shift
        return 4 if _EG_STEP_TABLES[slot.eg_rate_l][index & 7] else 0

    def _lookup_decay_step(self, slot: _Slot, counter: int) -> int:
        # rh >= 13 clamps to the chip's fixed fast-decay timings rather than
        # the normal per-tick step table; rh 0 never decays. Mirrors
        # emu2413's calc_envelope DECAY/SUSTAIN/RELEASE switch.
        rh = slot.eg_rate_h
        if rh == 0:
            return 0
        if rh == 13:
            index = ((counter & 0xC) >> 1) | (counter & 1)
            return _EG_STEP_TABLES[slot.eg_rate_l][index]
        if rh == 14:
            index = (counter & 0xC) >> 1
            return _EG_STEP_TABLES[slot.eg_rate_l][index] + 1
        if rh == 15:
            return 2
        index = counter >> slot.eg_shift
        return _EG_STEP_TABLES[slot.eg_rate_l][index & 7]

    def _start_envelope(self, slot: _Slot) -> None:
        if min(15, slot.patch.AR + (slot.rks >> 2)) == 15:
            slot.eg_state = _DECAY
            slot.eg_out = 0
        else:
            slot.eg_state = _ATTACK
        self._request_update(slot, _UPDATE_EG)

    def _calc_envelope(  # emu2413: calc_envelope
        self, slot: _Slot, buddy: _Slot | None, eg_counter: int, test: int
    ) -> None:
        mask = self._step_envelope_rate(slot, eg_counter)
        self._transition_envelope_state(slot, buddy, eg_counter, mask)
        if test:
            slot.eg_out = 0

    def _step_envelope_rate(self, slot: _Slot, eg_counter: int) -> int:
        """Advance eg_out by one attack/decay step if this tick is due for
        one (per the slot's own rate). Returns the rate mask (shared with
        _transition_envelope_state's DAMP-completion check below), since
        both are gated by the same eg_shift-derived tick cadence.
        """
        mask = (1 << slot.eg_shift) - 1
        if slot.eg_state == _ATTACK:
            if 0 < slot.eg_out and 0 < slot.eg_rate_h and (eg_counter & mask & ~3) == 0:
                s = self._lookup_attack_step(slot, eg_counter)
                if s > 0:
                    slot.eg_out = max(0, slot.eg_out - (slot.eg_out >> s) - 1)
        else:
            if slot.eg_rate_h > 0 and (eg_counter & mask) == 0:
                step = slot.eg_out + self._lookup_decay_step(slot, eg_counter)
                slot.eg_out = _EG_MUTE if step > _EG_MUTE else step  # inlined min (hot path)
        return mask

    def _transition_envelope_state(
        self, slot: _Slot, buddy: _Slot | None, eg_counter: int, mask: int
    ) -> None:
        """Advance the Damp/Attack/Decay/Sustain/Release state machine based
        on the eg_out level _step_envelope_rate just produced.
        """
        st = slot.eg_state
        if st == _DAMP:
            if slot.eg_out >= _EG_MAX and (eg_counter & mask) == 0:
                self._start_envelope(slot)
                if slot.type & 1:
                    if not slot.pg_keep:
                        slot.pg_phase = 0
                    if buddy is not None and not buddy.pg_keep:
                        buddy.pg_phase = 0
        elif st == _ATTACK:
            if slot.eg_out == 0:
                slot.eg_state = _DECAY
                self._request_update(slot, _UPDATE_EG)
        elif st == _DECAY:
            if (slot.eg_out >> 3) == slot.patch.SL:
                slot.eg_state = _SUSTAIN
                self._request_update(slot, _UPDATE_EG)

    def _update_slots(self) -> None:
        self._eg_counter += 1
        eg_counter = self._eg_counter
        test = self._test_flag
        slots = self._slot
        for i in range(18):
            slot = slots[i]
            buddy: _Slot | None = slots[slot.buddy_index] if slot.buddy_index is not None else None
            if slot.update_requests:
                self._commit_slot_update(slot)
            self._calc_envelope(slot, buddy, eg_counter, test & 1)
            self._calc_phase(slot, test & 4)

    def _to_linear(self, h: int, slot: _Slot, am: int) -> int:
        # emu2413: to_linear + lookup_exp_table, folded together — this runs once
        # per operator per chip tick (the hottest site), so min() and the exp
        # lookup are inlined rather than kept as separate calls. h = the
        # log-sin wave-table value at the current phase; i = the combined
        # log-domain exponent index (h plus envelope/tll/am attenuation,
        # scaled) fed into the exp table lookup below.
        if slot.eg_out > _EG_MAX:
            return 0
        s = slot.eg_out + slot.tll + am
        i = h + ((_EG_MUTE if s > _EG_MUTE else s) << 4)
        t = _EXP_TABLE[(i & 0xFF) ^ 0xFF] + 1024
        res = t >> ((i & 0x7F00) >> 8)
        if i & 0x8000:
            # ~res on the int16 intermediate, matching emu2413's int16_t arithmetic
            # (exp-table max ~2042 keeps res << 1 within int16 range).
            res = ~res
        return res << 1

    def _calc_slot_car(self, slot: _Slot, fm: int) -> int:
        am = self._lfo_am if slot.patch.AM else 0
        slot.output[1] = slot.output[0]
        idx = (slot.pg_out + 2 * (fm >> 1)) & (_PG_WIDTH - 1)
        slot.output[0] = self._to_linear(slot.wave_table[idx], slot, am)
        return slot.output[0]

    def _calc_slot_mod(self, slot: _Slot) -> int:
        if slot.patch.FB > 0:
            fm = (slot.output[1] + slot.output[0]) >> (9 - slot.patch.FB)
        else:
            fm = 0
        am = self._lfo_am if slot.patch.AM else 0
        slot.output[1] = slot.output[0]
        idx = (slot.pg_out + fm) & (_PG_WIDTH - 1)
        slot.output[0] = self._to_linear(slot.wave_table[idx], slot, am)
        return slot.output[0]

    def _calc_slot_tom(self) -> int:
        # Tom-tom behaves like an ordinary FM operator (its own phase, no
        # noise involvement) -- emu2413: calc_slot_tom. Direct slot index,
        # matching _update_output's own hot-path inlining convention.
        slot = self._slot[_SLOT_TOM]
        return self._to_linear(slot.wave_table[slot.pg_out], slot, 0)

    def _calc_slot_snare(self) -> int:
        # Fixed rhythm-mode phase taps (emu2413: calc_slot_snare) -- 0x0/
        # 0x100/0x200/0x300 select between the sine table's four quadrants,
        # chosen by the slot's own phase bit and the shared noise bit; not
        # independently meaningful constants beyond that table index.
        slot = self._slot[_SLOT_SD]
        if (slot.pg_out >> (_PG_BITS - 2)) & 1:
            phase = 0x300 if (self._noise & 1) else 0x200
        else:
            phase = 0x0 if (self._noise & 1) else 0x100
        return self._to_linear(slot.wave_table[phase], slot, 0)

    def _calc_slot_cym(self) -> int:
        # Fixed rhythm-mode phase taps (emu2413: calc_slot_cym), chosen by
        # the shared short-noise signal -- see _update_short_noise.
        slot = self._slot[_SLOT_CYM]
        phase = 0x300 if self._short_noise else 0x100
        return self._to_linear(slot.wave_table[phase], slot, 0)

    def _calc_slot_hat(self) -> int:
        # Fixed rhythm-mode phase taps (emu2413: calc_slot_hat), chosen by
        # the shared short-noise signal and the shared noise bit -- see
        # _update_short_noise.
        slot = self._slot[_SLOT_HH]
        if self._short_noise:
            phase = 0x2D0 if (self._noise & 1) else 0x234
        else:
            phase = 0x34 if (self._noise & 1) else 0xD0
        return self._to_linear(slot.wave_table[phase], slot, 0)

    def _update_output(self) -> None:
        self._update_ampm()
        self._update_short_noise()
        self._update_slots()
        out = self._ch_out
        slots = self._slot  # resolve mod/car by index inline (hot path)

        for i in range(6):
            mod = slots[i << 1]
            car = slots[(i << 1) | 1]
            out[i] = -(self._calc_slot_car(car, self._calc_slot_mod(mod))) >> 1

        mod6, car6 = slots[12], slots[13]
        if not self._rhythm_mode:
            out[6] = -(self._calc_slot_car(car6, self._calc_slot_mod(mod6))) >> 1
        else:
            out[9] = self._calc_slot_car(car6, self._calc_slot_mod(mod6))
        self._update_noise(14)

        if not self._rhythm_mode:
            mod7, car7 = slots[14], slots[15]
            out[7] = -(self._calc_slot_car(car7, self._calc_slot_mod(mod7))) >> 1
        else:
            out[10] = self._calc_slot_hat()
            out[11] = self._calc_slot_snare()
        self._update_noise(2)

        if not self._rhythm_mode:
            mod8, car8 = slots[16], slots[17]
            out[8] = -(self._calc_slot_car(car8, self._calc_slot_mod(mod8))) >> 1
        else:
            out[12] = self._calc_slot_tom()
            out[13] = self._calc_slot_cym()
        self._update_noise(2)

    def _mix(self) -> int:
        return sum(self._ch_out)

    # ------------------------------------------------------- sample generation

    def generate_samples(self, n: int) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples at SAMPLE_RATE.

        Runs the chip at its native clk/72 rate and decimates to the output
        rate with an accumulate-and-average step (emu2413's OPLL_calc timing,
        minus the windowed-sinc resampler). Gain is fixed so a mix of a few
        loud channels sits comfortably below full scale.
        """
        out = bytearray(n * 2)
        out_step = _CLOCK / 72.0
        inp_step = float(SAMPLE_RATE)
        out_time = self._out_time
        for i in range(n):
            acc = 0
            cnt = 0
            while out_step > out_time:
                out_time += inp_step
                self._update_output()
                acc += self._mix()
                cnt += 1
            out_time -= out_step
            raw = (acc // cnt) if cnt else 0
            scaled = raw * _OUTPUT_GAIN
            if scaled > 32767:
                scaled = 32767
            elif scaled < -32768:
                scaled = -32768
            out[i * 2] = scaled & 0xFF
            out[i * 2 + 1] = (scaled >> 8) & 0xFF
        self._out_time = out_time
        return out

    # --------------------------------------------------------- save / restore

    def snapshot(self) -> dict[str, object]:
        """Capture full chip state for save-state (paired with restore)."""
        return {
            "reg": bytes(self._reg),
            "patch_number": list(self._patch_number),
            "user_patch": [_patch_fields(self._patch[0]), _patch_fields(self._patch[1])],
            "slots": [_slot_fields(s) for s in self._slot],
            "adr": self._adr,
            "pm_phase": self._pm_phase,
            "am_phase": self._am_phase,
            "lfo_am": self._lfo_am,
            "noise": self._noise,
            "short_noise": self._short_noise,
            "test_flag": self._test_flag,
            "rhythm_mode": self._rhythm_mode,
            "slot_key_status": self._slot_key_status,
            "eg_counter": self._eg_counter,
            "ch_out": list(self._ch_out),
            "out_time": self._out_time,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Restore chip state produced by snapshot()."""
        self._reg = bytearray(state["reg"])
        self._patch_number = [int(x) for x in state["patch_number"]]
        # Rebuild the fixed preset bank, then the user tone from its saved fields.
        for i in range(19):
            mod, car = _dump_to_patch(_DEFAULT_INST[i])
            self._patch[i * 2 + 0] = mod
            self._patch[i * 2 + 1] = car
        _set_patch_fields(self._patch[0], state["user_patch"][0])
        _set_patch_fields(self._patch[1], state["user_patch"][1])
        for ch in range(9):
            self._mod(ch).patch = self._patch[self._patch_number[ch] * 2 + 0]
            self._car(ch).patch = self._patch[self._patch_number[ch] * 2 + 1]
        for i, sf in enumerate(state["slots"]):
            _restore_slot_fields(self._slot[i], sf)
        self._adr = int(state["adr"])
        self._pm_phase = int(state["pm_phase"])
        self._am_phase = int(state["am_phase"])
        self._lfo_am = int(state["lfo_am"])
        self._noise = int(state["noise"])
        self._short_noise = int(state["short_noise"])
        self._test_flag = int(state["test_flag"])
        self._rhythm_mode = int(state["rhythm_mode"])
        self._slot_key_status = int(state["slot_key_status"])
        self._eg_counter = int(state["eg_counter"])
        self._ch_out = [int(x) for x in state["ch_out"]]
        self._out_time = float(state["out_time"])


# Output gain from the chip's raw mix (each channel roughly ±4000 pre-mix,
# melody halved) to signed 16-bit. Tuned so a loud chord peaks well under
# full scale while a single note is clearly audible.
_OUTPUT_GAIN = 3

# Save-state helpers emit/consume each field explicitly (rather than by
# getattr/setattr reflection), so the snapshot dict maps 1:1 onto the emu2413
# OPLL_PATCH / OPLL_SLOT structs and ports directly to a serde/JSON struct.


def _patch_fields(p: _Patch) -> dict[str, int]:
    return {
        "AM": p.AM, "PM": p.PM, "EG": p.EG, "KR": p.KR, "ML": p.ML, "KL": p.KL,
        "TL": p.TL, "FB": p.FB, "WS": p.WS, "AR": p.AR, "DR": p.DR, "SL": p.SL, "RR": p.RR,
    }


def _set_patch_fields(p: _Patch, d: dict[str, Any]) -> None:
    p.AM = int(d["AM"])
    p.PM = int(d["PM"])
    p.EG = int(d["EG"])
    p.KR = int(d["KR"])
    p.ML = int(d["ML"])
    p.KL = int(d["KL"])
    p.TL = int(d["TL"])
    p.FB = int(d["FB"])
    p.WS = int(d["WS"])
    p.AR = int(d["AR"])
    p.DR = int(d["DR"])
    p.SL = int(d["SL"])
    p.RR = int(d["RR"])


def _slot_fields(s: _Slot) -> dict[str, Any]:
    return {
        "type": s.type, "pg_keep": s.pg_keep, "pg_phase": s.pg_phase,
        "eg_state": s.eg_state, "eg_shift": s.eg_shift, "eg_rate_h": s.eg_rate_h,
        "eg_rate_l": s.eg_rate_l, "rks": s.rks, "tll": s.tll, "key_flag": s.key_flag,
        "sus_flag": s.sus_flag, "blk_fnum": s.blk_fnum, "blk": s.blk, "fnum": s.fnum,
        "volume": s.volume, "pg_out": s.pg_out, "eg_out": s.eg_out,
        "update_requests": s.update_requests,
        "output": list(s.output),
        "ws": s.ws,
    }


def _restore_slot_fields(s: _Slot, d: dict[str, Any]) -> None:
    s.type = int(d["type"])
    s.buddy_index = _buddy_index(s.number, s.type)
    s.pg_keep = int(d["pg_keep"])
    s.pg_phase = int(d["pg_phase"])
    s.eg_state = int(d["eg_state"])
    s.eg_shift = int(d["eg_shift"])
    s.eg_rate_h = int(d["eg_rate_h"])
    s.eg_rate_l = int(d["eg_rate_l"])
    s.rks = int(d["rks"])
    s.tll = int(d["tll"])
    s.key_flag = int(d["key_flag"])
    s.sus_flag = int(d["sus_flag"])
    s.blk_fnum = int(d["blk_fnum"])
    s.blk = int(d["blk"])
    s.fnum = int(d["fnum"])
    s.volume = int(d["volume"])
    s.pg_out = int(d["pg_out"])
    s.eg_out = int(d["eg_out"])
    s.update_requests = int(d["update_requests"])
    s.output = [int(x) for x in d["output"]]
    s.ws = int(d["ws"])
    s.wave_table = _WAVE_TABLE_MAP[s.ws]
