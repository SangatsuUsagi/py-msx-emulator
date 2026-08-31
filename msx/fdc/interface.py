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

from enum import Enum
from typing import ClassVar, cast

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.tc8566af import TC8566AF
from msx.fdc.wd2793 import WD2793


class FdcKind(str, Enum):
    """Closed save-state identity discriminant for a FloppyDisk connection
    style, mirroring MapperKind's own rationale (msx/mapper.py): one member
    per (controller, connection_style) pair msx/machine_loader.py's own
    _SUPPORTED_FDC_PAIRS validates -- a class rename must not change
    save-state compatibility."""

    WD2793_SONY = "wd2793_sony"
    TC8566AF = "tc8566af"

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


class FloppyDiskState:
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
        controller: WD2793 | TC8566AF,
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

    def snapshot(self) -> dict[str, object]:
        """Capture the controller's and every drive's own state (in
        self.drives order). The connection-style-specific piece and the
        FdcKind discriminant are FloppyDisk's own concern, layered around
        this."""
        return {
            "controller": cast(dict[str, object], self.controller.snapshot()),
            "drives": [cast(dict[str, object], drive.snapshot()) for drive in self.drives],
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore the controller's and every drive's own state produced by
        snapshot()."""
        drive_states = cast(list[dict[str, object]], state["drives"])
        if len(drive_states) != len(self.drives):
            raise ValueError(
                f"FDC drive count mismatch: running has {len(self.drives)} drive(s), "
                f"saved state has {len(drive_states)}"
            )
        self.controller.restore(cast(dict[str, object], state["controller"]))
        for drive, drive_state in zip(self.drives, drive_states, strict=True):
            drive.restore(drive_state)


class FloppyDisk:
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
      `self.state.controller`'s static type is `WD2793 | TC8566AF` (not
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
        controller: WD2793 | TC8566AF,
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ) -> None:
        self.state = FloppyDiskState(controller, drives, disk_rom)

    @property
    def controller(self) -> WD2793 | TC8566AF:
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

    def _snapshot_connection_style(self) -> dict[str, object]:
        """Connection-style-specific extra state beyond the controller/
        drives FloppyDiskState already captures. Empty by default;
        SonyPhilipsInterface overrides this for side_reg/drive_reg."""
        return {}

    def _restore_connection_style(self, state: dict[str, object]) -> None:
        """Inverse of _snapshot_connection_style(). No-op by default."""

    def snapshot(self) -> dict[str, object]:
        """Capture this FDC's full save-state: identity (FdcKind),
        connection-style-specific state, and the composed controller/drives
        state (FloppyDiskState.snapshot())."""
        state = dict(self.state.snapshot())
        state["kind"] = self.kind.value
        state["connection_style"] = self._snapshot_connection_style()
        return state

    def restore(self, state: dict[str, object]) -> None:
        """Restore state produced by snapshot().

        Raises:
            ValueError: If the snapshot's FdcKind doesn't match this FDC's
                own kind (e.g. a WD2793/Sony state loaded into a TC8566AF
                machine, or vice versa).
        """
        saved_kind = FdcKind(state["kind"])
        if saved_kind != self.kind:
            raise ValueError(
                f"FDC kind mismatch: running {self.kind.value!r}, "
                f"saved {saved_kind.value!r}"
            )
        self._restore_connection_style(cast(dict[str, object], state["connection_style"]))
        self.state.restore(state)

    def read_mem(self, addr: int) -> int:
        raise NotImplementedError

    def write_mem(self, addr: int, value: int) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


class SonyPhilipsInterface(FloppyDisk):
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

    def _snapshot_connection_style(self) -> dict[str, object]:
        return {"side_reg": self.side_reg, "drive_reg": self.drive_reg}

    def _restore_connection_style(self, state: dict[str, object]) -> None:
        self.side_reg = cast(int, state["side_reg"])
        self.drive_reg = cast(int, state["drive_reg"])
        self._select_drive(self.drive_reg)

    @property
    def controller(self) -> WD2793:
        # self.state.controller's static type is WD2793 | TC8566AF; this
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
        # register write path and _restore_connection_style, so a restored
        # snapshot reattaches the same drive (or none) drive_reg encoded,
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


class TC8566AFInterface(FloppyDisk):
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

    # self.controller's static type is WD2793 | TC8566AF (FloppyDisk composes
    # FloppyDiskState, not WD2793-specific); this narrows it locally to the
    # concrete TC8566AF this class always constructs with, same as any other
    # union-to-member narrowing.
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
