"""Tests for Konami SCC wavetable synthesizer."""
import struct

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

def test_volume_write_read_back() -> None:
    scc = SCC()
    scc.write(0x8A, 0x0F)
    assert scc.read(0x8A) == 0x0F


def test_volume_nibble_masked() -> None:
    scc = SCC()
    scc.write(0x8A, 0xFF)
    assert scc._vol[0] == 0x0F


def test_enable_register_write_read() -> None:
    scc = SCC()
    scc.write(0x8F, 0x1F)
    assert scc.read(0x8F) == 0x1F


def test_enable_five_bits_masked() -> None:
    scc = SCC()
    scc.write(0x8F, 0xFF)
    assert scc._enable == 0x1F


def test_undefined_read_returns_0xff() -> None:
    scc = SCC()
    assert scc.read(0x90) == 0xFF
    assert scc.read(0xF0) == 0xFF


# ---------------------------------------------------------------------------
# Power-on state
# ---------------------------------------------------------------------------

def test_waveform_initialises_to_zero() -> None:
    scc = SCC()
    assert all(scc.read(i) == 0 for i in range(0x80))


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
