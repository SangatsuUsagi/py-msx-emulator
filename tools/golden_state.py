#!/usr/bin/env python3
"""Run the golden cases of the whole core and write or check their hashes.

``tests/vectors/golden_state.json`` lists fixed runs ("cases"): a machine, an
optional slot 1 cartridge, the cartridge mapper, an optional disk image for
drive A, an optional tape image (each medium a path and its SHA-256), an
input script of key matrix cells held from a first to a last frame (frames
count from 1), and checkpoint frames. For each checkpoint N the file can
record the SHA-256 of the audio of frames 1 to N, the SHA-256 of the video
pixels of frame N, and the SHA-256 of each top-level section of
``save_state()`` after frame N, as RFC 8785 canonical JSON. Only checkpoint
frames are rendered. A checkpoint has one of three forms: ``{frame}``,
``{frame, audio, video}`` or ``{frame, state, audio, video}``. A checkpoint
with no hashes is not compared, and only the hashes that a checkpoint
records are compared.

``--write`` records the audio and video hashes, and ``--write --state`` also
records the state-section hashes. The repository file records audio and
video only: the state sections change with every save-state layout change,
so they are recorded before the native core port starts, after the layout is
settled.

The hashes record what this core does, not what a real MSX does: a mismatch
is a change in behavior, or a difference between two implementations of the
core. The file is language-neutral, so a port of the core can read the same
cases (dev-golden-state).

The ROMs and media are not part of this repository. The machine ROMs come
from the machine YAML paths: ``roms/cbios/`` for the C-BIOS cases, and
``roms/hb_10/`` and ``roms/hb_f1xd/`` for the tape and disk cases. Each
cartridge, disk and tape image comes from the path its case gives, under
``tests/media/`` (see tools/README_tools.md). A case whose ROM or medium is
absent, or whose medium has another SHA-256, is skipped. The disk image is
only read: a run never writes it back.

Usage:
    venvs/py-msx-emulator/bin/python tools/golden_state.py           # check
    venvs/py-msx-emulator/bin/python tools/golden_state.py --write   # record
    venvs/py-msx-emulator/bin/python tools/golden_state.py --write --state \
        --vectors /tmp/golden_state.json                          # record all

The exit status is 1 when a case differs, else 0. A skipped case and a case
with no recorded hashes do not fail the check, and ``--write`` keeps the old
hashes of a skipped case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(os.environ.get("PY_MSX_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(_PROJECT_ROOT))

from host.machine_config import (  # noqa: E402
    load_device_registry,
    load_machine_config,
    load_machine_spec,
)
from host.tape import TapeDeck  # noqa: E402
from msx.boundary import STATUS_OK  # noqa: E402
from msx.core_api import CoreApi  # noqa: E402
from msx.input import InputFrame  # noqa: E402
from msx.machine_loader import create_machine  # noqa: E402
from msx.machine_spec import MachineLoadError  # noqa: E402

DEFAULT_VECTORS = _PROJECT_ROOT / "tests" / "vectors" / "golden_state.json"
_KEY_ROWS = 11


class MissingRom(Exception):
    """A ROM or medium that a case needs is absent or has another SHA-256."""


@dataclass(frozen=True)
class Checkpoint:
    """The hashes after one checkpoint frame."""

    frame: int
    state: dict[str, str]
    audio: str
    video: str

    def as_json(self, with_state: bool = True) -> dict[str, Any]:
        """The checkpoint for the file, without the state sections unless
        `with_state`."""
        if not with_state:
            return {"frame": self.frame, "audio": self.audio, "video": self.video}
        return {"frame": self.frame, "state": self.state, "audio": self.audio,
                "video": self.video}


def canonical_json(value: object) -> bytes:
    """`value` as RFC 8785 canonical JSON. The state and this file hold only
    integers, strings with ASCII keys, lists and objects (no float), and for
    those sorted keys with no whitespace give the RFC 8785 bytes.
    tests/test_golden_state.py checks this against the core's encoder."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_vectors(path: Path = DEFAULT_VECTORS) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_bytes())
    return data


def write_vectors(data: dict[str, Any], path: Path = DEFAULT_VECTORS) -> None:
    """Write the file as canonical JSON and a newline, so that the same
    hashes always give the same bytes."""
    path.write_bytes(canonical_json(data) + b"\n")


def medium_bytes(
    case: dict[str, Any], kind: str, root: Path = _PROJECT_ROOT
) -> bytes | None:
    """The case's `kind` medium ("cartridge", "disk" or "tape"), or None
    when the case has none. Raises MissingRom when the file is absent or its
    SHA-256 differs."""
    name = case[f"{kind}_path"]
    if name is None:
        return None
    path = root / str(name)
    if not path.is_file():
        raise MissingRom(f"{name} is absent")
    data = path.read_bytes()
    actual = sha256_hex(data)
    expected = case[f"{kind}_sha256"]
    if actual != expected:
        raise MissingRom(f"{name} has SHA-256 {actual}, expected {expected}")
    return data


def cartridge_bytes(case: dict[str, Any], root: Path = _PROJECT_ROOT) -> bytes | None:
    """The case's cartridge ROM, or None for a case with no cartridge."""
    return medium_bytes(case, "cartridge", root)


def build_case_machine(case: dict[str, Any], root: Path = _PROJECT_ROOT) -> CoreApi:
    """The case's machine, built as the CLI's headless run builds it, with
    its disk in drive A and its tape mounted. The disk is never written back
    (the run ignores the loaded config's disk_files)."""
    cartridge = cartridge_bytes(case, root)
    disk = None
    if medium_bytes(case, "disk", root) is not None:
        disk = root / str(case["disk_path"])
    tape = None
    if medium_bytes(case, "tape", root) is not None:
        tape = root / str(case["tape_path"])
    config_dir = root / "config"
    try:
        spec = load_machine_spec(
            case["machine"], config_dir, load_device_registry(config_dir), root
        )
        if cartridge is None:
            loaded = load_machine_config(spec, fdd1=disk)
        else:
            loaded = load_machine_config(
                spec, cartridge1=cartridge, mapper1=case["mapper"], fdd1=disk
            )
    except MachineLoadError as exc:
        if "not found" in str(exc):
            raise MissingRom(str(exc)) from exc
        raise
    result = create_machine(loaded.config)
    if result.status != STATUS_OK or result.machine is None:
        raise MachineLoadError(result.error_text.decode("utf-8"))
    if tape is not None:
        TapeDeck(result.machine).load(tape)
    return result.machine


def key_matrix(case: dict[str, Any], frame: int) -> bytes:
    """The active-low key matrix rows of `frame` from the case's script."""
    rows = bytearray([0xFF] * _KEY_ROWS)
    for held in case["inputs"]:
        if held["first"] <= frame <= held["last"]:
            rows[held["row"]] &= ~(1 << held["bit"]) & 0xFF
    return bytes(rows)


def section_hashes(state: bytes) -> dict[str, str]:
    """SHA-256 of each top-level section of a canonical save state."""
    decoded: dict[str, Any] = json.loads(state)
    result: dict[str, str] = {}
    for key in sorted(decoded):
        result[key] = sha256_hex(canonical_json(decoded[key]))
    return result


def run_case(
    case: dict[str, Any], root: Path = _PROJECT_ROOT, render_all: bool = False
) -> list[Checkpoint]:
    """Run the case to its last checkpoint and hash each checkpoint.
    `render_all` renders every frame (the result must not change)."""
    machine = build_case_machine(case, root)
    frames = sorted(int(point["frame"]) for point in case["checkpoints"])
    audio = hashlib.sha256()
    result: list[Checkpoint] = []
    for frame in range(1, frames[-1] + 1):
        render = render_all or frame in frames
        out = machine.step_frame(InputFrame(key_matrix=key_matrix(case, frame)), render)
        audio.update(out.audio)
        if frame in frames:
            result.append(Checkpoint(
                frame=frame,
                state=section_hashes(machine.save_state()),
                audio=audio.hexdigest(),
                video=sha256_hex(out.video_pixels),
            ))
    return result


def has_recorded_hashes(case: dict[str, Any]) -> bool:
    """Whether a checkpoint of the case gives any hash."""
    for point in case["checkpoints"]:
        if "state" in point or "audio" in point or "video" in point:
            return True
    return False


def differences(case: dict[str, Any], actual: list[Checkpoint]) -> list[str]:
    """One line for each hash of `actual` that differs from the case's
    record, naming the case, the frame and the hash. Only the hashes that a
    checkpoint records are compared."""
    lines: list[str] = []
    recorded = {int(point["frame"]): point for point in case["checkpoints"]}
    for point in actual:
        expected = recorded[point.frame]
        where = f"{case['id']} frame {point.frame}"
        if "state" in expected:
            for name in sorted(set(expected["state"]) | set(point.state)):
                if expected["state"].get(name) != point.state.get(name):
                    lines.append(f"{where}: state section {name} differs")
        if "audio" in expected and expected["audio"] != point.audio:
            lines.append(f"{where}: audio differs")
        if "video" in expected and expected["video"] != point.video:
            lines.append(f"{where}: video differs")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the golden core hashes.")
    parser.add_argument("--write", action="store_true",
                        help="record the audio and video hashes instead of checking them")
    parser.add_argument("--state", action="store_true",
                        help="with --write, also record the state-section hashes")
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS,
                        help="the data file (default: tests/vectors/golden_state.json)")
    args = parser.parse_args(argv)

    data = read_vectors(args.vectors)
    failed = False
    for case in data["cases"]:
        if not args.write and not has_recorded_hashes(case):
            print(f"{case['id']}: no recorded hashes")
            continue
        try:
            actual = run_case(case)
        except MissingRom as exc:
            print(f"{case['id']}: skipped, {exc}")
            continue
        if args.write:
            case["checkpoints"] = [point.as_json(args.state) for point in actual]
            print(f"{case['id']}: recorded")
            continue
        lines = differences(case, actual)
        for line in lines:
            print(line)
        if lines:
            failed = True
        else:
            print(f"{case['id']}: ok")
    if args.write:
        write_vectors(data, args.vectors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
