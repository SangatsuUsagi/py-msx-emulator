"""Save and restore complete machine state to/from disk.

The on-disk `.state` file is a stdlib-only JSON container (no pickle): scalar and
structured fields are stored as JSON, and byte blobs (RAM, VRAM, SRAM) are wrapped
as ``{"__b64__": "<base64>"}``. `format_version` guards compatibility.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image as _PIL_Image

from msx.vdp.v9938 import V9938

if TYPE_CHECKING:
    from msx.machine import Machine

# Version 5: stdlib JSON container replacing the legacy pickle format (<= 4).
CURRENT_FORMAT_VERSION: int = 5


@dataclass
class MachineSnapshot:
    format_version: int
    machine_type: str  # "msx1" or "msx2"
    # CPU
    cpu_regs: dict[str, int]
    cpu_halted: bool
    cpu_iff1: bool
    cpu_iff2: bool
    cpu_int_pending: bool
    cpu_nmi_pending: bool
    cpu_im: int
    # Memory
    ram: bytearray
    slot_register: int
    mapper_class: str
    mapper_state: dict[str, object]
    # VDP
    vdp_vram: bytearray
    vdp_regs: list[int]
    vdp_status: int
    vdp_latch: int | None
    vdp_addr: int
    vdp_read_buf: int
    vdp_frame_count: int
    # PSG (registers + synthesiser state)
    psg_regs: list[int]
    psg_latch: int
    psg_synth: dict[str, object]
    # SCC (None when absent)
    scc_state: dict[str, object] | None
    # MSX2-only (None for MSX1)
    vdp_palette: list[int] | None = None
    ram_mapper_ram: bytearray | None = None
    ram_mapper_banks: list[int] | None = None
    sub_slot_reg: int | None = None
    cmd_regs: list[int] | None = None
    status2: int | None = None
    cmd_remaining: int | None = None


# --- internal helpers ---------------------------------------------------------

def _cpu_regs_to_dict(machine: "Machine") -> dict[str, int]:
    r = machine.cpu.registers
    return {
        "A": r.A, "F": r.F, "BC": r.BC, "DE": r.DE, "HL": r.HL,
        "IX": r.IX, "IY": r.IY, "SP": r.SP, "PC": r.PC,
        "I": r.I, "R": r.R,
        "A_": r.A_, "F_": r.F_, "BC_": r.BC_, "DE_": r.DE_, "HL_": r.HL_,
    }


def _restore_cpu_regs(machine: "Machine", d: dict[str, int]) -> None:
    r = machine.cpu.registers
    r.A = d["A"]
    r.F = d["F"]
    r.BC = d["BC"]
    r.DE = d["DE"]
    r.HL = d["HL"]
    r.IX = d["IX"]
    r.IY = d["IY"]
    r.SP = d["SP"]
    r.PC = d["PC"]
    r.I = d["I"]
    r.R = d["R"]
    r.A_ = d["A_"]
    r.F_ = d["F_"]
    r.BC_ = d["BC_"]
    r.DE_ = d["DE_"]
    r.HL_ = d["HL_"]


def _psg_synth_to_dict(machine: "Machine") -> dict[str, object]:
    p = machine.psg
    return {
        "_tone_cnt": list(p._tone_cnt),
        "_tone_out": list(p._tone_out),
        "_noise_cnt": p._noise_cnt,
        "_lfsr": p._lfsr,
        "_env_cnt": p._env_cnt,
        "_env_step": p._env_step,
        "_env_attack": p._env_attack,
        "_env_alternate": p._env_alternate,
        "_env_hold_flag": p._env_hold_flag,
        "_env_holding": p._env_holding,
        "_clk_frac": p._clk_frac,
    }


def _restore_psg_synth(machine: "Machine", d: dict[str, object]) -> None:
    p = machine.psg
    p._tone_cnt = list(d["_tone_cnt"])  # type: ignore[arg-type]
    p._tone_out = list(d["_tone_out"])  # type: ignore[arg-type]
    p._noise_cnt = int(d["_noise_cnt"])  # type: ignore[arg-type]
    p._lfsr = int(d["_lfsr"])  # type: ignore[arg-type]
    p._env_cnt = int(d["_env_cnt"])  # type: ignore[arg-type]
    p._env_step = int(d["_env_step"])  # type: ignore[arg-type]
    p._env_attack = int(d["_env_attack"])  # type: ignore[arg-type]
    p._env_alternate = bool(d["_env_alternate"])
    p._env_hold_flag = bool(d["_env_hold_flag"])
    p._env_holding = bool(d["_env_holding"])
    p._clk_frac = int(d["_clk_frac"])  # type: ignore[arg-type]


def _scc_to_dict(machine: "Machine") -> dict[str, object] | None:
    if machine.scc is None:
        return None
    s = machine.scc
    return {
        "_waves": [list(w) for w in s._waves],
        "_freq": list(s._freq),
        "_vol": list(s._vol),
        "_enable": s._enable,
        "_phase_cnt": list(s._phase_cnt),
        "_phase_idx": list(s._phase_idx),
        "_clk_frac": s._clk_frac,
    }


def _restore_scc(machine: "Machine", d: dict[str, object] | None) -> None:
    if machine.scc is None or d is None:
        return
    s = machine.scc
    s._waves = [list(w) for w in d["_waves"]]  # type: ignore[arg-type]
    s._freq = list(d["_freq"])  # type: ignore[arg-type]
    s._vol = list(d["_vol"])  # type: ignore[arg-type]
    s._enable = int(d["_enable"])  # type: ignore[arg-type]
    s._phase_cnt = list(d["_phase_cnt"])  # type: ignore[arg-type]
    s._phase_idx = list(d["_phase_idx"])  # type: ignore[arg-type]
    s._clk_frac = int(d["_clk_frac"])  # type: ignore[arg-type]


def _snapshot_from_machine(machine: "Machine") -> MachineSnapshot:
    is_msx2 = isinstance(machine.vdp, V9938)
    mapper = machine.memory._mapper
    mapper_state = mapper.snapshot()
    if is_msx2:
        vdp_latch = machine.vdp.latch
        vdp_addr = machine.vdp.addr
        vdp_read_buf = machine.vdp.read_buf
        vdp_palette: list[int] | None = list(machine.vdp.palette)
        ram_mapper_ram: bytearray | None = bytearray(machine.memory.ram_mapper.ram)
        ram_mapper_banks: list[int] | None = list(machine.memory.ram_mapper.banks)
        sub_slot_reg: int | None = machine.memory.sub_slot_reg
        cmd_regs: list[int] | None = list(machine.vdp.cmd_regs)
        status2: int | None = machine.vdp._status2
        cmd_remaining: int | None = machine.vdp._cmd_remaining
    else:
        vdp_latch = machine.vdp.latch
        vdp_addr = machine.vdp.addr
        vdp_read_buf = machine.vdp.read_buf
        vdp_palette = None
        ram_mapper_ram = None
        ram_mapper_banks = None
        sub_slot_reg = None
        cmd_regs = None
        status2 = None
        cmd_remaining = None
    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type="msx2" if is_msx2 else "msx1",
        cpu_regs=_cpu_regs_to_dict(machine),
        cpu_halted=machine.cpu.halted,
        cpu_iff1=machine.cpu.iff1,
        cpu_iff2=machine.cpu.iff2,
        cpu_int_pending=machine.cpu.int_pending,
        cpu_nmi_pending=machine.cpu.nmi_pending,
        cpu_im=machine.cpu.im,
        ram=bytearray(machine.memory.ram),
        slot_register=machine.memory.slot_register,
        mapper_class=type(mapper).__name__,
        mapper_state=mapper_state,
        vdp_vram=bytearray(machine.vdp.vram),
        vdp_regs=list(machine.vdp.regs),
        vdp_status=machine.vdp.status,
        vdp_latch=vdp_latch,
        vdp_addr=vdp_addr,
        vdp_read_buf=vdp_read_buf,
        vdp_frame_count=machine.vdp._frame_count,
        psg_regs=list(machine.psg.regs),
        psg_latch=machine.psg.latch,
        psg_synth=_psg_synth_to_dict(machine),
        scc_state=_scc_to_dict(machine),
        vdp_palette=vdp_palette,
        ram_mapper_ram=ram_mapper_ram,
        ram_mapper_banks=ram_mapper_banks,
        sub_slot_reg=sub_slot_reg,
        cmd_regs=cmd_regs,
        status2=status2,
        cmd_remaining=cmd_remaining,
    )


def _restore_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    if snap.format_version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {snap.format_version}, "
            f"expected {CURRENT_FORMAT_VERSION}"
        )
    is_msx2 = isinstance(machine.vdp, V9938)
    expected_type = "msx2" if is_msx2 else "msx1"
    if snap.machine_type != expected_type:
        raise ValueError(
            f"machine type mismatch: running {expected_type!r}, "
            f"saved {snap.machine_type!r}"
        )
    mapper = machine.memory._mapper
    if type(mapper).__name__ != snap.mapper_class:
        raise ValueError(
            f"mapper mismatch: running {type(mapper).__name__!r}, "
            f"saved {snap.mapper_class!r}"
        )

    _restore_cpu_regs(machine, snap.cpu_regs)
    machine.cpu.halted = snap.cpu_halted
    machine.cpu.iff1 = snap.cpu_iff1
    machine.cpu.iff2 = snap.cpu_iff2
    machine.cpu.int_pending = snap.cpu_int_pending
    machine.cpu.nmi_pending = snap.cpu_nmi_pending
    machine.cpu.im = snap.cpu_im

    machine.memory.ram[:] = snap.ram
    machine.memory.slot_register = snap.slot_register
    mapper.restore(snap.mapper_state)

    machine.vdp.vram[:] = snap.vdp_vram
    machine.vdp.regs[:] = snap.vdp_regs
    machine.vdp.status = snap.vdp_status
    machine.vdp._frame_count = snap.vdp_frame_count
    if is_msx2:
        machine.vdp.latch = snap.vdp_latch
        machine.vdp.addr = snap.vdp_addr
        machine.vdp.read_buf = snap.vdp_read_buf
        machine.vdp.palette[:] = snap.vdp_palette  # type: ignore[arg-type]
        machine.memory.ram_mapper.ram[:] = snap.ram_mapper_ram  # type: ignore[index]
        machine.memory.ram_mapper.banks[:] = snap.ram_mapper_banks  # type: ignore[arg-type]
        if snap.sub_slot_reg is not None:
            machine.memory.sub_slot_reg = snap.sub_slot_reg
        if snap.cmd_regs is not None:
            machine.vdp.cmd_regs[:] = snap.cmd_regs
        if snap.status2 is not None:
            machine.vdp._status2 = snap.status2
        if snap.cmd_remaining is not None:
            machine.vdp._cmd_remaining = snap.cmd_remaining
    else:
        machine.vdp.latch = snap.vdp_latch
        machine.vdp.addr = snap.vdp_addr
        machine.vdp.read_buf = snap.vdp_read_buf

    machine.psg.regs[:] = snap.psg_regs
    machine.psg.latch = snap.psg_latch
    _restore_psg_synth(machine, snap.psg_synth)
    _restore_scc(machine, snap.scc_state)


# --- symlink helper -----------------------------------------------------------

def _update_symlink(link: Path, target: Path) -> None:
    """Atomically update (or create) a symlink to point at target."""
    tmp = link.with_suffix(link.suffix + ".tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        os.symlink(target.name, tmp)
        os.replace(tmp, link)
    except OSError as exc:
        import sys
        print(f"warning: could not update symlink {link}: {exc}", file=sys.stderr)


# --- public API ---------------------------------------------------------------

def _sanitise_title(title: str) -> str:
    """Replace spaces with underscores and strip filesystem-unsafe characters."""
    title = title.replace(" ", "_")
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", title) or "save"


def _to_jsonable(obj: object) -> object:
    """Recursively convert a snapshot dict to JSON-serialisable form.

    Byte blobs become ``{"__b64__": "<base64>"}``; lists and dicts recurse;
    scalars pass through unchanged.
    """
    if isinstance(obj, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _from_jsonable(obj: object) -> object:
    """Inverse of _to_jsonable: restore byte blobs (as bytearray) and containers."""
    if isinstance(obj, dict):
        if "__b64__" in obj and len(obj) == 1:
            return bytearray(base64.b64decode(obj["__b64__"]))
        return {k: _from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_jsonable(v) for v in obj]
    return obj


def save_state(machine: "Machine", rgb_buf: bytearray, title: str) -> Path:
    """Serialise machine state and screenshot to saves/<title>_YYYYMMDD_HHMMSS.*

    Args:
        machine: Running machine to snapshot.
        rgb_buf: Current frame as 256×192 RGB24 bytearray.
        title: Human-readable title used in the filename.

    Returns:
        Path of the written .state file.
    """
    saves_dir = Path("saves")
    saves_dir.mkdir(exist_ok=True)

    stem = f"{_sanitise_title(title)}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    state_path = saves_dir / f"{stem}.state"
    png_path = saves_dir / f"{stem}.png"

    snap = _snapshot_from_machine(machine)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(_to_jsonable(asdict(snap)), f)

    from msx.machine import SCREEN_HEIGHT, SCREEN_WIDTH
    img = _PIL_Image.frombytes("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bytes(rgb_buf))
    img.save(png_path)

    _update_symlink(saves_dir / "latest.state", state_path)
    _update_symlink(saves_dir / "latest.png", png_path)

    print(f"state saved: {state_path}")
    return state_path


def load_state(machine: "Machine", path: Path | None = None) -> None:
    """Restore machine state from a save file.

    Args:
        machine: Running machine to restore into (callbacks remain intact).
        path: Explicit .state file to load. When None, loads saves/latest.state.

    Raises:
        FileNotFoundError: If the target file does not exist.
        ValueError: If format version or mapper class does not match.
    """
    if path is None:
        link = Path("saves") / "latest.state"
        if not link.exists():
            raise FileNotFoundError("no save state found: saves/latest.state does not exist")
        resolved = link.resolve()
    else:
        if not path.exists():
            raise FileNotFoundError(f"save state not found: {path}")
        resolved = path

    with open(resolved, "rb") as f:
        raw = f.read()
    # Legacy pickle files (format_version <= 4) start with a pickle opcode byte,
    # not JSON. Refuse to unpickle them; the format is now stdlib JSON.
    stripped = raw.lstrip()
    if not stripped[:1] == b"{":
        raise ValueError(
            "legacy pickle save states are no longer supported; please re-save "
            f"({resolved})"
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"corrupt or unsupported save state: {resolved} ({exc})") from exc

    fields = _from_jsonable(data)
    snap = MachineSnapshot(**fields)  # type: ignore[arg-type]
    _restore_snapshot(machine, snap)
    print(f"state loaded: {resolved}")
