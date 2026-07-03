from __future__ import annotations

import hashlib
import sys
from pathlib import Path

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _yaml = None
    _YAML_AVAILABLE = False
    print(
        "warning: PyYAML not installed — ROM database auto-detection disabled",
        file=sys.stderr,
    )

_DB_PATH = Path(__file__).parent.parent / "roms" / "msx_romdb.yaml"
_db: dict[str, dict[str, object]] | None = None


def _load() -> dict[str, dict[str, object]]:
    global _db
    if _db is None:
        if not _YAML_AVAILABLE or not _DB_PATH.exists():
            _db = {}
        else:
            with open(_DB_PATH, encoding="utf-8") as fh:
                data = _yaml.safe_load(fh)
            _db = data.get("roms", {}) if isinstance(data, dict) else {}
    return _db


def _entry(cartridge: bytes) -> dict[str, object] | None:
    """Return the ROM-database entry for a cartridge, computing its sha1 once."""
    if not cartridge:
        return None
    sha1 = hashlib.sha1(cartridge, usedforsecurity=False).hexdigest()
    return _load().get(sha1)


def lookup(cartridge: bytes) -> str | None:
    """Return the mapper type string for the given cartridge ROM, or None if not found."""
    entry = _entry(cartridge)
    if entry is None:
        return None
    mapper = entry.get("mapper")
    return str(mapper) if mapper is not None else None


def lookup_system(cartridge: bytes) -> str | None:
    """Return the system string (e.g. 'MSX', 'MSX2') for the given cartridge ROM, or None."""
    entry = _entry(cartridge)
    if entry is None:
        return None
    return str(entry["system"]) if "system" in entry else None


def lookup_title(cartridge: bytes) -> str | None:
    """Return the title_jp for the given cartridge ROM, or None if not found."""
    entry = _entry(cartridge)
    if entry is None:
        return None
    return str(entry["title_jp"]) if "title_jp" in entry else None
