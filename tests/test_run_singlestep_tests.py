"""Tests for the SingleStepTests runner (tools/run_singlestep_tests.py), on synthetic cases."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_TOOLS = Path(__file__).parent.parent / "tools"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_singlestep_tests", _TOOLS / "run_singlestep_tests.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


_MOD = _load()


def _state(pc: int, ram: list[list[int]], **regs: int) -> dict[str, Any]:
    state: dict[str, Any] = {
        "pc": pc, "sp": 0xF000, "a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "f": 0,
        "h": 0, "l": 0, "i": 0, "r": 0, "ei": 0, "wz": 0, "ix": 0, "iy": 0,
        "af_": 0, "bc_": 0, "de_": 0, "hl_": 0, "im": 0, "p": 0, "q": 0,
        "iff1": 0, "iff2": 0, "ram": ram,
    }
    state.update(regs)
    return state


def _case(
    name: str, initial: dict[str, Any], ports: list[list[Any]] | None = None
) -> dict[str, Any]:
    """Build a case whose final state is what the core computes (a passing case)."""
    case: dict[str, Any] = {"name": name, "initial": initial, "final": {}, "cycles": []}
    if ports is not None:
        case["ports"] = ports
    cpu, mem, port_log = _MOD._setup(case)
    cycles = cpu.step()
    r = cpu.registers
    final = copy.deepcopy(initial)
    final.update({
        "pc": r.PC, "sp": r.SP, "a": r.A, "f": r.F, "b": r.BC >> 8, "c": r.BC & 0xFF,
        "d": r.DE >> 8, "e": r.DE & 0xFF, "h": r.HL >> 8, "l": r.HL & 0xFF, "i": r.I,
        "r": r.R, "wz": r.WZ, "ix": r.IX, "iy": r.IY, "q": cpu.q_next,
        "ei": int(cpu.ei_pending), "iff1": int(cpu.iff1), "iff2": int(cpu.iff2), "im": cpu.im,
    })
    final["ram"] = [[a, mem[a]] for a, _ in initial["ram"]]
    case["final"] = final
    case["cycles"] = [[0, None, "----"]] * cycles
    if ports is None:
        assert port_log.log == []
    return case


def _write(tmp_path: Path, name: str, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def _divergences(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "divergences.json"
    path.write_text(json.dumps({"format_version": 1, "divergences": entries}), encoding="utf-8")
    return path


def _nop_case() -> dict[str, Any]:
    case = _case("00 0000", _state(0x1000, [[0x1000, 0x00]], r=0x10))
    assert case["final"]["pc"] == 0x1001
    assert case["final"]["r"] == 0x11
    assert len(case["cycles"]) == 4
    return case


def _ldir_case() -> dict[str, Any]:
    # LDIR at 0x2800, BC = 2: one repeating iteration (the core sets MEMPTR 0x2802).
    ram = [[0x2800, 0xED], [0x2801, 0xB0], [0xC000, 0x00], [0xD000, 0x55]]
    case = _case("ED B0 0000", _state(0x2800, ram, b=0x00, c=0x02, h=0xC0, d=0xD0))
    assert case["final"]["wz"] == 0x2802
    return case


def test_a_matching_case_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Spec "A matching case passes".
    path = _write(tmp_path, "00", [_nop_case()])
    assert _MOD.main([str(path), "--divergences", str(_divergences(tmp_path, []))]) == 0
    assert "0 failed" in capsys.readouterr().out


def test_a_mismatching_field_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Spec "A mismatching field is reported".
    case = _nop_case()
    case["final"]["a"] = 0x01
    path = _write(tmp_path, "00", [case])
    assert _MOD.main([str(path), "--divergences", str(_divergences(tmp_path, []))]) == 1
    out = capsys.readouterr().out
    assert "00 0000 (a)" in out


def test_port_transactions_are_compared(tmp_path: Path) -> None:
    # Spec "Port transactions are compared": OUT (0x34),A with A = 0x12.
    initial = _state(0x1000, [[0x1000, 0xD3], [0x1001, 0x34]], a=0x12)
    case = _case("D3 0000", initial, ports=[[0x1234, 0x12, "w"]])
    divergences = str(_divergences(tmp_path, []))
    assert _MOD.main([str(_write(tmp_path, "d3", [case])), "--divergences", divergences]) == 0
    case["ports"] = [[0x1235, 0x12, "w"]]
    report = _MOD.Report()
    _MOD.run_file(_write(tmp_path, "d3", [case]), None, [], report)
    assert report.failures == 1


def test_a_known_divergence_does_not_fail_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Spec "A known divergence does not fail the run": the tests' MEMPTR is ED + 1.
    case = _ldir_case()
    case["final"]["wz"] = 0x2801
    entry = {"id": "ldxr", "files": ["ed b0"], "fields": ["wz"]}
    path = _write(tmp_path, "ed b0", [case])
    assert _MOD.main([str(path), "--divergences", str(_divergences(tmp_path, [entry]))]) == 0
    assert "known divergence ldxr: 1 cases" in capsys.readouterr().out


def test_an_unlisted_field_still_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Spec "An unlisted field still fails".
    case = _ldir_case()
    case["final"]["wz"] = 0x2801
    case["final"]["f"] ^= 0x01
    entry = {"id": "ldxr", "files": ["ed b0"], "fields": ["wz"]}
    path = _write(tmp_path, "ed b0", [case])
    assert _MOD.main([str(path), "--divergences", str(_divergences(tmp_path, [entry]))]) == 1
    out = capsys.readouterr().out
    assert "(f, wz)" in out or "(wz, f)" in out


def test_a_stale_entry_fails_the_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Spec "A stale entry fails the run".
    entry = {"id": "ldxr", "files": ["ed b0"], "fields": ["wz"]}
    path = _write(tmp_path, "ed b0", [_ldir_case()])
    assert _MOD.main([str(path), "--divergences", str(_divergences(tmp_path, [entry]))]) == 1
    assert "stale divergence entry (no matching failure): ldxr" in capsys.readouterr().out


def test_the_shipped_divergence_list_loads() -> None:
    entries = _MOD.load_divergences(_TOOLS / "singlestep_known_divergences.json")
    ids = []
    for d in entries:
        ids.append(d.id)
    assert ids == ["ldxr-cpxr-repeat-memptr", "block-out-port-high-byte"]
