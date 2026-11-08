"""The host side of the cassette deck: the mounted file and its block list.

The core plays an edge list and knows no file name (io-cassette). This module
reads a CAS file, converts it (host/cas_image.py), mounts the edge list, and
keeps the file name and the block list for the CLI, the debugger, the RPC
server and the frontend. Every operation raises TapeError with the reason and
leaves the deck as it was when it fails.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from host.cas_image import CPU_HZ, CasBlock, CasImageError, parse_cas
from msx.boundary import STATUS_OK

if TYPE_CHECKING:
    from msx.core_api import CoreApi

WAV_MESSAGE = (
    "only CAS tape files are supported; convert a WAV recording to CAS "
    "with an external tool"
)

# The largest position the core takes: seek_tape's argument is int64_t in the
# C ABI (include/msx_core.h).
_INT64_MAX = 0x7FFF_FFFF_FFFF_FFFF


class TapeError(Exception):
    """A tape operation failed; the message says why."""


@dataclass(frozen=True)
class TapeStatus:
    """The deck as the debugger and the RPC report it. `block` is the last
    block that starts at or before the position (None with no tape)."""

    mounted: bool
    path: Path | None
    motor: bool
    position: int
    length: int
    block: int | None

    @property
    def position_seconds(self) -> float:
        return seconds(self.position)

    @property
    def length_seconds(self) -> float:
        return seconds(self.length)


def seconds(tstates: int) -> float:
    """T-states as seconds of the 3.579545 MHz clock."""
    return tstates / CPU_HZ


def check_tape_path(path: Path) -> None:
    """Raise TapeError for a file name that does not end in .cas."""
    if path.suffix.lower() != ".cas":
        raise TapeError(WAV_MESSAGE)


class TapeDeck:
    """The mounted tape file and its blocks, beside the core's deck."""

    def __init__(self, machine: CoreApi) -> None:
        self._machine = machine
        self._path: Path | None = None
        self._blocks: tuple[CasBlock, ...] = ()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def blocks(self) -> tuple[CasBlock, ...]:
        return self._blocks

    def load(self, path: Path) -> tuple[str, ...]:
        """Convert and mount the CAS file at `path`; returns the conversion
        warnings."""
        check_tape_path(path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise TapeError(f"cannot read {path}: {exc.strerror or exc}") from exc
        try:
            image = parse_cas(data)
        except CasImageError as exc:
            raise TapeError(f"{path}: {exc}") from exc
        self._check(self._machine.mount_tape(image.edges))
        self._path = path
        self._blocks = image.blocks
        return image.warnings

    def eject(self) -> None:
        self._check(self._machine.eject_tape())
        self._path = None
        self._blocks = ()

    def rewind(self) -> None:
        self._check(self._machine.seek_tape(0))

    def seek_seconds(self, value: float) -> None:
        """Seek to `value` seconds. A value that is not finite, or too large
        for the core's int64 position, is an error before the core call."""
        try:
            seconds = float(value)
        except OverflowError:
            raise TapeError(f"tape position {value} s is outside the tape") from None
        if not math.isfinite(seconds):
            raise TapeError(f"tape position {value} s is not a finite number")
        if seconds < 0:
            raise TapeError(f"tape position {value} s is negative")
        position = int(seconds * CPU_HZ)
        if position > _INT64_MAX:
            raise TapeError(f"tape position {value} s is outside the tape")
        self._check(self._machine.seek_tape(position))

    def seek_block(self, number: int) -> None:
        if not self._machine.tape_info().mounted:
            # seek_tape fails here with the core's own no-tape status and text
            # (or no-port on a machine with no cassette port); _check raises it.
            self._check(self._machine.seek_tape(0))
        if number < 0 or number >= len(self._blocks):
            raise TapeError(f"no block {number}; the tape has {len(self._blocks)} blocks")
        self._check(self._machine.seek_tape(self._blocks[number].start))

    def status(self) -> TapeStatus:
        info = self._machine.tape_info()
        mounted = info.mounted == 1
        return TapeStatus(
            mounted=mounted,
            path=self._path if mounted else None,
            motor=info.motor == 1,
            position=info.position,
            length=info.length,
            block=self._block_at(info.position) if mounted else None,
        )

    def _block_at(self, position: int) -> int | None:
        found: int | None = None
        for block in self._blocks:
            if block.start > position:
                break
            found = block.number
        return found

    def _check(self, status: int) -> None:
        if status != STATUS_OK:
            raise TapeError(self._machine.last_error_text().decode("utf-8"))
