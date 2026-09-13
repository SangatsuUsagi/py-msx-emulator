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
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from msx.mapper import MapperKind
from msx.vdp.v9938 import V9938

if TYPE_CHECKING:
    from msx.machine import Machine

# Version 5: stdlib JSON container replacing the legacy pickle format (<= 4).
# Version 6: mapper_class: str (Python class name) replaced with
#   mapper_kind: MapperKind (closed enum, decoupled from class names) --
#   see openspec/changes/mapper-state-tagged-union. No other field changed.
# Version 7: fdc_state: dict[str, object] | None added (WD2793/TC8566AF/
#   DiskDrive/connection-style state, see
#   openspec/changes/2026-08-31-fdc-state-save-load). No other field
#   changed.
# Version 8: scci_state: dict[str, object] | None added (SCCICart's
#   RAM/banks/mode-register state, now needed since --extension scc_plus
#   moved SCCICart from primary slot 1 -- covered by the generic
#   mapper_kind/mapper_state fields -- to slot 2, which has no generic
#   mapper2 state path; see openspec/changes/add-extension-flag). No other
#   field changed.
# Version 9: slot2_sub_slot_reg: int | None added (primary slot 2's own
#   secondary slot register, independent of sub_slot_reg -- see
#   openspec/changes/add-hbi-j1-support). No other field changed.
# Version 10: halnote_state: dict[str, object] | None added (HBI-J1's
#   Halnote-mapped MSX-JE cartridge's bank/sub-bank registers, SRAM-enable/
#   submapper-enable flags, and 16 KB SRAM contents, since slot 2's
#   sub-slots have no generic mapper-state path -- mirrors scci_state's
#   own precedent; see openspec/changes/add-hbi-j1-support). No other field
#   changed.
# Version 11: fmpac_state/scci_state/halnote_state (three bespoke, per-
#   device-kind fields) replaced by a generic slot-2 mechanism mirroring
#   slot 1's mapper_kind/mapper_state: mapper2_kind: MapperKind and
#   mapper2_state: dict[str, object] (always present, the flat slot-2
#   case -- FM-PAC, SCC-I, or an empty mapper) plus
#   mapper2_subslot_kinds/mapper2_subslot_states: list[...] | None
#   (length-4 lists when slot 2 is expanded, e.g. HBI-J1's Halnote
#   cartridge -- otherwise both None). A slot-2 mapper-kind or wiring
#   mismatch now raises ValueError, matching slot 1's existing strict
#   check, in place of the prior three fields' silent-skip-on-mismatch
#   behavior. See openspec/changes/generalize-slot2-mapper-state.
# Version 12: HalnoteMapperState's sram_enabled/submapper_enabled fields
#   removed -- write-only in the persisted payload (HalnoteMapper.restore()
#   has always re-derived both flags from the restored bank registers'
#   top bit, never from these two fields). No behavior change; only the
#   persisted schema shrinks. See
#   openspec/changes/trim-halnote-derived-state-fields.
CURRENT_FORMAT_VERSION: int = 12


class StateLoadError(ValueError):
    """A save-state producer's restore() (or restore_synth()) failed on
    malformed data.

    Distinct from the plain ValueErrors _restore_snapshot raises directly
    for format/machine-type/mapper-kind mismatches (those already have
    clear top-level messages); this specifically wraps whatever a
    producer's own restore() raises when handed a malformed dict, so a
    corrupted or hand-edited .state file always fails with one predictable,
    catchable error naming which producer broke. The original exception is
    chained via __cause__, so a developer debugging the failure still sees
    the original traceback and exception type.
    """

    def __init__(self, producer: str, cause: Exception) -> None:
        super().__init__(f"failed to restore {producer} state: {cause}")
        self.producer = producer


def _restore_producer(producer: str, fn: Callable[[], None]) -> None:
    """Run one producer's restore call, wrapping a malformed-data failure
    as StateLoadError(producer, ...) instead of letting it propagate raw."""
    try:
        fn()
    except (KeyError, TypeError, ValueError) as exc:
        raise StateLoadError(producer, exc) from exc


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
    # `mapper_kind` is the persisted mapper-identity discriminant -- a closed
    # MapperKind enum (msx/mapper.py), not the mapper's Python class name, so
    # a class rename does not change save-state compatibility (see
    # openspec/changes/mapper-state-tagged-union). `mapper_state` itself is
    # still the untyped `dict[str, object]` + cast() shape documented on the
    # `Mapper` Protocol in msx/mapper.py (see that file's PORT-NOTE on
    # GameMaster2Mapper.snapshot()) -- restore() failures on a malformed
    # mapper_state are caught and re-raised as StateLoadError (see
    # _restore_producer below), but the dict's shape itself is not validated
    # ahead of that call. Replacing dict[str, object] with a per-kind typed
    # variant is a separate, larger redesign, deliberately out of scope here.
    mapper_kind: MapperKind
    mapper_state: dict[str, object]
    # Primary slot 2, same generic mechanism as slot 1 above -- see
    # openspec/changes/generalize-slot2-mapper-state. mapper2_kind/
    # mapper2_state are always present (machine.memory._mapper2 always
    # exists, even as an empty FlatMapper when nothing is configured
    # there). mapper2_subslot_kinds/mapper2_subslot_states (defined below
    # among the MSX2-only fields) are the expanded-slot-2 counterpart,
    # None unless slot2_sub_slot_enabled.
    mapper2_kind: MapperKind
    mapper2_state: dict[str, object]
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
    # FDC: WD2793/TC8566AF + connection-style + drives (None when no FDC)
    fdc_state: dict[str, object] | None = None
    # Primary slot 2's own secondary slot register (None unless an extension
    # needing an expanded slot 2, e.g. HBI-J1, is active) -- independent of
    # sub_slot_reg above, mirroring its shape exactly.
    slot2_sub_slot_reg: int | None = None
    # Expanded slot 2's per-sub-slot mapper kind/state (length-4 lists, one
    # entry per sub-slot; both None unless slot2_sub_slot_enabled) -- the
    # generic counterpart to mapper2_kind/mapper2_state above, covering
    # e.g. HBI-J1's Halnote-mapped MSX-JE cartridge in sub-slot 0. An
    # unassigned sub-slot is kind=None/state={}. See
    # openspec/changes/generalize-slot2-mapper-state.
    mapper2_subslot_kinds: list[MapperKind | None] | None = None
    mapper2_subslot_states: list[dict[str, object]] | None = None


class _MachineSnapshotFields(TypedDict):
    """Mirrors MachineSnapshot's field set, for typing `MachineSnapshot(**fields)`
    in load_state() -- `fields` there is JSON-decoded (loosely typed by
    `_from_jsonable`'s `object` return), and `asdict(snap)` always serializes
    every field (defaults included), so a loaded file always has all keys."""

    format_version: int
    machine_type: str
    cpu_regs: dict[str, int]
    cpu_halted: bool
    cpu_iff1: bool
    cpu_iff2: bool
    cpu_int_pending: bool
    cpu_nmi_pending: bool
    cpu_im: int
    ram: bytearray
    slot_register: int
    mapper_kind: MapperKind
    mapper_state: dict[str, object]
    mapper2_kind: MapperKind
    mapper2_state: dict[str, object]
    vdp_vram: bytearray
    vdp_regs: list[int]
    vdp_status: int
    vdp_latch: int | None
    vdp_addr: int
    vdp_read_buf: int
    vdp_frame_count: int
    psg_regs: list[int]
    psg_latch: int
    psg_synth: dict[str, object]
    scc_state: dict[str, object] | None
    vdp_palette: list[int] | None
    ram_mapper_ram: bytearray | None
    ram_mapper_banks: list[int] | None
    sub_slot_reg: int | None
    cmd_regs: list[int] | None
    status2: int | None
    cmd_remaining: int | None
    fdc_state: dict[str, object] | None
    slot2_sub_slot_reg: int | None
    mapper2_subslot_kinds: list[MapperKind | None] | None
    mapper2_subslot_states: list[dict[str, object]] | None


# --- internal helpers ---------------------------------------------------------

def _cpu_regs_to_dict(machine: "Machine") -> dict[str, int]:
    r = machine.cpu.registers
    return {
        "A": r.A, "F": r.F, "BC": r.BC, "DE": r.DE, "HL": r.HL,
        "IX": r.IX, "IY": r.IY, "SP": r.SP, "PC": r.PC,
        "I": r.I, "R": r.R,
        "A_": r.A_, "F_": r.F_, "BC_": r.BC_, "DE_": r.DE_, "HL_": r.HL_,
    }


def _restore_cpu_regs(machine: "Machine", regs: dict[str, int]) -> None:
    r = machine.cpu.registers
    r.A = regs["A"]
    r.F = regs["F"]
    r.BC = regs["BC"]
    r.DE = regs["DE"]
    r.HL = regs["HL"]
    r.IX = regs["IX"]
    r.IY = regs["IY"]
    r.SP = regs["SP"]
    r.PC = regs["PC"]
    r.I = regs["I"]
    r.R = regs["R"]
    r.A_ = regs["A_"]
    r.F_ = regs["F_"]
    r.BC_ = regs["BC_"]
    r.DE_ = regs["DE_"]
    r.HL_ = regs["HL_"]


def _scc_to_dict(machine: "Machine") -> dict[str, object] | None:
    if machine.scc is None:
        return None
    return cast(dict[str, object], machine.scc.snapshot())


def _restore_scc(machine: "Machine", scc_state: dict[str, object] | None) -> None:
    if machine.scc is None or scc_state is None:
        return
    machine.scc.restore(scc_state)


def _fdc_to_dict(machine: "Machine") -> dict[str, object] | None:
    if machine.fdc is None:
        return None
    # Flush pending disk writes first so the disk-identity hash
    # FloppyDisk.snapshot() computes (via each DiskDrive.snapshot()) matches
    # the .dsk file's own on-disk bytes.
    machine.fdc.flush()
    return machine.fdc.snapshot()


def _restore_fdc(machine: "Machine", fdc_state: dict[str, object] | None) -> None:
    if (fdc_state is None) != (machine.fdc is None):
        raise ValueError(
            "FDC wiring mismatch: "
            f"running machine has {'an FDC' if machine.fdc is not None else 'no FDC'}, "
            f"saved state has {'FDC state' if fdc_state is not None else 'no FDC state'}"
        )
    if machine.fdc is None or fdc_state is None:
        return
    machine.fdc.restore(fdc_state)


def _restore_mapper2_subslots(machine: "Machine", snap: "MachineSnapshot") -> None:
    """Restore expanded slot 2's per-sub-slot mapper state (see
    openspec/changes/generalize-slot2-mapper-state). Checks the slot-2
    expansion wiring, then every assigned sub-slot's mapper kind, before
    restoring anything -- mirroring _restore_fdc's wiring-mismatch check
    and the "check every drive before restoring any" atomicity pattern
    FloppyDiskState.restore() uses."""
    running_enabled = machine.memory.slot2_sub_slot_enabled
    saved_enabled = snap.mapper2_subslot_kinds is not None
    if running_enabled != saved_enabled:
        raise ValueError(
            "slot 2 expansion wiring mismatch: "
            f"running machine has {'an expanded' if running_enabled else 'a flat'} slot 2, "
            f"saved state has {'an expanded' if saved_enabled else 'a flat'} slot 2"
        )
    if not running_enabled:
        return
    saved_kinds = snap.mapper2_subslot_kinds
    saved_states = snap.mapper2_subslot_states
    assert saved_kinds is not None
    assert saved_states is not None
    subslots = machine.memory._mapper2_subslots
    for i, sub_mapper in enumerate(subslots):
        running_kind = sub_mapper.kind if sub_mapper is not None else None
        saved_kind = saved_kinds[i]
        if running_kind != saved_kind:
            running_repr = repr(running_kind.value) if running_kind is not None else "None"
            saved_repr = repr(saved_kind.value) if saved_kind is not None else "None"
            raise ValueError(
                f"slot 2 sub-slot {i} mapper mismatch: "
                f"running {running_repr}, saved {saved_repr}"
            )
    for i, sub_mapper in enumerate(subslots):
        if sub_mapper is not None:
            sub_mapper.restore(saved_states[i])


def _snapshot_from_machine(machine: "Machine") -> MachineSnapshot:
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    mapper = machine.memory._mapper
    # Mapper.snapshot() returns Mapping[str, object] (see msx/mapper.py); at
    # runtime every implementer still returns a plain dict, only read here
    # to serialise, never mutated -- cast to match MachineSnapshot's field.
    mapper_state = cast(dict[str, object], mapper.snapshot())
    # Slot 2, same generic mechanism -- machine.memory._mapper2 always
    # exists (an empty FlatMapper when nothing is configured), mirroring
    # slot 1 exactly. See openspec/changes/generalize-slot2-mapper-state.
    mapper2 = machine.memory._mapper2
    mapper2_state = cast(dict[str, object], mapper2.snapshot())
    mapper2_subslot_kinds: list[MapperKind | None] | None
    mapper2_subslot_states: list[dict[str, object]] | None
    if machine.memory.slot2_sub_slot_enabled:
        mapper2_subslot_kinds = []
        mapper2_subslot_states = []
        for sub_mapper in machine.memory._mapper2_subslots:
            if sub_mapper is None:
                mapper2_subslot_kinds.append(None)
                mapper2_subslot_states.append({})
            else:
                mapper2_subslot_kinds.append(sub_mapper.kind)
                mapper2_subslot_states.append(cast(dict[str, object], sub_mapper.snapshot()))
    else:
        mapper2_subslot_kinds = None
        mapper2_subslot_states = None
    # Common VDP address/latch state — identical field names on both VDP types.
    vdp_latch = machine.vdp.latch
    vdp_addr = machine.vdp.addr
    vdp_read_buf = machine.vdp.read_buf
    if vdp9938 is not None:
        vdp_palette: list[int] | None = list(vdp9938.palette)
        # An MSX2 machine may have no RAM mapper; skip those fields when absent.
        rm = machine.memory.ram_mapper
        ram_mapper_ram: bytearray | None = bytearray(rm.ram) if rm is not None else None
        ram_mapper_banks: list[int] | None = list(rm.banks) if rm is not None else None
        sub_slot_reg: int | None = machine.memory.sub_slot_reg
        cmd_regs: list[int] | None = list(vdp9938.cmd_regs)
        status2: int | None = vdp9938._status2
        cmd_remaining: int | None = vdp9938._cmd_remaining
    else:
        vdp_palette = None
        ram_mapper_ram = None
        ram_mapper_banks = None
        sub_slot_reg = None
        cmd_regs = None
        status2 = None
        cmd_remaining = None
    # slot2_sub_slot_enabled is a machine/extension shape flag, independent
    # of MSX1/MSX2-ness (unlike sub_slot_reg above, which is tied to
    # vdp9938 presence) -- computed separately from the vdp9938 branch.
    slot2_sub_slot_reg: int | None = (
        machine.memory.slot2_sub_slot_reg if machine.memory.slot2_sub_slot_enabled else None
    )
    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type="msx2" if vdp9938 is not None else "msx1",
        cpu_regs=_cpu_regs_to_dict(machine),
        cpu_halted=machine.cpu.halted,
        cpu_iff1=machine.cpu.iff1,
        cpu_iff2=machine.cpu.iff2,
        cpu_int_pending=machine.cpu.int_pending,
        cpu_nmi_pending=machine.cpu.nmi_pending,
        cpu_im=machine.cpu.im,
        ram=bytearray(machine.memory.ram),
        slot_register=machine.memory.slot_register,
        mapper_kind=mapper.kind,
        mapper_state=mapper_state,
        mapper2_kind=mapper2.kind,
        mapper2_state=mapper2_state,
        vdp_vram=bytearray(machine.vdp.vram),
        vdp_regs=list(machine.vdp.regs),
        vdp_status=machine.vdp.status,
        vdp_latch=vdp_latch,
        vdp_addr=vdp_addr,
        vdp_read_buf=vdp_read_buf,
        vdp_frame_count=machine.vdp.frame_count,
        psg_regs=list(machine.psg.regs),
        psg_latch=machine.psg.latch,
        psg_synth=cast(dict[str, object], machine.psg.snapshot_synth()),
        scc_state=_scc_to_dict(machine),
        vdp_palette=vdp_palette,
        ram_mapper_ram=ram_mapper_ram,
        ram_mapper_banks=ram_mapper_banks,
        sub_slot_reg=sub_slot_reg,
        cmd_regs=cmd_regs,
        status2=status2,
        cmd_remaining=cmd_remaining,
        fdc_state=_fdc_to_dict(machine),
        slot2_sub_slot_reg=slot2_sub_slot_reg,
        mapper2_subslot_kinds=mapper2_subslot_kinds,
        mapper2_subslot_states=mapper2_subslot_states,
    )


def _restore_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    if snap.format_version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {snap.format_version}, "
            f"expected {CURRENT_FORMAT_VERSION}"
        )
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    expected_type = "msx2" if vdp9938 is not None else "msx1"
    if snap.machine_type != expected_type:
        raise ValueError(
            f"machine type mismatch: running {expected_type!r}, "
            f"saved {snap.machine_type!r}"
        )
    mapper = machine.memory._mapper
    if mapper.kind != snap.mapper_kind:
        raise ValueError(
            f"mapper mismatch: running {mapper.kind.value!r}, "
            f"saved {snap.mapper_kind.value!r}"
        )
    mapper2 = machine.memory._mapper2
    if mapper2.kind != snap.mapper2_kind:
        raise ValueError(
            f"slot 2 mapper mismatch: running {mapper2.kind.value!r}, "
            f"saved {snap.mapper2_kind.value!r}"
        )

    _restore_cpu_regs(machine, snap.cpu_regs)
    machine.cpu.halted = snap.cpu_halted
    machine.cpu.iff1 = snap.cpu_iff1
    machine.cpu.iff2 = snap.cpu_iff2
    machine.cpu.int_pending = snap.cpu_int_pending
    machine.cpu.nmi_pending = snap.cpu_nmi_pending
    machine.cpu.im = snap.cpu_im

    machine.memory.ram[:] = snap.ram
    machine.memory.set_slot_register(snap.slot_register)
    _restore_producer(
        f"mapper ({mapper.kind.value})", lambda: mapper.restore(snap.mapper_state)
    )
    _restore_producer(
        f"mapper2 ({mapper2.kind.value})", lambda: mapper2.restore(snap.mapper2_state)
    )

    machine.vdp.vram[:] = snap.vdp_vram
    machine.vdp.regs[:] = snap.vdp_regs
    machine.vdp.status = snap.vdp_status
    machine.vdp.frame_count = snap.vdp_frame_count
    # Common VDP address/latch state — identical field names on both VDP types.
    machine.vdp.latch = snap.vdp_latch
    machine.vdp.addr = snap.vdp_addr
    machine.vdp.read_buf = snap.vdp_read_buf
    if vdp9938 is not None:
        if snap.vdp_palette is not None:
            vdp9938.palette[:] = snap.vdp_palette
        rm = machine.memory.ram_mapper
        if rm is not None and snap.ram_mapper_ram is not None:
            rm.ram[:] = snap.ram_mapper_ram
            if snap.ram_mapper_banks is not None:
                rm.banks[:] = snap.ram_mapper_banks
        if snap.sub_slot_reg is not None:
            machine.memory.set_sub_slot_reg(snap.sub_slot_reg)
        if snap.cmd_regs is not None:
            vdp9938.cmd_regs[:] = snap.cmd_regs
        if snap.status2 is not None:
            vdp9938._status2 = snap.status2
        if snap.cmd_remaining is not None:
            vdp9938._cmd_remaining = snap.cmd_remaining

    machine.psg.regs[:] = snap.psg_regs
    machine.psg.latch = snap.psg_latch
    _restore_producer("psg", lambda: machine.psg.restore_synth(snap.psg_synth))
    _restore_producer("scc", lambda: _restore_scc(machine, snap.scc_state))
    _restore_producer("fdc", lambda: _restore_fdc(machine, snap.fdc_state))
    _restore_producer("mapper2_subslots", lambda: _restore_mapper2_subslots(machine, snap))
    if snap.slot2_sub_slot_reg is not None:
        machine.memory.set_slot2_sub_slot_reg(snap.slot2_sub_slot_reg)


# --- symlink helper -----------------------------------------------------------

def _update_symlink(link: Path, target: Path) -> None:
    """Atomically update (or create) a symlink to point at target."""
    tmp_link = link.with_suffix(link.suffix + ".tmp")
    try:
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        os.symlink(target.name, tmp_link)
        os.replace(tmp_link, link)
    except OSError as exc:
        print(f"warning: could not update symlink {link}: {exc}", file=sys.stderr)


# --- public API ---------------------------------------------------------------

def _sanitise_title(title: str) -> str:
    """Replace spaces with underscores and strip filesystem-unsafe characters."""
    title = title.replace(" ", "_")
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", title) or "save"


def _to_jsonable(node: object) -> object:
    """Recursively convert a snapshot dict to JSON-serialisable form.

    Byte blobs become ``{"__b64__": "<base64>"}``; lists and dicts recurse;
    scalars pass through unchanged.
    """
    if isinstance(node, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(node)).decode("ascii")}
    if isinstance(node, dict):
        return {k: _to_jsonable(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_to_jsonable(v) for v in node]
    return node


def _from_jsonable(node: object) -> object:
    """Inverse of _to_jsonable: restore byte blobs (as bytearray) and containers."""
    if isinstance(node, dict):
        if "__b64__" in node and len(node) == 1:
            return bytearray(base64.b64decode(node["__b64__"]))
        return {k: _from_jsonable(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_from_jsonable(v) for v in node]
    return node


def save_state(machine: "Machine", rgb_buf: bytes | bytearray, title: str) -> Path:
    """Serialise machine state and screenshot to saves/states/<title>_YYYYMMDD_HHMMSS.*

    Args:
        machine: Running machine to snapshot.
        rgb_buf: Current frame as 256×192 RGB24 bytearray.
        title: Human-readable title used in the filename.

    Returns:
        Path of the written .state file.
    """
    saves_dir = Path("saves") / "states"
    saves_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{_sanitise_title(title)}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    state_path = saves_dir / f"{stem}.state"
    png_path = saves_dir / f"{stem}.png"

    snap = _snapshot_from_machine(machine)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(_to_jsonable(asdict(snap)), f)

    from msx.machine import SCREEN_HEIGHT, SCREEN_WIDTH
    from msx.screenshot import write_rgb24_png
    write_rgb24_png(rgb_buf, SCREEN_WIDTH, SCREEN_HEIGHT, png_path)

    _update_symlink(saves_dir / "latest.state", state_path)
    _update_symlink(saves_dir / "latest.png", png_path)

    print(f"state saved: {state_path}")
    return state_path


def load_state(machine: "Machine", path: Path | None = None) -> None:
    """Restore machine state from a save file.

    Args:
        machine: Running machine to restore into (callbacks remain intact).
        path: Explicit .state file to load. When None, loads saves/states/latest.state.

    Raises:
        FileNotFoundError: If the target file does not exist.
        ValueError: If format version or mapper class does not match.
    """
    if path is None:
        link = Path("saves") / "states" / "latest.state"
        if not link.exists():
            raise FileNotFoundError(
                "no save state found: saves/states/latest.state does not exist"
            )
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
    # Validate the version before expanding fields into MachineSnapshot, so a
    # mismatch yields a clear error rather than a TypeError on unexpected keys.
    version = fields.get("format_version") if isinstance(fields, dict) else None
    if version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {version}, "
            f"expected {CURRENT_FORMAT_VERSION} ({resolved})"
        )
    # JSON round-trips mapper_kind/mapper2_kind (and each entry of
    # mapper2_subslot_kinds) as plain str/None (MapperKind's own str-Enum
    # values serialize directly, see save_state); convert them back to
    # MapperKind members here so _restore_snapshot's identity checks and
    # any later `.value` access see real enum members, not bare strings.
    if isinstance(fields, dict):
        if "mapper_kind" in fields:
            fields["mapper_kind"] = MapperKind(fields["mapper_kind"])
        if "mapper2_kind" in fields:
            fields["mapper2_kind"] = MapperKind(fields["mapper2_kind"])
        subslot_kinds = fields.get("mapper2_subslot_kinds")
        if isinstance(subslot_kinds, list):
            fields["mapper2_subslot_kinds"] = [
                MapperKind(k) if k is not None else None for k in subslot_kinds
            ]
    typed_fields = cast(_MachineSnapshotFields, fields)
    snap = MachineSnapshot(**typed_fields)
    _restore_snapshot(machine, snap)
    print(f"state loaded: {resolved}")
