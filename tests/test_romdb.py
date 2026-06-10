import hashlib
import importlib

import pytest

import msx.romdb as romdb


def _reload() -> None:
    """Reset the module-level cache between tests."""
    romdb._db = None


# ---------------------------------------------------------------------------
# lookup — known SHA1
# ---------------------------------------------------------------------------

# SHA1 values taken directly from roms/msx_romdb.yaml (no ROM file required).
_KNOWN_KONAMISCO_SHA1 = "937464eb371c68add2236bcef91d24a8ce7c4ed1"
_KNOWN_MIRRORED_SHA1  = "6b8a684ddbadd798a8e599449b823bceca9cdb58"


def _fake_cartridge(sha1_hex: str) -> bytes:
    """Return a 1-byte stub whose SHA1 matches sha1_hex by patching the DB directly."""
    # We cannot synthesise arbitrary pre-image; instead we inject into the cache.
    return b"\x00"  # placeholder — test patches _db directly


def test_lookup_known_konamisco(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    monkeypatch.setattr(romdb, "_db", {
        _KNOWN_KONAMISCO_SHA1: {"mapper": "KonamiSCC", "title_en": "A1 Spirit"},
    })
    cart = b"\xAB"
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "KonamiSCC"}})
    assert romdb.lookup(cart) == "KonamiSCC"


def test_lookup_known_mirrored(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    cart = b"\xCD\xEF"
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"mapper": "Mirrored"}})
    assert romdb.lookup(cart) == "Mirrored"


# ---------------------------------------------------------------------------
# lookup — unknown SHA1
# ---------------------------------------------------------------------------

def test_lookup_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    monkeypatch.setattr(romdb, "_db", {})
    assert romdb.lookup(b"\x00\x01\x02") is None


# ---------------------------------------------------------------------------
# lookup — edge cases
# ---------------------------------------------------------------------------

def test_lookup_empty_bytes_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(romdb, "_db", {})
    assert romdb.lookup(b"") is None


def test_lookup_system_msx2(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    cart = b"\x11"
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"system": "MSX2", "mapper": "KonamiSCC"}})
    assert romdb.lookup_system(cart) == "MSX2"


def test_lookup_system_msx1(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    cart = b"\x22"
    sha1 = hashlib.sha1(cart).hexdigest()
    monkeypatch.setattr(romdb, "_db", {sha1: {"system": "MSX", "mapper": "Mirrored"}})
    assert romdb.lookup_system(cart) == "MSX"


def test_lookup_system_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload()
    monkeypatch.setattr(romdb, "_db", {})
    assert romdb.lookup_system(b"\x33\x44") is None


def test_lookup_system_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert romdb.lookup_system(b"") is None


def test_lookup_uses_db_file_sha1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that lookup() computes SHA1 correctly (not CRC32, etc.)."""
    _reload()
    data = b"test-rom-content"
    expected_sha1 = hashlib.sha1(data).hexdigest()
    monkeypatch.setattr(romdb, "_db", {expected_sha1: {"mapper": "ASCII8"}})
    assert romdb.lookup(data) == "ASCII8"
    # A different hash algorithm would not match
    wrong_sha1 = hashlib.md5(data).hexdigest()
    monkeypatch.setattr(romdb, "_db", {wrong_sha1: {"mapper": "ASCII8"}})
    assert romdb.lookup(data) is None
