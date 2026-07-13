"""A single floppy disk drive: head position, side, and geometry mapping.

Translates the FDC's (track, side, sector) request into a logical sector number
(LSN) for the mounted image. Milestone 1 uses fixed 720 KB 2DD geometry
(9 sectors/track, 2 sides, 80 tracks); boot-sector BPB detection is a follow-up.
"""
from __future__ import annotations

from msx.fdc.disk_image import DskDiskImage

# Fixed 720 KB 2DD geometry (see module docstring; BPB detection is deferred).
SECTORS_PER_TRACK: int = 9
SIDES: int = 2
FORMAT_FILL: int = 0xE5


class DiskDrive:
    """One drive with a physical head position and an optional mounted image.

    LSN ordering interleaves sides within a cylinder (MSX-DOS ``.dsk`` layout):
    ``LSN = (track * SIDES + side) * SECTORS_PER_TRACK + (sector - 1)`` with
    ``sector`` 1-based as issued by the FDC.
    """

    def __init__(self, image: DskDiskImage | None = None):
        self.image = image
        self.track = 0   # physical head position
        self.side = 0    # selected side (0 or 1)

    @property
    def has_disk(self) -> bool:
        return self.image is not None

    @property
    def write_protected(self) -> bool:
        return self.image is not None and self.image.write_protected

    def mount(self, image: DskDiskImage | None) -> None:
        self.image = image

    def unmount(self) -> None:
        self.image = None

    def lsn(self, track: int, side: int, sector: int) -> int:
        """Logical sector number for (track, side, 1-based sector)."""
        return (track * SIDES + side) * SECTORS_PER_TRACK + (sector - 1)

    def read_sector(self, track: int, side: int, sector: int) -> bytes | None:
        """Return sector bytes, or None if no disk / sector out of geometry."""
        if self.image is None:
            return None
        lsn = self.lsn(track, side, sector)
        if lsn < 0 or lsn >= self.image.num_sectors:
            return None
        return self.image.read_sector(lsn)

    def write_sector(self, track: int, side: int, sector: int, data: bytes) -> bool:
        """Write a sector; return False if no disk / write-protected / out of range."""
        if self.image is None or self.image.write_protected:
            return False
        lsn = self.lsn(track, side, sector)
        if lsn < 0 or lsn >= self.image.num_sectors:
            return False
        self.image.write_sector(lsn, data)
        return True

    def format_track(self, track: int, side: int, fill: int = FORMAT_FILL) -> bool:
        """Blank every sector of (track, side) to ``fill`` (WRITE TRACK model).

        Returns False if no disk / write-protected.
        """
        if self.image is None or self.image.write_protected:
            return False
        blank = bytes([fill & 0xFF]) * 512
        for sector in range(1, SECTORS_PER_TRACK + 1):
            lsn = self.lsn(track, side, sector)
            if 0 <= lsn < self.image.num_sectors:
                self.image.write_sector(lsn, blank)
        return True
