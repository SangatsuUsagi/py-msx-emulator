"""Checks shared by every save-state parser: msx/state.py's parse_snapshot,
msx/vdp/v9938.py's parse_v9938_state and each producer's parse_state.

Each helper returns the value with its type narrowed, or raises SchemaError
naming the field path (e.g. `cpu_regs.A`). A leaf module: it imports nothing
from the emulator, so every parser can use it without an import cycle.
"""
from __future__ import annotations

from typing import cast

# PORT-NOTE: these helpers are the hand-written form of what a port gets from
#   its deserializer.
# Rust equivalent: `#[derive(Deserialize)]` structs with
#   `#[serde(deny_unknown_fields)]`, plus a `validate()` for ranges and
#   lengths, returning `Result<_, SchemaError>`.
# C++ equivalent: a JSON library's typed accessors (e.g. nlohmann::json's
#   `at()` / `get<T>()`) wrapped to throw one SchemaError type.
# Kept as-is here because: the standard library has no schema layer, and the
#   parsers stay explicit field-by-field code that maps line for line.

# Largest value a non-negative counter may hold (i64 in a port).
I64_MAX: int = (1 << 63) - 1


class SchemaError(ValueError):
    """A save-state value that does not match its schema."""


def check_int(value: object, label: str, lo: int, hi: int) -> int:
    """Return `value` if it is an int (not a bool) in [lo, hi]."""
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise SchemaError(f"{label} must be an int in [{lo}, {hi}], got {value!r}")
    return value


def check_optional_int(value: object, label: str, lo: int, hi: int) -> int | None:
    """Return `value` if it is None or an int in [lo, hi]."""
    if value is None:
        return None
    return check_int(value, label, lo, hi)


def check_bool(value: object, label: str) -> bool:
    """Return `value` if it is a bool."""
    if not isinstance(value, bool):
        raise SchemaError(f"{label} must be a bool, got {value!r}")
    return value


def check_str(value: object, label: str) -> str:
    """Return `value` if it is a str."""
    if not isinstance(value, str):
        raise SchemaError(f"{label} must be a string, got {value!r}")
    return value


def check_int_list(value: object, label: str, length: int, lo: int, hi: int) -> list[int]:
    """Return `value` as a list of `length` ints, each in [lo, hi]."""
    if not isinstance(value, list) or len(value) != length:
        raise SchemaError(f"{label} must be a list of {length} ints, got {value!r}")
    result: list[int] = []
    for i, item in enumerate(value):
        result.append(check_int(item, f"{label}[{i}]", lo, hi))
    return result


def check_bytes(
    value: object, label: str, length: int | None = None, *, max_length: int | None = None
) -> bytearray:
    """Return `value` as a byte blob, of exactly `length` bytes, or at most
    `max_length` bytes, when given. A file's blob decodes as bytearray; a
    producer's own snapshot may hold bytes, which is accepted and copied."""
    if isinstance(value, bytes):
        value = bytearray(value)
    if not isinstance(value, bytearray):
        raise SchemaError(f"{label} must be a byte blob, got {type(value).__name__}")
    if length is not None and len(value) != length:
        raise SchemaError(f"{label} must be {length} bytes, got {len(value)}")
    if max_length is not None and len(value) > max_length:
        raise SchemaError(f"{label} must be at most {max_length} bytes, got {len(value)}")
    return value


def check_choice(value: object, label: str, choices: tuple[int, ...]) -> int:
    """Return `value` if it is an int (not a bool) equal to one of `choices`."""
    if isinstance(value, bool) or not isinstance(value, int) or value not in choices:
        raise SchemaError(f"{label} must be one of {list(choices)}, got {value!r}")
    return value


def check_float(value: object, label: str, lo: float, hi: float) -> float:
    """Return `value` as a finite float in [lo, hi] (an int is accepted, as
    JSON writes a whole-valued float without a fraction)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not lo <= number <= hi:  # also rejects NaN; infinities fall outside the range
        raise SchemaError(f"{label} must be a number in [{lo}, {hi}], got {value!r}")
    return number


def check_object(
    value: object, label: str, keys: frozenset[str] | None = None
) -> dict[str, object]:
    """Return `value` as a dict with str keys; when `keys` is given, it must
    hold exactly those keys (unknown and missing keys are both rejected)."""
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be an object, got {type(value).__name__}")
    unknown: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise SchemaError(f"{label} has a non-string key {key!r}")
        if keys is not None and key not in keys:
            unknown.append(key)
    if unknown:
        raise SchemaError(f"{label} has unknown keys {sorted(unknown)}")
    if keys is not None:
        missing: list[str] = []
        for key in keys:
            if key not in value:
                missing.append(key)
        if missing:
            raise SchemaError(f"{label} is missing keys {sorted(missing)}")
    return cast(dict[str, object], value)


def check_optional_object(value: object, label: str) -> dict[str, object] | None:
    """Return `value` if it is None or an object."""
    if value is None:
        return None
    return check_object(value, label)
