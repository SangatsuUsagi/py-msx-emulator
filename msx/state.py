"""Encode and decode a complete machine snapshot as versioned JSON bytes.

The snapshot is a stdlib-only JSON container (no pickle) written in RFC 8785
(JCS) canonical form: sorted keys, no whitespace, UTF-8, integers only (each
within +-SAFE_INT_MAX). Byte blobs (RAM, VRAM, SRAM) are plain base64
strings, which each field's parser decodes. `format_version` guards
compatibility.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from typing import TYPE_CHECKING, cast

from msx.canonical_json import decode_strict, encode_canonical, mismatched_version, to_jsonable
from msx.mapper import MapperKind
from msx.snapshot_schema import (
    SAFE_INT_MAX,
    SchemaError,
    SnapshotError,
    SnapshotMismatchError,
    check_bool,
    check_bytes,
    check_int,
    check_int_list,
    check_list,
    check_lower_hex,
    check_object,
    check_optional_bytes,
    check_optional_int,
    check_optional_object,
    check_str,
)
from msx.vdp.v9938 import V9938, V9938State, parse_v9938_state
from msx.vdp.vdp import VDP

if TYPE_CHECKING:
    from msx.cassette import TapeIdentity
    from msx.fdc.interface import FloppyDiskParsed
    from msx.machine import Machine
    from msx.mapper import Mapper
    from msx.memory import Memory
    from msx.mouse import MouseState
    from msx.psg import PsgSynthState
    from msx.rtc import RtcState
    from msx.scc import SccState


# Version 5: stdlib JSON container replacing the legacy pickle format (<= 4).
# Version 6: mapper_class: str (Python class name) replaced with
#   mapper_kind: MapperKind (closed enum, decoupled from class names) --
#   see machine-state "Closed mapper-kind discriminant for save-state". No
#   other field changed.
# Version 7: fdc_state: dict[str, object] | None added (WD2793/TC8566AF/
#   DiskDrive/connection-style state, see machine-state "FDC state save
#   and restore"). No other field changed.
# Version 8: scci_state: dict[str, object] | None added (SCCICart's
#   RAM/banks/mode-register state, now needed since --extension scc_plus
#   moved SCCICart from primary slot 1 -- covered by the generic
#   mapper_kind/mapper_state fields -- to slot 2, which has no generic
#   mapper2 state path). No other field changed.
# Version 9: slot2_sub_slot_reg: int | None added (primary slot 2's own
#   secondary slot register, independent of sub_slot_reg -- see
#   memory-slot-bus "State save/load includes slot2_sub_slot_reg"). No
#   other field changed.
# Version 10: halnote_state: dict[str, object] | None added (HBI-J1's
#   Halnote-mapped MSX-JE cartridge's bank/sub-bank registers, SRAM-enable/
#   submapper-enable flags, and 16 KB SRAM contents, since slot 2's
#   sub-slots have no generic mapper-state path -- mirrors scci_state's
#   own precedent). No other field changed.
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
#   behavior. See machine-state "Slot 2 mapper state save and restore".
# Version 12: HalnoteMapperState's sram_enabled/submapper_enabled fields
#   removed -- write-only in the persisted payload (HalnoteMapper.restore()
#   has always re-derived both flags from the restored bank registers'
#   top bit, never from these two fields). No behavior change; only the
#   persisted schema shrinks.
# Version 13: slot1_sub_slot_reg/mapper1_subslot_kinds/
#   mapper1_subslot_states added -- the identical mechanism
#   slot2_sub_slot_reg/mapper2_subslot_kinds/mapper2_subslot_states already
#   provides for slot 2's own expansion (version 9/11), now that slot 1 can
#   also be expanded (an extension needing an expanded slot 1, e.g. HBI-J1
#   or FM-PAC placed via --extension1). All three are None unless
#   machine.memory.slot1_sub_slot_enabled. See memory-slot-bus
#   "State save/load includes slot1_sub_slot_reg".
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
#   machine-state "Snapshot includes RTC state" and "Snapshot includes
#   mouse state".
# Version 17: RFC 8785 canonical JSON; blobs as plain base64 strings (no
#   {"__b64__": ...} wrapper); floats, NaN/Infinity, duplicate keys and
#   integers beyond +-(2**53 - 1) rejected. See machine-state "Snapshot
#   bytes are RFC 8785 canonical JSON".
# Version 18: machine_config_sha256 added -- the SHA-256 of the canonical
#   MachineConfig document the machine was built from; a state loads only into
#   a machine built from the same document. See machine-state "A snapshot
#   is bound to its machine configuration".
# Version 19: ppi added -- the PPI's Port C latch, last mode-set control word
#   and port-0xAB bus latch. See machine-state "The PPI state is saved".
# Version 20: no layout change; the format-19 layout with cpu.registers.WZ,
#   cpu.q and cpu.q_next, which add-z80-memptr added in v2.8.2 without a
#   version bump.
# Version 21: fdc.drives[].disk_path removed -- a mounted disk is identified
#   by its size and SHA1 only; the host keeps the disk's file.
#   cpu.ei_pending (the one-instruction delay after EI) was added later
#   without a version bump. See machine-state "The CPU interrupt state is
#   saved".
# Version 22: printer added -- the printer port's data latch and STROBE, None
#   when the machine has no port.
# Version 23: tape added -- the mounted tape's identity, the position and the
#   motor, None when the machine has no cassette port (io-cassette "The
#   tape state is saved").
# Version 24: mapper_latch added -- the S1985 latch of the mapper ports
#   0xFC-0xFF, None when the machine has no latch (memory-ram-mapper "S1985
#   mapper port latch").
#   These fields were added later without a version bump: the RTC counter
#   layout (fraction ... leap_year, tick_remainder and uncounted_cycles,
#   which replace the version 16 start and elapsed_cycles), the WD2793
#   immediate_irq, type1_phase, type1_cycles_left, hld and hld_idle_cycles,
#   and the TC8566AF pcn.
# Version 25: rtc.reset removed -- the RESET register is write-only and acts
#   at once, so it holds no state. rtc.uncounted_cycles is bounded to 2^48.
CURRENT_FORMAT_VERSION: int = 25

_SHA256_HEX_LEN = 64
_SHA1_HEX_LEN = 40
# The smallest edge list: one u32 interval (io-cassette).
_MIN_EDGE_LIST_BYTES = 4


class StateLoadError(SnapshotError):
    """A save-state value was rejected while loading: a top-level field
    (parse_snapshot) or a producer object (its producer's parse_state, run by
    _validate_snapshot), always before anything is restored.

    `producer` names the field or producer. Distinct from the plain
    SnapshotError raised for a file of another format version or layout, and
    from SnapshotMismatchError (machine type, mapper kind, wiring, mounted
    disk), both of which already carry clear top-level messages, so a
    corrupted or hand-edited .state file fails with one predictable,
    catchable error naming the part that broke. The original exception is
    chained via __cause__.
    """

    def __init__(self, producer: str, cause: Exception) -> None:
        super().__init__(f"invalid {producer} state: {cause}")
        self.producer = producer


# The snapshot layout: one dataclass per JSON object, so
# asdict(MachineSnapshot) is the document and parse_snapshot builds the same
# tree. Every field is always present in the file; None (JSON null) means the
# machine has no such part. msx/snapshot.schema.json states the same layout
# language-neutrally. Producer-owned objects (mapper / PSG synth / SCC / FDC /
# RTC / mouse state) stay dict[str, object] here: each is validated into its
# producer's typed state by that producer's parse_state in _validate_snapshot,
# before anything is restored, and the typed results (_ParsedProducers) are
# what _restore_snapshot applies.


@dataclass
class CpuRegistersSnapshot:
    A: int
    F: int
    BC: int
    DE: int
    HL: int
    IX: int
    IY: int
    SP: int
    PC: int
    I: int  # noqa: E741
    R: int
    WZ: int
    A_: int
    F_: int
    BC_: int
    DE_: int
    HL_: int


@dataclass
class CpuSnapshot:
    registers: CpuRegistersSnapshot
    halted: bool
    iff1: bool
    iff2: bool
    int_pending: bool
    nmi_pending: bool
    ei_pending: bool  # the one-instruction delay after EI
    im: int
    q: int  # Z80.q / q_next: the SCF/CCF flag latch (see msx/cpu/z80.py)
    q_next: int


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
    machine-state "Closed mapper-kind discriminant for save-state")."""

    kind: MapperKind
    state: dict[str, object]


@dataclass
class SlotSnapshot:
    """Primary slot 1 or 2. `mapper` is always present (the slot's flat
    mapper, an empty FlatMapper when nothing is configured). When the slot
    is expanded (an extension such as HBI-J1 or FM-PAC placed in an expanded
    slot), `secondary_slot_register` is its own secondary slot register and
    `subslots` holds one entry per sub-slot (None for an unassigned one);
    otherwise both are None. See memory-slot-bus "State save/load includes
    slot1_sub_slot_reg" and machine-state "Slot 2 mapper state save and
    restore"."""

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
class PpiSnapshot:
    port_c: int
    control: int     # a mode-set word: bit 7 always set
    bus_latch: int


@dataclass
class PrinterSnapshot:
    data: int
    strobe: int


@dataclass
class TapeIdentitySnapshot:
    size: int
    sha1: str


@dataclass
class TapeSnapshot:
    # None when no tape is mounted.
    identity: TapeIdentitySnapshot | None
    # The position at the save; it counts from the saved cycle count.
    position: int
    motor: int


@dataclass
class MachineSnapshot:
    format_version: int
    machine_type: str  # "msx1" or "msx2"
    # Lowercase hex SHA-256 of the MachineConfig document (Machine.
    # machine_config_sha256); empty for a Machine constructed by hand.
    machine_config_sha256: str
    cpu: CpuSnapshot
    memory: MemorySnapshot
    slots: SlotsSnapshot
    vdp: VdpSnapshot
    psg: PsgSnapshot
    # None only for a Machine constructed without a PPI (test fixtures);
    # build_machine always wires one.
    ppi: PpiSnapshot | None
    # Each None when the machine has no such device: SCC.snapshot(),
    # FloppyDisk.snapshot(), RTC.snapshot(), MouseDevice.snapshot().
    scc: dict[str, object] | None
    fdc: dict[str, object] | None
    rtc: dict[str, object] | None
    mouse: dict[str, object] | None
    # None when the machine has no printer port.
    printer: PrinterSnapshot | None
    # The four S1985 mapper port latches; None when the machine has no latch.
    mapper_latch: list[int] | None
    # None when the machine has no cassette port.
    tape: TapeSnapshot | None


# --- internal helpers ---------------------------------------------------------

def _cpu_regs_snapshot(machine: "Machine") -> CpuRegistersSnapshot:
    r = machine.cpu.registers
    return CpuRegistersSnapshot(
        A=r.A, F=r.F, BC=r.BC, DE=r.DE, HL=r.HL,
        IX=r.IX, IY=r.IY, SP=r.SP, PC=r.PC,
        I=r.I, R=r.R, WZ=r.WZ,
        A_=r.A_, F_=r.F_, BC_=r.BC_, DE_=r.DE_, HL_=r.HL_,
    )


def _restore_cpu_regs(machine: "Machine", regs: CpuRegistersSnapshot) -> None:
    r = machine.cpu.registers
    r.A = regs.A
    r.F = regs.F
    r.BC = regs.BC
    r.DE = regs.DE
    r.HL = regs.HL
    r.IX = regs.IX
    r.IY = regs.IY
    r.SP = regs.SP
    r.PC = regs.PC
    r.I = regs.I
    r.R = regs.R
    r.WZ = regs.WZ
    r.A_ = regs.A_
    r.F_ = regs.F_
    r.BC_ = regs.BC_
    r.DE_ = regs.DE_
    r.HL_ = regs.HL_


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


def _ppi_snapshot(machine: "Machine") -> PpiSnapshot | None:
    if machine.ppi is None:
        return None
    state = machine.ppi.snapshot_state()
    return PpiSnapshot(
        port_c=state["port_c"], control=state["control"], bus_latch=state["bus_latch"]
    )


def _printer_snapshot(machine: "Machine") -> PrinterSnapshot | None:
    if machine.printer is None:
        return None
    state = machine.printer.snapshot_state()
    return PrinterSnapshot(data=state["data"], strobe=state["strobe"])


def _identity_snapshot(identity: TapeIdentity | None) -> TapeIdentitySnapshot | None:
    if identity is None:
        return None
    return TapeIdentitySnapshot(size=identity["size"], sha1=identity["sha1"])


def _tape_snapshot(machine: "Machine") -> TapeSnapshot | None:
    if machine.cassette is None:
        return None
    state = machine.cassette.snapshot_state()
    return TapeSnapshot(
        identity=_identity_snapshot(state["identity"]),
        position=state["position"],
        motor=state["motor"],
    )


def _tape_text(identity: TapeIdentitySnapshot | None) -> str:
    if identity is None:
        return "no tape"
    return f"a tape of {identity.size} bytes, sha1 {identity.sha1}"


def _check_tape(machine: "Machine", saved: TapeSnapshot | None) -> None:
    """The saved tape must be the mounted one (io-cassette "A load checks
    the tape identity"), and its position must be on that tape."""
    _check_optional_wiring("cassette port", machine.cassette is not None, saved)
    if machine.cassette is None or saved is None:
        return
    running = _identity_snapshot(machine.cassette.identity)
    if running != saved.identity:
        raise SnapshotMismatchError(
            f"tape mismatch: running has {_tape_text(running)}, "
            f"saved state has {_tape_text(saved.identity)}"
        )
    if saved.position > machine.cassette.length:
        raise SnapshotMismatchError(
            f"tape.position {saved.position} is beyond the tape length "
            f"{machine.cassette.length}"
        )


def _check_optional_wiring(
    name: str, running: bool, state: object
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
    machine.fdc.catch_up()  # the due events belong in the capture
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
    sub-slot's mapper kind -- shared by slot 1 and slot 2 (see memory-slot-bus
    "State save/load includes slot1_sub_slot_reg" and, for slot 2
    specifically, machine-state "Slot 2 mapper state mismatch is
    rejected"). Run by _validate_snapshot,
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
        machine_config_sha256=machine.machine_config_sha256,
        cpu=CpuSnapshot(
            registers=_cpu_regs_snapshot(machine),
            halted=machine.cpu.halted,
            iff1=machine.cpu.iff1,
            iff2=machine.cpu.iff2,
            int_pending=machine.cpu.int_pending,
            nmi_pending=machine.cpu.nmi_pending,
            ei_pending=machine.cpu.ei_pending,
            im=machine.cpu.im,
            q=machine.cpu.q,
            q_next=machine.cpu.q_next,
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
                memory.mapper1, memory.slot1_sub_slot_enabled, memory.slot1_sub_slot_reg,
                memory.mapper1_subslots,
            ),
            slot2=_snapshot_slot(
                memory.mapper2, memory.slot2_sub_slot_enabled, memory.slot2_sub_slot_reg,
                memory.mapper2_subslots,
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
        ppi=_ppi_snapshot(machine),
        scc=_scc_to_dict(machine),
        fdc=_fdc_to_dict(machine),
        rtc=_rtc_to_dict(machine),
        mouse=_mouse_to_dict(machine),
        printer=_printer_snapshot(machine),
        mapper_latch=(
            machine.mapper_latch.snapshot() if machine.mapper_latch is not None else None
        ),
        tape=_tape_snapshot(machine),
    )


def _keys_of(cls: type) -> frozenset[str]:
    """The JSON object keys of a snapshot dataclass: its field names."""
    names: list[str] = []
    for f in dataclass_fields(cls):
        names.append(f.name)
    return frozenset(names)


_TOP_KEYS = _keys_of(MachineSnapshot)
_CPU_KEYS = _keys_of(CpuSnapshot)
_CPU_REG_KEYS = _keys_of(CpuRegistersSnapshot)
_MEMORY_KEYS = _keys_of(MemorySnapshot)
_RAM_MAPPER_KEYS = _keys_of(RamMapperSnapshot)
_MAPPED_KEYS = _keys_of(MappedSlot)
_SLOT_KEYS = _keys_of(SlotSnapshot)
_SLOT3_KEYS = _keys_of(Slot3Snapshot)
_SLOTS_KEYS = _keys_of(SlotsSnapshot)
_VDP_KEYS = _keys_of(VdpSnapshot)
_PSG_KEYS = _keys_of(PsgSnapshot)
_PPI_KEYS = _keys_of(PpiSnapshot)
_PRINTER_KEYS = _keys_of(PrinterSnapshot)
_TAPE_KEYS = _keys_of(TapeSnapshot)
_TAPE_IDENTITY_KEYS = _keys_of(TapeIdentitySnapshot)

_TMS_VRAM_SIZE = 0x4000
_V9938_VRAM_SIZE = 0x20000
_TMS_REG_COUNT = 8
_V9938_REG_COUNT = 28
_PSG_REG_COUNT = 16
_RAM_MAPPER_PAGES = 4  # the four 16 KB pages a RAM mapper bank register serves
_SUBSLOTS = 4


def _byte(value: object, label: str) -> int:
    return check_int(value, label, 0, 0xFF)


def _word(value: object, label: str) -> int:
    return check_int(value, label, 0, 0xFFFF)


def _parse_cpu(value: object) -> CpuSnapshot:
    obj = check_object(value, "cpu", _CPU_KEYS)
    regs_obj = check_object(obj["registers"], "cpu.registers", _CPU_REG_KEYS)
    return CpuSnapshot(
        registers=CpuRegistersSnapshot(
            A=_byte(regs_obj["A"], "cpu.registers.A"),
            F=_byte(regs_obj["F"], "cpu.registers.F"),
            BC=_word(regs_obj["BC"], "cpu.registers.BC"),
            DE=_word(regs_obj["DE"], "cpu.registers.DE"),
            HL=_word(regs_obj["HL"], "cpu.registers.HL"),
            IX=_word(regs_obj["IX"], "cpu.registers.IX"),
            IY=_word(regs_obj["IY"], "cpu.registers.IY"),
            SP=_word(regs_obj["SP"], "cpu.registers.SP"),
            PC=_word(regs_obj["PC"], "cpu.registers.PC"),
            I=_byte(regs_obj["I"], "cpu.registers.I"),
            R=_byte(regs_obj["R"], "cpu.registers.R"),
            WZ=_word(regs_obj["WZ"], "cpu.registers.WZ"),
            A_=_byte(regs_obj["A_"], "cpu.registers.A_"),
            F_=_byte(regs_obj["F_"], "cpu.registers.F_"),
            BC_=_word(regs_obj["BC_"], "cpu.registers.BC_"),
            DE_=_word(regs_obj["DE_"], "cpu.registers.DE_"),
            HL_=_word(regs_obj["HL_"], "cpu.registers.HL_"),
        ),
        halted=check_bool(obj["halted"], "cpu.halted"),
        iff1=check_bool(obj["iff1"], "cpu.iff1"),
        iff2=check_bool(obj["iff2"], "cpu.iff2"),
        int_pending=check_bool(obj["int_pending"], "cpu.int_pending"),
        nmi_pending=check_bool(obj["nmi_pending"], "cpu.nmi_pending"),
        ei_pending=check_bool(obj["ei_pending"], "cpu.ei_pending"),
        im=check_int(obj["im"], "cpu.im", 0, 2),
        q=_byte(obj["q"], "cpu.q"),
        q_next=_byte(obj["q_next"], "cpu.q_next"),
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
        v9938 = parse_v9938_state(obj["v9938"])
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


def _parse_ppi(value: object) -> PpiSnapshot | None:
    if value is None:
        return None
    obj = check_object(value, "ppi", _PPI_KEYS)
    return PpiSnapshot(
        port_c=check_int(obj["port_c"], "ppi.port_c", 0, 0xFF),
        control=check_int(obj["control"], "ppi.control", 0x80, 0xFF),
        bus_latch=check_int(obj["bus_latch"], "ppi.bus_latch", 0, 0xFF),
    )


def _parse_printer(value: object) -> PrinterSnapshot | None:
    if value is None:
        return None
    obj = check_object(value, "printer", _PRINTER_KEYS)
    return PrinterSnapshot(
        data=check_int(obj["data"], "printer.data", 0, 0xFF),
        strobe=check_int(obj["strobe"], "printer.strobe", 0, 1),
    )


def _parse_mapper_latch(value: object) -> list[int] | None:
    if value is None:
        return None
    return check_int_list(value, "mapper_latch", 4, 0, 0xFF)


def _parse_tape(value: object) -> TapeSnapshot | None:
    if value is None:
        return None
    obj = check_object(value, "tape", _TAPE_KEYS)
    identity: TapeIdentitySnapshot | None = None
    if obj["identity"] is not None:
        ident = check_object(obj["identity"], "tape.identity", _TAPE_IDENTITY_KEYS)
        identity = TapeIdentitySnapshot(
            size=check_int(ident["size"], "tape.identity.size", _MIN_EDGE_LIST_BYTES,
                           SAFE_INT_MAX),
            sha1=check_lower_hex(ident["sha1"], "tape.identity.sha1", _SHA1_HEX_LEN),
        )
    return TapeSnapshot(
        identity=identity,
        position=check_int(obj["position"], "tape.position", 0, SAFE_INT_MAX),
        motor=check_int(obj["motor"], "tape.motor", 0, 1),
    )


def _check_top_level_keys(data: dict[str, object]) -> None:
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
        raise SnapshotError(
            "incompatible state file layout: "
            f"unknown keys {sorted(unknown)}, missing keys {sorted(missing)}"
        )


def parse_snapshot(decoded: object) -> MachineSnapshot:
    """Turn a decoded state file into a MachineSnapshot, checking the format
    version, then the top-level key set, then every component object's key
    set and every value's type, range and (where the snapshot fixes it)
    length. A bad value raises StateLoadError naming its JSON path.
    Producer-owned objects are only checked to be objects here;
    _validate_snapshot parses their content with each producer's
    parse_state, which needs the running machine. Nothing here touches a
    machine."""
    if not isinstance(decoded, dict):
        raise SnapshotError("corrupt or unsupported save state (not an object)")
    data = cast(dict[str, object], decoded)
    version = data.get("format_version")
    if version != CURRENT_FORMAT_VERSION:
        raise SnapshotError(
            f"incompatible state file: version {version}, "
            f"expected {CURRENT_FORMAT_VERSION}"
        )
    _check_top_level_keys(data)

    # The top-level field being parsed: a SchemaError's message names the JSON
    # path, and StateLoadError.producer names this field.
    field_name = "machine_type"
    try:
        machine_type = check_str(data["machine_type"], "machine_type")
        if machine_type != "msx1" and machine_type != "msx2":
            raise SchemaError(f"machine_type must be 'msx1' or 'msx2', got {machine_type!r}")
        msx2 = machine_type == "msx2"
        field_name = "machine_config_sha256"
        config_sha256 = check_str(data["machine_config_sha256"], "machine_config_sha256")
        if config_sha256 != "":
            check_lower_hex(config_sha256, "machine_config_sha256", _SHA256_HEX_LEN)
        field_name = "cpu"
        cpu = _parse_cpu(data["cpu"])
        field_name = "memory"
        memory = _parse_memory(data["memory"], msx2)
        field_name = "slots"
        slots = _parse_slots(data["slots"], msx2)
        field_name = "vdp"
        vdp = _parse_vdp(data["vdp"], msx2)
        field_name = "psg"
        psg = _parse_psg(data["psg"])
        field_name = "ppi"
        ppi = _parse_ppi(data["ppi"])
        field_name = "scc"
        scc = check_optional_object(data["scc"], "scc")
        field_name = "fdc"
        fdc = check_optional_object(data["fdc"], "fdc")
        field_name = "rtc"
        rtc = check_optional_object(data["rtc"], "rtc")
        field_name = "mouse"
        mouse = check_optional_object(data["mouse"], "mouse")
        field_name = "printer"
        printer = _parse_printer(data["printer"])
        field_name = "mapper_latch"
        mapper_latch = _parse_mapper_latch(data["mapper_latch"])
        field_name = "tape"
        tape = _parse_tape(data["tape"])
    except SchemaError as exc:
        raise StateLoadError(field_name, exc) from exc
    return MachineSnapshot(
        format_version=CURRENT_FORMAT_VERSION,
        machine_type=machine_type,
        machine_config_sha256=config_sha256,
        cpu=cpu,
        memory=memory,
        slots=slots,
        vdp=vdp,
        psg=psg,
        ppi=ppi,
        scc=scc,
        fdc=fdc,
        rtc=rtc,
        mouse=mouse,
        printer=printer,
        mapper_latch=mapper_latch,
        tape=tape,
    )


def _validate_snapshot(machine: "Machine", snap: MachineSnapshot) -> _ParsedProducers:
    """Every check against the running machine that must pass before
    _restore_snapshot writes anything: format version, machine type, mapper
    kinds, RAM and RAM-mapper sizes, the FDC, expanded-slot and SCC wiring,
    the configuration hash, and every producer object's parse_state, whose
    results it returns.
    (parse_snapshot has already checked everything the snapshot alone decides.)"""
    # parse_snapshot already checked the version; this guards a MachineSnapshot
    # built by hand (tests, tools).
    if snap.format_version != CURRENT_FORMAT_VERSION:
        raise SnapshotError(
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
    _check_slot_mapper(1, memory.mapper1, snap.slots.slot1.mapper)
    _check_slot_mapper(2, memory.mapper2, snap.slots.slot2.mapper)
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
        1, memory.slot1_sub_slot_enabled, memory.mapper1_subslots, snap.slots.slot1.subslots
    )
    _check_expanded_subslots(
        2, memory.slot2_sub_slot_enabled, memory.mapper2_subslots, snap.slots.slot2.subslots
    )
    _check_optional_wiring("SCC", machine.scc is not None, snap.scc)
    _check_slot0_ram_wiring(machine, snap.memory.slot0_ram)
    _check_optional_wiring("RTC", machine.rtc is not None, snap.rtc)
    _check_optional_wiring("mouse", machine.psg.mouse is not None, snap.mouse)
    _check_optional_wiring("PPI", machine.ppi is not None, snap.ppi)
    _check_optional_wiring("printer port", machine.printer is not None, snap.printer)
    _check_optional_wiring(
        "mapper port latch", machine.mapper_latch is not None, snap.mapper_latch
    )
    _check_tape(machine, snap.tape)
    parsed = _parse_producer_objects(machine, snap)
    # Last of the identity checks: the specific ones above and in the
    # producers (FDC kind, mounted disks) name what differs; this one catches
    # what they cannot see (cartridge ROM bytes, the machine definition).
    if snap.machine_config_sha256 != machine.machine_config_sha256:
        raise SnapshotMismatchError(
            "machine configuration mismatch: running "
            f"{machine.machine_config_sha256 or '(none)'}, "
            f"saved {snap.machine_config_sha256 or '(none)'}"
        )
    return parsed


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


def _mapper_producers(
    memory: "Memory", slots: SlotsSnapshot
) -> list[tuple["Mapper", dict[str, object], str]]:
    """(mapper, its saved state, JSON path) of slot 1, slot 2, then each
    assigned sub-slot. _check_expanded_subslots matched every assigned sub-slot
    to an entry."""
    jobs: list[tuple[Mapper, dict[str, object], str]] = [
        (memory.mapper1, slots.slot1.mapper.state, "slots.slot1.mapper.state"),
        (memory.mapper2, slots.slot2.mapper.state, "slots.slot2.mapper.state"),
    ]
    expanded = (
        ("slots.slot1.subslots", memory.mapper1_subslots, slots.slot1.subslots),
        ("slots.slot2.subslots", memory.mapper2_subslots, slots.slot2.subslots),
    )
    for label, subslots, saved in expanded:
        if saved is None:
            continue
        for i, sub_mapper in enumerate(subslots):
            entry = saved[i]
            if sub_mapper is not None and entry is not None:
                jobs.append((sub_mapper, entry.state, f"{label}[{i}].state"))
    return jobs


def _parse_producer_objects(machine: "Machine", snap: MachineSnapshot) -> _ParsedProducers:
    """Parse every producer object the snapshot carries with its producer's
    parse_state, before anything is restored (the wiring checks have already
    matched each object to a running producer). A rejection, an identity
    mismatch included, becomes StateLoadError naming the producer."""
    mappers: _MapperStates = []
    for mapper, state, label in _mapper_producers(machine.memory, snap.slots):
        try:
            mappers.append((mapper, mapper.parse_state(state, label)))
        except SnapshotError as exc:
            raise StateLoadError(f"mapper ({mapper.kind.value})", exc) from exc
    scc = machine.scc
    fdc = machine.fdc
    rtc = machine.rtc
    mouse = machine.psg.mouse
    scc_parsed = None
    fdc_parsed = None
    rtc_parsed = None
    mouse_parsed = None
    producer = "psg"
    try:
        psg_synth = machine.psg.parse_synth_state(snap.psg.synth, "psg.synth")
        producer = "scc"
        if scc is not None and snap.scc is not None:
            scc_parsed = scc.parse_state(snap.scc, "scc")
        producer = "fdc"
        if fdc is not None and snap.fdc is not None:
            fdc_parsed = fdc.parse_state(snap.fdc, "fdc")
        producer = "rtc"
        if rtc is not None and snap.rtc is not None:
            rtc_parsed = rtc.parse_state(snap.rtc, "rtc")
        producer = "mouse"
        if mouse is not None and snap.mouse is not None:
            mouse_parsed = mouse.parse_state(snap.mouse, "mouse")
    except SnapshotError as exc:
        raise StateLoadError(producer, exc) from exc
    return _ParsedProducers(
        mappers, psg_synth, scc_parsed, fdc_parsed, rtc_parsed, mouse_parsed
    )


def _apply_cpu(machine: "Machine", cpu: CpuSnapshot) -> None:
    _restore_cpu_regs(machine, cpu.registers)
    machine.cpu.halted = cpu.halted
    # Not saved (Z80.idle_hint): HALT is the only idle state the saved
    # fields show; a JR $ loop sets the hint again on its next step.
    machine.cpu.idle_hint = cpu.halted
    machine.cpu.iff1 = cpu.iff1
    machine.cpu.iff2 = cpu.iff2
    machine.cpu.int_pending = cpu.int_pending
    machine.cpu.nmi_pending = cpu.nmi_pending
    machine.cpu.ei_pending = cpu.ei_pending
    machine.cpu.im = cpu.im
    machine.cpu.q = cpu.q
    machine.cpu.q_next = cpu.q_next


def _apply_memory_and_slots(
    machine: "Machine", snap: MachineSnapshot, parsed: _ParsedProducers
) -> None:
    memory = machine.memory
    memory.ram[:] = snap.memory.ram
    if memory.slot0_ram is not None and snap.memory.slot0_ram is not None:
        memory.slot0_ram[:] = snap.memory.slot0_ram
    rm = memory.ram_mapper
    saved_rm = snap.memory.ram_mapper
    if rm is not None and saved_rm is not None:
        rm.ram[:] = saved_rm.ram
        rm.banks[:] = saved_rm.banks
    memory.set_slot_register(snap.memory.primary_slot_register)
    slot1_reg = snap.slots.slot1.secondary_slot_register
    if slot1_reg is not None:
        memory.set_slot1_sub_slot_reg(slot1_reg)
    slot2_reg = snap.slots.slot2.secondary_slot_register
    if slot2_reg is not None:
        memory.set_slot2_sub_slot_reg(slot2_reg)
    slot3_reg = snap.slots.slot3.secondary_slot_register
    if slot3_reg is not None:
        memory.set_sub_slot_reg(slot3_reg)
    for producer, state in parsed.mappers:
        producer.apply_state(state)


def _apply_vdp(machine: "Machine", vdp: VdpSnapshot) -> None:
    machine.vdp.vram[:] = vdp.vram
    machine.vdp.regs[:] = vdp.registers
    machine.vdp.status = vdp.status
    machine.vdp.frame_count = vdp.frame_count
    # Common VDP address state -- identical field names on both VDP types. The
    # V9938's port latch is restored with the rest of its own state.
    machine.vdp.addr = vdp.address
    machine.vdp.read_buf = vdp.read_buffer
    if isinstance(machine.vdp, V9938):
        assert vdp.v9938 is not None  # parse_snapshot requires it for MSX2
        machine.vdp.restore_state(vdp.v9938)
    else:
        assert isinstance(machine.vdp, VDP)
        machine.vdp.restore_latch(vdp.latch)
    # The VDP's cached /INT level is what the scanline loop feeds the CPU, so
    # it must follow the restored status/registers (incl. V9938 S#1 FH).
    machine.vdp.update_irq()


def _apply_sound_and_io(
    machine: "Machine", snap: MachineSnapshot, parsed: _ParsedProducers
) -> None:
    machine.psg.regs[:] = snap.psg.registers
    machine.psg.latch = snap.psg.latch
    machine.psg.apply_synth_state(parsed.psg_synth)
    machine.psg.discard_frame_events()
    machine.psg.sync_mouse_pins()
    ppi = snap.ppi
    if machine.ppi is not None and ppi is not None:
        machine.ppi.apply_state(
            {"port_c": ppi.port_c, "control": ppi.control, "bus_latch": ppi.bus_latch}
        )
    if machine.scc is not None and parsed.scc is not None:
        machine.scc.apply_state(parsed.scc)
    if machine.fdc is not None and parsed.fdc is not None:
        machine.fdc.apply_state(parsed.fdc)
    if machine.rtc is not None and parsed.rtc is not None:
        machine.rtc.apply_state(parsed.rtc)
    mouse = machine.psg.mouse
    if mouse is not None and parsed.mouse is not None:
        mouse.apply_state(parsed.mouse, machine.cycle_count)
    if machine.printer is not None and snap.printer is not None:
        machine.printer.apply_state({"data": snap.printer.data, "strobe": snap.printer.strobe})
    if machine.mapper_latch is not None and snap.mapper_latch is not None:
        machine.mapper_latch.apply_state(snap.mapper_latch)
    if machine.cassette is not None and snap.tape is not None:
        # After the clock: the position counts from the restored cycle.
        machine.cassette.apply_state(snap.tape.position, snap.tape.motor)


def _restore_snapshot(machine: "Machine", snap: MachineSnapshot) -> None:
    # Everything that can fail runs here, before the first write; the functions
    # below only assign already-validated values.
    parsed = _validate_snapshot(machine, snap)
    _apply_cpu(machine, snap.cpu)
    _apply_memory_and_slots(machine, snap, parsed)
    _apply_vdp(machine, snap.vdp)
    _apply_sound_and_io(machine, snap, parsed)


# --- encoding ---------------------------------------------------------------

def save_state(machine: "Machine") -> bytes:
    """Encode the machine's snapshot as the versioned JSON state format.

    Chooses no file name and writes nothing: host/state_files.py stores the
    bytes (with a screenshot and the latest.* links).
    """
    snap = _snapshot_from_machine(machine)
    return encode_canonical(to_jsonable(asdict(snap)))


def load_state(machine: "Machine", data: bytes) -> None:
    """Restore a machine from save_state() bytes. The error messages do not
    name the data's file: the host adds that.

    Raises:
        StateLoadError: If a field or producer object is malformed.
        ValueError: If the data is not a state of this format version and
            layout, or does not match the running machine (machine type,
            mapper kind, slot / slot-0 RAM / FDC / SCC / RTC / mouse wiring,
            or the MachineConfig the machine was built from, by its SHA-256).
            Nothing is restored then.
    """
    # Legacy pickle files (format_version <= 4) start with a pickle opcode byte,
    # not JSON. Refuse to unpickle them; the format is now stdlib JSON.
    stripped = data.lstrip()
    if stripped[:1] != b"{":
        raise SnapshotError(
            "legacy pickle save states are no longer supported; please re-save"
        )
    try:
        decoded = decode_strict(data)
    except ValueError as exc:
        # An older format may hold floats: report its version, not the floats.
        version = mismatched_version(data, CURRENT_FORMAT_VERSION)
        if version is not None:
            raise SnapshotError(
                f"incompatible state file: version {version}, "
                f"expected {CURRENT_FORMAT_VERSION}"
            ) from exc
        raise SnapshotError(f"corrupt or unsupported save state ({exc})") from exc

    snap = parse_snapshot(decoded)
    _restore_snapshot(machine, snap)
