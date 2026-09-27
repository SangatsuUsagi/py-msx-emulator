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
from dataclasses import MISSING, asdict, dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from msx.mapper import MapperKind
from msx.snapshot_schema import (
    I64_MAX,
    SchemaError,
    check_bool,
    check_bytes,
    check_int,
    check_int_list,
    check_object,
    check_optional_int,
    check_optional_object,
    check_str,
)
from msx.vdp.v9938 import V9938, V9938State, parse_v9938_state
from msx.vdp.vdp import VDP

if TYPE_CHECKING:
    from msx.machine import Machine
    from msx.mapper import Mapper

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
# Version 13: slot1_sub_slot_reg/mapper1_subslot_kinds/
#   mapper1_subslot_states added -- the identical mechanism
#   slot2_sub_slot_reg/mapper2_subslot_kinds/mapper2_subslot_states already
#   provides for slot 2's own expansion (version 9/11), now that slot 1 can
#   also be expanded (an extension needing an expanded slot 1, e.g. HBI-J1
#   or FM-PAC placed via --extension1). All three are None unless
#   machine.memory.slot1_sub_slot_enabled. See
#   openspec/changes/slot1-slot2-symmetrization.
# Version 14: every V9938-only field (palette, command registers and engine
#   progress, S#1/S#2/S#8/S#9, collision coordinates, the shared first-byte
#   latch) in one `vdp9938` object produced and validated by the V9938
#   (V9938.snapshot_state()/restore_state()); None for MSX1. No other field
#   changed.
CURRENT_FORMAT_VERSION: int = 14


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


def _restore_producer(producer: str, fn: Callable[[], object]) -> None:
    """Run one producer's parse_state or restore call, wrapping a
    malformed-data failure as StateLoadError(producer, ...) instead of
    letting it propagate raw."""
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
    vdp9938: V9938State | None = None  # V9938.snapshot_state()
    ram_mapper_ram: bytearray | None = None
    ram_mapper_banks: list[int] | None = None
    sub_slot_reg: int | None = None
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
    # Primary slot 1's own secondary slot register and expanded-slot-1
    # per-sub-slot mapper kind/state -- the identical mechanism the three
    # slot2_sub_slot_reg/mapper2_subslot_kinds/mapper2_subslot_states fields
    # above provide for slot 2, applied independently to slot 1. See
    # openspec/changes/slot1-slot2-symmetrization.
    slot1_sub_slot_reg: int | None = None
    mapper1_subslot_kinds: list[MapperKind | None] | None = None
    mapper1_subslot_states: list[dict[str, object]] | None = None


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


def _check_scc_wiring(machine: "Machine", scc_state: dict[str, object] | None) -> None:
    if (scc_state is None) != (machine.scc is None):
        raise ValueError(
            "SCC wiring mismatch: "
            f"running machine has {'an SCC' if machine.scc is not None else 'no SCC'}, "
            f"saved state has {'SCC state' if scc_state is not None else 'no SCC state'}"
        )


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


def _check_fdc_wiring(machine: "Machine", fdc_state: dict[str, object] | None) -> None:
    if (fdc_state is None) != (machine.fdc is None):
        raise ValueError(
            "FDC wiring mismatch: "
            f"running machine has {'an FDC' if machine.fdc is not None else 'no FDC'}, "
            f"saved state has {'FDC state' if fdc_state is not None else 'no FDC state'}"
        )


def _restore_fdc(machine: "Machine", fdc_state: dict[str, object] | None) -> None:
    if machine.fdc is None or fdc_state is None:
        return
    machine.fdc.restore(fdc_state)


def _check_expanded_subslots(
    slot_num: int,
    running_enabled: bool,
    subslots: list["Mapper | None"],
    saved_kinds: list[MapperKind | None] | None,
) -> None:
    """Check an expanded primary slot's expansion wiring, then every assigned
    sub-slot's mapper kind -- shared by slot 1 and slot 2 (see openspec/changes/
    slot1-slot2-symmetrization and, for slot 2 specifically, the earlier
    openspec/changes/generalize-slot2-mapper-state). Run by _validate_snapshot,
    before anything is restored."""
    saved_enabled = saved_kinds is not None
    if running_enabled != saved_enabled:
        raise ValueError(
            f"slot {slot_num} expansion wiring mismatch: "
            f"running machine has {'an expanded' if running_enabled else 'a flat'} "
            f"slot {slot_num}, saved state has "
            f"{'an expanded' if saved_enabled else 'a flat'} slot {slot_num}"
        )
    if not running_enabled:
        return
    assert saved_kinds is not None
    for i, sub_mapper in enumerate(subslots):
        running_kind = sub_mapper.kind if sub_mapper is not None else None
        saved_kind = saved_kinds[i]
        if running_kind != saved_kind:
            running_repr = repr(running_kind.value) if running_kind is not None else "None"
            saved_repr = repr(saved_kind.value) if saved_kind is not None else "None"
            raise ValueError(
                f"slot {slot_num} sub-slot {i} mapper mismatch: "
                f"running {running_repr}, saved {saved_repr}"
            )


def _restore_expanded_subslots(
    subslots: list["Mapper | None"], saved_states: list[dict[str, object]] | None
) -> None:
    """Restore an expanded primary slot's per-sub-slot mapper state (the
    wiring and kinds were checked by _check_expanded_subslots)."""
    if saved_states is None:
        return
    for i, sub_mapper in enumerate(subslots):
        if sub_mapper is not None:
            sub_mapper.restore(saved_states[i])


def _restore_mapper1_subslots(machine: "Machine", snap: "MachineSnapshot") -> None:
    _restore_expanded_subslots(machine.memory._mapper1_subslots, snap.mapper1_subslot_states)


def _restore_mapper2_subslots(machine: "Machine", snap: "MachineSnapshot") -> None:
    _restore_expanded_subslots(machine.memory._mapper2_subslots, snap.mapper2_subslot_states)


def _snapshot_expanded_subslots(
    sub_slot_enabled: bool, subslots: list["Mapper | None"]
) -> tuple[list[MapperKind | None] | None, list[dict[str, object]] | None]:
    """Snapshot an expanded primary slot's per-sub-slot mapper kind/state --
    shared by slot 1 and slot 2 (see _restore_expanded_subslots' own
    docstring for the precedent this generalizes)."""
    if not sub_slot_enabled:
        return None, None
    kinds: list[MapperKind | None] = []
    states: list[dict[str, object]] = []
    for sub_mapper in subslots:
        if sub_mapper is None:
            kinds.append(None)
            states.append({})
        else:
            kinds.append(sub_mapper.kind)
            states.append(cast(dict[str, object], sub_mapper.snapshot()))
    return kinds, states


def _snapshot_from_machine(machine: "Machine") -> MachineSnapshot:
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    mapper = machine.memory._mapper
    # Mapper.snapshot() returns Mapping[str, object] (see msx/mapper.py); at
    # runtime every implementer still returns a plain dict, only read here
    # to serialise, never mutated -- cast to match MachineSnapshot's field.
    mapper_state = cast(dict[str, object], mapper.snapshot())
    mapper1_subslot_kinds, mapper1_subslot_states = _snapshot_expanded_subslots(
        machine.memory.slot1_sub_slot_enabled, machine.memory._mapper1_subslots
    )
    # Slot 2, same generic mechanism -- machine.memory._mapper2 always
    # exists (an empty FlatMapper when nothing is configured), mirroring
    # slot 1 exactly. See openspec/changes/generalize-slot2-mapper-state.
    mapper2 = machine.memory._mapper2
    mapper2_state = cast(dict[str, object], mapper2.snapshot())
    mapper2_subslot_kinds, mapper2_subslot_states = _snapshot_expanded_subslots(
        machine.memory.slot2_sub_slot_enabled, machine.memory._mapper2_subslots
    )
    # Common VDP address/latch state — identical field names on both VDP types.
    vdp_latch = machine.vdp.latch
    vdp_addr = machine.vdp.addr
    vdp_read_buf = machine.vdp.read_buf
    if vdp9938 is not None:
        # Plain JSON-able dict; load_state validates it with parse_v9938_state().
        vdp9938_state: V9938State | None = vdp9938.snapshot_state()
        # An MSX2 machine may have no RAM mapper; skip those fields when absent.
        rm = machine.memory.ram_mapper
        ram_mapper_ram: bytearray | None = bytearray(rm.ram) if rm is not None else None
        ram_mapper_banks: list[int] | None = list(rm.banks) if rm is not None else None
        sub_slot_reg: int | None = machine.memory.sub_slot_reg
    else:
        vdp9938_state = None
        ram_mapper_ram = None
        ram_mapper_banks = None
        sub_slot_reg = None
    # slot1/slot2_sub_slot_enabled are machine/extension shape flags,
    # independent of MSX1/MSX2-ness (unlike sub_slot_reg above, which is
    # tied to vdp9938 presence) -- computed separately from the vdp9938
    # branch.
    slot1_sub_slot_reg: int | None = (
        machine.memory.slot1_sub_slot_reg if machine.memory.slot1_sub_slot_enabled else None
    )
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
        vdp9938=vdp9938_state,
        ram_mapper_ram=ram_mapper_ram,
        ram_mapper_banks=ram_mapper_banks,
        sub_slot_reg=sub_slot_reg,
        fdc_state=_fdc_to_dict(machine),
        slot2_sub_slot_reg=slot2_sub_slot_reg,
        mapper2_subslot_kinds=mapper2_subslot_kinds,
        mapper2_subslot_states=mapper2_subslot_states,
        slot1_sub_slot_reg=slot1_sub_slot_reg,
        mapper1_subslot_kinds=mapper1_subslot_kinds,
        mapper1_subslot_states=mapper1_subslot_states,
    )


_T = TypeVar("_T")

_CPU_REGS_8: tuple[str, ...] = ("A", "F", "I", "R", "A_", "F_")
_CPU_REGS_16: tuple[str, ...] = (
    "BC", "DE", "HL", "IX", "IY", "SP", "PC", "BC_", "DE_", "HL_",
)
_CPU_REG_KEYS: frozenset[str] = frozenset(_CPU_REGS_8 + _CPU_REGS_16)

_TMS_VRAM_SIZE = 0x4000
_V9938_VRAM_SIZE = 0x20000
_TMS_REG_COUNT = 8
_V9938_REG_COUNT = 28
_SUBSLOTS = 4


def _field(name: str, parse: Callable[[], _T]) -> _T:
    """Run one top-level field's parse, reporting a SchemaError as
    StateLoadError naming that field."""
    try:
        return parse()
    except SchemaError as exc:
        raise StateLoadError(name, exc) from exc


def _parse_cpu_regs(value: object) -> dict[str, int]:
    obj = check_object(value, "cpu_regs", _CPU_REG_KEYS)
    regs: dict[str, int] = {}
    for key in _CPU_REGS_8:
        regs[key] = check_int(obj[key], f"cpu_regs.{key}", 0, 0xFF)
    for key in _CPU_REGS_16:
        regs[key] = check_int(obj[key], f"cpu_regs.{key}", 0, 0xFFFF)
    return regs


def _parse_mapper_kind(value: object, label: str) -> MapperKind:
    text = check_str(value, label)
    try:
        return MapperKind(text)
    except ValueError:
        raise SchemaError(f"{label} is not a mapper kind: {text!r}") from None


def _parse_subslot_kinds(value: object, label: str) -> list[MapperKind | None] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != _SUBSLOTS:
        raise SchemaError(f"{label} must be a list of {_SUBSLOTS} entries, got {value!r}")
    kinds: list[MapperKind | None] = []
    for i, entry in enumerate(value):
        kinds.append(None if entry is None else _parse_mapper_kind(entry, f"{label}[{i}]"))
    return kinds


def _parse_subslot_states(value: object, label: str) -> list[dict[str, object]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != _SUBSLOTS:
        raise SchemaError(f"{label} must be a list of {_SUBSLOTS} objects, got {value!r}")
    states: list[dict[str, object]] = []
    for i, entry in enumerate(value):
        states.append(check_object(entry, f"{label}[{i}]"))
    return states


def _parse_vdp9938(value: object, msx2: bool, vdp_latch: int | None) -> V9938State | None:
    if not msx2:
        if value is not None:
            raise SchemaError("an MSX1 snapshot carries no vdp9938 state")
        return None
    if value is None:
        raise SchemaError("MSX2 snapshot has no vdp9938 state")
    state = parse_v9938_state(value)
    # The common `vdp_latch` is the V9938 latch's port-0x99 view; reject a file
    # where the two disagree instead of letting restore order pick one.
    expected = state["first_byte"] if state["control_pending"] else None
    if vdp_latch != expected:
        raise SchemaError(
            f"vdp_latch {vdp_latch!r} disagrees with the V9938 latch ({expected!r})"
        )
    return state


def _check_snapshot_keys(data: dict[str, object], resolved: Path) -> None:
    """Refuse a file whose top-level keys are not MachineSnapshot's: an
    unknown key, or a missing key that has no default."""
    known: set[str] = set()
    missing: list[str] = []
    for f in dataclass_fields(MachineSnapshot):
        known.add(f.name)
        if f.name not in data and f.default is MISSING and f.default_factory is MISSING:
            missing.append(f.name)
    unknown: list[str] = []
    for key in data:
        if key not in known:
            unknown.append(key)
    if unknown or missing:
        raise ValueError(
            f"incompatible state file layout ({resolved}): "
            f"unknown keys {sorted(unknown)}, missing keys {sorted(missing)}"
        )


def parse_snapshot(decoded: object, resolved: Path) -> MachineSnapshot:
    """Turn a decoded state file into a MachineSnapshot, checking the format
    version, then the key set, then every top-level value's type, range and
    (where the snapshot fixes it) length. A bad value raises StateLoadError
    naming its field; the content of producer-owned objects is left to each
    producer's restore(). Nothing here touches a machine."""
    if not isinstance(decoded, dict):
        raise ValueError(f"corrupt or unsupported save state: {resolved} (not an object)")
    data = cast(dict[str, object], decoded)
    version = data.get("format_version")
    if version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {version}, "
            f"expected {CURRENT_FORMAT_VERSION} ({resolved})"
        )
    _check_snapshot_keys(data, resolved)

    def machine_type_of() -> str:
        text = check_str(data["machine_type"], "machine_type")
        if text != "msx1" and text != "msx2":
            raise SchemaError(f"machine_type must be 'msx1' or 'msx2', got {text!r}")
        return text

    machine_type = _field("machine_type", machine_type_of)
    msx2 = machine_type == "msx2"
    vram_size = _V9938_VRAM_SIZE if msx2 else _TMS_VRAM_SIZE
    reg_count = _V9938_REG_COUNT if msx2 else _TMS_REG_COUNT
    addr_max = 0x1FFFF if msx2 else 0x3FFF
    vdp_latch = _field(
        "vdp_latch", lambda: check_optional_int(data["vdp_latch"], "vdp_latch", 0, 0xFF)
    )

    def get(key: str) -> object:
        return data.get(key)  # every field with no value in the file defaults to None

    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type=machine_type,
        cpu_regs=_field("cpu_regs", lambda: _parse_cpu_regs(data["cpu_regs"])),
        cpu_halted=_field("cpu_halted", lambda: check_bool(data["cpu_halted"], "cpu_halted")),
        cpu_iff1=_field("cpu_iff1", lambda: check_bool(data["cpu_iff1"], "cpu_iff1")),
        cpu_iff2=_field("cpu_iff2", lambda: check_bool(data["cpu_iff2"], "cpu_iff2")),
        cpu_int_pending=_field(
            "cpu_int_pending", lambda: check_bool(data["cpu_int_pending"], "cpu_int_pending")
        ),
        cpu_nmi_pending=_field(
            "cpu_nmi_pending", lambda: check_bool(data["cpu_nmi_pending"], "cpu_nmi_pending")
        ),
        cpu_im=_field("cpu_im", lambda: check_int(data["cpu_im"], "cpu_im", 0, 2)),
        ram=_field("ram", lambda: check_bytes(data["ram"], "ram")),
        slot_register=_field(
            "slot_register", lambda: check_int(data["slot_register"], "slot_register", 0, 0xFF)
        ),
        mapper_kind=_field(
            "mapper_kind", lambda: _parse_mapper_kind(data["mapper_kind"], "mapper_kind")
        ),
        mapper_state=_field(
            "mapper_state", lambda: check_object(data["mapper_state"], "mapper_state")
        ),
        mapper2_kind=_field(
            "mapper2_kind", lambda: _parse_mapper_kind(data["mapper2_kind"], "mapper2_kind")
        ),
        mapper2_state=_field(
            "mapper2_state", lambda: check_object(data["mapper2_state"], "mapper2_state")
        ),
        vdp_vram=_field("vdp_vram", lambda: check_bytes(data["vdp_vram"], "vdp_vram", vram_size)),
        vdp_regs=_field(
            "vdp_regs", lambda: check_int_list(data["vdp_regs"], "vdp_regs", reg_count, 0, 0xFF)
        ),
        vdp_status=_field(
            "vdp_status", lambda: check_int(data["vdp_status"], "vdp_status", 0, 0xFF)
        ),
        vdp_latch=vdp_latch,
        vdp_addr=_field("vdp_addr", lambda: check_int(data["vdp_addr"], "vdp_addr", 0, addr_max)),
        vdp_read_buf=_field(
            "vdp_read_buf", lambda: check_int(data["vdp_read_buf"], "vdp_read_buf", 0, 0xFF)
        ),
        vdp_frame_count=_field(
            "vdp_frame_count",
            lambda: check_int(data["vdp_frame_count"], "vdp_frame_count", 0, I64_MAX),
        ),
        psg_regs=_field(
            "psg_regs", lambda: check_int_list(data["psg_regs"], "psg_regs", 16, 0, 0xFF)
        ),
        psg_latch=_field("psg_latch", lambda: check_int(data["psg_latch"], "psg_latch", 0, 0x0F)),
        psg_synth=_field("psg_synth", lambda: check_object(data["psg_synth"], "psg_synth")),
        scc_state=_field(
            "scc_state", lambda: check_optional_object(data["scc_state"], "scc_state")
        ),
        vdp9938=_field("vdp9938", lambda: _parse_vdp9938(get("vdp9938"), msx2, vdp_latch)),
        ram_mapper_ram=_field(
            "ram_mapper_ram",
            lambda: None if get("ram_mapper_ram") is None
            else check_bytes(get("ram_mapper_ram"), "ram_mapper_ram"),
        ),
        ram_mapper_banks=_field(
            "ram_mapper_banks",
            lambda: None if get("ram_mapper_banks") is None
            else check_int_list(get("ram_mapper_banks"), "ram_mapper_banks", 4, 0, 0xFF),
        ),
        sub_slot_reg=_field(
            "sub_slot_reg",
            lambda: check_optional_int(get("sub_slot_reg"), "sub_slot_reg", 0, 0xFF),
        ),
        fdc_state=_field(
            "fdc_state", lambda: check_optional_object(get("fdc_state"), "fdc_state")
        ),
        slot2_sub_slot_reg=_field(
            "slot2_sub_slot_reg",
            lambda: check_optional_int(get("slot2_sub_slot_reg"), "slot2_sub_slot_reg", 0, 0xFF),
        ),
        mapper2_subslot_kinds=_field(
            "mapper2_subslot_kinds",
            lambda: _parse_subslot_kinds(get("mapper2_subslot_kinds"), "mapper2_subslot_kinds"),
        ),
        mapper2_subslot_states=_field(
            "mapper2_subslot_states",
            lambda: _parse_paired_states(
                get("mapper2_subslot_kinds"), get("mapper2_subslot_states"),
                "mapper2_subslot_states",
            ),
        ),
        slot1_sub_slot_reg=_field(
            "slot1_sub_slot_reg",
            lambda: check_optional_int(get("slot1_sub_slot_reg"), "slot1_sub_slot_reg", 0, 0xFF),
        ),
        mapper1_subslot_kinds=_field(
            "mapper1_subslot_kinds",
            lambda: _parse_subslot_kinds(get("mapper1_subslot_kinds"), "mapper1_subslot_kinds"),
        ),
        mapper1_subslot_states=_field(
            "mapper1_subslot_states",
            lambda: _parse_paired_states(
                get("mapper1_subslot_kinds"), get("mapper1_subslot_states"),
                "mapper1_subslot_states",
            ),
        ),
    )


def _parse_paired_states(
    kinds: object, states: object, label: str
) -> list[dict[str, object]] | None:
    """A sub-slot state list is present exactly when its kind list is."""
    if (kinds is None) != (states is None):
        raise SchemaError(f"{label} must be present exactly when its kind list is")
    return _parse_subslot_states(states, label)


def _validate_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    """Every check against the running machine that must pass before
    _restore_snapshot writes anything: format version, machine type, mapper
    kinds, RAM and RAM-mapper sizes, the FDC, expanded-slot and SCC wiring,
    and every producer object's parse_state.
    (parse_snapshot has already checked everything the snapshot alone decides.)"""
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
    ram_len = len(machine.memory.ram)
    if len(snap.ram) != ram_len:
        raise StateLoadError(
            "ram", ValueError(f"ram must be {ram_len} bytes, got {len(snap.ram)}")
        )
    rm = machine.memory.ram_mapper
    if rm is not None and snap.ram_mapper_ram is not None and len(snap.ram_mapper_ram) != len(
        rm.ram
    ):
        raise StateLoadError(
            "ram_mapper_ram",
            ValueError(
                f"ram_mapper_ram must be {len(rm.ram)} bytes, got {len(snap.ram_mapper_ram)}"
            ),
        )
    _check_fdc_wiring(machine, snap.fdc_state)
    _check_expanded_subslots(
        1, machine.memory.slot1_sub_slot_enabled, machine.memory._mapper1_subslots,
        snap.mapper1_subslot_kinds,
    )
    _check_expanded_subslots(
        2, machine.memory.slot2_sub_slot_enabled, machine.memory._mapper2_subslots,
        snap.mapper2_subslot_kinds,
    )
    _check_scc_wiring(machine, snap.scc_state)
    _parse_producer_objects(machine, snap)


def _parse_subslot_producers(
    producer: str,
    label: str,
    subslots: list["Mapper | None"],
    states: list[dict[str, object]] | None,
) -> None:
    if states is None:
        return
    for i, sub_mapper in enumerate(subslots):
        if sub_mapper is not None:
            state = states[i]
            sub_label = f"{label}[{i}]"
            _restore_producer(producer, lambda: sub_mapper.parse_state(state, sub_label))


def _parse_producer_objects(machine: "Machine", snap: MachineSnapshot) -> None:
    """Validate every producer object the snapshot carries with its
    producer's parse_state, before anything is restored (the wiring checks
    have already matched each object to a running producer)."""
    mapper = machine.memory._mapper
    mapper2 = machine.memory._mapper2
    _restore_producer(
        f"mapper ({mapper.kind.value})",
        lambda: mapper.parse_state(snap.mapper_state, "mapper_state"),
    )
    _restore_producer(
        f"mapper2 ({mapper2.kind.value})",
        lambda: mapper2.parse_state(snap.mapper2_state, "mapper2_state"),
    )
    _parse_subslot_producers(
        "mapper1_subslots", "mapper1_subslot_states",
        machine.memory._mapper1_subslots, snap.mapper1_subslot_states,
    )
    _parse_subslot_producers(
        "mapper2_subslots", "mapper2_subslot_states",
        machine.memory._mapper2_subslots, snap.mapper2_subslot_states,
    )
    _restore_producer(
        "psg", lambda: machine.psg.parse_synth_state(snap.psg_synth, "psg_synth")
    )
    scc = machine.scc
    scc_state = snap.scc_state
    if scc is not None and scc_state is not None:
        _restore_producer("scc", lambda: scc.parse_state(scc_state, "scc_state"))
    fdc = machine.fdc
    fdc_state = snap.fdc_state
    if fdc is not None and fdc_state is not None:
        _restore_producer("fdc", lambda: fdc.parse_state(fdc_state, "fdc_state"))


def _restore_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    _validate_snapshot(machine, snap)
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    mapper = machine.memory._mapper
    mapper2 = machine.memory._mapper2

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
    # Common VDP address state — identical field names on both VDP types. The
    # V9938's port latch is restored with the rest of its own state.
    machine.vdp.addr = snap.vdp_addr
    machine.vdp.read_buf = snap.vdp_read_buf
    if vdp9938 is None:
        assert isinstance(machine.vdp, VDP)
        machine.vdp.latch = snap.vdp_latch
    else:
        assert snap.vdp9938 is not None  # parse_snapshot requires it for MSX2
        vdp9938.restore_state(snap.vdp9938)
        rm = machine.memory.ram_mapper
        if rm is not None and snap.ram_mapper_ram is not None:
            rm.ram[:] = snap.ram_mapper_ram
            if snap.ram_mapper_banks is not None:
                rm.banks[:] = snap.ram_mapper_banks
        if snap.sub_slot_reg is not None:
            machine.memory.set_sub_slot_reg(snap.sub_slot_reg)
    # The VDP's cached /INT level is what the scanline loop feeds the CPU, so
    # it must follow the restored status/registers (incl. V9938 S#1 FH).
    machine.vdp.update_irq()

    machine.psg.regs[:] = snap.psg_regs
    machine.psg.latch = snap.psg_latch
    _restore_producer("psg", lambda: machine.psg.restore_synth(snap.psg_synth))
    _restore_producer("scc", lambda: _restore_scc(machine, snap.scc_state))
    _restore_producer("fdc", lambda: _restore_fdc(machine, snap.fdc_state))
    _restore_producer("mapper1_subslots", lambda: _restore_mapper1_subslots(machine, snap))
    _restore_producer("mapper2_subslots", lambda: _restore_mapper2_subslots(machine, snap))
    if snap.slot1_sub_slot_reg is not None:
        machine.memory.set_slot1_sub_slot_reg(snap.slot1_sub_slot_reg)
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

    snap = parse_snapshot(_from_jsonable(data), resolved)
    _restore_snapshot(machine, snap)
    print(f"state loaded: {resolved}")
