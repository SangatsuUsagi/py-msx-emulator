"""YM2413 (OPLL) FM sound chip: register file + 9-channel 2-operator melody
synthesis.

Simplified relative to real hardware (see openspec/changes/add-fmpac-msx-music
design.md, Non-Goals): linear-domain envelope ramps instead of the chip's
logarithmic curves, no AM/vibrato LFO, no key-scale rate/level. Register
semantics (instrument ROM, F-number/block, ADSR rates, feedback) and note
pitch follow the real chip; timbre is audibly-correct, not sample-exact.
Ground truth for the register map and instrument ROM data: openMSX
YM2413Okazaki (itself derived from Mitsutaka Okazaki's emu2413).

Rhythm mode (register 0x0E) is not implemented yet (Phase 3): channels 6-8
always behave as ordinary melody channels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from msx.psg import SAMPLE_RATE, SAMPLES_PER_FRAME

__all__ = ["Opll", "SAMPLE_RATE", "SAMPLES_PER_FRAME"]

_CLOCK = 3_579_545  # Hz — full MSX CPU clock

# Phase table (sine lookup), TABLE_SIZE must be a power of two.
_TABLE_BITS = 10
TABLE_SIZE = 1 << _TABLE_BITS
_TABLE_MASK = TABLE_SIZE - 1
SIN_TABLE: tuple[float, ...] = tuple(
    math.sin(2.0 * math.pi * i / TABLE_SIZE) for i in range(TABLE_SIZE)
)

# Frequency multiplier per 4-bit "multiple" register value (standard OPL/OPLL
# ML table; value 0 means x0.5).
_MULTI_TABLE: tuple[float, ...] = (0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 12, 12, 15, 15)

# Self-feedback phase-offset scale per 3-bit FB register value (0 = none,
# doubling per step).
_FB_SCALE: tuple[float, ...] = (0.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0)

# How much a modulator's (envelope+TL-scaled) output shifts the carrier's
# phase index, in table units. Tuned for audible FM richness at low TL.
_MOD_DEPTH = TABLE_SIZE / 2.0

# Envelope segment durations (seconds for a full 0<->1 sweep at rate=1),
# halving per rate step; rate 0 is a very slow near-static approximation of
# the real chip's "never completes" behaviour.
_ATTACK_BASE_SEC = 2.5
_DECAY_BASE_SEC = 5.0


def _build_step_table(base_sec: float) -> tuple[float, ...]:
    steps = [1.0 / (SAMPLE_RATE * base_sec * 16)]  # rate 0
    for rate in range(1, 16):
        steps.append(1.0 / max(1.0, SAMPLE_RATE * base_sec / (2**rate)))
    return tuple(steps)


_ATTACK_STEP = _build_step_table(_ATTACK_BASE_SEC)
_DECAY_STEP = _build_step_table(_DECAY_BASE_SEC)

# Rhythm-mode percussion voices (HH/SD/TOM/TC) are simplified to a fixed
# envelope per voice rather than a user-configurable ADSR (real hardware also
# does not expose separate rates for these). Fast attack for all; decay speed
# differs per voice for a recognisably distinct feel.
_PERC_ATTACK_STEP = _ATTACK_STEP[15]
_PERC_DECAY_HH = _DECAY_STEP[13]
_PERC_DECAY_SD = _DECAY_STEP[9]
_PERC_DECAY_TOM = _DECAY_STEP[9]
_PERC_DECAY_TC = _DECAY_STEP[5]

# Envelope generator states.
_EG_OFF = 0
_EG_ATTACK = 1
_EG_DECAY = 2
_EG_SUSTAIN = 3
_EG_RELEASE = 4


@dataclass(frozen=True)
class _Patch:
    """Decoded 2-operator instrument parameters (register-byte layout below)."""

    mod_multi: float
    mod_tl: int      # 0-63, modulator total level (attenuation)
    fb: int           # 0-7, modulator self-feedback
    mod_ar: int
    mod_dr: int
    mod_sl: int
    mod_rr: int
    car_multi: float
    car_eg_type: int  # 1 = hold at sustain level while key on, 0 = keep decaying
    car_ar: int
    car_dr: int
    car_sl: int
    car_rr: int


def _decode_patch(data: bytes) -> _Patch:
    """Decode 8 instrument-ROM/user-tone bytes (registers 0x00-0x07 layout).

    byte0: mod EG-type/KR (unused here) + multi (bits0-3)
    byte1: car multi (bits0-3) + car EG-type (bit5)
    byte2: mod KSL (unused, bits6-7) + mod TL (bits0-5)
    byte3: car WF (bit4, unused) + mod WF (bit3, unused) + FB (bits0-2)
    byte4: mod AR (bits4-7) + mod DR (bits0-3)
    byte5: car AR (bits4-7) + car DR (bits0-3)
    byte6: mod SL (bits4-7) + mod RR (bits0-3)
    byte7: car SL (bits4-7) + car RR (bits0-3)
    """
    return _Patch(
        mod_multi=_MULTI_TABLE[data[0] & 0x0F],
        mod_tl=data[2] & 0x3F,
        fb=data[3] & 0x07,
        mod_ar=(data[4] >> 4) & 0x0F,
        mod_dr=data[4] & 0x0F,
        mod_sl=(data[6] >> 4) & 0x0F,
        mod_rr=data[6] & 0x0F,
        car_multi=_MULTI_TABLE[data[1] & 0x0F],
        car_eg_type=(data[1] >> 5) & 1,
        car_ar=(data[5] >> 4) & 0x0F,
        car_dr=data[5] & 0x0F,
        car_sl=(data[7] >> 4) & 0x0F,
        car_rr=data[7] & 0x0F,
    )


# Canonical YM2413 instrument ROM (registers 0x00-0x07 byte layout), indices
# 1-15: violin, guitar, piano, flute, clarinet, oboe, trumpet, organ, horn,
# synthesizer, harpsichord, vibraphone, synth bass, acoustic bass, electric
# guitar. Index 0 (user tone) is decoded live from registers 0x00-0x07.
_INST_ROM: tuple[bytes, ...] = (
    bytes((0x61, 0x61, 0x1E, 0x17, 0xF0, 0x7F, 0x00, 0x17)),
    bytes((0x13, 0x41, 0x16, 0x0E, 0xFD, 0xF4, 0x23, 0x23)),
    bytes((0x03, 0x01, 0x9A, 0x04, 0xF3, 0xF3, 0x13, 0xF3)),
    bytes((0x11, 0x61, 0x0E, 0x07, 0xFA, 0x64, 0x70, 0x17)),
    bytes((0x22, 0x21, 0x1E, 0x06, 0xF0, 0x76, 0x00, 0x28)),
    bytes((0x21, 0x22, 0x16, 0x05, 0xF0, 0x71, 0x00, 0x18)),
    bytes((0x21, 0x61, 0x1D, 0x07, 0x82, 0x80, 0x17, 0x17)),
    bytes((0x23, 0x21, 0x2D, 0x16, 0x90, 0x90, 0x00, 0x07)),
    bytes((0x21, 0x21, 0x1B, 0x06, 0x64, 0x65, 0x10, 0x17)),
    bytes((0x21, 0x21, 0x0B, 0x1A, 0x85, 0xA0, 0x70, 0x07)),
    bytes((0x23, 0x01, 0x83, 0x10, 0xFF, 0xB4, 0x10, 0xF4)),
    bytes((0x97, 0xC1, 0x20, 0x07, 0xFF, 0xF4, 0x22, 0x22)),
    bytes((0x61, 0x00, 0x0C, 0x05, 0xC2, 0xF6, 0x40, 0x44)),
    bytes((0x01, 0x01, 0x56, 0x03, 0x94, 0xC2, 0x03, 0x12)),
    bytes((0x21, 0x01, 0x89, 0x03, 0xF1, 0xE4, 0xF0, 0x23)),
)
_PRESETS: tuple[_Patch, ...] = tuple(_decode_patch(data) for data in _INST_ROM)


@dataclass
class Opll:
    # Register file: index 0x00-0x38 covers user-tone, rhythm/test, and the
    # per-channel F-number/block/key-on/sustain/instrument/volume registers.
    _regs: bytearray = field(default_factory=lambda: bytearray(0x40), init=False, repr=False)
    _addr_latch: int = field(default=0, init=False, repr=False)

    # Synthesis state, one entry per channel (0-8).
    _mod_phase: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _car_phase: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _mod_level: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _car_level: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _mod_state: list[int] = field(default_factory=lambda: [_EG_OFF] * 9, init=False, repr=False)
    _car_state: list[int] = field(default_factory=lambda: [_EG_OFF] * 9, init=False, repr=False)
    _mod_fb1: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _mod_fb2: list[float] = field(default_factory=lambda: [0.0] * 9, init=False, repr=False)
    _prev_kon: list[bool] = field(default_factory=lambda: [False] * 9, init=False, repr=False)

    # Rhythm mode (register 0x0E): HH/SD reuse channel 7's mod/car envelope
    # slots, TOM/TC reuse channel 8's (channels 7/8's normal melody use is
    # suppressed while rhythm is enabled, so this is conflict-free). Only the
    # edge-detect flags and the shared noise LFSR need dedicated fields.
    _prev_hh: bool = field(default=False, init=False, repr=False)
    _prev_sd: bool = field(default=False, init=False, repr=False)
    _prev_tom: bool = field(default=False, init=False, repr=False)
    _prev_tc: bool = field(default=False, init=False, repr=False)
    _noise_lfsr: int = field(default=0x1FFFF, init=False, repr=False)

    # ------------------------------------------------------------------ I/O

    def write_addr(self, value: int) -> None:
        """Latch a register address (register-select write)."""
        self._addr_latch = value & 0xFF

    def write_data(self, value: int) -> None:
        """Write a data byte to the currently latched register address."""
        self.write_reg(self._addr_latch, value)

    def write_reg(self, index: int, value: int) -> None:
        """Write value directly to register index (bypasses the latch)."""
        index &= 0xFF
        if index < len(self._regs):
            self._regs[index] = value & 0xFF

    def read_reg(self, index: int) -> int:
        """Return the stored value of register index (0 if out of range)."""
        index &= 0xFF
        if index < len(self._regs):
            return self._regs[index]
        return 0

    def read(self) -> int:
        """OPLL is write-only; any read returns 0xFF."""
        return 0xFF

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register state and silence all channels."""
        self._regs = bytearray(0x40)
        self._addr_latch = 0
        self._mod_phase = [0.0] * 9
        self._car_phase = [0.0] * 9
        self._mod_level = [0.0] * 9
        self._car_level = [0.0] * 9
        self._mod_state = [_EG_OFF] * 9
        self._car_state = [_EG_OFF] * 9
        self._mod_fb1 = [0.0] * 9
        self._mod_fb2 = [0.0] * 9
        self._prev_kon = [False] * 9
        self._prev_hh = False
        self._prev_sd = False
        self._prev_tom = False
        self._prev_tc = False
        self._noise_lfsr = 0x1FFFF

    # -------------------------------------------------------- sample generation

    def generate_samples(self, n: int) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples.

        Registers are treated as constant across the buffer (mirrors
        PSG/SCC.generate_samples): per-channel frequency, instrument, and
        volume are decoded once, while phase and envelope state persist
        across calls in instance fields.
        """
        out = bytearray(n * 2)
        regs = self._regs

        user_patch = _decode_patch(bytes(regs[0:8]))

        mod_inc = [0.0] * 9
        car_inc = [0.0] * 9
        vol_scale = [0.0] * 9
        tl_scale = [0.0] * 9
        fb_scale = [0.0] * 9
        mod_sl_frac = [0.0] * 9
        car_sl_frac = [0.0] * 9
        mod_ar_step = [0.0] * 9
        mod_dr_step = [0.0] * 9
        mod_rr_step = [0.0] * 9
        car_ar_step = [0.0] * 9
        car_dr_step = [0.0] * 9
        car_rr_step = [0.0] * 9
        car_eg_type = [0] * 9
        kons = [False] * 9

        for ch in range(9):
            freq_reg = regs[0x20 + ch]
            fnum = regs[0x10 + ch] | ((freq_reg & 0x01) << 8)
            block = (freq_reg >> 1) & 0x07
            kon = bool(freq_reg & 0x10)
            inst_vol = regs[0x30 + ch]
            inst_idx = inst_vol >> 4
            vol = inst_vol & 0x0F
            patch = user_patch if inst_idx == 0 else _PRESETS[inst_idx - 1]
            freq_hz = fnum * (1 << block) * _CLOCK / (1 << 19) / 72.0

            mod_inc[ch] = freq_hz * patch.mod_multi * TABLE_SIZE / SAMPLE_RATE
            car_inc[ch] = freq_hz * patch.car_multi * TABLE_SIZE / SAMPLE_RATE
            vol_scale[ch] = 1.0 - (vol / 15.0)
            tl_scale[ch] = 1.0 - (patch.mod_tl / 63.0)
            fb_scale[ch] = _FB_SCALE[patch.fb]
            mod_sl_frac[ch] = 1.0 - (patch.mod_sl / 15.0)
            car_sl_frac[ch] = 1.0 - (patch.car_sl / 15.0)
            mod_ar_step[ch] = _ATTACK_STEP[patch.mod_ar]
            mod_dr_step[ch] = _DECAY_STEP[patch.mod_dr]
            mod_rr_step[ch] = _DECAY_STEP[patch.mod_rr]
            car_ar_step[ch] = _ATTACK_STEP[patch.car_ar]
            car_dr_step[ch] = _DECAY_STEP[patch.car_dr]
            car_rr_step[ch] = _DECAY_STEP[patch.car_rr]
            car_eg_type[ch] = patch.car_eg_type
            kons[ch] = kon

        # Rhythm mode (register 0x0E): BD stays a normal 2-op FM voice on
        # channel 6, keyed by bit 0x10 instead of its own KON register.
        # Channels 7/8's ordinary melody use is suppressed (kons forced
        # False); HH/SD/TOM/TC are synthesised separately below, reusing
        # channel 7/8's now-idle mod/car envelope and phase state.
        rhythm = bool(regs[0x0E] & 0x20)
        if rhythm:
            kons[6] = bool(regs[0x0E] & 0x10)
            kons[7] = False
            kons[8] = False
            hh_kon = bool(regs[0x0E] & 0x01)
            sd_kon = bool(regs[0x0E] & 0x08)
            tom_kon = bool(regs[0x0E] & 0x04)
            tc_kon = bool(regs[0x0E] & 0x02)
            hh_vol = 1.0 - ((regs[0x37] >> 4) / 15.0)
            sd_vol = 1.0 - ((regs[0x37] & 0x0F) / 15.0)
            tom_vol = 1.0 - ((regs[0x38] >> 4) / 15.0)
            tc_vol = 1.0 - ((regs[0x38] & 0x0F) / 15.0)
            # TOM is a single fixed-multiplier tone at channel 8's own pitch.
            mod_inc[8] = car_inc[8]
        else:
            hh_kon = sd_kon = tom_kon = tc_kon = False
            hh_vol = sd_vol = tom_vol = tc_vol = 0.0

        mod_phase = self._mod_phase
        car_phase = self._car_phase
        mod_level = self._mod_level
        car_level = self._car_level
        mod_state = self._mod_state
        car_state = self._car_state
        mod_fb1 = self._mod_fb1
        mod_fb2 = self._mod_fb2
        prev_kon = self._prev_kon
        sin_table = SIN_TABLE

        # Key-on/off edge detection: buffer granularity (once per call), not
        # per-instruction — an acceptable coarsening (see module docstring).
        for ch in range(9):
            kon = kons[ch]
            if kon and not prev_kon[ch]:
                mod_state[ch] = _EG_ATTACK
                mod_level[ch] = 0.0
                car_state[ch] = _EG_ATTACK
                car_level[ch] = 0.0
                mod_phase[ch] = 0.0
                car_phase[ch] = 0.0
            elif not kon and prev_kon[ch]:
                if mod_state[ch] != _EG_OFF:
                    mod_state[ch] = _EG_RELEASE
                if car_state[ch] != _EG_OFF:
                    car_state[ch] = _EG_RELEASE
            prev_kon[ch] = kon

        if rhythm:
            if hh_kon and not self._prev_hh:
                mod_state[7] = _EG_ATTACK
                mod_level[7] = 0.0
            elif not hh_kon and self._prev_hh:
                mod_state[7] = _EG_RELEASE
            self._prev_hh = hh_kon

            if sd_kon and not self._prev_sd:
                car_state[7] = _EG_ATTACK
                car_level[7] = 0.0
            elif not sd_kon and self._prev_sd:
                car_state[7] = _EG_RELEASE
            self._prev_sd = sd_kon

            if tom_kon and not self._prev_tom:
                mod_state[8] = _EG_ATTACK
                mod_level[8] = 0.0
                mod_phase[8] = 0.0
            elif not tom_kon and self._prev_tom:
                mod_state[8] = _EG_RELEASE
            self._prev_tom = tom_kon

            if tc_kon and not self._prev_tc:
                car_state[8] = _EG_ATTACK
                car_level[8] = 0.0
            elif not tc_kon and self._prev_tc:
                car_state[8] = _EG_RELEASE
            self._prev_tc = tc_kon

        for i in range(n):
            sample = 0.0
            for ch in range(9):
                if rhythm and ch >= 7:
                    continue  # channels 7/8 repurposed for HH/SD/TOM/TC below
                m_state = mod_state[ch]
                m_level = mod_level[ch]
                if m_state == _EG_ATTACK:
                    m_level += mod_ar_step[ch]
                    if m_level >= 1.0:
                        m_level = 1.0
                        m_state = _EG_DECAY
                elif m_state == _EG_DECAY:
                    m_level -= mod_dr_step[ch]
                    if m_level <= mod_sl_frac[ch]:
                        m_level = mod_sl_frac[ch]
                        m_state = _EG_SUSTAIN
                elif m_state == _EG_RELEASE:
                    m_level -= mod_rr_step[ch]
                    if m_level <= 0.0:
                        m_level = 0.0
                        m_state = _EG_OFF
                mod_state[ch] = m_state
                mod_level[ch] = m_level

                c_state = car_state[ch]
                c_level = car_level[ch]
                if c_state == _EG_ATTACK:
                    c_level += car_ar_step[ch]
                    if c_level >= 1.0:
                        c_level = 1.0
                        c_state = _EG_DECAY
                elif c_state == _EG_DECAY:
                    c_level -= car_dr_step[ch]
                    if c_level <= car_sl_frac[ch]:
                        c_level = car_sl_frac[ch]
                        c_state = _EG_SUSTAIN if car_eg_type[ch] else _EG_RELEASE
                elif c_state == _EG_RELEASE:
                    c_level -= car_rr_step[ch]
                    if c_level <= 0.0:
                        c_level = 0.0
                        c_state = _EG_OFF
                car_state[ch] = c_state
                car_level[ch] = c_level

                if c_state == _EG_OFF:
                    continue

                fb_off = (mod_fb1[ch] + mod_fb2[ch]) * 0.5 * fb_scale[ch]
                mod_idx = int(mod_phase[ch] + fb_off) & _TABLE_MASK
                mod_out = sin_table[mod_idx] * m_level * tl_scale[ch]
                mod_fb2[ch] = mod_fb1[ch]
                mod_fb1[ch] = mod_out

                car_idx = int(car_phase[ch] + mod_out * _MOD_DEPTH) & _TABLE_MASK
                sample += sin_table[car_idx] * c_level * vol_scale[ch]

                mod_phase[ch] = (mod_phase[ch] + mod_inc[ch]) % TABLE_SIZE
                car_phase[ch] = (car_phase[ch] + car_inc[ch]) % TABLE_SIZE

            if rhythm:
                # HH (noise, channel 7 mod slot)
                state = mod_state[7]
                level = mod_level[7]
                if state == _EG_ATTACK:
                    level += _PERC_ATTACK_STEP
                    if level >= 1.0:
                        level = 1.0
                        state = _EG_DECAY
                elif state == _EG_DECAY or state == _EG_RELEASE:
                    level -= _PERC_DECAY_HH
                    if level <= 0.0:
                        level = 0.0
                        state = _EG_OFF
                mod_state[7] = state
                mod_level[7] = level

                # SD (noise, channel 7 car slot)
                state = car_state[7]
                level = car_level[7]
                if state == _EG_ATTACK:
                    level += _PERC_ATTACK_STEP
                    if level >= 1.0:
                        level = 1.0
                        state = _EG_DECAY
                elif state == _EG_DECAY or state == _EG_RELEASE:
                    level -= _PERC_DECAY_SD
                    if level <= 0.0:
                        level = 0.0
                        state = _EG_OFF
                car_state[7] = state
                car_level[7] = level

                # TOM (tone, channel 8 mod slot)
                state = mod_state[8]
                level = mod_level[8]
                if state == _EG_ATTACK:
                    level += _PERC_ATTACK_STEP
                    if level >= 1.0:
                        level = 1.0
                        state = _EG_DECAY
                elif state == _EG_DECAY or state == _EG_RELEASE:
                    level -= _PERC_DECAY_TOM
                    if level <= 0.0:
                        level = 0.0
                        state = _EG_OFF
                mod_state[8] = state
                mod_level[8] = level

                # TC (noise, channel 8 car slot)
                state = car_state[8]
                level = car_level[8]
                if state == _EG_ATTACK:
                    level += _PERC_ATTACK_STEP
                    if level >= 1.0:
                        level = 1.0
                        state = _EG_DECAY
                elif state == _EG_DECAY or state == _EG_RELEASE:
                    level -= _PERC_DECAY_TC
                    if level <= 0.0:
                        level = 0.0
                        state = _EG_OFF
                car_state[8] = state
                car_level[8] = level

                noise = self._noise_lfsr
                noise_bit = (noise ^ (noise >> 1)) & 1
                noise = (noise >> 1) | (noise_bit << 16)
                self._noise_lfsr = noise
                noise_signed = 1.0 if noise_bit else -1.0

                if mod_state[7] != _EG_OFF:
                    sample += noise_signed * mod_level[7] * hh_vol * 0.6
                if car_state[7] != _EG_OFF:
                    sample += noise_signed * car_level[7] * sd_vol * 0.8
                if mod_state[8] != _EG_OFF:
                    mod_phase[8] = (mod_phase[8] + mod_inc[8]) % TABLE_SIZE
                    sample += sin_table[int(mod_phase[8]) & _TABLE_MASK] * mod_level[8] * tom_vol
                if car_state[8] != _EG_OFF:
                    sample += noise_signed * car_level[8] * tc_vol * 0.5

            scaled = int(sample * 3200.0)
            if scaled > 32767:
                scaled = 32767
            elif scaled < -32768:
                scaled = -32768
            out[i * 2] = scaled & 0xFF
            out[i * 2 + 1] = (scaled >> 8) & 0xFF

        return out
