from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from functools import lru_cache
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


@dataclass(frozen=True, slots=True)
class RomDbEntry:
    """One ROM-database entry, parsed once from its raw YAML dict at load time."""

    mapper: str | None
    system: str | None
    title_jp: str | None


@lru_cache(maxsize=1)
def _load() -> dict[str, RomDbEntry]:
    if not _YAML_AVAILABLE or not _DB_PATH.exists():
        return {}
    with open(_DB_PATH, encoding="utf-8") as fh:
        data = _yaml.safe_load(fh)
    raw = data.get("roms", {}) if isinstance(data, dict) else {}
    result: dict[str, RomDbEntry] = {}
    for sha1, v in raw.items():
        if not isinstance(v, dict):
            continue
        mapper = v.get("mapper")
        result[sha1] = RomDbEntry(
            mapper=str(mapper) if mapper is not None else None,
            system=str(v["system"]) if "system" in v else None,
            title_jp=str(v["title_jp"]) if "title_jp" in v else None,
        )
    return result


def _entry(cartridge: bytes) -> RomDbEntry | None:
    """Return the ROM-database entry for a cartridge, computing its sha1 once."""
    if not cartridge:
        return None
    sha1 = hashlib.sha1(cartridge, usedforsecurity=False).hexdigest()
    return _load().get(sha1)


def lookup(cartridge: bytes) -> str | None:
    """Return the mapper type string for the given cartridge ROM, or None if not found."""
    entry = _entry(cartridge)
    return entry.mapper if entry is not None else None


def lookup_system(cartridge: bytes) -> str | None:
    """Return the system string (e.g. 'MSX', 'MSX2') for the given cartridge ROM, or None."""
    entry = _entry(cartridge)
    return entry.system if entry is not None else None


def lookup_title(cartridge: bytes) -> str | None:
    """Return the title_jp for the given cartridge ROM, or None if not found."""
    entry = _entry(cartridge)
    return entry.title_jp if entry is not None else None
