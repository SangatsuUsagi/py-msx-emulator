"""A single floppy disk drive: head position, side, and geometry mapping.

Translates the FDC's (track, side, sector) request into a logical sector number
(LSN) for the mounted image, using the mounted image's geometry (derived from its
boot-sector BPB, falling back to 720 KB 2DD). The LSN ordering interleaves sides
within a cylinder (MSX-DOS ``.dsk`` layout):
``LSN = (track * sides + side) * sectors_per_track + (sector - 1)``.
"""
from __future__ import annotations

from msx.fdc.disk_image import (
    FALLBACK_SECTORS_PER_TRACK,
    FALLBACK_SIDES,
    DskDiskImage,
)

FORMAT_FILL: int = 0xE5


class DiskDrive:
    """One drive with a physical head position and an optional mounted image."""

    def __init__(self, image: DskDiskImage | None = None):
        self.image = image
        self.track = 0          # physical head position
        self.side = 0           # selected side (0 or 1)
        self.disk_changed = False  # set on a media swap; consumed by a status read

    @property
    def has_disk(self) -> bool:
        return self.image is not None

    @property
    def write_protected(self) -> bool:
        return self.image is not None and self.image.write_protected

    def mount(self, image: DskDiskImage | None) -> None:
        self.image = image

    def unmount(self) -> None:
        # Kept for the symmetric mount()/unmount() public API; runtime ejection
        # currently goes through mount(None) / FloppyDisk.swap(drive, None).
        self.image = None

    def _geometry(self) -> tuple[int, int]:
        """(sectors_per_track, sides) from the mounted image, or the 2DD default."""
        if self.image is not None:
            return self.image.sectors_per_track, self.image.sides
        return FALLBACK_SECTORS_PER_TRACK, FALLBACK_SIDES

    def lsn(self, track: int, side: int, sector: int) -> int:
        """Logical sector number for (track, side, 1-based sector)."""
        spt, sides = self._geometry()
        return (track * sides + side) * spt + (sector - 1)

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
        spt, _ = self._geometry()
        blank = bytes([fill & 0xFF]) * 512
        for sector in range(1, spt + 1):
            lsn = self.lsn(track, side, sector)
            if 0 <= lsn < self.image.num_sectors:
                self.image.write_sector(lsn, blank)
        return True
