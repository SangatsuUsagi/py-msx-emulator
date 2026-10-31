"""tests/vectors/canonical_json.json: the canonical JSON vectors a native
core checks its own writer and reader against (machine-state "Snapshot
bytes are RFC 8785 canonical JSON", machine-loader's configuration
identity)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from msx.canonical_json import decode_strict, encode_canonical, to_jsonable

_VECTORS = json.loads(
    (Path(__file__).parent / "vectors" / "canonical_json.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _VECTORS["accept"], ids=lambda c: c["name"])
def test_accepted_input_gives_the_canonical_form_and_its_hash(case: dict[str, str]) -> None:
    canonical = encode_canonical(decode_strict(case["input"].encode("utf-8")))
    assert canonical.decode("utf-8") == case["canonical"]
    assert hashlib.sha256(canonical).hexdigest() == case["sha256"]


@pytest.mark.parametrize("case", _VECTORS["reject"], ids=lambda c: c["name"])
def test_rejected_input_is_refused(case: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        decode_strict(case["input"].encode("utf-8"))


@pytest.mark.parametrize("case", _VECTORS["base64"], ids=lambda c: c["bytes_hex"] or "empty")
def test_a_blob_becomes_padded_base64(case: dict[str, str]) -> None:
    assert to_jsonable(bytes.fromhex(case["bytes_hex"])) == case["base64"]
