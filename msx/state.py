"""Encode and decode a complete machine snapshot as versioned JSON bytes.

The snapshot is a stdlib-only JSON container (no pickle) written in RFC 8785
(JCS) canonical form: sorted keys, no whitespace, UTF-8, integers only (each
within +-SAFE_INT_MAX). Byte blobs (RAM, VRAM, SRAM) are plain base64
strings, which each field's parser decodes. `format_version` guards
compatibility.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from typing import TYPE_CHECKING, TypeVar, cast

from msx.mapper import MapperKind
from msx.snapshot_schema import (
    SAFE_INT_MAX,
    SchemaError,
    SnapshotMismatchError,
    check_bool,
    check_bytes,
    check_int,
    check_int_list,
    check_list,
    check_object,
    check_optional_bytes,
    check_optional_int,
    check_optional_object,
    check_str,
)
from msx.vdp.v9938 import V9938, V9938State, parse_v9938_state
from msx.vdp.vdp import VDP

if TYPE_CHECKING:
    from msx.fdc.interface import FloppyDiskParsed
    from msx.machine import Machine
    from msx.mapper import Mapper
    from msx.mouse import MouseState
    from msx.psg import PsgSynthState
    from msx.rtc import RtcState
    from msx.scc import SccState

_T = TypeVar("_T")

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
# Version 15: slot0_ram: bytearray | None added (the optional flat RAM
#   region in primary slot 0, e.g. Sony HB-10's 16 KB page 3 -- see
#   openspec/specs/memory-slot-bus's "Slot 0 MAY host flat RAM at a
#   configured page" Requirement). None unless machine.memory.slot0_ram is
#   configured.
# Version 16: rtc_state and mouse_state added (the RTC's start time,
#   elapsed emulated cycles and register blocks; the mouse's scan state),
#   each None when the machine has no RTC / no attached mouse. See
#   openspec/changes/enforce-core-boundary.
# Version 17: RFC 8785 canonical JSON; blobs as plain base64 strings (no
#   {"__b64__": ...} wrapper); floats, NaN/Infinity, duplicate keys and
#   integers beyond +-(2**53 - 1) rejected. See
#   openspec/changes/portable-snapshot-json.
CURRENT_FORMAT_VERSION: int = 17


class StateLoadError(ValueError):
    """A save-state value was rejected while loading: a top-level field
    (parse_snapshot) or a producer object (its producer's parse_state, run by
    _validate_snapshot), always before anything is restored.

    `producer` names the field or producer. Distinct from the plain
    ValueErrors raised for a file of another format version or layout, and
    from SnapshotMismatchError (machine type, mapper kind, wiring, mounted
    disk), both of which already carry clear top-level messages, so a
    corrupted or hand-edited .state file fails with one predictable,
    catchable error naming the part that broke. The original exception is
    chained via __cause__.
    """

    def __init__(self, producer: str, cause: Exception) -> None:
        super().__init__(f"invalid {producer} state: {cause}")
        self.producer = producer


def _parse_producer(producer: str, parse: Callable[[], _T]) -> _T:
    """Run one producer's parse_state, reporting a rejection (any ValueError,
    including SchemaError and identity mismatches) as StateLoadError(producer)."""
    try:
        return parse()
    except ValueError as exc:
        raise StateLoadError(producer, exc) from exc


# The snapshot layout (format 17): one dataclass per JSON object, so
# asdict(MachineSnapshot) is the document and parse_snapshot builds the same
# tree. Every field is always present in the file; None (JSON null) means the
# machine has no such part. msx/snapshot.schema.json states the same layout
# language-neutrally. Producer-owned objects (mapper / PSG synth / SCC / FDC /
# RTC / mouse state) stay dict[str, object] here: each is validated into its
# producer's typed state by that producer's parse_state in _validate_snapshot,
# before anything is restored, and the typed results (_ParsedProducers) are
# what _restore_snapshot applies.


@dataclass
class CpuSnapshot:
    registers: dict[str, int]
    halted: bool
    iff1: bool
    iff2: bool
    int_pending: bool
    nmi_pending: bool
    im: int


@dataclass
class RamMapperSnapshot:
    ram: bytearray
    banks: list[int]  # one bank register per 16 KB page


@dataclass
class MemorySnapshot:
    ram: bytearray
    primary_slot_register: int
    # Optional flat RAM region in primary slot 0 (e.g. Sony HB-10's 16 KB
    # page 3), None unless machine.memory.slot0_ram is configured. See
    # openspec/specs/memory-slot-bus's "Slot 0 MAY host flat RAM at a
    # configured page" Requirement.
    slot0_ram: bytearray | None
    # MSX2 only; None for MSX1 and for an MSX2 machine without a RAM mapper.
    ram_mapper: RamMapperSnapshot | None


@dataclass
class MappedSlot:
    """A mapper's persisted identity and state. `kind` is the closed
    MapperKind enum (msx/mapper.py), not the mapper's Python class name, so
    a class rename does not change save-state compatibility (see
    openspec/changes/mapper-state-tagged-union)."""

    kind: MapperKind
    state: dict[str, object]


@dataclass
class SlotSnapshot:
    """Primary slot 1 or 2. `mapper` is always present (the slot's flat
    mapper, an empty FlatMapper when nothing is configured). When the slot
    is expanded (an extension such as HBI-J1 or FM-PAC placed in an expanded
    slot), `secondary_slot_register` is its own secondary slot register and
    `subslots` holds one entry per sub-slot (None for an unassigned one);
    otherwise both are None. See openspec/changes/
    slot1-slot2-symmetrization and generalize-slot2-mapper-state."""

    mapper: MappedSlot
    secondary_slot_register: int | None
    subslots: list[MappedSlot | None] | None


@dataclass
class Slot3Snapshot:
    # MSX2's slot 3 secondary slot register; None for MSX1.
    secondary_slot_register: int | None


@dataclass
class SlotsSnapshot:
    slot1: SlotSnapshot
    slot2: SlotSnapshot
    slot3: Slot3Snapshot


@dataclass
class VdpSnapshot:
    vram: bytearray
    registers: list[int]
    status: int
    latch: int | None
    address: int
    read_buffer: int
    frame_count: int
    # Every V9938-only field (palette, command registers and engine
    # progress, S#1/S#2/S#8/S#9, collision coordinates, the shared
    # first-byte latch), produced and validated by the V9938; None for MSX1.
    v9938: V9938State | None


@dataclass
class PsgSnapshot:
    registers: list[int]
    latch: int
    synth: dict[str, object]  # PSG.snapshot_synth()


@dataclass
class MachineSnapshot:
    format_version: int
    machine_type: str  # "msx1" or "msx2"
    cpu: CpuSnapshot
    memory: MemorySnapshot
    slots: SlotsSnapshot
    vdp: VdpSnapshot
    psg: PsgSnapshot
    # Each None when the machine has no such device: SCC.snapshot(),
    # FloppyDisk.snapshot(), RTC.snapshot(), MouseDevice.snapshot().
    scc: dict[str, object] | None
    fdc: dict[str, object] | None
    rtc: dict[str, object] | None
    mouse: dict[str, object] | None


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


def _rtc_to_dict(machine: "Machine") -> dict[str, object] | None:
    if machine.rtc is None:
        return None
    return cast(dict[str, object], machine.rtc.snapshot())


def _mouse_to_dict(machine: "Machine") -> dict[str, object] | None:
    mouse = machine.psg.mouse
    if mouse is None:
        return None
    return cast(dict[str, object], mouse.snapshot(machine.cycle_count))


def _check_optional_wiring(
    name: str, running: bool, state: dict[str, object] | None
) -> None:
    if (state is None) == running:
        raise SnapshotMismatchError(
            f"{name} wiring mismatch: "
            f"running machine has {'a' if running else 'no'} {name}, "
            f"saved state has {name + ' state' if state is not None else 'no ' + name + ' state'}"
        )


def _fdc_to_dict(machine: "Machine") -> dict[str, object] | None:
    if machine.fdc is None:
        return None
    return machine.fdc.snapshot()


def _check_slot0_ram_wiring(machine: "Machine", slot0_ram: bytearray | None) -> None:
    running = machine.memory.slot0_ram
    if (slot0_ram is None) != (running is None):
        raise SnapshotMismatchError(
            "slot 0 RAM wiring mismatch: "
            f"running machine has {'slot 0 RAM' if running is not None else 'no slot 0 RAM'}, "
            f"saved state has {'slot 0 RAM' if slot0_ram is not None else 'no slot 0 RAM'}"
        )
    if running is not None and slot0_ram is not None and len(slot0_ram) != len(running):
        raise StateLoadError(
            "memory.slot0_ram",
            ValueError(f"memory.slot0_ram must be {len(running)} bytes, got {len(slot0_ram)}"),
        )


def _check_slot_mapper(slot_num: int, running: "Mapper", saved: MappedSlot) -> None:
    if running.kind != saved.kind:
        prefix = "mapper mismatch" if slot_num == 1 else f"slot {slot_num} mapper mismatch"
        raise SnapshotMismatchError(
            f"{prefix}: running {running.kind.value!r}, saved {saved.kind.value!r}"
        )


def _check_expanded_subslots(
    slot_num: int,
    running_enabled: bool,
    subslots: list["Mapper | None"],
    saved: list[MappedSlot | None] | None,
) -> None:
    """Check an expanded primary slot's expansion wiring, then every assigned
    sub-slot's mapper kind -- shared by slot 1 and slot 2 (see openspec/changes/
    slot1-slot2-symmetrization and, for slot 2 specifically, the earlier
    openspec/changes/generalize-slot2-mapper-state). Run by _validate_snapshot,
    before anything is restored."""
    saved_enabled = saved is not None
    if running_enabled != saved_enabled:
        raise SnapshotMismatchError(
            f"slot {slot_num} expansion wiring mismatch: "
            f"running machine has {'an expanded' if running_enabled else 'a flat'} "
            f"slot {slot_num}, saved state has "
            f"{'an expanded' if saved_enabled else 'a flat'} slot {slot_num}"
        )
    if saved is None:
        return
    for i, sub_mapper in enumerate(subslots):
        running_kind = sub_mapper.kind if sub_mapper is not None else None
        saved_entry = saved[i]
        saved_kind = saved_entry.kind if saved_entry is not None else None
        if running_kind != saved_kind:
            running_repr = repr(running_kind.value) if running_kind is not None else "None"
            saved_repr = repr(saved_kind.value) if saved_kind is not None else "None"
            raise SnapshotMismatchError(
                f"slot {slot_num} sub-slot {i} mapper mismatch: "
                f"running {running_repr}, saved {saved_repr}"
            )


def _mapped(mapper: "Mapper") -> MappedSlot:
    # Mapper.snapshot() returns Mapping[str, object] (see msx/mapper.py); at
    # runtime every implementer still returns a plain dict, only read here
    # to serialise, never mutated -- cast to match MappedSlot's field.
    return MappedSlot(mapper.kind, cast(dict[str, object], mapper.snapshot()))


def _snapshot_slot(
    mapper: "Mapper", sub_slot_enabled: bool, sub_slot_reg: int, subslots: list["Mapper | None"]
) -> SlotSnapshot:
    """Snapshot primary slot 1 or 2: its flat mapper and, when expanded, its
    secondary slot register and per-sub-slot mappers."""
    if not sub_slot_enabled:
        return SlotSnapshot(_mapped(mapper), None, None)
    entries: list[MappedSlot | None] = []
    for sub_mapper in subslots:
        entries.append(_mapped(sub_mapper) if sub_mapper is not None else None)
    return SlotSnapshot(_mapped(mapper), sub_slot_reg, entries)


def _snapshot_from_machine(machine: "Machine") -> MachineSnapshot:
    memory = machine.memory
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    if vdp9938 is not None:
        # Plain JSON-able dict; load_state validates it with parse_v9938_state().
        vdp9938_state: V9938State | None = vdp9938.snapshot_state()
        # An MSX2 machine may have no RAM mapper; skip it when absent.
        rm = memory.ram_mapper
        ram_mapper: RamMapperSnapshot | None = (
            RamMapperSnapshot(bytearray(rm.ram), list(rm.banks)) if rm is not None else None
        )
        slot3_reg: int | None = memory.sub_slot_reg
    else:
        vdp9938_state = None
        ram_mapper = None
        slot3_reg = None
    slot0_ram: bytearray | None = (
        bytearray(memory.slot0_ram) if memory.slot0_ram is not None else None
    )
    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type="msx2" if vdp9938 is not None else "msx1",
        cpu=CpuSnapshot(
            registers=_cpu_regs_to_dict(machine),
            halted=machine.cpu.halted,
            iff1=machine.cpu.iff1,
            iff2=machine.cpu.iff2,
            int_pending=machine.cpu.int_pending,
            nmi_pending=machine.cpu.nmi_pending,
            im=machine.cpu.im,
        ),
        memory=MemorySnapshot(
            ram=bytearray(memory.ram),
            primary_slot_register=memory.slot_register,
            slot0_ram=slot0_ram,
            ram_mapper=ram_mapper,
        ),
        # slot1/slot2_sub_slot_enabled are machine/extension shape flags,
        # independent of MSX1/MSX2-ness (unlike slot 3's register, which is
        # tied to vdp9938 presence).
        slots=SlotsSnapshot(
            slot1=_snapshot_slot(
                memory._mapper, memory.slot1_sub_slot_enabled, memory.slot1_sub_slot_reg,
                memory._mapper1_subslots,
            ),
            slot2=_snapshot_slot(
                memory._mapper2, memory.slot2_sub_slot_enabled, memory.slot2_sub_slot_reg,
                memory._mapper2_subslots,
            ),
            slot3=Slot3Snapshot(slot3_reg),
        ),
        vdp=VdpSnapshot(
            vram=bytearray(machine.vdp.vram),
            registers=list(machine.vdp.regs),
            status=machine.vdp.status,
            latch=machine.vdp.latch,
            address=machine.vdp.addr,
            read_buffer=machine.vdp.read_buf,
            frame_count=machine.vdp.frame_count,
            v9938=vdp9938_state,
        ),
        psg=PsgSnapshot(
            registers=list(machine.psg.regs),
            latch=machine.psg.latch,
            synth=cast(dict[str, object], machine.psg.snapshot_synth()),
        ),
        scc=_scc_to_dict(machine),
        fdc=_fdc_to_dict(machine),
        rtc=_rtc_to_dict(machine),
        mouse=_mouse_to_dict(machine),
    )


_CPU_REGS_8: tuple[str, ...] = ("A", "F", "I", "R", "A_", "F_")
_CPU_REGS_16: tuple[str, ...] = (
    "BC", "DE", "HL", "IX", "IY", "SP", "PC", "BC_", "DE_", "HL_",
)
_CPU_REG_KEYS: frozenset[str] = frozenset(_CPU_REGS_8 + _CPU_REGS_16)


def _keys_of(cls: type) -> frozenset[str]:
    """The JSON object keys of a snapshot dataclass: its field names."""
    names: list[str] = []
    for f in dataclass_fields(cls):
        names.append(f.name)
    return frozenset(names)


_TOP_KEYS = _keys_of(MachineSnapshot)
_CPU_KEYS = _keys_of(CpuSnapshot)
_MEMORY_KEYS = _keys_of(MemorySnapshot)
_RAM_MAPPER_KEYS = _keys_of(RamMapperSnapshot)
_MAPPED_KEYS = _keys_of(MappedSlot)
_SLOT_KEYS = _keys_of(SlotSnapshot)
_SLOT3_KEYS = _keys_of(Slot3Snapshot)
_SLOTS_KEYS = _keys_of(SlotsSnapshot)
_VDP_KEYS = _keys_of(VdpSnapshot)
_PSG_KEYS = _keys_of(PsgSnapshot)

_TMS_VRAM_SIZE = 0x4000
_V9938_VRAM_SIZE = 0x20000
_TMS_REG_COUNT = 8
_V9938_REG_COUNT = 28
_PSG_REG_COUNT = 16
_RAM_MAPPER_PAGES = 4  # the four 16 KB pages a RAM mapper bank register serves
_SUBSLOTS = 4


def _field(name: str, parse: Callable[[], _T]) -> _T:
    """Run one top-level field's parse, reporting a SchemaError (whose
    message names the JSON path) as StateLoadError naming that field."""
    try:
        return parse()
    except SchemaError as exc:
        raise StateLoadError(name, exc) from exc


def _byte(value: object, label: str) -> int:
    return check_int(value, label, 0, 0xFF)


def _parse_cpu(value: object) -> CpuSnapshot:
    obj = check_object(value, "cpu", _CPU_KEYS)
    regs_obj = check_object(obj["registers"], "cpu.registers", _CPU_REG_KEYS)
    regs: dict[str, int] = {}
    for key in _CPU_REGS_8:
        regs[key] = check_int(regs_obj[key], f"cpu.registers.{key}", 0, 0xFF)
    for key in _CPU_REGS_16:
        regs[key] = check_int(regs_obj[key], f"cpu.registers.{key}", 0, 0xFFFF)
    return CpuSnapshot(
        registers=regs,
        halted=check_bool(obj["halted"], "cpu.halted"),
        iff1=check_bool(obj["iff1"], "cpu.iff1"),
        iff2=check_bool(obj["iff2"], "cpu.iff2"),
        int_pending=check_bool(obj["int_pending"], "cpu.int_pending"),
        nmi_pending=check_bool(obj["nmi_pending"], "cpu.nmi_pending"),
        im=check_int(obj["im"], "cpu.im", 0, 2),
    )


def _parse_memory(value: object, msx2: bool) -> MemorySnapshot:
    obj = check_object(value, "memory", _MEMORY_KEYS)
    ram_mapper: RamMapperSnapshot | None = None
    if obj["ram_mapper"] is not None:
        if not msx2:
            raise SchemaError("memory.ram_mapper must be null in an MSX1 snapshot")
        rm = check_object(obj["ram_mapper"], "memory.ram_mapper", _RAM_MAPPER_KEYS)
        ram_mapper = RamMapperSnapshot(
            ram=check_bytes(rm["ram"], "memory.ram_mapper.ram"),
            banks=check_int_list(
                rm["banks"], "memory.ram_mapper.banks", _RAM_MAPPER_PAGES, 0, 0xFF
            ),
        )
    return MemorySnapshot(
        ram=check_bytes(obj["ram"], "memory.ram"),
        primary_slot_register=_byte(obj["primary_slot_register"], "memory.primary_slot_register"),
        slot0_ram=check_optional_bytes(obj["slot0_ram"], "memory.slot0_ram"),
        ram_mapper=ram_mapper,
    )


def _parse_mapped(value: object, label: str) -> MappedSlot:
    obj = check_object(value, label, _MAPPED_KEYS)
    text = check_str(obj["kind"], f"{label}.kind")
    try:
        kind = MapperKind(text)
    except ValueError:
        raise SchemaError(f"{label}.kind must be a mapper kind, got {text!r}") from None
    return MappedSlot(kind, check_object(obj["state"], f"{label}.state"))


def _parse_slot(value: object, label: str) -> SlotSnapshot:
    obj = check_object(value, label, _SLOT_KEYS)
    register = check_optional_int(
        obj["secondary_slot_register"], f"{label}.secondary_slot_register", 0, 0xFF
    )
    subslots: list[MappedSlot | None] | None = None
    if obj["subslots"] is not None:
        subslots = []
        entries = check_list(obj["subslots"], f"{label}.subslots", _SUBSLOTS)
        for i, entry in enumerate(entries):
            entry_label = f"{label}.subslots[{i}]"
            subslots.append(None if entry is None else _parse_mapped(entry, entry_label))
    # Both come from the slot's expansion flag: present together or not at all.
    if (register is None) != (subslots is None):
        raise SchemaError(
            f"{label}.secondary_slot_register must be null exactly when subslots is"
        )
    return SlotSnapshot(_parse_mapped(obj["mapper"], f"{label}.mapper"), register, subslots)


def _parse_slots(value: object, msx2: bool) -> SlotsSnapshot:
    obj = check_object(value, "slots", _SLOTS_KEYS)
    slot3 = check_object(obj["slot3"], "slots.slot3", _SLOT3_KEYS)
    slot3_reg = check_optional_int(
        slot3["secondary_slot_register"], "slots.slot3.secondary_slot_register", 0, 0xFF
    )
    if (slot3_reg is not None) != msx2:
        raise SchemaError(
            "slots.slot3.secondary_slot_register must be set exactly in an MSX2 snapshot"
        )
    return SlotsSnapshot(
        slot1=_parse_slot(obj["slot1"], "slots.slot1"),
        slot2=_parse_slot(obj["slot2"], "slots.slot2"),
        slot3=Slot3Snapshot(slot3_reg),
    )


def _parse_vdp(value: object, msx2: bool) -> VdpSnapshot:
    obj = check_object(value, "vdp", _VDP_KEYS)
    vram_size = _V9938_VRAM_SIZE if msx2 else _TMS_VRAM_SIZE
    reg_count = _V9938_REG_COUNT if msx2 else _TMS_REG_COUNT
    latch = check_optional_int(obj["latch"], "vdp.latch", 0, 0xFF)
    v9938: V9938State | None = None
    if not msx2:
        if obj["v9938"] is not None:
            raise SchemaError("vdp.v9938 must be null in an MSX1 snapshot")
    else:
        if obj["v9938"] is None:
            raise SchemaError("vdp.v9938 is required in an MSX2 snapshot")
        v9938 = parse_v9938_state(obj["v9938"], "vdp.v9938")
        # `latch` is the V9938 latch's port-0x99 view; reject a file where
        # the two disagree instead of letting restore order pick one.
        expected = v9938["first_byte"] if v9938["control_pending"] else None
        if latch != expected:
            raise SchemaError(
                f"vdp.latch {latch!r} disagrees with the V9938 latch ({expected!r})"
            )
    return VdpSnapshot(
        vram=check_bytes(obj["vram"], "vdp.vram", vram_size),
        registers=check_int_list(obj["registers"], "vdp.registers", reg_count, 0, 0xFF),
        status=_byte(obj["status"], "vdp.status"),
        latch=latch,
        address=check_int(obj["address"], "vdp.address", 0, vram_size - 1),
        read_buffer=_byte(obj["read_buffer"], "vdp.read_buffer"),
        frame_count=check_int(obj["frame_count"], "vdp.frame_count", 0, SAFE_INT_MAX),
        v9938=v9938,
    )


def _parse_psg(value: object) -> PsgSnapshot:
    obj = check_object(value, "psg", _PSG_KEYS)
    return PsgSnapshot(
        registers=check_int_list(obj["registers"], "psg.registers", _PSG_REG_COUNT, 0, 0xFF),
        latch=check_int(obj["latch"], "psg.latch", 0, 0x0F),
        synth=check_object(obj["synth"], "psg.synth"),
    )


def _check_top_level_keys(data: dict[str, object], source: str) -> None:
    """Refuse a file whose top-level keys are not the layout's: an unknown
    or a missing key."""
    unknown: list[str] = []
    for key in data:
        if key not in _TOP_KEYS:
            unknown.append(key)
    missing: list[str] = []
    for key in _TOP_KEYS:
        if key not in data:
            missing.append(key)
    if unknown or missing:
        raise ValueError(
            f"incompatible state file layout ({source}): "
            f"unknown keys {sorted(unknown)}, missing keys {sorted(missing)}"
        )


def parse_snapshot(decoded: object, source: str) -> MachineSnapshot:
    """Turn a decoded state file into a MachineSnapshot, checking the format
    version, then the top-level key set, then every component object's key
    set and every value's type, range and (where the snapshot fixes it)
    length. A bad value raises StateLoadError naming its JSON path.
    Producer-owned objects are only checked to be objects here;
    _validate_snapshot parses their content with each producer's
    parse_state, which needs the running machine. Nothing here touches a
    machine."""
    if not isinstance(decoded, dict):
        raise ValueError(f"corrupt or unsupported save state: {source} (not an object)")
    data = cast(dict[str, object], decoded)
    version = data.get("format_version")
    if version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {version}, "
            f"expected {CURRENT_FORMAT_VERSION} ({source})"
        )
    _check_top_level_keys(data, source)

    def machine_type_of() -> str:
        text = check_str(data["machine_type"], "machine_type")
        if text != "msx1" and text != "msx2":
            raise SchemaError(f"machine_type must be 'msx1' or 'msx2', got {text!r}")
        return text

    machine_type = _field("machine_type", machine_type_of)
    msx2 = machine_type == "msx2"
    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type=machine_type,
        cpu=_field("cpu", lambda: _parse_cpu(data["cpu"])),
        memory=_field("memory", lambda: _parse_memory(data["memory"], msx2)),
        slots=_field("slots", lambda: _parse_slots(data["slots"], msx2)),
        vdp=_field("vdp", lambda: _parse_vdp(data["vdp"], msx2)),
        psg=_field("psg", lambda: _parse_psg(data["psg"])),
        scc=_field("scc", lambda: check_optional_object(data["scc"], "scc")),
        fdc=_field("fdc", lambda: check_optional_object(data["fdc"], "fdc")),
        rtc=_field("rtc", lambda: check_optional_object(data["rtc"], "rtc")),
        mouse=_field("mouse", lambda: check_optional_object(data["mouse"], "mouse")),
    )


def _validate_snapshot(machine: "Machine", snap: MachineSnapshot) -> _ParsedProducers:
    """Every check against the running machine that must pass before
    _restore_snapshot writes anything: format version, machine type, mapper
    kinds, RAM and RAM-mapper sizes, the FDC, expanded-slot and SCC wiring,
    and every producer object's parse_state, whose results it returns.
    (parse_snapshot has already checked everything the snapshot alone decides.)"""
    # parse_snapshot already checked the version; this guards a MachineSnapshot
    # built by hand (tests, tools).
    if snap.format_version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {snap.format_version}, "
            f"expected {CURRENT_FORMAT_VERSION}"
        )
    memory = machine.memory
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None
    expected_type = "msx2" if vdp9938 is not None else "msx1"
    if snap.machine_type != expected_type:
        raise SnapshotMismatchError(
            f"machine type mismatch: running {expected_type!r}, "
            f"saved {snap.machine_type!r}"
        )
    _check_slot_mapper(1, memory._mapper, snap.slots.slot1.mapper)
    _check_slot_mapper(2, memory._mapper2, snap.slots.slot2.mapper)
    ram_len = len(memory.ram)
    if len(snap.memory.ram) != ram_len:
        raise StateLoadError(
            "memory.ram",
            ValueError(f"memory.ram must be {ram_len} bytes, got {len(snap.memory.ram)}"),
        )
    rm = memory.ram_mapper
    saved_rm = snap.memory.ram_mapper
    if rm is not None and saved_rm is not None:
        size = len(rm.ram)
        if len(saved_rm.ram) != size:
            raise StateLoadError(
                "memory.ram_mapper.ram",
                ValueError(
                    f"memory.ram_mapper.ram must be {size} bytes, got {len(saved_rm.ram)}"
                ),
            )
    _check_optional_wiring("FDC", machine.fdc is not None, snap.fdc)
    _check_expanded_subslots(
        1, memory.slot1_sub_slot_enabled, memory._mapper1_subslots, snap.slots.slot1.subslots
    )
    _check_expanded_subslots(
        2, memory.slot2_sub_slot_enabled, memory._mapper2_subslots, snap.slots.slot2.subslots
    )
    _check_optional_wiring("SCC", machine.scc is not None, snap.scc)
    _check_slot0_ram_wiring(machine, snap.memory.slot0_ram)
    _check_optional_wiring("RTC", machine.rtc is not None, snap.rtc)
    _check_optional_wiring("mouse", machine.psg.mouse is not None, snap.mouse)
    return _parse_producer_objects(machine, snap)


_MapperStates = list[tuple["Mapper", Mapping[str, object]]]


@dataclass
class _ParsedProducers:
    """Every producer object of a snapshot, parsed once by the producer that
    will apply it. Each state travels with its producer, so _restore_snapshot
    can only hand a state to the instance that validated it, and applying
    cannot fail."""

    mappers: _MapperStates  # slot 1, slot 2, then each assigned sub-slot mapper
    psg_synth: "PsgSynthState"
    scc: "SccState | None"
    fdc: "FloppyDiskParsed | None"
    rtc: "RtcState | None"
    mouse: "MouseState | None"


def _parse_mapper_producer(
    mapper: "Mapper", state: dict[str, object], label: str, parsed: _MapperStates
) -> None:
    producer = f"mapper ({mapper.kind.value})"
    parsed.append((mapper, _parse_producer(producer, lambda: mapper.parse_state(state, label))))


def _parse_subslot_producers(
    label: str,
    subslots: list["Mapper | None"],
    saved: list[MappedSlot | None] | None,
    parsed: _MapperStates,
) -> None:
    if saved is None:
        return
    for i, sub_mapper in enumerate(subslots):
        entry = saved[i]
        # _check_expanded_subslots matched every assigned sub-slot to an entry.
        if sub_mapper is not None and entry is not None:
            _parse_mapper_producer(sub_mapper, entry.state, f"{label}[{i}].state", parsed)


def _parse_producer_objects(machine: "Machine", snap: MachineSnapshot) -> _ParsedProducers:
    """Parse every producer object the snapshot carries with its producer's
    parse_state, before anything is restored (the wiring checks have already
    matched each object to a running producer)."""
    memory = machine.memory
    slots = snap.slots
    mappers: _MapperStates = []
    _parse_mapper_producer(memory._mapper, slots.slot1.mapper.state, "slots.slot1.mapper.state",
                           mappers)
    _parse_mapper_producer(memory._mapper2, slots.slot2.mapper.state,
                           "slots.slot2.mapper.state", mappers)
    _parse_subslot_producers(
        "slots.slot1.subslots", memory._mapper1_subslots, slots.slot1.subslots, mappers
    )
    _parse_subslot_producers(
        "slots.slot2.subslots", memory._mapper2_subslots, slots.slot2.subslots, mappers
    )
    psg_synth = _parse_producer(
        "psg", lambda: machine.psg.parse_synth_state(snap.psg.synth, "psg.synth")
    )
    scc = machine.scc
    scc_state = snap.scc
    scc_parsed = None
    if scc is not None and scc_state is not None:
        scc_parsed = _parse_producer("scc", lambda: scc.parse_state(scc_state, "scc"))
    fdc = machine.fdc
    fdc_state = snap.fdc
    fdc_parsed = None
    if fdc is not None and fdc_state is not None:
        fdc_parsed = _parse_producer("fdc", lambda: fdc.parse_state(fdc_state, "fdc"))
    rtc = machine.rtc
    rtc_state = snap.rtc
    rtc_parsed = None
    if rtc is not None and rtc_state is not None:
        rtc_parsed = _parse_producer("rtc", lambda: rtc.parse_state(rtc_state, "rtc"))
    mouse = machine.psg.mouse
    mouse_state = snap.mouse
    mouse_parsed = None
    if mouse is not None and mouse_state is not None:
        mouse_parsed = _parse_producer("mouse", lambda: mouse.parse_state(mouse_state, "mouse"))
    return _ParsedProducers(
        mappers, psg_synth, scc_parsed, fdc_parsed, rtc_parsed, mouse_parsed
    )


def _restore_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    # Everything that can fail runs here, before the first write; the rest of
    # this function only assigns already-validated values.
    parsed = _validate_snapshot(machine, snap)
    memory = machine.memory
    vdp9938 = machine.vdp if isinstance(machine.vdp, V9938) else None

    cpu = snap.cpu
    _restore_cpu_regs(machine, cpu.registers)
    machine.cpu.halted = cpu.halted
    machine.cpu.iff1 = cpu.iff1
    machine.cpu.iff2 = cpu.iff2
    machine.cpu.int_pending = cpu.int_pending
    machine.cpu.nmi_pending = cpu.nmi_pending
    machine.cpu.im = cpu.im

    memory.ram[:] = snap.memory.ram
    memory.set_slot_register(snap.memory.primary_slot_register)
    for producer, state in parsed.mappers:
        producer.apply_state(state)

    vdp = snap.vdp
    machine.vdp.vram[:] = vdp.vram
    machine.vdp.regs[:] = vdp.registers
    machine.vdp.status = vdp.status
    machine.vdp.frame_count = vdp.frame_count
    # Common VDP address state — identical field names on both VDP types. The
    # V9938's port latch is restored with the rest of its own state.
    machine.vdp.addr = vdp.address
    machine.vdp.read_buf = vdp.read_buffer
    if vdp9938 is None:
        assert isinstance(machine.vdp, VDP)
        machine.vdp.latch = vdp.latch
    else:
        assert vdp.v9938 is not None  # parse_snapshot requires it for MSX2
        vdp9938.restore_state(vdp.v9938)
        rm = memory.ram_mapper
        saved_rm = snap.memory.ram_mapper
        if rm is not None and saved_rm is not None:
            rm.ram[:] = saved_rm.ram
            rm.banks[:] = saved_rm.banks
        slot3_reg = snap.slots.slot3.secondary_slot_register
        if slot3_reg is not None:
            memory.set_sub_slot_reg(slot3_reg)
    # The VDP's cached /INT level is what the scanline loop feeds the CPU, so
    # it must follow the restored status/registers (incl. V9938 S#1 FH).
    machine.vdp.update_irq()

    machine.psg.regs[:] = snap.psg.registers
    machine.psg.latch = snap.psg.latch
    machine.psg.apply_synth_state(parsed.psg_synth)
    if machine.scc is not None and parsed.scc is not None:
        machine.scc.apply_state(parsed.scc)
    if machine.fdc is not None and parsed.fdc is not None:
        machine.fdc.apply_state(parsed.fdc)
    slot1_reg = snap.slots.slot1.secondary_slot_register
    if slot1_reg is not None:
        memory.set_slot1_sub_slot_reg(slot1_reg)
    slot2_reg = snap.slots.slot2.secondary_slot_register
    if slot2_reg is not None:
        memory.set_slot2_sub_slot_reg(slot2_reg)
    if memory.slot0_ram is not None and snap.memory.slot0_ram is not None:
        memory.slot0_ram[:] = snap.memory.slot0_ram
    if machine.rtc is not None and parsed.rtc is not None:
        machine.rtc.apply_state(parsed.rtc)
    mouse = machine.psg.mouse
    if mouse is not None and parsed.mouse is not None:
        mouse.apply_state(parsed.mouse, machine.cycle_count)


# --- encoding ---------------------------------------------------------------

def _to_jsonable(node: object) -> object:
    """Recursively convert a snapshot dict to JSON-serialisable form.

    Byte blobs become base64 strings; lists and dicts recurse; other scalars
    pass through. A float, or an int beyond +-SAFE_INT_MAX, raises ValueError
    (a writer bug: such a file would not read back the same everywhere).
    """
    if isinstance(node, (bytes, bytearray)):
        return base64.b64encode(bytes(node)).decode("ascii")
    if isinstance(node, dict):
        result: dict[str, object] = {}
        for key, value in node.items():
            result[key] = _to_jsonable(value)
        return result
    if isinstance(node, (list, tuple)):
        items: list[object] = []
        for value in node:
            items.append(_to_jsonable(value))
        return items
    if isinstance(node, float):
        raise ValueError(f"snapshot holds a float ({node!r}); only integers are portable")
    if isinstance(node, int) and not isinstance(node, bool):
        if not -SAFE_INT_MAX <= node <= SAFE_INT_MAX:
            raise ValueError(f"snapshot integer {node} is outside +-(2**53 - 1)")
    return node


def _encode_canonical(document: object) -> bytes:
    """RFC 8785 (JCS) form of a document with ASCII keys and no floats:
    sorted keys, no whitespace, minimal string escaping, raw UTF-8."""
    text = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def _reject_float(text: str) -> object:
    raise ValueError(f"floating-point number {text} (only integers are allowed)")


def _reject_constant(text: str) -> object:
    raise ValueError(f"{text} is not allowed")


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _decode_strict(data: bytes) -> object:
    """Parse JSON text, rejecting floats, NaN/Infinity and duplicate keys.
    Whitespace and key order are free (only writing is canonical)."""
    return json.loads(
        data.decode("utf-8"),
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_object_without_duplicates,
    )


def _check_other_version(data: bytes, source: str) -> None:
    """Called when strict decoding failed: if the data is ordinary JSON from
    another format version (format 16 saved the OPLL phase as a float), report
    the version mismatch rather than the strict-decoding error."""
    try:
        lenient = json.loads(data.decode("utf-8"))
    except ValueError:
        return
    if not isinstance(lenient, dict):
        return
    version = lenient.get("format_version")
    if version != CURRENT_FORMAT_VERSION:
        raise ValueError(
            f"incompatible state file: version {version}, "
            f"expected {CURRENT_FORMAT_VERSION} ({source})"
        )


def save_state(machine: "Machine") -> bytes:
    """Encode the machine's snapshot as the versioned JSON state format.

    Chooses no file name and writes nothing: host/state_files.py stores the
    bytes (with a screenshot and the latest.* links).
    """
    snap = _snapshot_from_machine(machine)
    return _encode_canonical(_to_jsonable(asdict(snap)))


def load_state(machine: "Machine", data: bytes, source: str = "<state>") -> None:
    """Restore a machine from save_state() bytes. `source` names the data in
    error messages (the host passes the file path).

    Raises:
        StateLoadError: If a field or producer object is malformed.
        ValueError: If the data is not a state of this format version and
            layout, or does not match the running machine (machine type,
            mapper kind, slot / FDC / SCC wiring). Nothing is restored then.
    """
    # Legacy pickle files (format_version <= 4) start with a pickle opcode byte,
    # not JSON. Refuse to unpickle them; the format is now stdlib JSON.
    stripped = data.lstrip()
    if stripped[:1] != b"{":
        raise ValueError(
            "legacy pickle save states are no longer supported; please re-save "
            f"({source})"
        )
    try:
        decoded = _decode_strict(data)
    except ValueError as exc:  # also UnicodeDecodeError and json.JSONDecodeError
        _check_other_version(data, source)
        raise ValueError(f"corrupt or unsupported save state: {source} ({exc})") from exc

    snap = parse_snapshot(decoded, source)
    _restore_snapshot(machine, snap)
