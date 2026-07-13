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

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.wd2793 import WD2793


class FloppyDisk:
    """Base connection-style device wiring a controller + drives + DISK ROM."""

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

    def read_mem(self, addr: int) -> int:
        reg = addr & 0x3FFF
        if 0x3FF8 <= reg <= 0x3FFF:
            return self._read_reg(reg)
        if self.disk_rom is not None and reg < len(self.disk_rom):
            return self.disk_rom[reg]
        return 0xFF

    def write_mem(self, addr: int, value: int) -> None:
        reg = addr & 0x3FFF
        if 0x3FF8 <= reg <= 0x3FFF:
            self._write_reg(reg, value & 0xFF)
        # DISK ROM (non-register addresses in the window) is read-only.

    def _read_reg(self, reg: int) -> int:
        if reg == 0x3FF8:
            return self.controller.get_status()
        if reg == 0x3FF9:
            return self.controller.get_track()
        if reg == 0x3FFA:
            return self.controller.get_sector()
        if reg == 0x3FFB:
            return self.controller.get_data()
        if reg == 0x3FFC:
            return self.side_reg & 0xFF
        if reg == 0x3FFD:
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
        if reg == 0x3FFE:
            return 0xFF  # not connected
        # 0x3FFF: drive control lines, active low (bit 6 = !INTRQ, bit 7 = !DRQ).
        value = 0xFF
        if self.controller.get_irq():
            value &= ~0x40
        if self.controller.get_drq():
            value &= ~0x80
        return value

    def _write_reg(self, reg: int, value: int) -> None:
        if reg == 0x3FF8:
            self.controller.set_command(value)
        elif reg == 0x3FF9:
            self.controller.set_track(value)
        elif reg == 0x3FFA:
            self.controller.set_sector(value)
        elif reg == 0x3FFB:
            self.controller.set_data(value)
        elif reg == 0x3FFC:
            # bit 0 = side select
            self.side_reg = value
            for drive in self.drives:
                drive.side = value & 1
        elif reg == 0x3FFD:
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
        # 0x3FFE / 0x3FFF: no writable control bits.
