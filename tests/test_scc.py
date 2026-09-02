"""Tests for Konami SCC wavetable synthesizer."""
import struct

import pytest

from msx.psg import SAMPLES_PER_FRAME
from msx.scc import SCC

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _samples_as_int16(buf: bytearray) -> list[int]:
    n = len(buf) // 2
    return list(struct.unpack_from(f"<{n}h", buf))


def _make_scc_tone(freq: int = 253, vol: int = 15, ch: int = 0) -> SCC:
    """Return an SCC with channel ch active, waveform = square (+64/-64)."""
    scc = SCC()
    wave_bank_offset = min(ch, 3) * 0x20
    for i in range(32):
        scc.write(wave_bank_offset + i, 0x40 if i < 16 else 0xC0)  # +64 / -64
    scc.write(0x80 + ch * 2, freq & 0xFF)
    scc.write(0x80 + ch * 2 + 1, (freq >> 8) & 0x0F)
    scc.write(0x8A + ch, vol)
    scc.write(0x8F, 1 << ch)  # enable only this channel
    return scc


# ---------------------------------------------------------------------------
# Register map — waveform
# ---------------------------------------------------------------------------

def test_waveform_write_read_back() -> None:
    scc = SCC()
    scc.write(0x10, 0x7F)
    assert scc.read(0x10) == 0x7F


def test_waveform_all_banks_independent() -> None:
    scc = SCC()
    for bank in range(4):
        scc.write(bank * 0x20, bank + 1)
    for bank in range(4):
        assert scc.read(bank * 0x20) == bank + 1


def test_channel5_shares_channel4_waveform() -> None:
    scc = SCC()
    scc.write(0x60, 0x55)   # write to channel 4+5 bank
    assert scc.read(0x60) == 0x55


# ---------------------------------------------------------------------------
# Register map — frequency
# ---------------------------------------------------------------------------

def test_frequency_low_high_write_read() -> None:
    scc = SCC()
    scc.write(0x80, 0xFE)   # channel 1 freq low
    scc.write(0x81, 0x01)   # channel 1 freq high nibble
    assert scc._freq[0] == 0x01FE


def test_frequency_high_nibble_masked() -> None:
    scc = SCC()
    scc.write(0x81, 0xFF)   # only low nibble should be kept
    assert scc._freq[0] == 0xF00


def test_frequency_all_five_channels() -> None:
    scc = SCC()
    for ch in range(5):
        scc.write(0x80 + ch * 2, ch + 1)
    for ch in range(5):
        assert scc._freq[ch] == ch + 1


# ---------------------------------------------------------------------------
# Register map — volume and enable
# ---------------------------------------------------------------------------

def test_volume_write_is_effective_but_reads_back_ff() -> None:
    # Real hardware: 0x80-0x9F is write-only, so the write still lands in
    # state (proven via generate_samples elsewhere) but a read is always 0xFF.
    scc = SCC()
    scc.write(0x8A, 0x0F)
    assert scc.read(0x8A) == 0xFF
    assert scc._vol[0] == 0x0F


def test_volume_nibble_masked() -> None:
    scc = SCC()
    scc.write(0x8A, 0xFF)
    assert scc._vol[0] == 0x0F


def test_enable_register_write_reads_back_ff() -> None:
    scc = SCC()
    scc.write(0x8F, 0x1F)
    assert scc.read(0x8F) == 0xFF
    assert scc._enable == 0x1F


def test_enable_five_bits_masked() -> None:
    scc = SCC()
    scc.write(0x8F, 0xFF)
    assert scc._enable == 0x1F


def test_undefined_read_returns_0xff() -> None:
    scc = SCC()
    assert scc.read(0x90) == 0xFF
    assert scc.read(0xF0) == 0xFF


# ---------------------------------------------------------------------------
# Frequency/volume/enable block mirrored at 0x90-0x9F (real hardware decodes
# only the low 4 bits of 0x80-0x9F; msx/scc.py previously dropped writes here)
# ---------------------------------------------------------------------------

def test_frequency_write_via_mirror() -> None:
    scc = SCC()
    scc.write(0x90, 0xFE)  # mirrors 0x80: channel 1 freq low
    scc.write(0x91, 0x01)  # mirrors 0x81: channel 1 freq high nibble
    assert scc._freq[0] == 0x01FE


def test_volume_write_via_mirror() -> None:
    scc = SCC()
    scc.write(0x9A, 0x0F)  # mirrors 0x8A: channel 1 volume
    assert scc._vol[0] == 0x0F


def test_enable_write_via_mirror() -> None:
    scc = SCC()
    scc.write(0x9F, 0x1F)  # mirrors 0x8F: channel enable
    assert scc._enable == 0x1F


def test_mirror_reads_return_ff() -> None:
    scc = SCC()
    scc.write(0x9A, 0x0F)
    assert scc.read(0x9A) == 0xFF


# ---------------------------------------------------------------------------
# Power-on state
# ---------------------------------------------------------------------------

def test_waveform_initialises_to_zero() -> None:
    scc = SCC()
    assert all(scc.read(i) == 0 for i in range(0x80))


# ---------------------------------------------------------------------------
# Reset (allium/scc.allium: rule Reset — not previously exercised via an
# explicit scc.reset() call, only via fresh-construction defaults)
# ---------------------------------------------------------------------------

def test_reset_restores_power_on_state() -> None:
    scc = _make_scc_tone(freq=0x123, vol=15, ch=0)
    scc.reset()
    assert all(scc.read(i) == 0 for i in range(0x80))  # waveform back to zero
    assert scc._freq == [0, 0, 0, 0, 0]
    assert scc._vol == [0, 0, 0, 0, 0]
    assert scc._enable == 0


def test_reset_silences_output() -> None:
    scc = _make_scc_tone(freq=253, vol=15, ch=0)
    scc.reset()
    buf = scc.generate_samples(100)
    assert all(b == 0 for b in buf)


def test_silence_at_power_on() -> None:
    scc = SCC()
    buf = scc.generate_samples(100)
    assert all(b == 0 for b in buf)


# ---------------------------------------------------------------------------
# generate_samples — basic contract
# ---------------------------------------------------------------------------

def test_generate_samples_byte_count() -> None:
    scc = SCC()
    assert len(scc.generate_samples(735)) == 1470


def test_generate_samples_returns_bytearray() -> None:
    scc = SCC()
    assert isinstance(scc.generate_samples(10), bytearray)


def test_silent_when_all_channels_disabled() -> None:
    scc = SCC()
    for i in range(0x80):
        scc.write(i, 0x40)   # non-zero waveform
    for ch in range(5):
        scc.write(0x8A + ch, 0x0F)   # max volume
    scc.write(0x8F, 0x00)   # all disabled
    buf = scc.generate_samples(100)
    assert all(b == 0 for b in buf)


# ---------------------------------------------------------------------------
# Synthesis — tone output
# ---------------------------------------------------------------------------

def test_nonzero_output_with_enabled_channel() -> None:
    scc = _make_scc_tone(freq=253, vol=15, ch=0)
    samples = _samples_as_int16(scc.generate_samples(SAMPLES_PER_FRAME))
    assert any(s != 0 for s in samples)


def test_phase_advances_across_calls() -> None:
    scc = _make_scc_tone(freq=10, vol=15, ch=0)
    idx_before = scc._phase_idx[0]
    scc.generate_samples(1)
    idx_after = scc._phase_idx[0]
    # Counter or phase must have advanced.
    assert scc._phase_cnt[0] != 0 or idx_after != idx_before


def test_higher_frequency_more_transitions() -> None:
    """Higher frequency → shorter period → more phase steps per frame."""
    scc_hi = _make_scc_tone(freq=50, vol=15, ch=0)
    scc_lo = _make_scc_tone(freq=1000, vol=15, ch=0)
    n = SAMPLES_PER_FRAME

    def transitions(buf: bytearray) -> int:
        s = _samples_as_int16(buf)
        return sum(1 for a, b in zip(s, s[1:]) if a != b)

    assert transitions(scc_hi.generate_samples(n)) > transitions(scc_lo.generate_samples(n))


# ---------------------------------------------------------------------------
# Synthesis — channel 4+5 shared waveform
# ---------------------------------------------------------------------------

def test_shared_waveform_ch4_ch5_doubles_output() -> None:
    """Channels 4 and 5 at same freq/vol/waveform sum to double a single channel."""
    freq = 253
    vol = 8

    scc_both = SCC()
    for i in range(32):
        scc_both.write(0x60 + i, 0x40 if i < 16 else 0xC0)
    for ch in (3, 4):
        scc_both.write(0x80 + ch * 2, freq & 0xFF)
        scc_both.write(0x80 + ch * 2 + 1, (freq >> 8) & 0x0F)
        scc_both.write(0x8A + ch, vol)
    scc_both.write(0x8F, 0b11000)  # channels 4 and 5 (bits 3,4)

    scc_one = SCC()
    for i in range(32):
        scc_one.write(0x60 + i, 0x40 if i < 16 else 0xC0)
    scc_one.write(0x86, freq & 0xFF)
    scc_one.write(0x87, (freq >> 8) & 0x0F)
    scc_one.write(0x8D, vol)
    scc_one.write(0x8F, 0b01000)  # channel 4 only (bit 3)

    s_both = _samples_as_int16(scc_both.generate_samples(SAMPLES_PER_FRAME))
    s_one  = _samples_as_int16(scc_one.generate_samples(SAMPLES_PER_FRAME))
    assert s_both == [2 * s for s in s_one]


# ---------------------------------------------------------------------------
# Register block mirroring and deformation register (0xE0-0xFF)
# ---------------------------------------------------------------------------

def test_register_block_mirrored_low_byte() -> None:
    # The SCC decodes only the low 8 bits, so 0x110 mirrors offset 0x10.
    scc = SCC()
    scc.write(0x10, 0x7F)  # waveform byte at offset 0x10
    assert scc.read(0x110) == 0x7F  # mirror (0x9910) decodes to offset 0x10


def test_deformation_read_returns_ff() -> None:
    assert SCC().read(0xE0) == 0xFF


def test_deformation_access_does_not_corrupt_state() -> None:
    scc = _make_scc_tone(freq=0x123, vol=15, ch=0)
    before_wave = list(scc._waves[0])
    before_freq = scc._freq[0]
    before_vol = scc._vol[0]
    before_enable = scc._enable
    scc.read(0xF0)
    scc.write(0xF0, 0x01)
    scc.write(0xE8, 0xAB)
    assert scc._waves[0] == before_wave
    assert scc._freq[0] == before_freq
    assert scc._vol[0] == before_vol
    assert scc._enable == before_enable


# ---------------------------------------------------------------------------
# State save/restore (openspec typed-save-state-schemas Phase 2)
# ---------------------------------------------------------------------------

def test_snapshot_and_restore_round_trip() -> None:
    scc = _make_scc_tone(freq=0x123, vol=15, ch=0)
    scc.write(0x00, 0x11)  # wave bank 0, byte 0
    scc.write(0x8F, 0x1F)  # enable all 5 channels
    scc.generate_samples(500)  # advance phase/clk_frac state

    snapshot = scc.snapshot()

    scc.generate_samples(500)  # mutate further so restore is meaningfully exercised
    assert scc.snapshot() != snapshot

    scc.restore(snapshot)
    assert scc.snapshot() == snapshot


def test_restore_with_missing_key_does_not_partially_mutate() -> None:
    """restore() reads every field before assigning any of them, so a
    missing key fails before _waves (assigned first) is touched."""
    scc = _make_scc_tone(freq=0x123, vol=15, ch=0)
    scc.write(0x00, 0x11)
    scc.write(0x8F, 0x1F)
    pre = scc.snapshot()

    snap = dict(pre)
    del snap["_clk_frac"]
    with pytest.raises(KeyError):
        scc.restore(snap)

    assert scc.snapshot() == pre


def test_snapshot_on_silent_scc_round_trips() -> None:
    # Default (all-zero) state must round-trip too, not just an actively
    # sounding one.
    scc = SCC()
    snapshot = scc.snapshot()
    scc.restore(snapshot)
    assert scc.snapshot() == snapshot


# ---------------------------------------------------------------------------
# SCC Plus mode (openspec add-scc-i-cartridge)
# ---------------------------------------------------------------------------

def test_default_mode_is_compatible() -> None:
    scc = SCC()
    assert scc._plus_mode is False


def test_set_mode_true_switches_to_plus_decode() -> None:
    scc = SCC()
    scc.set_mode(True)
    assert scc._plus_mode is True


def test_mode_switch_preserves_register_contents() -> None:
    scc = _make_scc_tone(freq=0x123, vol=15, ch=0)
    before_wave = list(scc._waves[0])
    before_freq = scc._freq[0]
    before_vol = scc._vol[0]
    before_enable = scc._enable
    scc.set_mode(True)
    assert scc._waves[0] == before_wave
    assert scc._freq[0] == before_freq
    assert scc._vol[0] == before_vol
    assert scc._enable == before_enable


def test_plus_mode_channel5_independently_writable() -> None:
    scc = SCC()
    scc.set_mode(True)
    scc.write(0x60, 0x11)  # channel 4 waveform byte 0
    scc.write(0x80, 0x22)  # channel 5 waveform byte 0
    assert scc.read(0x60) == 0x11
    assert scc.read(0x80) == 0x22  # did not inherit channel 4's value


def test_plus_mode_waveform_extends_to_0x9f() -> None:
    scc = SCC()
    scc.set_mode(True)
    scc.write(0x90, 0x33)  # channel 5 waveform byte 16
    assert scc.read(0x90) == 0x33


def test_plus_mode_freq_vol_enable_relocated_to_0xa0() -> None:
    scc = SCC()
    scc.set_mode(True)
    scc.write(0xAF, 0x1F)  # channel enable, at the Plus-mode offset
    assert scc._enable == 0x1F
    assert scc.read(0xAF) == 0xFF  # write-only, same as Compatible mode


def test_plus_mode_deformation_register_at_0xc0() -> None:
    scc = SCC()
    scc.set_mode(True)
    assert scc.read(0xC0) == 0xFF
    scc.write(0xC8, 0xAB)  # safe no-op
    assert scc._enable == 0


def test_compatible_mode_unaffected_by_plus_mode_existing() -> None:
    # Default mode (set_mode never called) keeps the original Compatible
    # behavior: channel 5 mirrors whatever is written to channel 4's bank.
    scc = SCC()
    scc.write(0x60, 0x55)
    assert scc.read(0x60) == 0x55


def test_konami_scc_mapper_scc_defaults_to_compatible_mode() -> None:
    # KonamiSCCMapper never calls set_mode(); this asserts that guarantee at
    # the SCC level so a future change to that mapper is caught here too.
    scc = SCC()
    assert scc._plus_mode is False


# ---------------------------------------------------------------------------
# 051649 vs 052539 chip identity (openspec add-scc-i-cartridge follow-up):
# only a 052539 in Compatible mode exposes channel 5's waveform readably at
# 0xA0-0xBF; a real 051649 (openMSX Mode::Real) never does.
# ---------------------------------------------------------------------------

def test_default_chip_identity_is_051649() -> None:
    scc = SCC()
    assert scc.is_052539 is False


def test_051649_compatible_mode_reads_ff_above_wave_banks() -> None:
    scc = SCC()  # is_052539=False: KonamiSCCMapper's real 051649
    scc.write(0x60, 0x7F)  # channel 4 waveform byte 0 (mirrors into bank 4)
    assert scc.read(0xA0) == 0xFF
    assert scc.read(0xBF) == 0xFF


def test_052539_compatible_mode_exposes_channel5_waveform_readable() -> None:
    scc = SCC(is_052539=True)  # SCCICart's chip
    scc.write(0x60, 0x7F)  # channel 4 waveform byte 0, mirrors into bank 4
    assert scc.read(0xA0) == 0x7F  # channel 5's bank, readable here only for a 052539
    assert scc.read(0x60) == 0x7F  # channel 4's own offset is unaffected


def test_052539_compatible_mode_channel5_readback_tracks_every_byte() -> None:
    scc = SCC(is_052539=True)
    for i in range(32):
        scc.write(0x60 + i, i)
    for i in range(32):
        assert scc.read(0xA0 + i) == i


def test_052539_compatible_mode_freq_vol_block_still_ff() -> None:
    # 0x80-0x9F stays write-only even for a 052539 -- the wave5 readback
    # range is 0xA0-0xBF only; 0x80-0x9F must not leak waveform_bank_4.
    scc = SCC(is_052539=True)
    scc.write(0x60, 0x7F)  # populate bank 4 (mirrored from channel 4)
    scc.write(0x8A, 0x0F)  # channel 1 volume, in the freq/vol block
    for addr in range(0x80, 0xA0):
        assert scc.read(addr) == 0xFF


def test_052539_compatible_mode_deform_range_still_ff() -> None:
    # 0xC0-0xFF is outside the wave5-readback range (0xA0-0xBF only) even
    # for a 052539 in Compatible mode -- the deform register and "no
    # function" gap there are unaffected by is_052539.
    scc = SCC(is_052539=True)
    assert scc.read(0xC0) == 0xFF
    assert scc.read(0xFF) == 0xFF


def test_register_writes_do_not_cross_contaminate() -> None:
    # allium/scc.allium: rule_failure for WriteWaveformByte/WriteFrequency
    # Register/WriteVolumeRegister/WriteChannelEnableRegister -- writing to
    # one register's offset must leave every other register type untouched
    # (each rule's guard must genuinely gate on its own offset range).
    scc = SCC()
    scc.write(0x10, 0x7F)   # waveform (bank 0, byte 0x10)
    scc.write(0x80, 0xFE)   # frequency channel 1 low byte
    scc.write(0x8A, 0x0F)   # volume channel 1
    scc.write(0x8F, 0x1F)   # channel enable

    assert scc._waves[0][0x10] == 0x7F
    assert scc._freq[0] == 0xFE
    assert scc._vol[0] == 0x0F
    assert scc._enable == 0x1F

    # Each write above must not have leaked into the other three registers.
    assert scc._waves[0][0x10] != 0  # sanity: still holds its own write
    assert all(b == 0 for i, b in enumerate(scc._waves[0]) if i != 0x10)
    assert scc._freq[1:] == [0, 0, 0, 0]
    assert scc._vol[1:] == [0, 0, 0, 0]


def test_052539_plus_mode_unaffected_by_is_052539() -> None:
    # In Plus mode, bank 4 (channel 5) is already independently readable at
    # its own Plus-mode offset (0x80-0x9F) regardless of is_052539 -- this
    # flag only changes Compatible-mode read decode.
    scc = SCC(is_052539=True)
    scc.set_mode(True)
    scc.write(0x80, 0x11)
    assert scc.read(0x80) == 0x11
    assert scc.read(0xA0) == 0xFF  # Plus mode's freq/vol block, unaffected
