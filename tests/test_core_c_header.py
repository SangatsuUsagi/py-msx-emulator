"""include/msx_core.h, the C form of the core API (machine-c-abi): one
function per CoreApi member, the boundary constants with the Python values,
valid C11, and the ownership rule in its comments. The header is read as
text; nothing is compiled into or loaded by Python."""
from __future__ import annotations

import enum
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import msx.boundary as boundary
from msx.boundary import KeyboardLayout, MapperId, MediaId, VideoStandard
from msx.core_api import CoreApi

_HEADER = Path(__file__).resolve().parent.parent / "include" / "msx_core.h"
_EXTRA_FUNCTIONS = {"create", "destroy", "abi_version"}

# The ABI version of the header, and the fingerprint of the declarations it
# belongs to (machine-c-abi "The ABI version changes with the declarations").
# A change to the declarations or macro values increases the version by 1 and
# records the new fingerprint, both in the same change.
_ABI_VERSION = 1
_ABI_FINGERPRINT = "834df8172580cbf2372c1d12b3e04a2b81b09ae3eb230bcfbf467252c783301d"


def _text() -> str:
    return _HEADER.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def _code() -> str:
    """The header without its comments."""
    return _strip_comments(_text())


def _fingerprint(text: str) -> str:
    """SHA-256 of the header text without its comments and whitespace runs."""
    tokens = " ".join(_strip_comments(text).split())
    return hashlib.sha256(tokens.encode("utf-8")).hexdigest()


def _core_api_members() -> set[str]:
    return {name for name in vars(CoreApi) if not name.startswith("_")}


def _python_constants() -> dict[str, int]:
    """Every boundary constant, by its MSX_ macro name."""
    result: dict[str, int] = {}
    for name, value in vars(boundary).items():
        if name.isupper() and type(value) is int:
            result[name] = value
    enums: tuple[tuple[str, type[enum.IntEnum]], ...] = (
        ("MEDIA", MediaId), ("VIDEO", VideoStandard), ("KEYBOARD", KeyboardLayout),
        ("MAPPER", MapperId),
    )
    for prefix, enum_type in enums:
        for member in enum_type:
            result[f"{prefix}_{member.name}"] = int(member)
    result["MEDIA_COUNT"] = len(MediaId)
    return result


def test_every_core_api_member_has_a_c_function() -> None:
    # machine-c-abi "Every core API member has a C function".
    declared = set(re.findall(r"\bmsx_core_(\w+)\s*\(", _code()))
    assert declared - _EXTRA_FUNCTIONS == _core_api_members()
    assert _EXTRA_FUNCTIONS <= declared


def test_every_constant_matches() -> None:
    # machine-c-abi "Every constant matches".
    pattern = r"^#define\s+MSX_(\w+)\s+\(?(-?(?:0x[0-9A-Fa-f]+|\d+))\)?\s*$"
    macros = {
        name: int(value, 0)
        for name, value in re.findall(pattern, _code(), flags=re.MULTILINE)
    }
    assert macros.pop("CORE_ABI_VERSION") == _ABI_VERSION
    assert macros == _python_constants()


def test_a_declaration_change_needs_a_new_version() -> None:
    # machine-c-abi "A declaration change needs a new version".
    assert _fingerprint(_text()) == _ABI_FINGERPRINT, (
        "the header's declarations changed: increase MSX_CORE_ABI_VERSION by 1 "
        "and record the new _ABI_VERSION and _ABI_FINGERPRINT"
    )
    changed = _text().replace("int32_t mouse_dx;", "int16_t mouse_dx;")
    assert changed != _text()
    assert _fingerprint(changed) != _ABI_FINGERPRINT


def test_a_comment_change_keeps_the_version() -> None:
    # machine-c-abi "A comment change keeps the version".
    changed = "/* a new comment */\n" + _text().replace(";\n", ";  // note\n\n")
    assert _fingerprint(changed) == _ABI_FINGERPRINT


def test_the_mouse_motion_and_the_frame_breakpoint_are_wide() -> None:
    # machine-c-abi "The mouse motion and the frame breakpoint are wide".
    code = _code()
    assert re.search(r"\bint32_t\s+mouse_dx;", code)
    assert re.search(r"\bint32_t\s+mouse_dy;", code)
    assert re.search(
        r"msx_core_set_frame_breakpoint\(\s*msx_core\s*\*core,\s*int64_t\s+frame\s*\)", code
    )


def test_the_create_call_takes_a_caller_buffer() -> None:
    # machine-c-abi "The create call takes a caller buffer".
    code = " ".join(_code().split())
    assert (
        "int32_t msx_core_create(const msx_machine_config *config, msx_core **out_core, "
        "char *error_text, size_t error_cap, size_t *error_len);"
    ) in code
    assert "has_rtc_initial_time" not in code


def test_the_header_states_the_ownership_rule() -> None:
    # machine-c-abi "The header states the ownership rule".
    # One line of words: each comment line's leading "*" dropped.
    lines = [re.sub(r"^\s*\*\s?", "", line) for line in _text().splitlines()]
    text = " ".join(" ".join(lines).split())
    assert "valid until the next step_frame, reset or load_state" in text
    assert "valid until the next call on the same core" in text
    assert "read only during the call" in text
    assert "one thread at a time" in text


@pytest.mark.skipif(shutil.which("cc") is None, reason="no cc on the path")
def test_the_header_compiles(tmp_path: Path) -> None:
    # machine-c-abi "The header compiles".
    source = tmp_path / "check.c"
    source.write_text(f'#include "{_HEADER}"\n', encoding="utf-8")
    result = subprocess.run(
        ["cc", "-std=c11", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", str(source)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
