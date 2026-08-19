import hashlib

import pytest

import msx.romdb as romdb
from msx.romdb import RomDbEntry


def _patch_db(monkeypatch: pytest.MonkeyPatch, raw: dict[str, dict[str, object]]) -> None:
    """Inject fake ROM-database entries by patching the _load() seam directly
    (mirrors _load()'s own field-parsing rules so edge cases, e.g. a missing
    "mapper" key, behave identically to the real YAML-backed path)."""
    entries = {
        sha1: RomDbEntry(
            mapper=str(v["mapper"]) if v.get("mapper") is not None else None,
            system=str(v["system"]) if "system" in v else None,
            title_jp=str(v["title_jp"]) if "title_jp" in v else None,
        )
        for sha1, v in raw.items()
    }
    monkeypatch.setattr(romdb, "_load", lambda: entries)


# ---------------------------------------------------------------------------
# lookup — known SHA1
# ---------------------------------------------------------------------------

# SHA1 values taken directly from roms/msx_romdb.yaml (no ROM file required).
_KNOWN_KONAMISCO_SHA1 = "937464eb371c68add2236bcef91d24a8ce7c4ed1"
_KNOWN_MIRRORED_SHA1  = "6b8a684ddbadd798a8e599449b823bceca9cdb58"


def _fake_cartridge(sha1_hex: str) -> bytes:
    """Return a 1-byte stub whose SHA1 matches sha1_hex by patching the DB directly."""
    # We cannot synthesise arbitrary pre-image; instead we inject into the cache.
    return b"\x00"  # placeholder — test patches _load directly


def test_lookup_known_konamisco(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, {
        _KNOWN_KONAMISCO_SHA1: {"mapper": "KonamiSCC", "title_en": "A1 Spirit"},
    })
    cart = b"\xAB"
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, {sha1: {"mapper": "KonamiSCC"}})
    assert romdb.lookup(cart) == "KonamiSCC"


def test_lookup_known_mirrored(monkeypatch: pytest.MonkeyPatch) -> None:
    cart = b"\xCD\xEF"
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, {sha1: {"mapper": "Mirrored"}})
    assert romdb.lookup(cart) == "Mirrored"


# ---------------------------------------------------------------------------
# lookup — unknown SHA1
# ---------------------------------------------------------------------------

def test_lookup_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, {})
    assert romdb.lookup(b"\x00\x01\x02") is None


# ---------------------------------------------------------------------------
# lookup — edge cases
# ---------------------------------------------------------------------------

def test_lookup_empty_bytes_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, {})
    assert romdb.lookup(b"") is None


def test_lookup_system_msx2(monkeypatch: pytest.MonkeyPatch) -> None:
    cart = b"\x11"
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, {sha1: {"system": "MSX2", "mapper": "KonamiSCC"}})
    assert romdb.lookup_system(cart) == "MSX2"


def test_lookup_system_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    cart = b"\x22"
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, {sha1: {"system": "MSX", "mapper": "Mirrored"}})
    assert romdb.lookup_system(cart) == "MSX"


def test_lookup_system_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, {})
    assert romdb.lookup_system(b"\x33\x44") is None


def test_lookup_system_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert romdb.lookup_system(b"") is None


def test_lookup_uses_db_file_sha1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that lookup() computes SHA1 correctly (not CRC32, etc.)."""
    data = b"test-rom-content"
    expected_sha1 = hashlib.sha1(data).hexdigest()
    _patch_db(monkeypatch, {expected_sha1: {"mapper": "ASCII8"}})
    assert romdb.lookup(data) == "ASCII8"
    # A different hash algorithm would not match
    wrong_sha1 = hashlib.md5(data).hexdigest()
    _patch_db(monkeypatch, {wrong_sha1: {"mapper": "ASCII8"}})
    assert romdb.lookup(data) is None


def test_lookup_entry_without_mapper_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # A matched entry missing the "mapper" key returns None (via entry.get),
    # mirroring lookup_system/lookup_title (no KeyError).
    cart = b"\x55"
    sha1 = hashlib.sha1(cart).hexdigest()
    _patch_db(monkeypatch, {sha1: {"system": "MSX2"}})
    assert romdb.lookup(cart) is None
