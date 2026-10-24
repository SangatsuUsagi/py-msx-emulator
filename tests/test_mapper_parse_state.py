"""Mapper and RamMapper `parse_state()` / `apply_state()`: every producer validates its
own save-state object (exact key set, types, ranges, lengths) in `parse_state()`, and
`apply_state()` assigns the parsed result, so `restore()` (= apply(parse)) rejects a
malformed object with the mapper unchanged.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from msx.mapper import (
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    FixedPageMapper,
    FlatMapper,
    GameMaster2Mapper,
    HalnoteMapper,
    KoeiSRAM32Mapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    RTypeMapper,
    SCCICart,
)
from msx.ram_mapper import RamMapper
from msx.scc import SCC
from msx.snapshot_schema import SchemaError
from msx.state import _to_jsonable

_PAGE_8K = 8192


def _rom(pages: int) -> bytes:
    rom = bytearray(pages * _PAGE_8K)
    for page in range(pages):
        for i in range(_PAGE_8K):
            rom[page * _PAGE_8K + i] = (page * 7 + i) & 0xFF
    return bytes(rom)


def _ascii8() -> Any:
    m = Ascii8Mapper(rom=_rom(8))
    m.write(0x6800, 3)
    m.write(0x7800, 5)
    return m


def _ascii16() -> Any:
    m = Ascii16Mapper(rom=_rom(8))
    m.write(0x6000, 2)
    m.write(0x7000, 3)
    return m


def _ascii8_sram(cls: type[Ascii8Sram2Mapper]) -> Callable[[], Any]:
    def make() -> Any:
        m = cls(rom=_rom(8))
        m.write(0x6000, 2)
        m.write(0x7000, 4)
        m.sram_image.write(0, 0x5A)
        m.sram_image.write(-1, 0xA5)
        return m
    return make


def _ascii16_sram(cls: type[Ascii16Sram2Mapper]) -> Callable[[], Any]:
    def make() -> Any:
        m = cls(rom=_rom(8))
        m.write(0x6000, 2)
        m.sram_image.write(0, 0x5A)
        m.sram_image.write(-1, 0xA5)
        return m
    return make


def _gamemaster2() -> Any:
    m = GameMaster2Mapper(rom=_rom(16))
    m.write(0x6000, 5)
    m.write(0xA000, 0x10)
    m.write(0xB100, 0x99)
    return m


def _rtype() -> Any:
    m = RTypeMapper(rom=_rom(48))
    m.write(0x4000, 0x12)
    return m


def _konami() -> Any:
    m = KonamiMapper(_rom(8))
    m.write(0x6000, 3)
    m.write(0x8000, 5)
    return m


def _majutsushi() -> Any:
    m = MajutsushiMapper(_rom(8))
    m.write(0x6000, 3)
    m.write(0x5000, 0x40)
    m.generate_samples(4)
    return m


def _konami_scc() -> Any:
    m = KonamiSCCMapper(rom=_rom(8), scc=SCC())
    m.write(0x5000, 2)
    m.write(0x9000, 0x3F)
    return m


def _scci() -> Any:
    m = SCCICart(scc=SCC(is_052539=True))
    m.write(0xBFFE, 0x10)
    m.write(0x4000, 0x99)
    m.write(0x7000, 2)
    return m


def _halnote() -> Any:
    m = HalnoteMapper(rom=_rom(128))
    m.write(0x4FFF, 0x85)
    m.write(0x0010, 0x77)
    m.write(0x77FF, 9)
    return m


def _ram_mapper() -> Any:
    m = RamMapper()
    m.write_port(0xFC, 3)
    m.write(0x0000, 0x42)
    return m


_STATEFUL: dict[str, Callable[[], Any]] = {
    "ascii8": _ascii8,
    "ascii16": _ascii16,
    "ascii8_sram2": _ascii8_sram(Ascii8Sram2Mapper),
    "ascii8_sram8": _ascii8_sram(Ascii8Sram8Mapper),
    "koei_sram32": _ascii8_sram(KoeiSRAM32Mapper),
    "gamemaster2": _gamemaster2,
    "ascii16_sram2": _ascii16_sram(Ascii16Sram2Mapper),
    "ascii16_sram8": _ascii16_sram(Ascii16Sram8Mapper),
    "rtype": _rtype,
    "konami": _konami,
    "majutsushi": _majutsushi,
    "konami_scc": _konami_scc,
    "scc_i": _scci,
    "halnote": _halnote,
    "ram_mapper": _ram_mapper,
}

_STATELESS: dict[str, Callable[[], Any]] = {
    "flat": lambda: FlatMapper(cartridge=bytes(0x4000)),
    "fixed_page": lambda: FixedPageMapper(rom=bytes(0x4000), base=0x4000),
}


def _bad_values(value: object) -> list[object]:
    """Malformed replacements for a well-formed snapshot field value."""
    if isinstance(value, bool):
        return [1, 0, "true", None, 1.0]
    if isinstance(value, int):
        return [str(value), True, float(value), None, -1, 256, 0x10000]
    if isinstance(value, bytearray):
        return [value[:-1], value + b"\x00", bytearray(), list(value[:4]), None]
    if isinstance(value, list):
        bad: list[object] = [value[:-1], value + [0], [], tuple(value), None]
        for replacement in (256, -1, "1", True, 1.5):
            mutated = list(value)
            mutated[-1] = replacement
            bad.append(mutated)
        return bad
    raise AssertionError(f"unhandled snapshot value {value!r}")


def _malformed_states(snap: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    cases: list[tuple[str, dict[str, object]]] = []
    extra = dict(snap)
    extra["bogus"] = 0
    cases.append(("unknown key", extra))
    for key in snap:
        missing = dict(snap)
        del missing[key]
        cases.append((f"missing {key}", missing))
        for i, bad in enumerate(_bad_values(snap[key])):
            mutated = dict(snap)
            mutated[key] = bad
            cases.append((f"{key} bad #{i}", mutated))
    return cases


def _other_values(value: object) -> list[object]:
    """Well-typed replacements for a snapshot field value, differing from it."""
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [c for c in (0, 1, 0x1000) if c != value]
    if isinstance(value, bytearray):
        return [c for c in (bytearray(len(value)), bytearray(b"\x11" * len(value))) if c != value]
    if isinstance(value, list):
        candidates = ([0] * len(value), [1] * len(value), [0x1000] * len(value))
        return [c for c in candidates if c != value]
    raise AssertionError(f"unhandled snapshot value {value!r}")


def _perturbed(m: Any, snap: dict[str, object]) -> dict[str, object]:
    """A state `m` accepts in which every field differs from `snap`."""
    out = dict(snap)
    for key in snap:
        for candidate in _other_values(snap[key]):
            trial = dict(out)
            trial[key] = candidate
            try:
                m.parse_state(trial, "state")
            except SchemaError:
                continue
            out = trial
            break
        else:
            raise AssertionError(f"no valid alternative for {key!r}")
    return out


def _copy(snap: object) -> dict[str, object]:
    """A deep copy whose blobs are bytearrays, as an in-memory snapshot holds them."""
    assert isinstance(snap, Mapping)
    copied: dict[str, object] = {}
    for key, value in snap.items():
        if isinstance(value, (bytes, bytearray)):
            copied[key] = bytearray(value)
        else:
            copied[key] = copy.deepcopy(value)
    return copied


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_own_snapshot_parses_to_itself(name: str) -> None:
    m = _STATEFUL[name]()
    snap = dict(m.snapshot())
    assert m.parse_state(snap, "state") == snap


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_json_decoded_snapshot_parses_and_restores(name: str) -> None:
    m = _STATEFUL[name]()
    decoded = json.loads(json.dumps(_to_jsonable(dict(m.snapshot()))))
    assert isinstance(decoded, dict)
    assert m.parse_state(decoded, "state") == m.snapshot()
    fresh = _STATEFUL[name]()
    fresh.reset()
    fresh.restore(decoded)
    assert fresh.snapshot() == m.snapshot()


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_apply_state_of_parsed_snapshot_restores_same_state(name: str) -> None:
    m = _STATEFUL[name]()
    snap = _copy(m.snapshot())
    fresh = _STATEFUL[name]()
    fresh.apply_state(fresh.parse_state(_perturbed(fresh, snap), "state"))
    assert fresh.snapshot() != m.snapshot()
    fresh.apply_state(fresh.parse_state(snap, "state"))
    assert fresh.snapshot() == m.snapshot()


@pytest.mark.parametrize("name", sorted(_STATELESS))
def test_stateless_apply_state_of_parsed_snapshot_is_noop(name: str) -> None:
    m = _STATELESS[name]()
    m.apply_state(m.parse_state(_copy(m.snapshot()), "state"))
    assert m.snapshot() == {}


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_parse_state_does_not_mutate(name: str) -> None:
    m = _STATEFUL[name]()
    before = m.snapshot()
    other = _STATEFUL[name]()
    other.write(0x6000, 1)
    m.parse_state(_copy(other.snapshot()), "state")
    assert m.snapshot() == before


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_malformed_state_rejected_and_restore_leaves_mapper_unchanged(name: str) -> None:
    m = _STATEFUL[name]()
    snap = _copy(m.snapshot())
    for case, bad in _malformed_states(snap):
        with pytest.raises(SchemaError):
            m.parse_state(bad, "state")
        before = m.snapshot()
        with pytest.raises(ValueError):
            m.restore(bad)
        assert m.snapshot() == before, f"{name}: {case} changed the mapper"


@pytest.mark.parametrize("name", sorted(_STATEFUL))
def test_non_object_state_rejected(name: str) -> None:
    m = _STATEFUL[name]()
    for bad in ([], None, "state", 0):
        with pytest.raises(SchemaError):
            m.parse_state(bad, "state")


def test_error_message_names_field_path() -> None:
    m = _konami()
    with pytest.raises(SchemaError, match=r"mapper_state\.banks\[3\]"):
        m.parse_state({"banks": [0, 1, 2, 256]}, "mapper_state")


@pytest.mark.parametrize("name", sorted(_STATELESS))
def test_stateless_mapper_accepts_only_empty_object(name: str) -> None:
    m = _STATELESS[name]()
    assert m.parse_state({}, "state") == {}
    m.restore({})
    for bad in ({"banks": [0, 0, 0, 0]}, [], None):
        with pytest.raises(SchemaError):
            m.parse_state(bad, "state")
    with pytest.raises(ValueError):
        m.restore({"banks": [0]})


def _assert_rejected_unchanged(m: Any, bad: dict[str, object]) -> None:
    before = m.snapshot()
    with pytest.raises(ValueError):
        m.restore(bad)
    assert m.snapshot() == before


def test_gamemaster2_sram_half_must_be_0_or_0x1000() -> None:
    m = _gamemaster2()
    snap = _copy(m.snapshot())
    snap["sram_half"] = 0x800
    _assert_rejected_unchanged(m, snap)
    snap = _copy(m.snapshot())
    snap["window_sram_half"] = [0, 0x1000, 0, 0x800]
    _assert_rejected_unchanged(m, snap)
    snap = _copy(m.snapshot())
    snap["sram_half"] = 0x1000
    snap["window_sram_half"] = [0x1000, 0, 0x1000, 0]
    m.restore(snap)
    assert m._sram_half == 0x1000


def test_rtype_bank_above_0x17_rejected() -> None:
    m = _rtype()
    _assert_rejected_unchanged(m, {"bank": 0x18})
    m.restore({"bank": 0x17})
    assert m._bank == 0x17


def test_halnote_subbank_256_rejected() -> None:
    m = _halnote()
    snap = _copy(m.snapshot())
    snap["subbanks"] = [0, 256]
    _assert_rejected_unchanged(m, snap)


def test_majutsushi_missing_last_dac_leaves_banks_unchanged() -> None:
    m = _majutsushi()
    pre_banks = list(m._banks)
    with pytest.raises(ValueError):
        m.restore({"banks": [0, 1, 2, 3]})
    assert list(m._banks) == pre_banks


def test_scci_non_int_mode_register_leaves_ram_unchanged() -> None:
    m = _scci()
    pre_ram = bytes(m.ram)
    snap = _copy(m.snapshot())
    snap["ram"] = bytearray(len(pre_ram))
    snap["mode_register"] = "x"
    with pytest.raises(ValueError):
        m.restore(snap)
    assert bytes(m.ram) == pre_ram


def test_ram_mapper_ram_length_follows_instance_size() -> None:
    small = RamMapper(size_kb=64)
    big = RamMapper(size_kb=256)
    with pytest.raises(SchemaError):
        big.parse_state(_copy(small.snapshot()), "state")
    _assert_rejected_unchanged(big, _copy(small.snapshot()))
    assert big.parse_state(_copy(big.snapshot()), "state") == big.snapshot()


def test_sram_length_follows_class_size() -> None:
    m2 = _ascii8_sram(Ascii8Sram2Mapper)()
    m8 = _ascii8_sram(Ascii8Sram8Mapper)()
    with pytest.raises(SchemaError):
        m8.parse_state(_copy(m2.snapshot()), "state")
    _assert_rejected_unchanged(m8, _copy(m2.snapshot()))
