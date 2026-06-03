from __future__ import annotations

import hashlib
import sys
from pathlib import Path

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]
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
            with open(_DB_PATH) as fh:
                data = _yaml.safe_load(fh)
            _db = data.get("roms", {}) if isinstance(data, dict) else {}
    return _db


def lookup(cartridge: bytes) -> str | None:
    """Return the mapper type string for the given cartridge ROM, or None if not found."""
    if not cartridge:
        return None
    sha1 = hashlib.sha1(cartridge).hexdigest()
    entry = _load().get(sha1)
    if entry is None:
        return None
    return str(entry["mapper"])
