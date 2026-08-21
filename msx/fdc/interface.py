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

from typing import cast

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.tc8566af import TC8566AF
from msx.fdc.wd2793 import WD2793

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


class FloppyDisk:
    """Base connection-style device wiring a controller + drives + DISK ROM.

    PORT-NOTE: this base class both owns concrete state (controller, drives,
      disk_rom) and declares read_mem/write_mem/reset as NotImplementedError
      for subclasses to fill in -- the project's usual interface convention
      (no abc.ABC anywhere in msx/).
    Rust equivalent: a trait can't carry required fields the way this class
      carries them -- split into an owned-state struct (has-a: controller,
      drives, disk_rom) plus a `FloppyInterface` trait for the dispatch
      methods (is-a), composed rather than inherited.
    C++ equivalent: same split -- an owned-state struct/class plus a small
      abstract base (or concept) purely for read_mem/write_mem/reset, not one
      class carrying both.
    Kept as-is here because: port target/shape not decided yet. The split's
      external ergonomics (has-a state accessed via `.drives`/`.swap()`/
      `.mount()`, is-a dispatch via `.read_mem`/`.write_mem`/`.reset`) are
      already exercised by today's single implementation, so that much is
      answerable now -- what's actually blocked is validating the dispatch
      trait's method *signatures* (read_mem/write_mem/reset's exact shape),
      which needs a second, structurally different connection style to
      confirm against. SonyPhilipsInterface alone can't tell us that. See
      logs/review-python-20260814-210824.md.
    RESOLVED (was a TODO to revisit this split when a second, structurally
      different connection style existed): TC8566AFInterface below is that
      case, and it validates the dispatch trait as designed -- read_mem(addr)
      -> int / write_mem(addr, value) -> None / reset() -> None hold
      unchanged, no time parameter or extra return value needed, confirmed
      against both this implementation and openMSX's own TC8566AF-based
      connection style (TurboRFDC). No has-a split was needed; the has-a/is-a
      analysis above is otherwise unaffected and still applies if/when a
      Rust/C++ port is undertaken (still not decided). See
      openspec/changes/add-tc8566af-fdc/design.md for the full comparison.
      One real (if narrow) signature gap did surface only once a second
      controller *type* existed, not a second connection-style *dispatch
      signature*: this class's own `__init__(self, controller: WD2793, ...)`
      type hint is WD2793-specific, so `self.controller`'s static type is
      inherited as `WD2793` in TC8566AFInterface too; that class works around
      it locally with `typing.cast` rather than widening this hint here (a
      small, deliberately deferred follow-up -- not a decision to make from
      inside a single connection-style class).
    """

    def __init__(
        self,
        controller: WD2793,
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ):
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

    def __init__(
        self,
        controller: WD2793,
        drives: list[DiskDrive],
        disk_rom: bytes | None = None,
    ):
        super().__init__(controller, drives, disk_rom)
        self.side_reg = 0
        self.drive_reg = 0

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
        # REG_UNCONNECTED / REG_CONTROL_STATUS: no writable control bits.


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
    openspec/changes/add-tc8566af-fdc/design.md.
    """

    # PORT-NOTE: FloppyDisk.__init__ (not touched by this change) declares
    #   `controller: WD2793`, so `self.controller`'s static type is inherited
    #   as WD2793 here too. `_ctrl()` narrows it locally to TC8566AF via
    #   typing.cast rather than widening that base-class type hint --
    #   loosening it is a small, deliberately deferred follow-up (see
    #   openspec/changes/add-tc8566af-fdc/design.md), not a decision to make
    #   from inside the connection-style layer alone.
    def _ctrl(self) -> TC8566AF:
        return cast(TC8566AF, self.controller)

    def reset(self) -> None:
        self._ctrl().reset()

    def read_mem(self, addr: int) -> int:
        reg = addr & 0x3FFF
        if reg == TC_REG_STATUS:
            return self._ctrl().read_main_status()
        if reg == TC_REG_DATA:
            return self._ctrl().read_data()
        if reg in _TC_RESERVED:
            return _TC_RESERVED[reg]
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
