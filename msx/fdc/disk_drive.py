"""A single floppy disk drive: head position, side, and geometry mapping.

Translates the FDC's (track, side, sector) request into a logical sector number
(LSN) for the mounted image, using the mounted image's geometry (derived from its
boot-sector BPB, falling back to 720 KB 2DD). The LSN ordering interleaves sides
within a cylinder (MSX-DOS ``.dsk`` layout):
``LSN = (track * sides + side) * sectors_per_track + (sector - 1)``.
"""
from __future__ import annotations

from typing import TypedDict, cast

from msx.fdc.disk_image import (
    FALLBACK_SECTORS_PER_TRACK,
    FALLBACK_SIDES,
    SECTOR_SIZE,
    DskDiskImage,
)

FORMAT_FILL: int = 0xE5


class DiskDriveState(TypedDict):
    """Save-state schema for DiskDrive.snapshot()/restore().

    disk_path/disk_size/disk_sha1 are the mounted image's identity (None
    when no disk is mounted), not its content -- restore() rejects a
    mismatch against the currently-mounted image rather than re-mounting
    from the saved path, since the disk file itself is treated like
    ROM/SRAM: an external file save-state doesn't own or embed.
    """

    track: int
    side: int
    disk_changed: bool
    disk_path: str | None
    disk_size: int | None
    disk_sha1: str | None


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
        blank = bytes([fill & 0xFF]) * SECTOR_SIZE
        for sector in range(1, spt + 1):
            lsn = self.lsn(track, side, sector)
            if 0 <= lsn < self.image.num_sectors:
                self.image.write_sector(lsn, blank)
        return True

    def snapshot(self) -> DiskDriveState:
        """Capture head position and the mounted image's identity (not its
        content -- see DiskDriveState)."""
        if self.image is None:
            disk_path: str | None = None
            disk_size: int | None = None
            disk_sha1: str | None = None
        else:
            disk_path = str(self.image.path)
            disk_size = self.image.num_sectors * SECTOR_SIZE
            disk_sha1 = self.image.content_sha1()
        return {
            "track": self.track,
            "side": self.side,
            "disk_changed": self.disk_changed,
            "disk_path": disk_path,
            "disk_size": disk_size,
            "disk_sha1": disk_sha1,
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore head position after verifying the currently-mounted
        image's identity matches the snapshot's.

        Raises:
            ValueError: If exactly one of {a disk is mounted now, a disk was
                mounted at snapshot time} is true, or both are mounted but
                their (path, size, sha1) identity differs.
        """
        typed_state = cast(DiskDriveState, state)
        saved_path = typed_state["disk_path"]
        if (saved_path is None) != (self.image is None):
            raise ValueError(
                "disk mount mismatch: "
                f"running has {'a disk' if self.image is not None else 'no disk'} mounted, "
                f"saved state has {'a disk' if saved_path is not None else 'no disk'} mounted"
            )
        if self.image is not None and saved_path is not None:
            current = (
                str(self.image.path),
                self.image.num_sectors * SECTOR_SIZE,
                self.image.content_sha1(),
            )
            saved = (saved_path, typed_state["disk_size"], typed_state["disk_sha1"])
            if current != saved:
                raise ValueError(
                    f"disk mismatch: running {current[0]!r} "
                    f"({current[1]} bytes, sha1 {current[2]}), "
                    f"saved {saved[0]!r} ({saved[1]} bytes, sha1 {saved[2]})"
                )
        self.track = typed_state["track"]
        self.side = typed_state["side"]
        self.disk_changed = typed_state["disk_changed"]
