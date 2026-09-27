"""PSG.parse_synth_state() and SCC.parse_state(): a producer's own snapshot
parses back to itself; malformed objects are rejected with ValueError and
restore leaves the producer unchanged (openspec producer-state-validation)."""
import copy

import pytest

from msx.psg import PSG
from msx.scc import SCC


def _active_psg() -> PSG:
    psg = PSG()
    writes = [
        (0, 0x50), (1, 0x01),   # channel A tone period
        (2, 0x03), (3, 0x00),   # channel B tone period (ultrasonic)
        (6, 0x0F),              # noise period
        (7, 0x30),              # tone A/B/C, noise on A
        (8, 0x1F), (9, 0x0C),   # A on envelope, B fixed volume
        (11, 0x20), (12, 0x00),  # envelope period
        (13, 0x0E),             # continuing, alternating ramp
    ]
    for reg, value in writes:
        psg.write_port(0xA0, reg)
        psg.write_port(0xA1, value)
    psg.generate_samples(1000)
    return psg


def _active_scc() -> SCC:
    scc = SCC()
    for i in range(0x80):
        scc.write(i, (i * 37) & 0xFF)
    for ch in range(5):
        scc.write(0x80 + ch * 2, 0x23 + ch)       # freq low
        scc.write(0x81 + ch * 2, ch)              # freq high
        scc.write(0x8A + ch, 15 - ch)             # volume
    scc.write(0x8F, 0x1F)
    scc.generate_samples(1000)
    return scc


# --------------------------------------------------------------------- PSG

def test_psg_own_snapshot_parses_and_equals_snapshot() -> None:
    psg = _active_psg()
    snap = psg.snapshot_synth()
    assert psg.parse_synth_state(snap) == snap
    assert psg.snapshot_synth() == snap  # parse is pure


def test_psg_default_snapshot_parses() -> None:
    psg = PSG()
    snap = psg.snapshot_synth()
    assert psg.parse_synth_state(snap) == snap


def test_psg_parse_returns_fresh_lists() -> None:
    psg = _active_psg()
    snap = psg.snapshot_synth()
    parsed = psg.parse_synth_state(snap)
    assert parsed["_tone_cnt"] is not snap["_tone_cnt"]


def _psg_bad(field: str, value: object) -> dict[str, object]:
    snap: dict[str, object] = copy.deepcopy(dict(_active_psg().snapshot_synth()))
    snap[field] = value
    return snap


_PSG_BAD_CASES: list[tuple[str, object]] = [
    ("_tone_cnt", [1, 1]),
    ("_tone_cnt", [1, 1, 1, 1]),
    ("_tone_cnt", [0, 1, 1]),
    ("_tone_cnt", [1, 4096, 1]),
    ("_tone_cnt", "12"),
    ("_tone_out", [0, 2, 0]),
    ("_tone_out", [0, True, 0]),
    ("_noise_cnt", -1000000),
    ("_noise_cnt", 0),
    ("_noise_cnt", 63),
    ("_noise_cnt", "12"),
    ("_noise_cnt", 12.0),
    ("_noise_cnt", True),
    ("_lfsr", 0),
    ("_lfsr", 0x20000),
    ("_env_cnt", -17),
    ("_env_cnt", 65536),
    ("_env_step", 32),
    ("_env_step", -1),
    ("_env_attack", 1),
    ("_env_attack", 0x0F),
    ("_env_alternate", 1),
    ("_env_hold_flag", "true"),
    ("_env_holding", None),
    ("_clk_frac", 44100),
    ("_clk_frac", -1),
]


@pytest.mark.parametrize(("field", "value"), _PSG_BAD_CASES)
def test_psg_malformed_field_rejected_and_restore_leaves_psg_unchanged(
    field: str, value: object
) -> None:
    psg = _active_psg()
    pre = copy.deepcopy(psg.snapshot_synth())
    bad = _psg_bad(field, value)
    with pytest.raises(ValueError, match=f"psg_synth.{field}"):
        psg.parse_synth_state(bad)
    with pytest.raises(ValueError):
        psg.restore_synth(bad)
    assert psg.snapshot_synth() == pre


def test_psg_env_cnt_small_negative_accepted() -> None:
    psg = PSG()
    snap: dict[str, object] = dict(psg.snapshot_synth())
    snap["_env_cnt"] = -16
    assert psg.parse_synth_state(snap)["_env_cnt"] == -16


def test_psg_missing_and_unknown_keys_rejected() -> None:
    psg = _active_psg()
    pre = copy.deepcopy(psg.snapshot_synth())

    missing: dict[str, object] = dict(copy.deepcopy(pre))
    del missing["_clk_frac"]
    with pytest.raises(ValueError, match="missing"):
        psg.restore_synth(missing)

    unknown: dict[str, object] = dict(copy.deepcopy(pre))
    unknown["_extra"] = 0
    with pytest.raises(ValueError, match="unknown"):
        psg.restore_synth(unknown)

    with pytest.raises(ValueError):
        psg.restore_synth([])  # type: ignore[arg-type]

    assert psg.snapshot_synth() == pre


def test_psg_label_appears_in_message() -> None:
    psg = PSG()
    bad = _psg_bad("_lfsr", 0)
    with pytest.raises(ValueError, match=r"custom\._lfsr"):
        psg.parse_synth_state(bad, "custom")


def test_psg_restore_after_parse_round_trips_and_generates() -> None:
    src = _active_psg()
    snap = copy.deepcopy(src.snapshot_synth())
    dst = PSG()
    dst.regs = list(src.regs)
    dst.restore_synth(dict(snap))
    assert dst.snapshot_synth() == snap
    assert dst.generate_samples(100) == src.generate_samples(100)


# --------------------------------------------------------------------- SCC

def test_scc_own_snapshot_parses_and_equals_snapshot() -> None:
    scc = _active_scc()
    snap = scc.snapshot()
    assert scc.parse_state(snap) == snap
    assert scc.snapshot() == snap  # parse is pure


def test_scc_default_snapshot_parses() -> None:
    scc = SCC()
    snap = scc.snapshot()
    assert scc.parse_state(snap) == snap


def _scc_bad(field: str, value: object) -> dict[str, object]:
    snap: dict[str, object] = copy.deepcopy(dict(_active_scc().snapshot()))
    snap[field] = value
    return snap


def _waves_with(bank: int, entry: object) -> list[list[object]]:
    waves: list[list[object]] = [[0] * 32 for _ in range(5)]
    waves[bank][7] = entry
    return waves


_SCC_BAD_CASES: list[tuple[str, object]] = [
    ("_waves", [[0] * 32 for _ in range(4)]),
    ("_waves", [[0] * 32 for _ in range(6)]),
    ("_waves", [[0] * 31] + [[0] * 32 for _ in range(4)]),
    ("_waves", _waves_with(2, 256)),
    ("_waves", _waves_with(4, -1)),
    ("_waves", _waves_with(0, "12")),
    ("_waves", "blob"),
    ("_freq", [0, 0, 0, 0]),
    ("_freq", [0, 0x1000, 0, 0, 0]),
    ("_freq", [0, 0, 0, 0, 1.5]),
    ("_vol", [0, 16, 0, 0, 0]),
    ("_vol", [0, 0, 0, 0, True]),
    ("_enable", 0x20),
    ("_enable", "12"),
    ("_phase_cnt", [0, 0, 4096, 0, 0]),
    ("_phase_cnt", [0, 0, -1, 0, 0]),
    ("_phase_idx", [0, 0, 0, 32, 0]),
    ("_phase_idx", [0, 0, 0, 0, 0, 0]),
    ("_clk_frac", 44100),
    ("_clk_frac", 12.0),
]


@pytest.mark.parametrize(("field", "value"), _SCC_BAD_CASES)
def test_scc_malformed_field_rejected_and_restore_leaves_scc_unchanged(
    field: str, value: object
) -> None:
    scc = _active_scc()
    pre = copy.deepcopy(scc.snapshot())
    bad = _scc_bad(field, value)
    with pytest.raises(ValueError, match=f"scc_state.{field}"):
        scc.parse_state(bad)
    with pytest.raises(ValueError):
        scc.restore(bad)
    assert scc.snapshot() == pre


def test_scc_missing_and_unknown_keys_rejected() -> None:
    scc = _active_scc()
    pre = copy.deepcopy(scc.snapshot())

    missing: dict[str, object] = dict(copy.deepcopy(pre))
    del missing["_waves"]
    with pytest.raises(ValueError, match="missing"):
        scc.restore(missing)

    unknown: dict[str, object] = dict(copy.deepcopy(pre))
    unknown["_plus_mode"] = False
    with pytest.raises(ValueError, match="unknown"):
        scc.restore(unknown)

    assert scc.snapshot() == pre


def test_scc_restore_does_not_alias_the_input_lists() -> None:
    src = _active_scc()
    snap: dict[str, object] = dict(copy.deepcopy(src.snapshot()))
    dst = SCC()
    dst.restore(snap)
    waves = snap["_waves"]
    assert isinstance(waves, list)
    waves[0][0] = 0x99 if waves[0][0] != 0x99 else 0x98
    assert dst.snapshot() == src.snapshot()
    assert dst.generate_samples(100) == src.generate_samples(100)
