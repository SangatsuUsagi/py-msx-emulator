"""Save-state files: names, timestamps, the screenshot PNG and latest.* links.

The core encodes and decodes a snapshot (msx/state.py's save_state/load_state
work on bytes); this module stores those bytes under
saves/states/<title>_YYYYMMDD_HHMMSS.state with a matching .png, and keeps
saves/states/latest.state / latest.png pointing at the newest pair.
"""
from __future__ import annotations

import datetime
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from host.media import persist_dirty_media
from host.screenshot import scale_rgb24, write_rgb24_png
from msx import state
from msx.boundary import RgbFrame

if TYPE_CHECKING:
    from msx.machine import Machine

# The saved PNG thumbnail's size (MSX1 native resolution), whatever the frame's.
THUMBNAIL_W = 256
THUMBNAIL_H = 192


def _update_symlink(link: Path, target: Path) -> None:
    """Atomically update (or create) a symlink to point at target."""
    tmp_link = link.with_suffix(link.suffix + ".tmp")
    try:
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        os.symlink(target.name, tmp_link)
        os.replace(tmp_link, link)
    except OSError as exc:
        print(f"warning: could not update symlink {link}: {exc}", file=sys.stderr)


def _sanitise_title(title: str) -> str:
    """Replace spaces with underscores and strip filesystem-unsafe characters."""
    title = title.replace(" ", "_")
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "", title) or "save"


def save_state(machine: Machine, frame: RgbFrame, title: str) -> Path:
    """Serialise machine state and screenshot to saves/states/<title>_YYYYMMDD_HHMMSS.*

    Args:
        machine: Running machine to snapshot.
        frame: Current frame, any size; the PNG is it scaled to
            THUMBNAIL_W x THUMBNAIL_H.
        title: Human-readable title used in the filename.

    Returns:
        Path of the written .state file.
    """
    saves_dir = Path("saves") / "states"
    saves_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{_sanitise_title(title)}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    state_path = saves_dir / f"{stem}.state"
    png_path = saves_dir / f"{stem}.png"

    # Write pending disk writes back first so the disk-identity hash
    # FloppyDisk.snapshot() computes (via each DiskDrive.snapshot()) matches
    # the .dsk file's own on-disk bytes.
    persist_dirty_media(machine)
    state_path.write_bytes(state.save_state(machine))

    pixels = frame.pixels
    if (frame.width, frame.height) != (THUMBNAIL_W, THUMBNAIL_H):
        pixels = scale_rgb24(pixels, frame.width, frame.height, THUMBNAIL_W, THUMBNAIL_H)
    write_rgb24_png(pixels, THUMBNAIL_W, THUMBNAIL_H, png_path)

    _update_symlink(saves_dir / "latest.state", state_path)
    _update_symlink(saves_dir / "latest.png", png_path)

    print(f"state saved: {state_path}")
    return state_path


def load_state(machine: Machine, path: Path | None = None) -> None:
    """Restore machine state from a save file.

    Args:
        machine: Running machine to restore into (callbacks remain intact).
        path: Explicit .state file to load. When None, loads saves/states/latest.state.

    Raises:
        FileNotFoundError: If the target file does not exist.
        StateLoadError: If a field or producer object is malformed.
        ValueError: See msx.state.load_state. Nothing is restored then.
    """
    if path is None:
        link = Path("saves") / "states" / "latest.state"
        if not link.exists():
            raise FileNotFoundError(
                "no save state found: saves/states/latest.state does not exist"
            )
        resolved = link.resolve()
    else:
        if not path.exists():
            raise FileNotFoundError(f"save state not found: {path}")
        resolved = path

    state.load_state(machine, resolved.read_bytes(), str(resolved))
    print(f"state loaded: {resolved}")
