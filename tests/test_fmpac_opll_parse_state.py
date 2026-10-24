"""FM-PAC and OPLL save-state parsing: parse_state() accepts the producer's own
snapshot and rejects malformed values as ValueError (never IndexError), and
restore() leaves the FM-PAC and its OPLL unchanged on a rejection.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable

import pytest

from msx.canonical_json import to_jsonable
from msx.fmpac import SRAM_SIZE, FmPac
from msx.opll import Opll
from msx.snapshot_schema import SchemaError

_BANK_SIZE = 0x4000


def _bank_rom(num_banks: int = 4) -> bytes:
    buf = bytearray()
    for bank in range(num_banks):
        buf.extend(bytes([bank & 0xFF] * _BANK_SIZE))
    return bytes(buf)


def _played_fmpac(rom: bytes | None = None) -> FmPac:
    """An FM-PAC whose OPLL has played melody and rhythm notes, and whose
    SRAM, bank and enable registers have changed from power-on."""
    fmpac = FmPac(rom=rom if rom is not None else _bank_rom(), opll=Opll())
    fmpac.write(0x5FFE, 0x4D)
    fmpac.write(0x5FFF, 0x69)
    for i in range(16):
        fmpac.write(0x4000 + i, 0xA0 + i)
    fmpac.write(0x7FF7, 0x02)  # bank 2
    fmpac.write(0x7FF6, 0x01)  # I/O-port OPLL access on
    opll = fmpac.opll
    user_tone = (0x21, 0x21, 0x1F, 0x0B, 0xF3, 0xF4, 0x23, 0x13)
    for reg in range(8):
        opll.write_reg(reg, user_tone[reg])
    opll.write_reg(0x30, 0x02)  # ch0: user tone, volume 2
    opll.write_reg(0x10, 0x55)
    opll.write_reg(0x20, 0x17)  # ch0: block 3, key on, fnum bit 8
    opll.write_reg(0x31, 0x40)  # ch1: instrument 4
    opll.write_reg(0x11, 0xAB)
    opll.write_reg(0x21, 0x3C)  # ch1: sustain, key on, block 6
    opll.write_reg(0x16, 0x20)
    opll.write_reg(0x26, 0x05)
    opll.write_reg(0x0E, 0x3F)  # rhythm mode, every drum keyed
    opll.generate_samples(300)
    opll.write_reg(0x21, 0x2C)  # ch1 key off: release
    opll.generate_samples(150)
    return fmpac


@pytest.fixture()
def fmpac() -> FmPac:
    return _played_fmpac()


# ---------------------------------------------------------------------------
# The producers' own snapshots parse
# ---------------------------------------------------------------------------

def test_fmpac_own_snapshot_parses_and_equals_itself(fmpac: FmPac) -> None:
    snap = fmpac.snapshot()
    assert fmpac.parse_state(snap, "state") == snap
    assert fmpac.snapshot() == snap  # parse_state does not mutate


def test_opll_own_snapshot_parses_and_equals_itself(fmpac: FmPac) -> None:
    snap = fmpac.opll.snapshot()
    assert fmpac.opll.parse_state(snap, "opll") == snap


def test_json_round_tripped_snapshot_parses(fmpac: FmPac) -> None:
    """The load path hands parse_state bytearray blobs and JSON numbers."""
    snap = fmpac.snapshot()
    decoded = json.loads(json.dumps(to_jsonable(snap)))
    assert fmpac.parse_state(decoded, "state") == snap


def test_restore_from_parsed_snapshot_is_bit_identical(fmpac: FmPac) -> None:
    snap = fmpac.snapshot()
    expected = bytes(fmpac.opll.generate_samples(400))
    other = FmPac(rom=_bank_rom(), opll=Opll())
    other.restore(dict(snap))
    assert other.snapshot() == snap
    assert bytes(other.opll.generate_samples(400)) == expected
    assert other.read(0x4000) == 0xA0  # SRAM re-enabled by the restored magic


# ---------------------------------------------------------------------------
# Rejections leave the FM-PAC and its OPLL unchanged
# ---------------------------------------------------------------------------

def _assert_rejected(fmpac: FmPac, mutate: Callable[[dict[str, Any]], None], path: str) -> None:
    pre = fmpac.snapshot()
    bad: dict[str, Any] = copy.deepcopy(dict(pre))
    mutate(bad)
    with pytest.raises(ValueError, match=path):
        fmpac.parse_state(bad, "state")
    with pytest.raises(ValueError, match=path):
        fmpac.restore(bad)
    assert fmpac.snapshot() == pre
    assert len(fmpac.sram_image) == SRAM_SIZE


def test_short_sram_rejected_without_resizing(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["sram"] = bytes(100)

    pre_sram = fmpac.sram_image.data()
    _assert_rejected(fmpac, mutate, r"state\.sram")
    assert fmpac.sram_image.data() == pre_sram


def test_bank_past_a_16kb_rom_rejected() -> None:
    fmpac = _played_fmpac(rom=_bank_rom(1))
    fmpac.write(0x7FF7, 0x00)  # a 16 KB ROM only has bank 0

    def mutate(s: dict[str, Any]) -> None:
        s["bank"] = 1

    _assert_rejected(fmpac, mutate, r"state\.bank")


def test_bank_above_3_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["bank"] = 4

    _assert_rejected(fmpac, mutate, r"state\.bank")


def test_enable_outside_its_mask_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["enable"] = 0x02

    _assert_rejected(fmpac, mutate, r"state\.enable")


def test_magic_register_as_string_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["r1ffe"] = "77"

    _assert_rejected(fmpac, mutate, r"state\.r1ffe")


def test_missing_key_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        del s["r1fff"]

    _assert_rejected(fmpac, mutate, "r1fff")


def test_unknown_key_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["extra"] = 0

    _assert_rejected(fmpac, mutate, "extra")


def test_opll_patch_number_above_18_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["patch_number"][0] = 19

    _assert_rejected(fmpac, mutate, r"state\.opll\.patch_number\[0\]")


def test_opll_19_slots_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["slots"].append(copy.deepcopy(s["opll"]["slots"][0]))

    _assert_rejected(fmpac, mutate, r"state\.opll\.slots")


def test_opll_short_user_patch_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        del s["opll"]["user_patch"][1]

    _assert_rejected(fmpac, mutate, r"state\.opll\.user_patch")


def test_opll_user_patch_field_out_of_range_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["user_patch"][0]["ML"] = 16

    _assert_rejected(fmpac, mutate, r"state\.opll\.user_patch\[0\]\.ML")


@pytest.mark.parametrize("phase", [-1, 72 * 44100, 1.5, True])
def test_opll_resample_phase_out_of_range_rejected(fmpac: FmPac, phase: object) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["resample_phase"] = phase

    _assert_rejected(fmpac, mutate, r"state\.opll\.resample_phase")


def test_opll_slot_ws_2_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["slots"][0]["ws"] = 2

    _assert_rejected(fmpac, mutate, r"state\.opll\.slots\[0\]\.ws")


def test_opll_slot_eg_rate_l_4_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["slots"][5]["eg_rate_l"] = 4

    _assert_rejected(fmpac, mutate, r"state\.opll\.slots\[5\]\.eg_rate_l")


def test_opll_slot_output_wrong_length_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["slots"][0]["output"] = [0]

    _assert_rejected(fmpac, mutate, r"state\.opll\.slots\[0\]\.output")


def test_opll_ch_out_out_of_range_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["ch_out"][3] = 5000

    _assert_rejected(fmpac, mutate, r"state\.opll\.ch_out\[3\]")


def test_opll_short_reg_blob_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["reg"] = bytes(0x3F)

    _assert_rejected(fmpac, mutate, r"state\.opll\.reg")


def test_opll_zero_noise_rejected(fmpac: FmPac) -> None:
    def mutate(s: dict[str, Any]) -> None:
        s["opll"]["noise"] = 0

    _assert_rejected(fmpac, mutate, r"state\.opll\.noise")


def test_opll_restore_rejects_without_mutating() -> None:
    opll = _played_fmpac().opll
    pre = opll.snapshot()
    bad: dict[str, Any] = copy.deepcopy(dict(pre))
    bad["patch_number"][8] = 19
    with pytest.raises(SchemaError, match=r"opll\.patch_number\[8\]"):
        opll.restore(bad)
    assert opll.snapshot() == pre


# ---------------------------------------------------------------------------
# apply_state(parse_state(snapshot)) reproduces the state
# ---------------------------------------------------------------------------

def test_fmpac_apply_parsed_snapshot_reproduces_state(fmpac: FmPac) -> None:
    snap = fmpac.snapshot()
    expected = bytes(fmpac.opll.generate_samples(400))
    other = FmPac(rom=_bank_rom(), opll=Opll())
    other.apply_state(other.parse_state(snap, "state"))
    assert other.snapshot() == snap
    assert bytes(other.opll.generate_samples(400)) == expected
    assert other.read(0x4000) == 0xA0  # SRAM enable recomputed by apply_state


def test_opll_apply_parsed_snapshot_reproduces_state(fmpac: FmPac) -> None:
    snap = fmpac.opll.snapshot()
    expected = bytes(fmpac.opll.generate_samples(400))
    other = Opll()
    other.apply_state(other.parse_state(snap, "opll"))
    assert other.snapshot() == snap
    assert bytes(other.generate_samples(400)) == expected
