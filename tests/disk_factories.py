"""Shared .dsk-image byte helpers for disk/FDC tests.

Split out of tests/test_disk_image.py so tests/test_disk_drive.py doesn't
have to duplicate the same constant/helper (both cover DiskDrive/DskDiskImage
from different angles -- see each file's own module docstring).
"""
from __future__ import annotations

from pathlib import Path

from msx.fdc.disk_image import DskDiskImage

_2DD_BYTES = 737280  # 720 KB: 80 tracks * 2 sides * 9 sectors * 512


def make_dsk(path: Path, size: int = _2DD_BYTES, fill: int = 0x00) -> Path:
    path.write_bytes(bytes([fill]) * size)
    return path


def dsk_image(path: Path, *, write_protected: bool = False) -> DskDiskImage:
    """A DskDiskImage built from the file's bytes, named by its path (what
    host.media.read_disk_medium plus Machine.mount_disk do, with write
    protection chosen by the test instead of the file mode)."""
    return DskDiskImage(path.read_bytes(), write_protected=write_protected, path=path)
