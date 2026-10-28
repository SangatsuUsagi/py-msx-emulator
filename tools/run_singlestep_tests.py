#!/usr/bin/env python3
"""Run SingleStepTests/z80 cases on the bare msx.cpu.z80.Z80 core.

SingleStepTests/z80 (https://github.com/SingleStepTests/z80, MIT) gives, per
opcode, a JSON file of 1000 cases: the CPU state and RAM before one
instruction and after it, the I/O transactions and the bus cycles. The cases
come from an emulator core (Ares, then fixed), not from real hardware.

For each case this tool loads the initial state into a Z80 with flat 64 KB
RAM and no M1 wait state, answers port reads from the case's ``ports``, runs
one ``step()``, and compares every register, WZ, Q, IFF1/IFF2, IM, the
EI-pending state, the final RAM, the port transactions and the T-states (the
number of ``cycles`` entries). It does not compare the per-cycle bus states,
nor ``p`` (the LD A,I/LD A,R state, not modelled).

Cases where the core deliberately differs are listed in
``singlestep_known_divergences.json`` (next to this file): a failing case of a
listed file whose differing fields are all listed counts as a known
divergence. An entry with no such case among the files run is reported as
stale. The exit status is 0 when there is no failure and no stale entry,
else 1.

The test files are not part of this repository; pass their paths. PyPy runs
all 1604 files in about 3 minutes; on CPython use ``--limit``.

Usage:
    ./venvs/pypy-msx-emulator/bin/python tools/run_singlestep_tests.py \\
        path/to/SingleStepTests/z80/v1/*.json

The core is imported from the repository that holds this file; set
PY_MSX_ROOT to test the core of another checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(os.environ.get("PY_MSX_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(_PROJECT_ROOT))

from msx.cpu.z80 import Z80  # noqa: E402

_DEFAULT_DIVERGENCES = Path(__file__).resolve().parent / "singlestep_known_divergences.json"
_UNLISTED_PORT_VALUE = 0xFF  # a read from a port the case does not list
_REG8 = (("a", "A"), ("f", "F"), ("i", "I"), ("r", "R"))
_REG16 = (
    ("pc", "PC"), ("sp", "SP"), ("ix", "IX"), ("iy", "IY"), ("wz", "WZ"),
    ("bc_", "BC_"), ("de_", "DE_"), ("hl_", "HL_"),
)


class _Ports:
    """Answer port reads from a case's ``ports`` and record every access."""

    def __init__(self, expected: list[list[Any]]) -> None:
        self._reads: dict[int, int] = {}
        for address, value, kind in expected:
            if kind == "r":
                self._reads[address] = value
        self.log: list[list[Any]] = []

    def read(self, port: int) -> int:
        value = self._reads.get(port, _UNLISTED_PORT_VALUE)
        self.log.append([port, value, "r"])
        return value

    def write(self, port: int, value: int) -> None:
        self.log.append([port, value, "w"])


@dataclass
class Divergence:
    id: str
    files: frozenset[str]
    fields: frozenset[str]
    count: int = 0


@dataclass
class Report:
    cases: int = 0
    failures: int = 0
    stale: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.failures == 0 and not self.stale


def load_divergences(path: Path) -> list[Divergence]:
    """Read the known-divergence list (format_version 1)."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format_version") != 1:
        raise ValueError(f"{path}: format_version is not 1")
    result: list[Divergence] = []
    for entry in document["divergences"]:
        files = frozenset(entry["files"])
        result.append(Divergence(entry["id"], files, frozenset(entry["fields"])))
    return result


def _setup(case: dict[str, Any]) -> tuple[Z80, bytearray, _Ports]:
    initial = case["initial"]
    mem = bytearray(0x10000)
    for address, value in initial["ram"]:
        mem[address] = value
    ports = _Ports(case.get("ports", []))
    cpu = Z80(
        read_byte=mem.__getitem__, write_byte=mem.__setitem__,
        read_port=ports.read, write_port=ports.write,
    )
    r = cpu.registers
    for key, name in _REG8 + _REG16:
        setattr(r, name, initial[key])
    r.BC = (initial["b"] << 8) | initial["c"]
    r.DE = (initial["d"] << 8) | initial["e"]
    r.HL = (initial["h"] << 8) | initial["l"]
    r.A_ = initial["af_"] >> 8
    r.F_ = initial["af_"] & 0xFF
    cpu.iff1 = bool(initial["iff1"])
    cpu.iff2 = bool(initial["iff2"])
    cpu.im = initial["im"]
    cpu.ei_pending = bool(initial["ei"])
    cpu.q_next = initial["q"]  # step() moves q_next into q before the instruction
    return cpu, mem, ports


def _differences(
    case: dict[str, Any], cpu: Z80, mem: bytearray, ports: _Ports, cycles: int
) -> list[str]:
    """Return the names of the fields whose value after the step differs from ``final``."""
    final = case["final"]
    r = cpu.registers
    actual: dict[str, int] = {}
    for key, name in _REG8 + _REG16:
        actual[key] = getattr(r, name)
    actual["b"] = r.BC >> 8
    actual["c"] = r.BC & 0xFF
    actual["d"] = r.DE >> 8
    actual["e"] = r.DE & 0xFF
    actual["h"] = r.HL >> 8
    actual["l"] = r.HL & 0xFF
    actual["af_"] = (r.A_ << 8) | r.F_
    actual["iff1"] = int(cpu.iff1)
    actual["iff2"] = int(cpu.iff2)
    actual["im"] = cpu.im
    actual["ei"] = int(cpu.ei_pending)
    actual["q"] = cpu.q_next  # the latch the next instruction will see
    bad: list[str] = []
    for key, value in actual.items():
        if final[key] != value:
            bad.append(key)
    for address, value in final["ram"]:
        if mem[address] != value:
            bad.append("ram")
            break
    if ports.log != case.get("ports", []):
        bad.append("ports")
    if cycles != len(case["cycles"]):
        bad.append("cycles")
    return bad


def run_file(path: Path, limit: int | None, divergences: list[Divergence], report: Report) -> None:
    """Run the cases of one test file and add the results to ``report``."""
    name = path.stem
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    if limit is not None:
        cases = cases[:limit]
    listed: list[Divergence] = []
    for d in divergences:
        if name in d.files:
            listed.append(d)
    counts: dict[str, int] = {}
    first_failure = ""
    failures = 0
    for case in cases:
        cpu, mem, ports = _setup(case)
        cycles = cpu.step()
        bad = _differences(case, cpu, mem, ports, cycles)
        if not bad:
            continue
        known = None
        for d in listed:
            if set(bad) <= d.fields:
                known = d
                break
        if known is not None:
            known.count += 1
            continue
        failures += 1
        for key in bad:
            counts[key] = counts.get(key, 0) + 1
        if not first_failure:
            first_failure = f"{case['name']} ({', '.join(bad)})"
    report.cases += len(cases)
    if failures:
        report.failures += failures
        print(f"{name}: {failures}/{len(cases)} failed {counts}, first: {first_failure}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SingleStepTests/z80 cases on the Z80 core.")
    parser.add_argument("files", nargs="+", type=Path, help="SingleStepTests v1 .json files")
    parser.add_argument(
        "--limit", type=int, default=None, help="run only the first N cases of each file"
    )
    parser.add_argument(
        "--divergences", type=Path, default=_DEFAULT_DIVERGENCES,
        help="known-divergence list (default: singlestep_known_divergences.json here)",
    )
    args = parser.parse_args(argv)
    for path in args.files:
        if not path.is_file():
            parser.error(f"{path}: not found")
    divergences = load_divergences(args.divergences)
    report = Report()
    run_names: set[str] = set()
    for path in args.files:
        run_names.add(path.stem)
        run_file(path, args.limit, divergences, report)
    for d in divergences:
        if d.files & run_names and d.count == 0:
            report.stale.append(d.id)
    print(f"\n{report.cases} cases in {len(args.files)} files: {report.failures} failed")
    for d in divergences:
        if d.files & run_names:
            print(f"known divergence {d.id}: {d.count} cases")
    for entry_id in report.stale:
        print(f"stale divergence entry (no matching failure): {entry_id}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
