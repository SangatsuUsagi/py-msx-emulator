#!/usr/bin/env python3
"""Create a blank ``*.dsk`` disk image for use with ``--fdd1`` / ``--fdd2``.

The image is filled with 0xE5 (the standard floppy format fill byte) so it looks
like a freshly formatted-but-empty medium; MSX ``CALL FORMAT`` then writes the
real boot sector / FAT / directory.

Usage:
    python scripts/make_blank_dsk.py OUTPUT.dsk [--size KB]

    --size  Image size in KiB (default: 720, a 2DD floppy). Must be a multiple
            of 0.5 KiB (512-byte sectors).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SECTOR_SIZE = 512
FILL = 0xE5


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a blank *.dsk image")
    parser.add_argument("output", help="Output .dsk path")
    parser.add_argument("--size", type=float, default=720.0,
                        help="Image size in KiB (default: 720 = 2DD)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite the output file if it already exists")
    args = parser.parse_args()

    total = int(round(args.size * 1024))
    if total <= 0 or (total % SECTOR_SIZE) != 0:
        print(f"error: size {args.size} KiB is not a positive multiple of "
              f"{SECTOR_SIZE} bytes", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"error: {out} already exists (use --force to overwrite)", file=sys.stderr)
        sys.exit(1)

    out.write_bytes(bytes([FILL]) * total)
    print(f"wrote {out} ({total} bytes, {total // SECTOR_SIZE} sectors)")


if __name__ == "__main__":
    main()
