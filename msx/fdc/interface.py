"""FDC connection-style layer: memory-mapped register decode + DISK ROM.

`FloppyDisk` owns a controller and a list of drives and exposes `read_mem` /
`write_mem` over the DISK-ROM address window (page 1, 0x4000-0x7FFF). Concrete
subclasses implement a machine's "connection style" — how the CPU-visible
addresses map onto controller registers. `SonyPhilipsInterface` is the style used
by the Sony HB-F1XD (openMSX connection style "Sony", implemented by PhilipsFDC).

The drive list is present from the start (indexed by drive number) so a second
drive / runtime disk swap is an additive change, not a refactor.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Generic, Protocol, TypedDict, TypeVar, cast

from msx.fdc.disk_drive import DiskDrive, DiskDriveState
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.tc8566af import TC8566AF, TC8566AFState
from msx.fdc.wd2793 import WD2793, WD2793State
from msx.snapshot_schema import SchemaError, check_int, check_object, check_str

# A controller's own parsed save-state (WD2793State / TC8566AFState).
S = TypeVar("S", bound=Mapping[str, object])
# A connection style's own parsed save-state (SonyPhilipsConnectionState / ...).
C = TypeVar("C", bound=Mapping[str, object])


class FdcController(Protocol[S]):
    """The members `FloppyDiskState`/`FloppyDisk` call on their controller,
    formalised as an explicit contract -- mirrors `Mapper(Protocol)`'s own
    rationale (msx/mapper.py): `WD2793`/`TC8566AF` satisfy this structurally,
    with no explicit inheritance. Generic in the controller's own state
    `TypedDict`, so `apply_state` takes exactly what `parse_state` returned."""

    drive: DiskDrive | None
    def abort(self) -> None: ...
    def snapshot(self) -> Mapping[str, object]: ...
    def restore(self, state: dict[str, object]) -> None: ...
    def parse_state(self, obj: object, label: str) -> S: ...
    def apply_state(self, state: S) -> None: ...


class FdcKind(str, Enum):
    """Closed save-state identity discriminant for a FloppyDisk connection
    style, mirroring MapperKind's own rationale (msx/mapper.py): one member
    per (controller, connection_style) pair msx/machine_loader.py's own
    _SUPPORTED_FDC_PAIRS validates -- a class rename must not change
    save-state compatibility."""

    WD2793_SONY = "wd2793_sony"
    TC8566AF = "tc8566af"


class FloppyDiskStateSnapshot(TypedDict):
    """Save-state schema for FloppyDiskState.snapshot()/parse_state()."""

    controller: Mapping[str, object]  # the controller's own WD2793State / TC8566AFState
    drives: list[DiskDriveState]


class FloppyDiskSnapshot(FloppyDiskStateSnapshot):
    """Save-state schema for FloppyDisk's full state (FloppyDisk.parse_state())."""

    kind: str  # an FdcKind value
    connection_style: Mapping[str, object]  # the connection style's own state


class SonyPhilipsConnectionState(TypedDict):
    """SonyPhilipsInterface's connection-style state."""

    side_reg: int
    drive_reg: int


class TC8566AFConnectionState(TypedDict):
    """TC8566AFInterface's connection-style state: no registers of its own."""


@dataclass(frozen=True)
class FloppyDiskStateParsed(Generic[S]):
    """FloppyDiskState.parse_state()'s result: the controller's and every
    drive's parsed state (drives in FloppyDiskState.drives order)."""

    controller: S
    drives: list[DiskDriveState]


@dataclass(frozen=True)
class FloppyDiskParsedBase(FloppyDiskStateParsed[S], Generic[S, C]):
    """FloppyDisk.parse_state()'s result: FloppyDiskStateParsed plus the
    connection style's parsed state. The FdcKind is not kept -- parse_state()
    already checked it equals the running FDC's own."""

    connection_style: C


_FLOPPY_DISK_STATE_KEYS: frozenset[str] = frozenset({"controller", "drives"})
_FLOPPY_DISK_KEYS: frozenset[str] = frozenset(
    {"kind", "connection_style", "controller", "drives"}
)
_SONY_PHILIPS_CONNECTION_KEYS: frozenset[str] = frozenset({"side_reg", "drive_reg"})

# Sony/Philips register window offsets (addr & 0x3FFF), shared by
# SonyPhilipsInterface.read_mem/write_mem/reset and by _read_reg/_write_reg so
# the two decode tables can't drift apart from each other or from the class
# docstring below.
REG_STATUS_CMD: int = 0x3FF8      # STATUS (read) / COMMAND (write)
REG_TRACK: int = 0x3FF9
REG_SECTOR: int = 0x3FFA
REG_DATA: int = 0x3FFB
REG_SIDE: int = 0x3FFC
REG_DRIVE: int = 0x3FFD           # drive select (bits 1:0) / motor (bit 7)
REG_UNCONNECTED: int = 0x3FFE     # not wired to anything; reads open bus
REG_CONTROL_STATUS: int = 0x3FFF  # active-low INTRQ (bit 6) / DRQ (bit 7)


class FloppyDiskState(Generic[S]):
    """Owned device state every connection style composes: controller, drives,
    DISK ROM, plus the operations that only touch that state.

    Has-a half of the has-a/is-a split documented on `FloppyDisk` below --
    composed into each concrete connection style rather than inherited, so a
    Rust/C++ port's per-style struct can hold this as a plain field and
    implement a separate dispatch trait/interface (read_mem/write_mem/reset)
    on the side, which a single class combining both can't model directly.
    """

    def __init__(
        self,
        controller: FdcController[S],
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ) -> None:
        if not drives:
            raise ValueError("FloppyDisk requires at least one drive")
        self.controller = controller
        self.drives = drives
        self.disk_rom = disk_rom
        # The active drive is tracked solely via self.controller.drive.
        self.controller.drive = self.drives[0]

    def mount(self, image: DskDiskImage | None, drive: int = 0) -> None:
        """Mount (or unmount with None) an image into a drive."""
        self.drives[drive].mount(image)

    def swap(self, drive: int, image: DskDiskImage | None) -> None:
        """Replace a drive's image at runtime (hot swap / eject).

        Flushes the outgoing image so pending writes reach its file, mounts the
        new image (or None to eject), asserts the drive's disk-change signal so
        Disk BASIC re-reads the new medium, and aborts any in-progress controller
        transfer so no buffer keeps referencing the previous disk.
        """
        target = self.drives[drive]
        if target.image is not None:
            target.image.flush()
        target.mount(image)
        target.disk_changed = True
        # Only abort a transfer on the drive actually connected to the
        # controller -- swapping a *different* drive's disk on a multi-drive
        # machine must not disturb an in-progress transfer on the selected one.
        if self.controller.drive is target:
            self.controller.abort()

    def flush(self) -> None:
        """Flush every mounted image's pending writes back to its file."""
        for drive in self.drives:
            if drive.image is not None:
                drive.image.flush()

    def snapshot(self) -> FloppyDiskStateSnapshot:
        """Capture the controller's and every drive's own state (in
        self.drives order). The connection-style-specific piece and the
        FdcKind discriminant are FloppyDisk's own concern, layered around
        this."""
        drives: list[DiskDriveState] = []
        for drive in self.drives:
            drives.append(drive.snapshot())
        return {"controller": self.controller.snapshot(), "drives": drives}

    def parse_state(self, obj: object, label: str) -> FloppyDiskStateParsed[S]:
        """Validate a snapshot() object -- the controller and every drive --
        without mutating anything.

        Raises:
            SchemaError: A malformed field (the message names its path).
            ValueError: A drive-count or mounted-disk identity mismatch.
        """
        controller, drives = self.parse_fields(
            check_object(obj, label, _FLOPPY_DISK_STATE_KEYS), label
        )
        return FloppyDiskStateParsed(controller=controller, drives=drives)

    def parse_fields(
        self, fields: dict[str, object], label: str
    ) -> tuple[S, list[DiskDriveState]]:
        """parse_state() on an object whose key set the caller already
        checked (FloppyDisk's own state adds keys around these two)."""
        drive_objs = fields["drives"]
        if not isinstance(drive_objs, list):
            raise SchemaError(f"{label}.drives must be a list, got {drive_objs!r}")
        if len(drive_objs) != len(self.drives):
            raise ValueError(
                f"FDC drive count mismatch in {label}.drives: running has "
                f"{len(self.drives)} drive(s), saved state has {len(drive_objs)}"
            )
        controller = self.controller.parse_state(fields["controller"], f"{label}.controller")
        drives: list[DiskDriveState] = []
        for i, drive in enumerate(self.drives):
            drives.append(drive.parse_state(drive_objs[i], f"{label}.drives[{i}]"))
        return controller, drives

    def restore(self, state: dict[str, object]) -> None:
        """Restore the controller's and every drive's own state produced by
        snapshot(); the whole state is validated before anything is assigned."""
        self.apply_state(self.parse_state(state, "fdc_state"))

    def apply_state(self, state: FloppyDiskStateParsed[S]) -> None:
        """Assign a state returned by parse_state()."""
        self.controller.apply_state(state.controller)
        for i, drive in enumerate(self.drives):
            drive.apply_state(state.drives[i])


class FloppyDiskBase(Generic[S, C]):
    """Base connection-style device: composes FloppyDiskState, declares the
    read_mem/write_mem/reset register-dispatch surface for subclasses to fill in.

    PORT-NOTE: is-a half of the has-a/is-a split -- read_mem/write_mem/reset
      are declared as NotImplementedError for subclasses to fill in (the
      project's usual interface convention, no abc.ABC anywhere in msx/),
      while the has-a state (controller, drives, disk_rom) lives on the
      composed `self.state: FloppyDiskState` instead of directly on this
      class, exposed here only via read-only properties/thin delegating
      methods for external API compatibility (`.controller`, `.drives`,
      `.disk_rom`, `.mount()`, `.swap()`, `.flush()`).
    Rust equivalent: FloppyDiskState -> an owned-state struct; this class's
      read_mem/write_mem/reset -> a `FloppyInterface` trait each concrete
      connection-style struct implements, holding a `FloppyDiskState` field.
    C++ equivalent: same split -- FloppyDiskState as an owned-state
      struct/class, a small abstract base (or concept) purely for
      read_mem/write_mem/reset.
    RESOLVED: this split was validated against two structurally different
      connection styles (SonyPhilipsInterface's externally-swapped WD2793
      `.drive` vs TC8566AFInterface's TC8566AF, which owns all drives
      directly) -- read_mem(addr) -> int / write_mem(addr, value) -> None /
      reset() -> None hold unchanged across both, confirmed against openMSX's
      own TC8566AF-based connection style (TurboRFDC) too. See
      openspec/changes/archive/2026-08-21-add-tc8566af-fdc/design.md for the
      full comparison (including its post-archive addendum recording this
      split's actual implementation).
      `self.state.controller`'s static type is `FdcController[S]` (not
      WD2793-specific), so TC8566AFInterface no longer needs `typing.cast` to
      construct one -- only `_ctrl()`'s narrowing back to the concrete
      TC8566AF type remains, which is ordinary type narrowing, not a
      declared-type mismatch.
    """

    # Save-state identity discriminant -- each concrete connection style
    # (SonyPhilipsInterface, TC8566AFInterface) assigns its own FdcKind.
    # Never assigned here: FloppyDisk is never instantiated directly, only
    # its two concrete subclasses (mirrors Mapper(Protocol)'s own required
    # `kind` member, msx/mapper.py).
    kind: ClassVar[FdcKind]

    def __init__(
        self,
        controller: FdcController[S],
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ) -> None:
        self.state = FloppyDiskState(controller, drives, disk_rom)

    @property
    def controller(self) -> FdcController[S]:
        return self.state.controller

    @property
    def drives(self) -> list[DiskDrive]:
        return self.state.drives

    @property
    def disk_rom(self) -> bytes | None:
        return self.state.disk_rom

    def mount(self, image: DskDiskImage | None, drive: int = 0) -> None:
        """Mount (or unmount with None) an image into a drive."""
        self.state.mount(image, drive)

    def swap(self, drive: int, image: DskDiskImage | None) -> None:
        """Replace a drive's image at runtime (hot swap / eject). See
        FloppyDiskState.swap for the full behaviour."""
        self.state.swap(drive, image)

    def flush(self) -> None:
        """Flush every mounted image's pending writes back to its file."""
        self.state.flush()

    def _snapshot_connection_style(self) -> C:
        """Connection-style-specific extra state beyond the controller/
        drives FloppyDiskState already captures."""
        raise NotImplementedError

    def _parse_connection_style(self, obj: object, label: str) -> C:
        """Validate a _snapshot_connection_style() object without mutating
        anything."""
        raise NotImplementedError

    def _apply_connection_style(self, state: C) -> None:
        """Assign a state returned by _parse_connection_style()."""
        raise NotImplementedError

    def snapshot(self) -> dict[str, object]:
        """Capture this FDC's full save-state: identity (FdcKind),
        connection-style-specific state, and the composed controller/drives
        state (FloppyDiskState.snapshot())."""
        state: FloppyDiskSnapshot = {
            **self.state.snapshot(),
            "kind": self.kind.value,
            "connection_style": self._snapshot_connection_style(),
        }
        # Returned as a plain dict: msx/state.py stores it as the snapshot's
        # dict[str, object] fdc_state field.
        return dict(state)

    def parse_state(self, obj: object, label: str) -> FloppyDiskParsedBase[S, C]:
        """Validate a snapshot() object -- kind, connection style, the drive
        count, the controller and every drive (with its mounted-disk
        identity) -- without mutating anything.

        Raises:
            SchemaError: A malformed field (the message names its path).
            ValueError: If the snapshot's FdcKind doesn't match this FDC's
                own kind (e.g. a WD2793/Sony state loaded into a TC8566AF
                machine, or vice versa), or on a drive-count or mounted-disk
                identity mismatch.
        """
        fields = check_object(obj, label, _FLOPPY_DISK_KEYS)
        kind_value = check_str(fields["kind"], f"{label}.kind")
        try:
            saved_kind = FdcKind(kind_value)
        except ValueError:
            raise SchemaError(
                f"{label}.kind must be an FdcKind value, got {kind_value!r}"
            ) from None
        if saved_kind != self.kind:
            raise ValueError(
                f"FDC kind mismatch: running {self.kind.value!r}, "
                f"saved {saved_kind.value!r}"
            )
        connection_style = self._parse_connection_style(
            fields["connection_style"], f"{label}.connection_style"
        )
        controller, drives = self.state.parse_fields(fields, label)
        return FloppyDiskParsedBase(
            controller=controller, drives=drives, connection_style=connection_style
        )

    def apply_state(self, state: FloppyDiskParsedBase[S, C]) -> None:
        """Assign a state returned by parse_state(): connection style first
        (it reselects the controller's drive), then controller and drives."""
        self._apply_connection_style(state.connection_style)
        self.state.apply_state(state)

    def restore(self, state: dict[str, object]) -> None:
        """Restore state produced by snapshot(). The whole state is
        validated by parse_state() before anything is assigned.

        Raises:
            ValueError: See parse_state().
        """
        self.apply_state(self.parse_state(state, "fdc_state"))

    def read_mem(self, addr: int) -> int:
        raise NotImplementedError

    def write_mem(self, addr: int, value: int) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


# The FDC as the rest of the machine holds it (Machine.fdc, MemoryBus.fdc):
# which connection style is wired is chosen at run time from the machine YAML,
# so its state types are unknown statically. FloppyDisk.parse_state() returns a
# FloppyDiskParsed, and apply_state() is only ever handed the result of the same
# instance's parse_state(), which keeps the Any sound.
# PORT-NOTE: Rust/C++ -- an enum over the concrete connection styles (and a
#   matching enum of their parsed states), or a trait object whose parsed state
#   is an associated type behind the same enum.
FloppyDisk = FloppyDiskBase[Any, Any]
FloppyDiskParsed = FloppyDiskParsedBase[Any, Any]


class SonyPhilipsInterface(FloppyDiskBase[WD2793State, SonyPhilipsConnectionState]):
    """Sony/Philips connection style (openMSX PhilipsFDC).

    Registers are decoded from ``addr & 0x3FFF`` and appear at 0x?FF8-0x?FFF;
    in the DISK-ROM page that is 0x7FF8-0x7FFF. The DISK ROM is visible at
    0x4000-0x7FFF everywhere else in the page.
    """

    kind: ClassVar[FdcKind] = FdcKind.WD2793_SONY

    def __init__(
        self,
        controller: WD2793,
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ):
        super().__init__(controller, drives, disk_rom)
        self.side_reg = 0
        self.drive_reg = 0

    def _snapshot_connection_style(self) -> SonyPhilipsConnectionState:
        return {"side_reg": self.side_reg, "drive_reg": self.drive_reg}

    def _parse_connection_style(self, obj: object, label: str) -> SonyPhilipsConnectionState:
        fields = check_object(obj, label, _SONY_PHILIPS_CONNECTION_KEYS)
        return {
            "side_reg": check_int(fields["side_reg"], f"{label}.side_reg", 0, 0xFF),
            "drive_reg": check_int(fields["drive_reg"], f"{label}.drive_reg", 0, 0xFF),
        }

    def _apply_connection_style(self, state: SonyPhilipsConnectionState) -> None:
        self.side_reg = state["side_reg"]
        self.drive_reg = state["drive_reg"]
        self._select_drive(self.drive_reg)

    @property
    def controller(self) -> WD2793:
        # self.state.controller's static type is FdcController; this
        # class always constructs with a WD2793, so narrow it back here --
        # mirrors TC8566AFInterface._ctrl().
        return cast(WD2793, self.state.controller)

    def reset(self) -> None:
        """Power-on/Z80-reset: WD2793 core, side select, and drive/motor
        select -- mirrors openMSX's PhilipsFDC::reset() (WD2793BasedFDC::reset()
        plus writing 0 to both 0x3FFC and 0x3FFD). Reuses write_mem() so side 0
        reaches every drive and drive A is reselected regardless of drive count.
        """
        self.controller.reset()
        self.write_mem(REG_SIDE, 0x00)
        self.write_mem(REG_DRIVE, 0x00)

    def read_mem(self, addr: int) -> int:
        reg = addr & 0x3FFF
        if REG_STATUS_CMD <= reg <= REG_CONTROL_STATUS:
            return self._read_reg(reg)
        if self.disk_rom is not None and reg < len(self.disk_rom):
            return self.disk_rom[reg]
        return 0xFF

    def write_mem(self, addr: int, value: int) -> None:
        reg = addr & 0x3FFF
        if REG_STATUS_CMD <= reg <= REG_CONTROL_STATUS:
            self._write_reg(reg, value & 0xFF)
        # DISK ROM (non-register addresses in the window) is read-only.

    def _read_reg(self, reg: int) -> int:
        if reg == REG_STATUS_CMD:
            return self.controller.get_status()
        if reg == REG_TRACK:
            return self.controller.get_track()
        if reg == REG_SECTOR:
            return self.controller.get_sector()
        if reg == REG_DATA:
            return self.controller.get_data()
        if reg == REG_SIDE:
            return self.side_reg & 0xFF
        if reg == REG_DRIVE:
            # bit 2 = 0 iff the disk changed since the last status read. The read
            # is consuming (openMSX PhilipsFDC / diskChanged): it reports the
            # change once, then reverts to "not changed" so the DISK ROM re-reads
            # a swapped-in disk once instead of looping.
            res = self.drive_reg & ~0x04
            drive = self.controller.drive
            if drive is not None and drive.disk_changed:
                drive.disk_changed = False  # consume
            else:
                res |= 0x04  # not changed
            return res
        if reg == REG_UNCONNECTED:
            return 0xFF  # not connected
        # REG_CONTROL_STATUS: active low (bit 6 = !INTRQ, bit 7 = !DRQ).
        value = 0xFF
        if self.controller.get_irq():
            value &= ~0x40
        if self.controller.get_drq():
            value &= ~0x80
        return value

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == REG_STATUS_CMD:
            self.controller.set_command(value)
        elif reg == REG_TRACK:
            self.controller.set_track(value)
        elif reg == REG_SECTOR:
            self.controller.set_sector(value)
        elif reg == REG_DATA:
            self.controller.set_data(value)
        elif reg == REG_SIDE:
            # bit 0 = side select
            self.side_reg = value
            for drive in self.drives:
                drive.side = value & 1
        elif reg == REG_DRIVE:
            # bits 1:0 -> drive (00/10 = A, 01 = B, 11 = none); bit 7 -> motor.
            self.drive_reg = value
            self._select_drive(value)
        # REG_UNCONNECTED / REG_CONTROL_STATUS: no writable control bits.

    def _select_drive(self, value: int) -> None:
        # bits 1:0 -> drive (00/10 = A, 01 = B, 11 = none). Shared by the
        # register write path and _apply_connection_style, so a restored
        # snapshot reattaches whichever drive drive_reg encodes (or none),
        # instead of leaving FloppyDiskState.__init__'s construction-time
        # drives[0] default in place.
        sel = value & 0x03
        if sel in (0, 2):
            idx: int | None = 0
        elif sel == 1:
            idx = 1
        else:
            idx = None
        if idx is not None and idx < len(self.drives):
            self.controller.drive = self.drives[idx]
        else:
            self.controller.drive = None


# TC8566AF connection style register window offsets (addr & 0x3FFF), used by
# the non-turboR TC8566AF-based machines (e.g. Panasonic FS-A1F) -- the
# "R7FF8" register set from openMSX's TurboRFDC.cc.
TC_REG_CONTROL0: int = 0x3FF8  # write-only
TC_REG_CONTROL1: int = 0x3FF9  # write-only
TC_REG_STATUS: int = 0x3FFA    # read-only Main Status Register
TC_REG_DATA: int = 0x3FFB      # read/write Data Register
# 0x3FFC-0x3FFF: not wired to controller state; reads return fixed values
# observed on real non-turboR TC8566AF-based MSX hardware.
_TC_RESERVED: dict[int, int] = {0x3FFC: 0xFC, 0x3FFD: 0xFC, 0x3FFE: 0xFF, 0x3FFF: 0x3F}


class TC8566AFInterface(FloppyDiskBase[TC8566AFState, TC8566AFConnectionState]):
    """TC8566AF connection style (Panasonic FS-A1F).

    Registers are decoded from ``addr & 0x3FFF`` and appear at 0x?FF8-0x?FFF;
    in the DISK-ROM page that is 0x7FF8-0x7FFF. The DISK ROM is visible at
    0x4000-0x7FFF everywhere else in the page, same as SonyPhilipsInterface.
    Unlike SonyPhilipsInterface's WD2793, the TC8566AF controller owns all
    drives directly (Control Register 0 addresses one of them) rather than
    being handed a single externally-swapped "current drive" reference -- see
    openspec/changes/archive/2026-08-21-add-tc8566af-fdc/design.md.
    """

    kind: ClassVar[FdcKind] = FdcKind.TC8566AF

    def __init__(
        self,
        controller: TC8566AF,
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ) -> None:
        super().__init__(controller, drives, disk_rom)

    def _snapshot_connection_style(self) -> TC8566AFConnectionState:
        return {}

    def _parse_connection_style(self, obj: object, label: str) -> TC8566AFConnectionState:
        check_object(obj, label, frozenset())
        return {}

    def _apply_connection_style(self, state: TC8566AFConnectionState) -> None:
        pass  # no connection-style registers

    # self.controller's static type is FdcController (FloppyDisk composes
    # FloppyDiskState, not WD2793-specific); this narrows it locally to the
    # concrete TC8566AF this class always constructs with, same as any other
    # structural-to-concrete narrowing.
    def _ctrl(self) -> TC8566AF:
        return cast(TC8566AF, self.controller)

    def reset(self) -> None:
        self._ctrl().reset()

    def read_mem(self, addr: int) -> int:
        reg = addr & 0x3FFF
        if TC_REG_CONTROL0 <= reg <= TC_REG_DATA or reg in _TC_RESERVED:
            # The whole 0x3FF8-0x3FFF register window (including the two
            # write-only control registers, which have no read behaviour) is
            # never DISK ROM, matching openMSX TurboRFDC::peekMem's default
            # 0xFF for undefined offsets in this region.
            if reg == TC_REG_STATUS:
                return self._ctrl().read_main_status()
            if reg == TC_REG_DATA:
                return self._ctrl().read_data()
            if reg in _TC_RESERVED:
                return _TC_RESERVED[reg]
            return 0xFF  # TC_REG_CONTROL0 / TC_REG_CONTROL1: write-only
        if self.disk_rom is not None and reg < len(self.disk_rom):
            return self.disk_rom[reg]
        return 0xFF

    def write_mem(self, addr: int, value: int) -> None:
        reg = addr & 0x3FFF
        value &= 0xFF
        if reg == TC_REG_CONTROL0:
            self._ctrl().write_control_reg0(value)
        elif reg == TC_REG_CONTROL1:
            self._ctrl().write_control_reg1(value)
        elif reg == TC_REG_DATA:
            self._ctrl().write_data(value)
        # TC_REG_STATUS / reserved offsets / DISK ROM: not writable.
