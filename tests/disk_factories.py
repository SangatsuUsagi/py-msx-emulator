"""Shared .dsk-image byte helpers for disk/FDC tests.

Split out of tests/test_disk_image.py so tests/test_disk_drive.py doesn't
have to duplicate the same constant/helper (both cover DiskDrive/DskDiskImage
from different angles -- see each file's own module docstring).
"""
from __future__ import annotations

from pathlib import Path

_2DD_BYTES = 737280  # 720 KB: 80 tracks * 2 sides * 9 sectors * 512


def make_dsk(path: Path, size: int = _2DD_BYTES, fill: int = 0x00) -> Path:
    path.write_bytes(bytes([fill]) * size)
    return path
