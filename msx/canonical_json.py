"""Canonical JSON for the core's portable documents (the save-state snapshot
and the MachineConfig document).

Writing produces RFC 8785 (JCS) canonical bytes for a document with ASCII
keys and integers only: sorted keys, no whitespace, minimal string escaping,
raw UTF-8. Reading accepts any whitespace and key order but rejects floats,
NaN/Infinity and duplicate keys. Byte blobs are written as padded RFC 4648
base64 strings; each field's parser decodes them (msx/snapshot_schema.py
check_bytes).
"""
from __future__ import annotations

import base64
import json

from msx.snapshot_schema import SAFE_INT_MAX


def to_jsonable(node: object) -> object:
    """Recursively convert a document to JSON-serialisable form.

    Byte blobs become base64 strings; lists and dicts recurse; other scalars
    pass through. A float, or an int beyond +-SAFE_INT_MAX, raises ValueError
    (a writer bug: such a document would not read back the same everywhere).
    """
    if isinstance(node, (bytes, bytearray)):
        return base64.b64encode(bytes(node)).decode("ascii")
    if isinstance(node, dict):
        result: dict[str, object] = {}
        for key, value in node.items():
            result[key] = to_jsonable(value)
        return result
    if isinstance(node, (list, tuple)):
        items: list[object] = []
        for value in node:
            items.append(to_jsonable(value))
        return items
    if isinstance(node, float):
        raise ValueError(f"document holds a float ({node!r}); only integers are portable")
    if isinstance(node, int) and not isinstance(node, bool):
        if not -SAFE_INT_MAX <= node <= SAFE_INT_MAX:
            raise ValueError(f"document integer {node} is outside +-(2**53 - 1)")
    return node


def encode_canonical(document: object) -> bytes:
    """RFC 8785 (JCS) form of a document with ASCII keys and no floats:
    sorted keys, no whitespace, minimal string escaping, raw UTF-8."""
    text = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def _reject_float(text: str) -> object:
    raise ValueError(f"floating-point number {text} (only integers are allowed)")


def _reject_constant(text: str) -> object:
    raise ValueError(f"{text} is not allowed")


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def decode_strict(data: bytes) -> object:
    """Parse JSON text, rejecting floats, NaN/Infinity and duplicate keys.
    Whitespace and key order are free (only writing is canonical).

    Raises:
        ValueError: Also UnicodeDecodeError and json.JSONDecodeError.
    """
    return json.loads(
        data.decode("utf-8"),
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_object_without_duplicates,
    )


def mismatched_version(data: bytes, expected: int) -> str | None:
    """For data that strict decoding rejected: if it is ordinary JSON (an
    older format may hold floats) whose `format_version` is not `expected`,
    return that version as text, so the caller reports the version mismatch
    rather than the strict-decoding error; otherwise None."""
    try:
        lenient = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(lenient, dict):
        return None
    version = lenient.get("format_version")
    if version == expected:
        return None
    return str(version)
