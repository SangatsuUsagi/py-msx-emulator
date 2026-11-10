"""Sector-based ``*.dsk`` disk image.

A ``.dsk`` file is a linear dump of 512-byte sectors; sector count is the file
size divided by 512. The image is built from the bytes the host read and held
in memory; a sector write marks it dirty, and the host writes ``data()`` back
to the file (see host/media.py). This module opens no files.
"""
from __future__ import annotations

import hashlib

SECTOR_SIZE: int = 512

# Geometry assumed with no image mounted: 720 KB 2DD, 9 sectors/track,
# 2 sides.
FALLBACK_SECTORS_PER_TRACK: int = 9
FALLBACK_SIDES: int = 2
# A 720 KB (2DD) image is always read as 9 sectors, 2 sides, whatever its
# boot sector says (openMSX SectorBasedDisk::detectGeometry).
_SECTORS_2DD: int = 1440
# A 360 KB image with no usable boot sector or FAT media byte is 1 side.
_SECTORS_1DD: int = 720


class DskDiskImage:
    """Linear 512-byte-sector disk image built from a ``*.dsk`` file's bytes.

    Args:
        data: The image bytes.
        write_protected: Reject sector writes (the host passes True when the
            file is not writable). The image holds no file name: the host
            keeps that (host/media.py DiskFiles).

    Raises:
        ValueError: If the size is not a positive multiple of 512.
    """

    def __init__(self, data: bytes, *, write_protected: bool = False) -> None:
        if len(data) == 0 or (len(data) % SECTOR_SIZE) != 0:
            raise ValueError(
                f"size {len(data)} is not a positive multiple of {SECTOR_SIZE}"
            )
        self._data = bytearray(data)
        self._dirty = False
        self._write_protected = write_protected
        self.sectors_per_track, self.sides = self._read_geometry()

    def _read_geometry(self) -> tuple[int, int]:
        """Derive (sectors_per_track, sides) as openMSX's Disk::detectGeometry
        does (the MSX Red Book method): a 720 KB image is always 9/2; else a
        boot sector starting with an 8086 jump (0xE9/0xEB) gives the BPB's
        0x18 sectors/track and 0x1A heads (both little-endian, each 1-255);
        else a FAT media byte (LSN 1, byte 0) of 0xF8 or more gives 8 or 9
        sectors (bit 1) and 1 or 2 sides (bit 0); else 9 sectors, and 1 side
        only for a 360 KB image.
        """
        if self.num_sectors == _SECTORS_2DD:
            return 9, 2
        d = self._data
        if d[0] == 0xE9 or d[0] == 0xEB:
            spt = d[0x18] | (d[0x19] << 8)
            heads = d[0x1A] | (d[0x1B] << 8)
            if 1 <= spt <= 255 and 1 <= heads <= 255:
                return spt, heads
        elif self.num_sectors > 1:
            media = d[SECTOR_SIZE]
            if media >= 0xF8:
                spt = 8 if media & 0x02 else 9
                sides = 2 if media & 0x01 else 1
                return spt, sides
        sides = 1 if self.num_sectors == _SECTORS_1DD else 2
        return 9, sides

    @property
    def num_sectors(self) -> int:
        """Number of 512-byte sectors in the image."""
        return len(self._data) // SECTOR_SIZE

    @property
    def write_protected(self) -> bool:
        """True if sector writes are rejected."""
        return self._write_protected

    def read_sector(self, lsn: int) -> bytes:
        """Return the 512 bytes of logical sector ``lsn`` (0-based).

        Raises:
            IndexError: If ``lsn`` is outside the image.
        """
        if lsn < 0 or lsn >= self.num_sectors:
            raise IndexError(f"sector {lsn} out of range (0..{self.num_sectors - 1})")
        off = lsn * SECTOR_SIZE
        return bytes(self._data[off:off + SECTOR_SIZE])

    def write_sector(self, lsn: int, data: bytes) -> None:
        """Write 512 bytes to logical sector ``lsn`` (0-based).

        Raises:
            PermissionError: If the image is write-protected.
            IndexError: If ``lsn`` is outside the image.
            ValueError: If ``data`` is not exactly 512 bytes.
        """
        if self._write_protected:
            raise PermissionError("disk image is write-protected")
        if lsn < 0 or lsn >= self.num_sectors:
            raise IndexError(f"sector {lsn} out of range (0..{self.num_sectors - 1})")
        if len(data) != SECTOR_SIZE:
            raise ValueError(f"sector data must be {SECTOR_SIZE} bytes, got {len(data)}")
        off = lsn * SECTOR_SIZE
        self._data[off:off + SECTOR_SIZE] = data
        self._dirty = True

    @property
    def dirty(self) -> bool:
        """True after a sector write the host has not persisted yet."""
        return self._dirty

    def mark_persisted(self) -> None:
        """Clear the dirty flag once the host has written data() back."""
        self._dirty = False

    def data(self) -> bytes:
        """The image's current bytes."""
        return bytes(self._data)

    def content_sha1(self) -> str:
        """SHA1 hex digest of the image's current bytes (save-state disk
        identity check; same hashlib convention as host/romdb.py's ROM
        identity)."""
        return hashlib.sha1(bytes(self._data), usedforsecurity=False).hexdigest()
