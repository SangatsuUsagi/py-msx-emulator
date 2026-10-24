"""msx/snapshot_schema.py: base64 blobs and the JSON-safe integer bound
(machine-state "Byte blobs are base64 strings", "Snapshot decoding is strict")."""
from __future__ import annotations

import base64

import pytest

from msx.snapshot_schema import SAFE_INT_MAX, SchemaError, check_bytes, check_int


def test_base64_string_decodes_to_bytearray() -> None:
    text = base64.b64encode(bytes(range(7))).decode("ascii")
    assert check_bytes(text, "blob", 7) == bytearray(range(7))


def test_bytes_and_bytearray_are_accepted() -> None:
    raw = bytearray(b"\x01\x02")
    result = check_bytes(raw, "blob")
    assert result == raw and result is raw
    assert check_bytes(b"\x01\x02", "blob") == bytearray(b"\x01\x02")


@pytest.mark.parametrize(
    "text",
    [
        "AAE*",      # character outside the alphabet
        "AAE",       # missing padding
        "AAF=",      # non-zero unused bits (canonical form is "AAE=")
        "AA E=",     # whitespace
        "AAE=\n",    # trailing newline
    ],
)
def test_non_canonical_base64_is_rejected_naming_the_field(text: str) -> None:
    with pytest.raises(SchemaError, match="memory.ram"):
        check_bytes(text, "memory.ram")


def test_decoded_length_is_checked() -> None:
    with pytest.raises(SchemaError, match="must be 4 bytes"):
        check_bytes(base64.b64encode(b"abc").decode("ascii"), "blob", 4)


def test_safe_int_bound_is_two_to_the_53_minus_one() -> None:
    assert SAFE_INT_MAX == 2**53 - 1
    assert check_int(SAFE_INT_MAX, "n", 0, SAFE_INT_MAX) == SAFE_INT_MAX
    with pytest.raises(SchemaError):
        check_int(2**53, "n", 0, SAFE_INT_MAX)
