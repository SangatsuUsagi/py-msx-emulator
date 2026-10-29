"""The text spelling of a watchpoint's access kind.

The debugger, the RPC server and the CLI spell a kind "r", "w" or "rw"; the
core takes the WATCH_READ / WATCH_WRITE bit flags (msx/boundary.py). This
module is the one conversion between the two.
"""
from __future__ import annotations

from msx.boundary import WATCH_READ, WATCH_WRITE


def watch_kind(text: str) -> int | None:
    """The kind a non-empty mix of "r" and "w" spells (any case, so "R",
    "wr" and "rw" all work), or None for any other text."""
    kind = 0
    text = text.strip().lower()
    if not text:
        return None
    for char in text:
        if char == "r":
            kind |= WATCH_READ
        elif char == "w":
            kind |= WATCH_WRITE
        else:
            return None
    return kind


def watch_text(kind: int) -> str:
    """The spelling of `kind`: "r", "w" or "rw". Raises ValueError for a
    kind of 0 or with another bit."""
    if kind == WATCH_READ:
        return "r"
    if kind == WATCH_WRITE:
        return "w"
    if kind == WATCH_READ | WATCH_WRITE:
        return "rw"
    raise ValueError(f"invalid watch kind {kind}")
