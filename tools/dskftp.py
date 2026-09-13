#!/usr/bin/env python3
"""
dskftp.py - Interactive, ftp-style shell for MSX-DOS (FAT12) .dsk disk images.

Handles standard MSX floppy formats (media descriptor F8h/F9h/FAh/FBh:
1DD 360KB, 2DD 720KB, 1DD 320KB, 2DD 640KB), including images formatted by
MSX BASIC's CALL FORMAT. Subdirectories (MSX-DOS2) are fully supported, so an
MSX-DOS1 image simply behaves as one that only ever has a root directory.

All writes go straight to the image file, so there is no separate save step.
Transfers are always binary; no CR/LF translation is performed.

Usage:
    python dskftp.py [IMAGE.dsk]

Commands (see `help` inside the shell):
    open <image.dsk>     connect to a disk image
    format <image.dsk>   create an empty image and connect to it
    dir / ls [path]      list a remote directory
    cd <dir>             change the remote directory
    lcd [dir]            change the local directory
    pwd                  print the remote directory
    lpwd                 print the local directory
    get <remote> [local] download a file
    put <local> [remote] upload a file (alias: send)
    mget <pattern>       download several files
    mput <pattern>       upload several files
    delete <file>        delete a remote file
    mkdir <dir>          create a remote directory
    rmdir <dir>          remove an empty remote directory
    quit / bye           leave the shell
"""

from __future__ import annotations

import argparse
import cmd
import datetime
import fnmatch
import glob
import os
import shlex
import sys
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterator

try:
    import readline
except ImportError:  # pragma: no cover - readline is absent on some platforms
    readline = None  # type: ignore[assignment]

SECTOR_SIZE = 512
DIR_ENTRY_SIZE = 32

# MSX-DOS stores filenames as raw bytes; CP932 covers both plain ASCII names
# and the Shift_JIS names Japanese titles use.
MSX_ENCODING = "cp932"

# Characters MSX-DOS cannot store in an 8.3 directory field. Space is the pad
# byte, so an embedded space would leave the entry unreachable from MSX-DOS.
ILLEGAL_NAME_CHARS = " \"*+,./:;<=>?[]|\\"

HISTORY_FILE = os.path.expanduser("~/.dskftp_history")
HISTORY_LENGTH = 1000

# The standard MSX floppy formats, keyed by media descriptor byte. Read twice:
# as fallback geometry when a raw MSX-DOS1 dump carries only the media ID byte
# instead of a full BPB, and as the layout `format` writes.
MEDIA_FORMATS = {
    0xF8: dict(sectors_per_fat=2, sectors_per_cluster=2, root_entries=112,
               total_sectors=720, sectors_per_track=9, sides=1),    # 1DD 360KB
    0xF9: dict(sectors_per_fat=3, sectors_per_cluster=2, root_entries=112,
               total_sectors=1440, sectors_per_track=9, sides=2),   # 2DD 720KB
    0xFA: dict(sectors_per_fat=1, sectors_per_cluster=2, root_entries=112,
               total_sectors=640, sectors_per_track=8, sides=1),    # 1DD 320KB
    0xFB: dict(sectors_per_fat=2, sectors_per_cluster=2, root_entries=112,
               total_sectors=1280, sectors_per_track=8, sides=2),   # 2DD 640KB
}

# What the operator types after `format`: capacity in KB -> media descriptor.
FORMAT_SIZES = {"360": 0xF8, "720": 0xF9, "320": 0xFA, "640": 0xFB}
DEFAULT_FORMAT_SIZE = "720"

NUM_FATS = 2
RESERVED_SECTORS = 1

# A physical format leaves unwritten data sectors reading as 0xE5; matches
# openMSX's formatter, so a fresh image looks like freshly formatted media.
FORMAT_FILL = 0xE5

ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME = 0x08
ATTR_DIR = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = 0x0F  # VFAT long-name slot; never created here, only skipped

SLOT_FREE = 0x00      # never used; also marks the end of the directory
SLOT_DELETED = 0xE5
SLOT_E5_ESCAPE = 0x05  # first byte 05h stands for a real E5h (Shift_JIS names)

CLUSTER_EOC = 0xFFF
CLUSTER_MAX_VALID = 0xFEF
ROOT_CLUSTER = 0      # sentinel: the fixed-size root directory region


class Fat12Error(Exception):
    pass


@dataclass
class DirEntry:
    """One directory slot, with the image offset it was read from."""

    name: str
    attr: int
    cluster: int
    size: int
    mtime: datetime.datetime | None
    offset: int

    @property
    def is_dir(self) -> bool:
        return bool(self.attr & ATTR_DIR)


# -------------------- 8.3 name handling --------------------

def name83_bytes(name: str) -> bytes:
    """Encode an MSX filename as the raw 11-byte 8.3 directory field."""
    name = name.strip().upper()
    if not name or name in (".", ".."):
        raise Fat12Error(f"Invalid filename: {name!r}")
    if "/" in name or "\\" in name:
        raise Fat12Error(f"Filename must not contain a path: {name!r}")
    if "." in name:
        base, ext = name.split(".", 1)
    else:
        base, ext = name, ""
    for ch in base + ext:
        if ch in ILLEGAL_NAME_CHARS or ord(ch) < 0x20:
            raise Fat12Error(f"Character {ch!r} not allowed in an MSX filename: {name!r}")
    try:
        base_b = base.encode(MSX_ENCODING)[:8]
        ext_b = ext.encode(MSX_ENCODING)[:3]
    except UnicodeEncodeError as exc:
        raise Fat12Error(f"Filename not representable on an MSX disk: {name!r}") from exc
    if not base_b:
        raise Fat12Error(f"Invalid filename: {name!r}")
    if base_b[0] == SLOT_DELETED:
        base_b = bytes([SLOT_E5_ESCAPE]) + base_b[1:]
    return base_b.ljust(8, b" ") + ext_b.ljust(3, b" ")


def _decode_field(raw: bytes) -> str:
    return raw.decode(MSX_ENCODING, "replace").rstrip()


def entry_name(raw11: bytes) -> str:
    """Display name ("FILE.EXT") for a raw 11-byte 8.3 directory field."""
    if raw11[0] == SLOT_E5_ESCAPE:
        raw11 = bytes([SLOT_DELETED]) + raw11[1:]
    base = _decode_field(raw11[0:8])
    ext = _decode_field(raw11[8:11])
    return base + ("." + ext if ext else "")


def name_key(raw11: bytes) -> str:
    """Case-insensitive comparison key for a raw 11-byte 8.3 field."""
    return raw11.decode(MSX_ENCODING, "replace").upper()


def _decode_mtime(date_word: int, time_word: int) -> datetime.datetime | None:
    if date_word == 0:
        return None
    year = 1980 + ((date_word >> 9) & 0x7F)
    month = (date_word >> 5) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 11) & 0x1F
    minute = (time_word >> 5) & 0x3F
    second = min((time_word & 0x1F) * 2, 59)
    try:
        return datetime.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _encode_mtime(when: datetime.datetime) -> tuple[int, int]:
    date_word = (((when.year - 1980) & 0x7F) << 9) | (when.month << 5) | when.day
    time_word = (when.hour << 11) | (when.minute << 5) | (when.second // 2)
    return date_word, time_word


# -------------------- disk image --------------------

class MsxDisk:
    """A FAT12 MSX disk image, read and written in place."""

    def __init__(self, path: str) -> None:
        # Absolute: lcd moves the local directory, and flush/rollback must keep
        # writing the image the user actually opened.
        self.path = os.path.abspath(path)
        with open(self.path, "rb") as f:
            self.data = bytearray(f.read())
        self._parse_bpb()

    # ---------------- BPB / geometry ----------------

    def _parse_bpb(self) -> None:
        # These byte offsets mirror format_image()'s BPB field writes below --
        # keep the two in step if either changes.
        b = self.data
        if len(b) < SECTOR_SIZE:
            raise Fat12Error("File too small to be a disk image")

        bytes_per_sector = int.from_bytes(b[0x0B:0x0D], "little")
        sectors_per_cluster = b[0x0D]
        reserved_sectors = int.from_bytes(b[0x0E:0x10], "little")
        num_fats = b[0x10]
        root_entries = int.from_bytes(b[0x11:0x13], "little")
        total_sectors_16 = int.from_bytes(b[0x13:0x15], "little")
        media = b[0x15]
        sectors_per_fat = int.from_bytes(b[0x16:0x18], "little")

        bpb_valid = (
            bytes_per_sector == SECTOR_SIZE
            and sectors_per_cluster in (1, 2, 4, 8)
            and num_fats in (1, 2)
            and root_entries > 0
            and sectors_per_fat > 0
            and total_sectors_16 > 0
            and reserved_sectors > 0
        )

        if not bpb_valid:
            defaults = MEDIA_FORMATS.get(media, MEDIA_FORMATS[0xF9])
            bytes_per_sector = SECTOR_SIZE
            sectors_per_cluster = defaults["sectors_per_cluster"]
            reserved_sectors = 1
            num_fats = 2
            root_entries = defaults["root_entries"]
            total_sectors_16 = defaults["total_sectors"]
            sectors_per_fat = defaults["sectors_per_fat"]
            if media not in MEDIA_FORMATS:
                media = 0xF9

        self.bytes_per_sector = bytes_per_sector
        self.sectors_per_cluster = sectors_per_cluster
        self.reserved_sectors = reserved_sectors
        self.num_fats = num_fats
        self.root_entries = root_entries
        self.total_sectors = total_sectors_16
        self.media = media
        self.sectors_per_fat = sectors_per_fat

        self.fat_start = reserved_sectors * SECTOR_SIZE
        self.fat_size_bytes = sectors_per_fat * SECTOR_SIZE
        self.root_start = self.fat_start + num_fats * self.fat_size_bytes
        self.root_size_bytes = root_entries * DIR_ENTRY_SIZE
        self.data_start = self.root_start + self.root_size_bytes
        self.cluster_size = sectors_per_cluster * SECTOR_SIZE

        if self.data_start > len(self.data):
            raise Fat12Error(
                "Image too small for parsed geometry "
                f"(need {self.data_start} bytes, have {len(self.data)})"
            )

        root_sectors = -(-self.root_size_bytes // SECTOR_SIZE)
        data_sectors = total_sectors_16 - (
            reserved_sectors + num_fats * sectors_per_fat + root_sectors
        )
        declared = max(0, data_sectors // sectors_per_cluster)
        available = (len(self.data) - self.data_start) // self.cluster_size
        self.total_clusters = min(declared, available)
        if self.total_clusters < 1:
            raise Fat12Error("Image has no usable data area")

    @property
    def label(self) -> str:
        for entry in self._iter_entries(ROOT_CLUSTER, include_dot=True):
            if entry.attr & ATTR_VOLUME:
                return entry.name
        return ""

    # ---------------- FAT12 ----------------

    def _fat_offset(self, n: int, fat_index: int) -> int:
        if not 0 <= n < self.total_clusters + 2:
            raise Fat12Error(f"Cluster {n} out of range")
        return self.fat_start + fat_index * self.fat_size_bytes + (n * 3) // 2

    def _read_fat(self, n: int, fat_index: int) -> int:
        off = self._fat_offset(n, fat_index)
        if n % 2 == 0:
            return self.data[off] | ((self.data[off + 1] & 0x0F) << 8)
        return (self.data[off] >> 4) | (self.data[off + 1] << 4)

    def get_fat(self, n: int) -> int:
        """FAT entry as MSX-DOS sees it: the first copy is the authoritative one."""
        return self._read_fat(n, 0)

    def is_free(self, n: int) -> bool:
        """Free only when every FAT copy agrees; a diverging copy may still hold
        live data, and on a damaged image that is the data worth not overwriting."""
        for i in range(self.num_fats):
            if self._read_fat(n, i) != 0:
                return False
        return True

    def fat_mismatches(self) -> int:
        """Clusters whose FAT copies disagree; non-zero means a damaged image."""
        count = 0
        for c in range(2, self.total_clusters + 2):
            first = self._read_fat(c, 0)
            for i in range(1, self.num_fats):
                if self._read_fat(c, i) != first:
                    count += 1
                    break
        return count

    def set_fat(self, n: int, value: int) -> None:
        value &= 0xFFF
        for i in range(self.num_fats):
            off = self._fat_offset(n, i)
            if n % 2 == 0:
                self.data[off] = value & 0xFF
                self.data[off + 1] = (self.data[off + 1] & 0xF0) | (value >> 8)
            else:
                self.data[off] = (self.data[off] & 0x0F) | ((value & 0x0F) << 4)
                self.data[off + 1] = value >> 4

    def cluster_offset(self, cluster: int) -> int:
        if not 2 <= cluster < self.total_clusters + 2:
            raise Fat12Error(f"Cluster {cluster} out of range")
        return self.data_start + (cluster - 2) * self.cluster_size

    def cluster_chain(self, start_cluster: int) -> list[int]:
        chain: list[int] = []
        seen = set()
        c = start_cluster
        while 2 <= c <= CLUSTER_MAX_VALID:
            if c in seen:
                raise Fat12Error("FAT loop detected")
            seen.add(c)
            chain.append(c)
            c = self.get_fat(c)
        return chain

    def alloc_clusters(self, count: int) -> list[int]:
        """Pick `count` free clusters without linking them yet."""
        free: list[int] = []
        for c in range(2, self.total_clusters + 2):
            if self.is_free(c):
                free.append(c)
                if len(free) == count:
                    return free
        raise Fat12Error("Not enough free space on the disk image")

    def free_chain(self, start_cluster: int) -> None:
        if start_cluster == 0:
            return
        for c in self.cluster_chain(start_cluster):
            self.set_fat(c, 0)

    def free_bytes(self) -> int:
        free = 0
        for c in range(2, self.total_clusters + 2):
            if self.is_free(c):
                free += 1
        return free * self.cluster_size

    def _zero_cluster(self, cluster: int) -> None:
        off = self.cluster_offset(cluster)
        self.data[off:off + self.cluster_size] = bytes(self.cluster_size)

    # ---------------- directories ----------------

    def _slot_offsets(self, dir_cluster: int) -> Iterator[int]:
        if dir_cluster == ROOT_CLUSTER:
            for i in range(self.root_entries):
                yield self.root_start + i * DIR_ENTRY_SIZE
        else:
            slots = self.cluster_size // DIR_ENTRY_SIZE
            for c in self.cluster_chain(dir_cluster):
                base = self.cluster_offset(c)
                for i in range(slots):
                    yield base + i * DIR_ENTRY_SIZE

    def _iter_entries(self, dir_cluster: int, include_dot: bool = False) -> Iterator[DirEntry]:
        for off in self._slot_offsets(dir_cluster):
            raw = self.data[off:off + DIR_ENTRY_SIZE]
            if raw[0] == SLOT_FREE:
                return
            if raw[0] == SLOT_DELETED:
                continue
            attr = raw[11]
            if attr & ATTR_LFN == ATTR_LFN:
                continue
            name = entry_name(bytes(raw[0:11]))
            if not include_dot and name in (".", ".."):
                continue
            yield DirEntry(
                name=name,
                attr=attr,
                cluster=int.from_bytes(raw[26:28], "little"),
                size=int.from_bytes(raw[28:32], "little"),
                mtime=_decode_mtime(
                    int.from_bytes(raw[24:26], "little"),
                    int.from_bytes(raw[22:24], "little"),
                ),
                offset=off,
            )

    def list_dir(self, dir_cluster: int) -> list[DirEntry]:
        return [e for e in self._iter_entries(dir_cluster) if not e.attr & ATTR_VOLUME]

    def find_entry(self, dir_cluster: int, name: str) -> DirEntry | None:
        key = name_key(name83_bytes(name))
        for entry in self._iter_entries(dir_cluster):
            if name_key(bytes(self.data[entry.offset:entry.offset + 11])) == key:
                return entry
        return None

    def _alloc_slot(self, dir_cluster: int) -> int:
        """Offset of a usable directory slot, growing a subdirectory if needed."""
        for off in self._slot_offsets(dir_cluster):
            if self.data[off] in (SLOT_FREE, SLOT_DELETED):
                return off
        if dir_cluster == ROOT_CLUSTER:
            raise Fat12Error("Root directory is full")
        chain_end = self.cluster_chain(dir_cluster)[-1]
        new_cluster = self.alloc_clusters(1)[0]
        self._zero_cluster(new_cluster)
        self.set_fat(new_cluster, CLUSTER_EOC)
        self.set_fat(chain_end, new_cluster)
        return self.cluster_offset(new_cluster)

    def _write_slot(self, off: int, raw11: bytes, attr: int, cluster: int, size: int,
                    when: datetime.datetime | None = None) -> None:
        date_word, time_word = _encode_mtime(when or datetime.datetime.now())
        raw = bytearray(DIR_ENTRY_SIZE)
        raw[0:11] = raw11
        raw[11] = attr
        raw[22:24] = time_word.to_bytes(2, "little")
        raw[24:26] = date_word.to_bytes(2, "little")
        raw[26:28] = cluster.to_bytes(2, "little")
        raw[28:32] = size.to_bytes(4, "little")
        self.data[off:off + DIR_ENTRY_SIZE] = raw

    def resolve_dir(self, components: list[str]) -> int:
        """Cluster of the directory named by an already-normalised path."""
        cluster = ROOT_CLUSTER
        walked: list[str] = []
        for name in components:
            entry = self.find_entry(cluster, name)
            walked.append(name)
            if entry is None:
                raise Fat12Error("No such directory: /" + "/".join(walked))
            if not entry.is_dir:
                raise Fat12Error("Not a directory: /" + "/".join(walked))
            cluster = entry.cluster
        return cluster

    # ---------------- file operations ----------------

    def read_file(self, dir_cluster: int, name: str) -> bytes:
        entry = self.find_entry(dir_cluster, name)
        if entry is None:
            raise Fat12Error(f"File not found: {name}")
        if entry.attr & (ATTR_DIR | ATTR_VOLUME):
            raise Fat12Error(f"Not a regular file: {name}")
        if entry.cluster == 0 or entry.size == 0:
            return b""
        out = bytearray()
        for c in self.cluster_chain(entry.cluster):
            off = self.cluster_offset(c)
            out += self.data[off:off + self.cluster_size]
            if len(out) >= entry.size:
                break
        if len(out) < entry.size:
            raise Fat12Error(f"Truncated cluster chain for {name}")
        return bytes(out[:entry.size])

    def write_file(self, dir_cluster: int, name: str, content: bytes) -> None:
        raw11 = name83_bytes(name)
        existing = self.find_entry(dir_cluster, name)
        if existing is not None:
            if existing.is_dir:
                raise Fat12Error(f"Is a directory: {name}")
            self.free_chain(existing.cluster)
            slot = existing.offset
        else:
            slot = self._alloc_slot(dir_cluster)

        clusters: list[int] = []
        if content:
            needed = -(-len(content) // self.cluster_size)
            clusters = self.alloc_clusters(needed)
            for idx, c in enumerate(clusters):
                chunk = content[idx * self.cluster_size:(idx + 1) * self.cluster_size]
                off = self.cluster_offset(c)
                self.data[off:off + self.cluster_size] = chunk.ljust(self.cluster_size, b"\x00")
                nxt = clusters[idx + 1] if idx + 1 < len(clusters) else CLUSTER_EOC
                self.set_fat(c, nxt)

        first = clusters[0] if clusters else 0
        self._write_slot(slot, raw11, ATTR_ARCHIVE, first, len(content))

    def delete_file(self, dir_cluster: int, name: str) -> int:
        entry = self.find_entry(dir_cluster, name)
        if entry is None:
            raise Fat12Error(f"File not found: {name}")
        if entry.is_dir:
            raise Fat12Error(f"Is a directory (use rmdir): {name}")
        if entry.attr & ATTR_VOLUME:
            raise Fat12Error(f"Not a regular file: {name}")
        self.free_chain(entry.cluster)
        self.data[entry.offset] = SLOT_DELETED
        return entry.size

    def mkdir(self, dir_cluster: int, name: str) -> None:
        raw11 = name83_bytes(name)
        if self.find_entry(dir_cluster, name) is not None:
            raise Fat12Error(f"Already exists: {name}")
        # Claim the parent slot first: growing the parent may itself consume a
        # cluster, so the directory's own cluster must be picked afterwards.
        slot = self._alloc_slot(dir_cluster)
        new_cluster = self.alloc_clusters(1)[0]
        self._zero_cluster(new_cluster)
        self.set_fat(new_cluster, CLUSTER_EOC)

        now = datetime.datetime.now()
        base = self.cluster_offset(new_cluster)
        self._write_slot(base, b".          ", ATTR_DIR, new_cluster, 0, now)
        self._write_slot(base + DIR_ENTRY_SIZE, b"..         ", ATTR_DIR, dir_cluster, 0, now)
        self._write_slot(slot, raw11, ATTR_DIR, new_cluster, 0, now)

    def rmdir(self, dir_cluster: int, name: str) -> None:
        entry = self.find_entry(dir_cluster, name)
        if entry is None:
            raise Fat12Error(f"Directory not found: {name}")
        if not entry.is_dir:
            raise Fat12Error(f"Not a directory (use delete): {name}")
        if list(self._iter_entries(entry.cluster)):
            raise Fat12Error(f"Directory not empty: {name}")
        self.free_chain(entry.cluster)
        self.data[entry.offset] = SLOT_DELETED

    # ---------------- persistence ----------------

    def flush(self) -> None:
        with open(self.path, "r+b") as f:
            f.write(self.data)

    def rollback(self) -> None:
        """Drop unflushed changes. A command flushes only once it has fully
        succeeded, so re-reading the file undoes a half-applied operation."""
        with open(self.path, "rb") as f:
            self.data = bytearray(f.read())


# -------------------- formatting --------------------

def format_image(path: str, media: int) -> None:
    """Write an empty MSX-DOS disk image: BPB boot sector, two empty FATs, an
    empty root directory, and a data area filled the way a physical format
    leaves it.

    No boot loader is written, so the result is a data disk: MSX-DOS reads and
    writes it normally, but it cannot be booted from."""
    geometry = MEDIA_FORMATS[media]
    total_sectors = geometry["total_sectors"]
    sectors_per_fat = geometry["sectors_per_fat"]
    root_entries = geometry["root_entries"]
    root_sectors = -(-root_entries * DIR_ENTRY_SIZE // SECTOR_SIZE)
    system_sectors = RESERVED_SECTORS + NUM_FATS * sectors_per_fat + root_sectors

    image = bytearray([FORMAT_FILL]) * (total_sectors * SECTOR_SIZE)
    image[0:system_sectors * SECTOR_SIZE] = bytes(system_sectors * SECTOR_SIZE)

    # MS-DOS-compatible jump and OEM name, as every MSX-formatted disk carries.
    # These byte offsets mirror _parse_bpb()'s BPB field reads above -- keep
    # the two in step if either changes.
    image[0:3] = b"\xEB\xFE\x90"
    image[3:11] = b"DSKFTP  "
    image[0x0B:0x0D] = SECTOR_SIZE.to_bytes(2, "little")
    image[0x0D] = geometry["sectors_per_cluster"]
    image[0x0E:0x10] = RESERVED_SECTORS.to_bytes(2, "little")
    image[0x10] = NUM_FATS
    image[0x11:0x13] = root_entries.to_bytes(2, "little")
    image[0x13:0x15] = total_sectors.to_bytes(2, "little")
    image[0x15] = media
    image[0x16:0x18] = sectors_per_fat.to_bytes(2, "little")
    image[0x18:0x1A] = geometry["sectors_per_track"].to_bytes(2, "little")
    image[0x1A:0x1C] = geometry["sides"].to_bytes(2, "little")

    # Clusters 0 and 1 have no data sectors; their entries carry the media
    # descriptor and an end-of-chain marker instead, in every FAT copy.
    for i in range(NUM_FATS):
        off = (RESERVED_SECTORS + i * sectors_per_fat) * SECTOR_SIZE
        image[off:off + 3] = bytes([media, 0xFF, 0xFF])

    with open(path, "wb") as f:
        f.write(image)


# -------------------- path helpers --------------------

def split_args(line: str) -> list[str]:
    """Split a command line, keeping backslashes (MSX path separators) intact."""
    tokens = shlex.split(line, posix=False)
    out: list[str] = []
    for tok in tokens:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def normalize_path(path: str, cwd: list[str]) -> list[str]:
    """Resolve a remote path (either separator, absolute or relative) to components."""
    path = path.replace("\\", "/")
    components = [] if path.startswith("/") else list(cwd)
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if components:
                components.pop()
            continue
        components.append(part)
    return components


def split_remote(path: str, cwd: list[str]) -> tuple[list[str], str]:
    """Split a remote file path into (parent components, filename)."""
    path = path.replace("\\", "/")
    if not path or path.endswith("/"):
        raise Fat12Error(f"Not a filename: {path!r}")
    head, _, name = path.rpartition("/")
    if name in (".", ".."):
        raise Fat12Error(f"Not a filename: {path!r}")
    return normalize_path(head, cwd), name


def format_path(components: list[str]) -> str:
    return "/" + "/".join(components)


def pad(text: str, width: int) -> str:
    """Left-align `text` in `width` terminal columns (CP932 names are double-width)."""
    columns = 0
    for ch in text:
        columns += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return text + " " * max(1, width - columns)


def attr_flags(attr: int) -> str:
    return "".join([
        "R" if attr & ATTR_READONLY else "-",
        "H" if attr & ATTR_HIDDEN else "-",
        "S" if attr & ATTR_SYSTEM else "-",
        "A" if attr & ATTR_ARCHIVE else "-",
    ])


# -------------------- interactive shell --------------------

class DskFtpShell(cmd.Cmd):
    """ftp-style command loop over an MSX .dsk image."""

    intro = "dskftp - MSX-DOS disk image shell. Type 'help' for commands."

    def __init__(self) -> None:
        super().__init__()
        self.disk: MsxDisk | None = None
        self.cwd: list[str] = []
        self._update_prompt()

    # ---------------- infrastructure ----------------

    def _update_prompt(self) -> None:
        if self.disk is None:
            self.prompt = "dskftp (not connected)> "
        else:
            self.prompt = f"dskftp:{format_path(self.cwd)}> "

    def _require_disk(self) -> MsxDisk:
        if self.disk is None:
            raise Fat12Error("Not connected; use 'open <image.dsk>'")
        return self.disk

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except (Fat12Error, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            self._rollback()
        return False

    def _rollback(self) -> None:
        """Undo a partially applied change so a failed command leaves no damage."""
        if self.disk is None:
            return
        try:
            self.disk.rollback()
        except OSError as exc:
            print(f"Error: cannot re-read {self.disk.path}: {exc}", file=sys.stderr)

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> bool:  # type: ignore[override]
        # cmd.Cmd's stub declares this -> None, but at runtime (like do_*,
        # emptyline, postcmd) a truthy return stops the command loop -- the
        # stub is narrower than the actual contract, not a real mismatch.
        print(f"Unknown command: {line.split()[0]}", file=sys.stderr)
        return False

    def postcmd(self, stop: bool, line: str) -> bool:
        self._update_prompt()
        return stop

    # ---------------- connection ----------------

    def do_open(self, arg: str) -> None:
        """open <image.dsk> - connect to a disk image."""
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: open <image.dsk>")
        self.open_image(args[0])

    def open_image(self, path: str) -> None:
        disk = MsxDisk(path)
        self.disk = disk
        self.cwd = []
        self._update_prompt()
        label = disk.label
        geometry = f"{disk.total_sectors * SECTOR_SIZE // 1024} KB"
        print(f"Connected to {disk.path} ({geometry}, media {disk.media:02X}h"
              + (f", label {label}" if label else "") + ")")
        mismatches = disk.fat_mismatches()
        if mismatches:
            print(f"Warning: the FAT copies disagree on {mismatches} cluster(s); this image's"
                  " allocation table is damaged. Listings follow the first copy, and those"
                  " clusters are never handed out for new data.", file=sys.stderr)

    def do_format(self, arg: str) -> None:
        """format <image.dsk> [720|640|360|320] - create an empty image (capacity
        in KB, default 720) and connect to it. The image is a data disk: it holds
        no boot loader."""
        args = split_args(arg)
        if not 1 <= len(args) <= 2:
            raise Fat12Error("Usage: format <image.dsk> [720|640|360|320]")
        path = os.path.expanduser(args[0])
        size = args[1] if len(args) == 2 else DEFAULT_FORMAT_SIZE
        if size not in FORMAT_SIZES:
            choices = "/".join(sorted(FORMAT_SIZES))
            raise Fat12Error(f"Unknown capacity {size!r}; choose one of {choices}")
        if os.path.exists(path):
            raise Fat12Error(f"{path} already exists; formatting it would erase it")
        format_image(path, FORMAT_SIZES[size])
        print(f"Formatted {path} as a {size} KB disk")
        self.open_image(path)

    def do_quit(self, arg: str) -> bool:
        """quit - leave the shell."""
        print("Goodbye.")
        return True

    def do_bye(self, arg: str) -> bool:
        """bye - leave the shell."""
        return self.do_quit(arg)

    def do_EOF(self, arg: str) -> bool:
        """EOF (Ctrl-D) - leave the shell."""
        print()
        return self.do_quit(arg)

    # ---------------- navigation ----------------

    def do_pwd(self, arg: str) -> None:
        """pwd - print the remote working directory."""
        self._require_disk()
        print(format_path(self.cwd))

    def do_cd(self, arg: str) -> None:
        """cd <dir> - change the remote working directory."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: cd <dir>")
        components = normalize_path(args[0], self.cwd)
        disk.resolve_dir(components)
        self.cwd = components
        print(format_path(self.cwd))

    def do_lcd(self, arg: str) -> None:
        """lcd [dir] - change the local working directory (no argument: print it)."""
        args = split_args(arg)
        if len(args) > 1:
            raise Fat12Error("Usage: lcd [dir]")
        if args:
            os.chdir(os.path.expanduser(args[0]))
        self._print_local_dir()

    def do_lpwd(self, arg: str) -> None:
        """lpwd - print the local working directory."""
        if split_args(arg):
            raise Fat12Error("Usage: lpwd")
        self._print_local_dir()

    @staticmethod
    def _print_local_dir() -> None:
        print(f"Local directory: {os.getcwd()}")

    # ---------------- listing ----------------

    def _listing_target(self, path: str) -> tuple[list[str], str]:
        """Resolve a dir/ls argument into (directory components, glob pattern)."""
        disk = self._require_disk()
        if not path:
            return self.cwd, "*"
        components = normalize_path(path, self.cwd)
        try:
            disk.resolve_dir(components)
            return components, "*"
        except Fat12Error:
            parent, name = split_remote(path, self.cwd)
            disk.resolve_dir(parent)
            return parent, name.upper()

    def do_dir(self, arg: str) -> None:
        """dir [path] - list a remote directory (a path may end in a wildcard)."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) > 1:
            raise Fat12Error("Usage: dir [path]")
        components, pattern = self._listing_target(args[0] if args else "")
        cluster = disk.resolve_dir(components)

        entries = [e for e in disk.list_dir(cluster)
                   if fnmatch.fnmatchcase(e.name.upper(), pattern)]
        entries.sort(key=lambda e: (not e.is_dir, e.name))

        print(f"Directory of {format_path(components)}")
        files = 0
        dirs = 0
        total = 0
        for entry in entries:
            stamp = entry.mtime.strftime("%Y-%m-%d %H:%M") if entry.mtime else " " * 16
            if entry.is_dir:
                dirs += 1
                size = "<DIR>"
            else:
                files += 1
                total += entry.size
                size = str(entry.size)
            print(f"{pad(entry.name, 13)}{size:>9}  {attr_flags(entry.attr)}  {stamp}")
        print(f"{files} file(s), {total} bytes; {dirs} dir(s); "
              f"{disk.free_bytes()} bytes free")

    def do_ls(self, arg: str) -> None:
        """ls [path] - alias for dir."""
        self.do_dir(arg)

    # ---------------- transfers ----------------

    def _get_one(self, remote: str, local: str) -> None:
        disk = self._require_disk()
        parent, name = split_remote(remote, self.cwd)
        content = disk.read_file(disk.resolve_dir(parent), name)
        if os.path.isdir(local):
            local = os.path.join(local, name)
        with open(local, "wb") as f:
            f.write(content)
        print(f"get {name} -> {local} ({len(content)} bytes)")

    def _put_one(self, local: str, remote: str) -> None:
        disk = self._require_disk()
        with open(local, "rb") as f:
            content = f.read()
        parent, name = split_remote(remote, self.cwd)
        cluster = disk.resolve_dir(parent)
        disk.write_file(cluster, name, content)
        disk.flush()
        print(f"put {local} -> {name} ({len(content)} bytes)")

    def _transfer(
        self, action: Callable[[str, str], None], source: str, target: str
    ) -> bool:
        """Run one transfer of a multi-file command, reporting errors instead of raising."""
        try:
            action(source, target)
        except (Fat12Error, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            self._rollback()
            return False
        return True

    @staticmethod
    def _report_transfers(transferred: int, attempted: int) -> None:
        failed = attempted - transferred
        summary = f"{transferred} file(s) transferred"
        if failed:
            summary += f", {failed} failed"
        print(summary)

    def do_get(self, arg: str) -> None:
        """get <remote> [local] - download a file."""
        self._require_disk()
        args = split_args(arg)
        if not 1 <= len(args) <= 2:
            raise Fat12Error("Usage: get <remote> [local]")
        remote = args[0]
        local = args[1] if len(args) == 2 else split_remote(remote, self.cwd)[1]
        self._get_one(remote, os.path.expanduser(local))

    def do_put(self, arg: str) -> None:
        """put <local> [remote] - upload a file."""
        self._require_disk()
        args = split_args(arg)
        if not 1 <= len(args) <= 2:
            raise Fat12Error("Usage: put <local> [remote]")
        local = os.path.expanduser(args[0])
        remote = args[1] if len(args) == 2 else os.path.basename(local.rstrip("/"))
        self._put_one(local, remote)

    def do_send(self, arg: str) -> None:
        """send <local> [remote] - alias for put."""
        self.do_put(arg)

    def do_mget(self, arg: str) -> None:
        """mget <pattern> - download every matching remote file into the local directory."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: mget <pattern>")
        components, pattern = self._listing_target(args[0])
        cluster = disk.resolve_dir(components)
        matched = [e for e in disk.list_dir(cluster)
                   if not e.is_dir and fnmatch.fnmatchcase(e.name.upper(), pattern)]
        if not matched:
            print(f"No remote file matches {args[0]}")
            return
        transferred = 0
        for entry in matched:
            remote = format_path(components + [entry.name])
            if self._transfer(self._get_one, remote, entry.name):
                transferred += 1
        self._report_transfers(transferred, len(matched))

    def do_mput(self, arg: str) -> None:
        """mput <pattern> - upload every matching local file into the remote directory."""
        self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: mput <pattern>")
        matched = sorted(p for p in glob.glob(os.path.expanduser(args[0]))
                         if os.path.isfile(p))
        if not matched:
            print(f"No local file matches {args[0]}")
            return
        transferred = 0
        for local in matched:
            if self._transfer(self._put_one, local, os.path.basename(local)):
                transferred += 1
        self._report_transfers(transferred, len(matched))

    # ---------------- remote mutation ----------------

    def do_delete(self, arg: str) -> None:
        """delete <file> - delete a remote file."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: delete <file>")
        parent, name = split_remote(args[0], self.cwd)
        size = disk.delete_file(disk.resolve_dir(parent), name)
        disk.flush()
        print(f"Deleted {name} ({size} bytes)")

    def do_mkdir(self, arg: str) -> None:
        """mkdir <dir> - create a remote directory."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: mkdir <dir>")
        parent, name = split_remote(args[0], self.cwd)
        disk.mkdir(disk.resolve_dir(parent), name)
        disk.flush()
        print(f"Created {format_path(parent + [name])}")

    def do_rmdir(self, arg: str) -> None:
        """rmdir <dir> - remove an empty remote directory."""
        disk = self._require_disk()
        args = split_args(arg)
        if len(args) != 1:
            raise Fat12Error("Usage: rmdir <dir>")
        parent, name = split_remote(args[0], self.cwd)
        target = parent + [name]
        if self.cwd[:len(target)] == target:
            raise Fat12Error("Cannot remove the current remote directory")
        disk.rmdir(disk.resolve_dir(parent), name)
        disk.flush()
        print(f"Removed {format_path(target)}")


# -------------------- entry point --------------------

def _load_history() -> None:
    if readline is None:
        return
    try:
        readline.read_history_file(HISTORY_FILE)
    except OSError:
        pass
    readline.set_history_length(HISTORY_LENGTH)


def _save_history() -> None:
    if readline is None:
        return
    try:
        readline.write_history_file(HISTORY_FILE)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive ftp-style shell for MSX-DOS (.dsk) FAT12 disk images"
    )
    parser.add_argument("image", nargs="?", help="Disk image to open on startup")
    args = parser.parse_args()

    shell = DskFtpShell()
    if args.image:
        try:
            shell.open_image(args.image)
        except (Fat12Error, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    _load_history()
    intro = shell.intro
    try:
        while True:
            try:
                shell.cmdloop(intro)
                break
            except KeyboardInterrupt:
                print()
                intro = ""
    finally:
        _save_history()


if __name__ == "__main__":
    main()
