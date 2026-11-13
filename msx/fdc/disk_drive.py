"""A single floppy disk drive: head position, side, and geometry mapping.

Translates the FDC's (track, side, sector) request into a logical sector number
(LSN) for the mounted image, using the mounted image's geometry (see
msx/fdc/disk_image.py). The LSN ordering interleaves sides
within a cylinder (MSX-DOS ``.dsk`` layout):
``LSN = (track * sides + side) * sectors_per_track + (sector - 1)``.
"""
from __future__ import annotations

from typing import TypedDict

from msx.fdc.disk_image import (
    FALLBACK_SECTORS_PER_TRACK,
    FALLBACK_SIDES,
    SECTOR_SIZE,
    DskDiskImage,
)
from msx.snapshot_schema import (
    SAFE_INT_MAX,
    SchemaError,
    SnapshotMismatchError,
    check_bool,
    check_int,
    check_object,
    check_optional_int,
    check_optional_str,
)

# Highest head position: openMSX RealDrive's MAX_TRACK. A step-in at this
# track leaves the head where it is.
MAX_TRACK: int = 85

# Sector IDs on every track: 1..9. openMSX builds the raw track of a
# sector-based image with nine ID fields whatever the geometry, so sector 9
# of an 8-sector image still resolves (to the next side's first sector).
MAX_SECTOR_ID: int = 9

# MSX FORMAT writes each sector's data as E5h (MSX Computing, August 1985,
# "Peripherals").
FORMAT_FILL: int = 0xE5


class DiskDriveState(TypedDict):
    """Save-state schema for DiskDrive.snapshot()/restore().

    disk_size/disk_sha1 are the mounted image's identity (None when no
    disk is mounted), not its content and not its file -- parse_state()
    rejects a mismatch against the currently-mounted image rather than
    re-mounting it, since the disk image is treated like ROM/SRAM: external
    data a save state doesn't own or embed. The same content mounted from
    another file is the same disk.
    """

    track: int
    side: int
    disk_changed: bool
    disk_size: int | None
    disk_sha1: str | None


# PORT-NOTE: key set read from the TypedDict at import time (see
#   msx/vdp/v9938.py's _STATE_KEYS note: serde `deny_unknown_fields` in Rust,
#   a constexpr key array in C++).
_STATE_KEYS: frozenset[str] = frozenset(DiskDriveState.__annotations__)


def _disk_identity(image: DskDiskImage) -> tuple[int, str]:
    """What the snapshot records of a mounted disk: its size and SHA1."""
    return image.num_sectors * SECTOR_SIZE, image.content_sha1()


class DiskDrive:
    """One drive with a physical head position and an optional mounted image."""

    def __init__(self, image: DskDiskImage | None = None):
        self.image = image
        self.track = 0          # physical head position
        self.side = 0           # selected side (0 or 1)
        # Set at power-on and on every mount/swap; consumed by a status read.
        # openMSX's DiskChanger starts with an eject, which sets its flag.
        self.disk_changed = True

    @property
    def has_disk(self) -> bool:
        return self.image is not None

    @property
    def write_protected(self) -> bool:
        return self.image is not None and self.image.write_protected

    def mount(self, image: DskDiskImage | None) -> None:
        self.image = image
        self.disk_changed = True

    def step(self, inward: bool) -> None:
        """Move the head one track in (toward MAX_TRACK) or out (toward 0),
        stopping at either end."""
        if inward:
            if self.track < MAX_TRACK:
                self.track += 1
        elif self.track > 0:
            self.track -= 1

    def _geometry(self) -> tuple[int, int]:
        """(sectors_per_track, sides) from the mounted image, or the 2DD default."""
        if self.image is not None:
            return self.image.sectors_per_track, self.image.sides
        return FALLBACK_SECTORS_PER_TRACK, FALLBACK_SIDES

    def lsn(self, track: int, side: int, sector: int) -> int:
        """Logical sector number for (track, side, 1-based sector), or -1 when
        the sector number is outside 1..MAX_SECTOR_ID (no such sector ID on
        the track)."""
        if sector < 1 or sector > MAX_SECTOR_ID:
            return -1
        return self._track_first_lsn(track, side) + (sector - 1)

    def _track_first_lsn(self, track: int, side: int) -> int:
        """The LSN of sector 1 on (track, side). format_track uses it
        directly: lsn() rejects sector numbers above MAX_SECTOR_ID, and a
        track can hold more sectors than that."""
        spt, sides = self._geometry()
        return (track * sides + side) * spt

    def has_sector(self, track: int, side: int, sector: int) -> bool:
        """True when read_sector would find the sector; copies no data."""
        if self.image is None:
            return False
        lsn = self.lsn(track, side, sector)
        return 0 <= lsn < self.image.num_sectors

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
        spt, _sides = self._geometry()
        blank = bytes([fill & 0xFF]) * SECTOR_SIZE
        first = self._track_first_lsn(track, side)
        for lsn in range(first, first + spt):
            if 0 <= lsn < self.image.num_sectors:
                self.image.write_sector(lsn, blank)
        return True

    def snapshot(self) -> DiskDriveState:
        """Capture head position and the mounted image's identity (not its
        content -- see DiskDriveState)."""
        if self.image is None:
            disk_size: int | None = None
            disk_sha1: str | None = None
        else:
            disk_size, disk_sha1 = _disk_identity(self.image)
        return {
            "track": self.track,
            "side": self.side,
            "disk_changed": self.disk_changed,
            "disk_size": disk_size,
            "disk_sha1": disk_sha1,
        }

    def parse_state(self, obj: object, label: str) -> DiskDriveState:
        """Validate a snapshot() object against this drive without mutating
        it, including the mounted image's identity.

        Raises:
            SchemaError: A missing/unknown key or a value of the wrong type
                or range, or an identity that is only partly None.
            SnapshotMismatchError: If exactly one of {a disk is mounted now, a disk was
                mounted at snapshot time} is true, or both are mounted but
                their (size, sha1) identity differs.
        """
        fields = check_object(obj, label, _STATE_KEYS)
        track = check_int(fields["track"], f"{label}.track", 0, 0xFF)
        side = check_int(fields["side"], f"{label}.side", 0, 1)
        disk_changed = check_bool(fields["disk_changed"], f"{label}.disk_changed")
        disk_size = check_optional_int(fields["disk_size"], f"{label}.disk_size", 1, SAFE_INT_MAX)
        disk_sha1 = check_optional_str(fields["disk_sha1"], f"{label}.disk_sha1")
        has_disk = disk_size is not None
        if has_disk != (disk_sha1 is not None):
            raise SchemaError(f"{label}: disk_size and disk_sha1 must be both None or both set")
        if has_disk != (self.image is not None):
            raise SnapshotMismatchError(
                f"{label}: disk mount mismatch: "
                f"running has {'a disk' if self.image is not None else 'no disk'} mounted, "
                f"saved state has {'a disk' if has_disk else 'no disk'} mounted"
            )
        if self.image is not None:
            current = _disk_identity(self.image)
            saved = (disk_size, disk_sha1)
            if current != saved:
                raise SnapshotMismatchError(
                    f"{label}: disk mismatch: running ({current[0]} bytes, sha1 {current[1]}), "
                    f"saved ({saved[0]} bytes, sha1 {saved[1]})"
                )
        return {
            "track": track,
            "side": side,
            "disk_changed": disk_changed,
            "disk_size": disk_size,
            "disk_sha1": disk_sha1,
        }

    def apply_state(self, state: DiskDriveState) -> None:
        """Assign a state returned by parse_state()."""
        self.track = state["track"]
        self.side = state["side"]
        self.disk_changed = state["disk_changed"]
