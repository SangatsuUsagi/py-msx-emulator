"""Host side of the machine's writable media: files <-> media bytes.

The core exposes SRAM, RTC CMOS RAM and mounted disk images as bytes with
dirty flags (Machine.dirty_media/read_media/mark_persisted/mount_disk); this
module reads disk files, keeps the file of each drive's disk (DiskFiles),
writes dirty media back to their files and resolves swap paths.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from msx.boundary import STATUS_OK, DiskMedium, MediaId, drive_media_id

if TYPE_CHECKING:
    from msx.core_api import CoreApi


def read_disk_medium(path: Path) -> DiskMedium:
    """Read a .dsk file, write-protected when the file is not writable.
    Raises OSError when it cannot be read."""
    return DiskMedium(path.read_bytes(), not os.access(path, os.W_OK))


class DiskFiles:
    """The file of the disk image in each floppy drive (0 = A), by drive
    index. The core keeps no file name for a disk: the host writes a dirty
    image back to the file named here and shows it in the window title and
    the debugger's fdd command. One table is shared by everything that
    swaps or persists disks in a run."""

    def __init__(self, paths: Mapping[int, Path] | None = None) -> None:
        self._paths: dict[int, Path] = dict(paths) if paths is not None else {}

    def path(self, drive: int) -> Path | None:
        """The file of the image in `drive`, or None when the drive is
        empty or its image has no file."""
        return self._paths.get(drive)

    def set(self, drive: int, path: Path | None) -> None:
        """Record `path` (None: no file) as the file of `drive`."""
        if path is None:
            self._paths.pop(drive, None)
        else:
            self._paths[drive] = path

    def items(self) -> list[tuple[int, Path]]:
        """(drive, file) pairs, ascending by drive."""
        return sorted(self._paths.items())


def media_paths(
    machine: CoreApi, sram_paths: Mapping[MediaId, Path], disk_files: DiskFiles
) -> dict[MediaId, Path]:
    """The file each persisted medium is written to, by media id: the SRAM
    files `sram_paths` names (host.machine_config.sram_paths of the
    machine's config) plus the file `disk_files` records for each mounted
    disk."""
    paths = dict(sram_paths)
    for drive, path in disk_files.items():
        if machine.disk_mounted(drive):
            paths[drive_media_id(drive)] = path
    return paths


def persist_dirty_media(
    machine: CoreApi,
    sram_paths: Mapping[MediaId, Path] | None = None,
    disk_files: DiskFiles | None = None,
) -> None:
    """Write every dirty medium that has a file back to it. Without
    `sram_paths` no SRAM is written, and without `disk_files` no disk."""
    paths = media_paths(
        machine,
        sram_paths if sram_paths is not None else {},
        disk_files if disk_files is not None else DiskFiles(),
    )
    for media_id in machine.dirty_media():
        path = paths.get(media_id)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(machine.read_media(media_id))
        machine.mark_persisted(media_id)


def swap_disk(
    machine: CoreApi, disk_files: DiskFiles, drive: int, path: Path | None
) -> None:
    """Mount the .dsk at `path` (None ejects) in `drive` (0 = A), writing the
    outgoing image back to its file in `disk_files` first when it is dirty,
    and record `path` as the drive's file. `path` is taken relative to the
    working directory, with `~` expanded. Raises OSError or ValueError,
    leaving the drive and its recorded file as they were, when the file
    cannot be read, is not a valid image, the drive does not exist, or the
    outgoing image cannot be written back (it then stays dirty)."""
    full_path = path.expanduser() if path is not None else None
    medium = read_disk_medium(full_path) if full_path is not None else None
    outgoing_path = disk_files.path(drive)
    if outgoing_path is not None:
        media_id = drive_media_id(drive)
        if media_id in machine.dirty_media():
            outgoing_path.write_bytes(machine.read_media(media_id))
            machine.mark_persisted(media_id)
    if medium is None:
        status = machine.mount_disk(drive, None, False)
    else:
        status = machine.mount_disk(drive, medium.data, medium.write_protected)
    if status != STATUS_OK:
        message = machine.last_error_text().decode("utf-8")
        name = path if path is not None else f"drive {drive}"
        raise ValueError(f"{name}: {message}")
    disk_files.set(drive, full_path)
