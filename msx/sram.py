"""Battery-backed SRAM as a value, and the medium shape the host persists.

An SRAM owner (a cartridge mapper, the FM-PAC, the Halnote, the RTC's CMOS
RAM) holds one SramImage instead of carrying the bytes and dirty flag itself.
SramImage and the floppy DskDiskImage both satisfy Medium, so the machine
treats every writable medium the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Medium(Protocol):
    """A writable image the host persists: its bytes and a dirty flag set by
    a change and cleared once the host has written the bytes."""

    @property
    def dirty(self) -> bool: ...
    def data(self) -> bytes: ...
    def mark_persisted(self) -> None: ...


@dataclass
class SramImage:
    """A fixed-size SRAM image. A write or restore that changes a byte marks
    it dirty; rewriting the stored value does not."""

    _data: bytearray
    _dirty: bool = field(default=False, repr=False)

    @classmethod
    def blank(cls, size: int) -> SramImage:
        return cls(bytearray(size))

    @classmethod
    def of(cls, size: int, image: bytes | bytearray | None) -> SramImage:
        """A copy of `image` when it is `size` bytes, otherwise blank."""
        if image is None or len(image) != size:
            return cls.blank(size)
        return cls(bytearray(image))

    def __len__(self) -> int:
        return len(self._data)

    def read(self, offset: int) -> int:
        return self._data[offset]

    def write(self, offset: int, value: int) -> None:
        if self._data[offset] != value:
            self._data[offset] = value
            self._dirty = True

    def restore(self, image: bytes | bytearray) -> None:
        """Replace the contents (same size); a differing image is unsaved."""
        if self._data != image:
            self._data[:] = image
            self._dirty = True

    def data(self) -> bytes:
        return bytes(self._data)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_persisted(self) -> None:
        self._dirty = False
