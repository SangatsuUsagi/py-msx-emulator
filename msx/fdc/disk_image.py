"""Sector-based ``*.dsk`` disk image.

A ``.dsk`` file is a linear dump of 512-byte sectors; sector count is the file
size divided by 512. Data is held in memory and written back to the file on
``flush()`` so an aborted run cannot leave the image half-written.
"""
from __future__ import annotations

import os
from pathlib import Path

SECTOR_SIZE: int = 512

# Fallback geometry when the boot sector holds no recognised MSX-DOS BPB
# (e.g. a blank/unformatted image): 720 KB 2DD, 9 sectors/track, 2 sides.
FALLBACK_SECTORS_PER_TRACK: int = 9
FALLBACK_SIDES: int = 2


class DskDiskImage:
    """Linear 512-byte-sector disk image backed by a ``*.dsk`` file.

    Args:
        path: Path to the ``.dsk`` file (must already exist).
        write_protected: Force write protection. When None (default) the image
            is write-protected iff the underlying file is not writable.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file size is not a positive multiple of 512.
    """

    def __init__(self, path: str | os.PathLike[str], *, write_protected: bool | None = None):
        self.path = Path(path)
        raw = self.path.read_bytes()
        if len(raw) == 0 or (len(raw) % SECTOR_SIZE) != 0:
            raise ValueError(
                f"{self.path}: size {len(raw)} is not a positive multiple of {SECTOR_SIZE}"
            )
        self._data = bytearray(raw)
        self._dirty = False
        if write_protected is None:
            write_protected = not os.access(self.path, os.W_OK)
        self._write_protected = bool(write_protected)
        self.sectors_per_track, self.sides = self._read_geometry()

    def _read_geometry(self) -> tuple[int, int]:
        """Derive (sectors_per_track, sides) from the FAT12 boot-sector BPB.

        BPB fields (little-endian): 0x0B bytes/sector, 0x13 total sectors,
        0x18 sectors/track, 0x1A number of heads. Falls back to 720 KB 2DD when
        the boot sector is not a recognised MSX-DOS BPB (e.g. a blank image),
        validated by a 512-byte sector size, sane geometry, and a total-sector
        count that matches the file size.
        """
        d = self._data
        if len(d) >= SECTOR_SIZE:
            bytes_per_sector = d[0x0B] | (d[0x0C] << 8)
            total = d[0x13] | (d[0x14] << 8)
            spt = d[0x18] | (d[0x19] << 8)
            heads = d[0x1A] | (d[0x1B] << 8)
            if (bytes_per_sector == SECTOR_SIZE
                    and 1 <= spt <= 63
                    and heads in (1, 2)
                    and total == self.num_sectors):
                return spt, heads
        return FALLBACK_SECTORS_PER_TRACK, FALLBACK_SIDES

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
            raise PermissionError(f"{self.path} is write-protected")
        if lsn < 0 or lsn >= self.num_sectors:
            raise IndexError(f"sector {lsn} out of range (0..{self.num_sectors - 1})")
        if len(data) != SECTOR_SIZE:
            raise ValueError(f"sector data must be {SECTOR_SIZE} bytes, got {len(data)}")
        off = lsn * SECTOR_SIZE
        self._data[off:off + SECTOR_SIZE] = data
        self._dirty = True

    def flush(self) -> None:
        """Write pending changes back to the file (no-op if unchanged/protected)."""
        if self._dirty and not self._write_protected:
            self.path.write_bytes(self._data)
            self._dirty = False
