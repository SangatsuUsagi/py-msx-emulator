#!/usr/bin/env python3
"""dskdd.py - Copy raw sectors between a .dsk image and a USB-connected 3-mode FDD.

Linux only. Implements both directions of "method 1: plain USB FDD + dd" from
msx_2dd_disk_imaging_linux.md:

    read    physical disk -> .dsk image  (replaces `dd if=/dev/sdb of=disk.dsk`)
    write   .dsk image -> physical disk

On Linux a USB FDD shows up as /dev/sdX (a block device), not /dev/fd0. Run
`sudo ufiformat -i` to find the device node, and `sudo ufiformat -i /dev/sdX`
to confirm the inserted media, before running this.

A .dsk image in this project is a headerless linear array of 512-byte sectors
(see openspec/specs/fdc-disk-image/spec.md); 720 KB 2DD = 737280 bytes = 1440
sectors.

Usage:
    sudo python3 dskdd.py read /dev/sdb msxdisk.dsk
    sudo python3 dskdd.py read /dev/sdb msxdisk.dsk --retries 5
    sudo python3 dskdd.py write msxdisk.dsk /dev/sdb --verify

Both directions refuse to run unless the device is a block device, is not
mounted, holds media, and is small enough to plausibly be a floppy (<=4MB,
--force overrides). write additionally makes the operator retype the device
path, because writing to the wrong device destroys whatever is on it; read
refuses to overwrite an existing image unless --overwrite is given.

Exit status: 0 success, 1 error, 2 the disk was imaged but some sectors stayed
unreadable -- those are zero-filled in the image and listed on stdout.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import struct
import sys
from typing import BinaryIO

SECTOR_SIZE = 512
BLKGETSIZE64 = 0x80081272
MAX_PLAUSIBLE_FLOPPY_BYTES = 4 * 1024 * 1024
WRITE_CHUNK_SIZE = 64 * 1024

# Read granularity. A media defect usually takes out a whole track, so read one
# cylinder of a 2DD disk (2 heads x 9 sectors) at a time and fall back to
# sector-by-sector retries only for the cylinder that failed.
READ_CHUNK_SECTORS = 18

EXIT_INCOMPLETE = 2


class Failure(Exception):
    """A condition the operator has to fix; reported as `error: ...`."""


# ---------------- device inspection ----------------

def get_block_device_size(fd: int) -> int:
    buf = fcntl.ioctl(fd, BLKGETSIZE64, struct.pack("L", 0))
    return int(struct.unpack("L", buf)[0])


def read_sys_attr(device_name: str, attr: str) -> str:
    path = f"/sys/block/{device_name}/{attr}"
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return "?"


def is_mounted(device_path: str) -> bool:
    with open("/proc/mounts", "r") as f:
        for line in f:
            mounted_dev = line.split()[0]
            if mounted_dev == device_path or mounted_dev.startswith(device_path + "1"):
                return True
    return False


def validate_device(device_path: str, allow_large: bool) -> int:
    """Size in bytes of a device that is safe to use as a floppy end of the copy."""
    try:
        dev_stat = os.stat(device_path)
    except OSError as exc:
        raise Failure(f"cannot stat device {device_path}: {exc}") from exc
    if not stat.S_ISBLK(dev_stat.st_mode):
        raise Failure(f"{device_path} is not a block device.")
    if is_mounted(device_path):
        raise Failure(f"{device_path} (or a partition of it) is currently mounted.")

    try:
        fd = os.open(device_path, os.O_RDONLY)
    except OSError as exc:
        raise Failure(f"cannot open {device_path}: {exc}") from exc
    try:
        size = get_block_device_size(fd)
    finally:
        os.close(fd)

    if size == 0:
        raise Failure(
            f"{device_path} reports 0 bytes; no disk inserted, or the drive is not ready."
        )
    if size > MAX_PLAUSIBLE_FLOPPY_BYTES and not allow_large:
        raise Failure(
            f"{device_path} reports {size} bytes, too large to be a floppy "
            f"(limit {MAX_PLAUSIBLE_FLOPPY_BYTES}). Pass --force to override."
        )
    return size


def describe_device(device_path: str, size: int) -> str:
    name = os.path.basename(device_path)
    vendor = read_sys_attr(name, "device/vendor")
    model = read_sys_attr(name, "device/model")
    removable = read_sys_attr(name, "removable")
    return (
        f"{device_path}  ({size} bytes, vendor={vendor}, model={model}, "
        f"removable={removable})"
    )


# ---------------- transfer ----------------

def show_progress(done: int, total: int) -> None:
    print(f"\r  {done}/{total} bytes", end="", flush=True)


def read_sectors(fd: int, offset: int, length: int) -> bytes | None:
    """Positional read, or None if the device errored or returned a short read."""
    try:
        data = os.pread(fd, length, offset)
    except OSError:
        return None
    if len(data) != length:
        return None
    return data


def _read_sector_with_retry(fd: int, sector: int, retries: int) -> bytes | None:
    """Try one sector up to `retries + 1` times, returning the first successful
    read or None once every attempt has failed."""
    for _ in range(retries + 1):
        data = read_sectors(fd, sector * SECTOR_SIZE, SECTOR_SIZE)
        if data is not None:
            return data
    return None


def read_disk(fd: int, sectors: int, retries: int, out: BinaryIO) -> list[int]:
    """Copy `sectors` sectors from the device into `out`, zero-filling whatever
    cannot be read. Returns the sector numbers that stayed unreadable."""
    bad: list[int] = []
    total_bytes = sectors * SECTOR_SIZE
    done = 0
    while done < sectors:
        count = min(READ_CHUNK_SECTORS, sectors - done)
        data: bytes | bytearray | None = read_sectors(
            fd, done * SECTOR_SIZE, count * SECTOR_SIZE
        )
        if data is None:
            data = bytearray()
            for i in range(count):
                sector = done + i
                one = _read_sector_with_retry(fd, sector, retries)
                if one is None:
                    bad.append(sector)
                    one = bytes(SECTOR_SIZE)
                data += one
        out.write(data)
        done += count
        show_progress(done * SECTOR_SIZE, total_bytes)
    return bad


def write_disk(image_path: str, device_path: str, image_size: int) -> None:
    with open(image_path, "rb") as src, open(device_path, "wb") as dst:
        written = 0
        while True:
            chunk = src.read(WRITE_CHUNK_SIZE)
            if not chunk:
                break
            try:
                dst.write(chunk)
            except OSError as exc:
                raise Failure(
                    f"writing failed at byte offset {written} "
                    f"(sector {written // SECTOR_SIZE}): {exc}"
                ) from exc
            written += len(chunk)
            show_progress(written, image_size)
        try:
            dst.flush()
            os.fsync(dst.fileno())
        except OSError as exc:
            raise Failure(f"flushing {device_path} failed: {exc}") from exc


def first_difference(image_path: str, device_path: str, image_size: int) -> int | None:
    """Byte offset where the device stops matching the image, or None if equal."""
    with open(image_path, "rb") as src, open(device_path, "rb") as dst:
        offset = 0
        while True:
            want = src.read(WRITE_CHUNK_SIZE)
            if not want:
                return None
            got = dst.read(len(want))
            if len(got) != len(want):
                return offset + len(got)
            if want != got:
                for i in range(len(want)):
                    if want[i] != got[i]:
                        return offset + i
            offset += len(want)
            show_progress(offset, image_size)


def format_sector_ranges(sectors: list[int]) -> str:
    parts = []
    start = sectors[0]
    prev = sectors[0]
    for sector in sectors[1:]:
        if sector == prev + 1:
            prev = sector
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = sector
        prev = sector
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


# ---------------- commands ----------------

def cmd_read(args: argparse.Namespace) -> int:
    device_size = validate_device(args.device, args.force)

    sectors = args.sectors if args.sectors is not None else device_size // SECTOR_SIZE
    if sectors <= 0:
        raise Failure("--sectors must be positive.")
    if sectors * SECTOR_SIZE > device_size:
        raise Failure(
            f"{sectors} sectors ({sectors * SECTOR_SIZE} bytes) exceed the device "
            f"({device_size} bytes)."
        )
    if os.path.exists(args.image) and not args.overwrite:
        raise Failure(f"{args.image} already exists; pass --overwrite to replace it.")

    print("about to read:")
    print(f"  device: {describe_device(args.device, device_size)}")
    print(f"  image : {args.image}  ({sectors} sectors, {sectors * SECTOR_SIZE} bytes)")

    try:
        fd = os.open(args.device, os.O_RDONLY)
    except OSError as exc:
        raise Failure(f"cannot open {args.device}: {exc}") from exc
    try:
        with open(args.image, "wb") as out:
            bad = read_disk(fd, sectors, args.retries, out)
            out.flush()
            os.fsync(out.fileno())
    finally:
        os.close(fd)
    print()

    if bad:
        print(f"warning: {len(bad)} of {sectors} sector(s) unreadable, zero-filled:")
        print(f"  {format_sector_ranges(bad)}")
        print(
            "the image is incomplete. For a disk this drive cannot read, see the "
            "flux-level route (Greaseweazle / FluxEngine) in "
            "msx_2dd_disk_imaging_linux.md."
        )
        return EXIT_INCOMPLETE

    print(f"read complete: {args.image}")
    print(f"inspect it with: python3 dskftp.py {args.image}")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.image):
        raise Failure(f"image not found: {args.image}")
    image_size = os.path.getsize(args.image)
    if image_size == 0 or image_size % SECTOR_SIZE != 0:
        raise Failure(f"image size {image_size} is not a positive multiple of {SECTOR_SIZE}.")

    device_size = validate_device(args.device, args.force)
    if image_size > device_size:
        raise Failure(
            f"image ({image_size} bytes) is larger than the device ({device_size} bytes)."
        )

    print("about to write:")
    print(f"  image : {args.image}  ({image_size} bytes)")
    print(f"  device: {describe_device(args.device, device_size)}")
    if image_size < device_size:
        print(
            f"  note  : image is smaller than the device; only the first "
            f"{image_size} bytes are overwritten."
        )

    if not args.yes:
        answer = input(f"type the device path ({args.device}) to confirm write: ")
        if answer != args.device:
            raise Failure("aborted: confirmation did not match.")

    write_disk(args.image, args.device, image_size)
    print()
    print("write complete.")

    if args.verify:
        print("verifying...")
        offset = first_difference(args.image, args.device, image_size)
        print()
        if offset is not None:
            raise Failure(f"mismatch at byte offset {offset} (sector {offset // SECTOR_SIZE}).")
        print("verify OK.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser(
        "read",
        help="image the disk in the drive into a .dsk file",
        description="Image the disk in the drive into a .dsk file.",
    )
    p_read.add_argument("device", help="source block device, e.g. /dev/sdb")
    p_read.add_argument("image", help="path of the .dsk image to create")
    p_read.add_argument(
        "--sectors",
        type=int,
        help="number of 512-byte sectors to read (default: the whole device)",
    )
    p_read.add_argument(
        "--retries",
        type=int,
        default=2,
        help="retries per unreadable sector, i.e. 2 means 3 attempts (default: 2)",
    )
    p_read.add_argument(
        "--overwrite", action="store_true", help="replace the image file if it exists"
    )
    p_read.add_argument(
        "--force",
        action="store_true",
        help=f"allow devices larger than {MAX_PLAUSIBLE_FLOPPY_BYTES} bytes",
    )
    p_read.set_defaults(func=cmd_read)

    p_write = sub.add_parser(
        "write",
        help="write a .dsk file onto the disk in the drive",
        description="Write a .dsk file onto the disk in the drive.",
    )
    p_write.add_argument("image", help="path to the .dsk image to write")
    p_write.add_argument("device", help="target block device, e.g. /dev/sdb")
    p_write.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    p_write.add_argument(
        "--verify", action="store_true", help="read back and compare after writing"
    )
    p_write.add_argument(
        "--force",
        action="store_true",
        help=f"allow devices larger than {MAX_PLAUSIBLE_FLOPPY_BYTES} bytes",
    )
    p_write.set_defaults(func=cmd_write)

    args = parser.parse_args()

    if sys.platform != "linux":
        print("error: this script targets Linux (block device ioctls).", file=sys.stderr)
        return 1
    if os.geteuid() != 0:
        print("error: run as root (raw block device access requires it).", file=sys.stderr)
        return 1

    try:
        return args.func(args)
    except Failure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
