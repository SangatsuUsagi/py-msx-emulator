"""msx/canonical_json.py: the canonical writer and strict reader shared by the
snapshot and the MachineConfig document."""
from __future__ import annotations

import json

import pytest

from msx.canonical_json import decode_strict, encode_canonical, mismatched_version, to_jsonable


def test_canonical_bytes_sort_keys_and_drop_whitespace() -> None:
    document = {"b": [1, 2], "a": {"y": True, "x": None}, "ü": "ゲーム"}
    expected = '{"a":{"x":null,"y":true},"b":[1,2],"ü":"ゲーム"}'
    assert encode_canonical(document) == expected.encode("utf-8")


def test_blobs_become_base64_strings() -> None:
    assert to_jsonable({"ram": bytearray(b"\x00\x01\x02")}) == {"ram": "AAEC"}


@pytest.mark.parametrize("value", [1.5, 2**53, -(2**53)])
def test_the_writer_refuses_floats_and_unsafe_integers(value: object) -> None:
    with pytest.raises(ValueError):
        to_jsonable({"n": value})


@pytest.mark.parametrize(
    "text", ['{"n":1.0}', '{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}', '{"n":1,"n":2}']
)
def test_the_reader_refuses_floats_constants_and_duplicate_keys(text: str) -> None:
    with pytest.raises(ValueError):
        decode_strict(text.encode())


def test_the_reader_accepts_any_whitespace_and_order() -> None:
    assert decode_strict(b'{ "b" : 2 ,\n "a" : 1 }') == {"a": 1, "b": 2}


def test_mismatched_version_reports_an_older_version_holding_floats() -> None:
    data = json.dumps({"format_version": 16, "out_time": 1.5}).encode()
    assert mismatched_version(data, 17) == "16"
    assert mismatched_version(json.dumps({"format_version": 17}).encode(), 17) is None
    assert mismatched_version(b"not json", 17) is None
    assert mismatched_version(b"[1]", 17) is None
