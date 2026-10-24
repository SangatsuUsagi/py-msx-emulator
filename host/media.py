"""Host side of the machine's writable media: files <-> media bytes.

The core exposes SRAM, RTC CMOS RAM and mounted disk images as bytes with
dirty flags (Machine.dirty_media/read_media/mark_persisted/mount_disk); this
module reads disk files, writes dirty media back to their files and resolves
swap paths.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from msx.boundary import DiskMedium, drive_media_id

if TYPE_CHECKING:
    from msx.machine import Machine


def read_disk_medium(path: Path) -> DiskMedium:
    """Read a .dsk file, write-protected when the file is not writable.
    Raises OSError when it cannot be read."""
    return DiskMedium(path.read_bytes(), not os.access(path, os.W_OK), path)


def media_paths(machine: Machine, sram_paths: Mapping[str, Path]) -> dict[str, Path]:
    """The file each persisted medium is written to, by media id: the SRAM
    files `sram_paths` names (host.machine_config.sram_paths of the
    machine's config) plus each mounted disk's own file."""
    paths = dict(sram_paths)
    if machine.fdc is not None:
        for index in range(len(machine.fdc.drives)):
            path = machine.disk_path(index)
            if path is not None:
                paths[drive_media_id(index)] = path
    return paths


def persist_dirty_media(
    machine: Machine, sram_paths: Mapping[str, Path] | None = None
) -> None:
    """Write every dirty medium that has a file back to it. Without
    `sram_paths` only disk images are written."""
    paths = media_paths(machine, sram_paths if sram_paths is not None else {})
    for media_id in machine.dirty_media():
        path = paths.get(media_id)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(machine.read_media(media_id))
        machine.mark_persisted(media_id)


def swap_disk(machine: Machine, drive: int, path: Path | None) -> None:
    """Mount the .dsk at `path` (None ejects) in `drive` (0 = A), writing the
    outgoing image back to its file first when it is dirty. `path` is taken
    relative to the working directory, with `~` expanded. Raises OSError or
    ValueError, leaving the drive as it was, when the file cannot be read, is
    not a valid image, or the drive does not exist."""
    medium = read_disk_medium(path.expanduser()) if path is not None else None
    outgoing_path = machine.disk_path(drive)
    outgoing = machine.mount_disk(drive, medium)
    if outgoing is not None and outgoing_path is not None:
        outgoing_path.write_bytes(outgoing)
