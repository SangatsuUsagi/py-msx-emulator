"""include/msx_core.h, the C form of the core API (machine-c-abi): one
function per CoreApi member, the boundary constants with the Python values,
valid C11, the ownership and version rules in its comments, and the version
against a baseline of the declarations. The header is read as
text; nothing is compiled into or loaded by Python."""
from __future__ import annotations

import enum
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

# The header's declarations and macro values with their version, one item per
# line (machine-c-abi "The ABI version follows the kind of header change").
_BASELINE = Path(__file__).resolve().parent / "msx_core_abi_baseline.txt"
_MACRO_PATTERN = r"^#define\s+MSX_(\w+)\s+\(?(-?(?:0x[0-9A-Fa-f]+|\d+))\)?\s*$"
_VERSION_MACROS = {"CORE_ABI_VERSION_MAJOR", "CORE_ABI_VERSION_MINOR"}


def _text() -> str:
    return _HEADER.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def _code() -> str:
    """The header without its comments."""
    return _strip_comments(_text())


def _macros(code: str) -> dict[str, int]:
    return {
        name: int(value, 0)
        for name, value in re.findall(_MACRO_PATTERN, code, flags=re.MULTILINE)
    }


def _abi_items(text: str) -> set[str]:
    """One line per macro value, typedef and function of the header's text,
    with its whitespace collapsed. The version macros are not items, and a
    function-like macro is read as one `#` line, which is not an item."""
    code = _strip_comments(text).replace("\\\n", " ")
    items = set()
    for name, value in _macros(code).items():
        if name not in _VERSION_MACROS:
            items.add(f"macro {name} {value}")
    lines = [line for line in code.splitlines() if not line.startswith("#")]
    body = " ".join(line for line in lines if line.strip() not in ('extern "C" {', "}"))
    # A `}` that ends a struct stays with its typedef's name and `;`.
    for match in re.finditer(r"[^;{}]+(?:\{[^}]*\}[^;]*)?;", body):
        items.add(" ".join(match.group(0).split()))
    return items


def _classify(baseline: set[str], current: set[str]) -> str:
    if baseline == current:
        return "none"
    return "minor" if baseline <= current else "major"


def _read_baseline() -> tuple[str, set[str]]:
    version, *items = _BASELINE.read_text(encoding="utf-8").splitlines()
    return version, set(items)


def _version_macros() -> tuple[int, int]:
    macros = _macros(_code())
    return macros["CORE_ABI_VERSION_MAJOR"], macros["CORE_ABI_VERSION_MINOR"]


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
    macros = _macros(_code())
    for name in _VERSION_MACROS:
        del macros[name]
    assert macros == _python_constants()


def test_the_version_value_combines_both_parts() -> None:
    # machine-c-abi "The version value combines both parts".
    code = " ".join(_code().split())
    assert (
        "#define MSX_CORE_ABI_VERSION ((MSX_CORE_ABI_VERSION_MAJOR << 16) | "
        "MSX_CORE_ABI_VERSION_MINOR)"
    ) in code
    assert "int32_t msx_core_abi_version(void);" in code


def test_the_major_version_stays_1_before_a_native_core() -> None:
    # machine-c-abi "The major version stays 1 before a native core".
    assert _version_macros()[0] == 1


def test_the_printer_port_is_the_first_minor_increase() -> None:
    # machine-c-abi "The printer port is the first minor increase".
    assert _version_macros()[1] >= 1
    _version, items = _read_baseline()
    assert "int32_t msx_core_attach_printer(msx_core *core);" in items
    assert "msx_bytes msx_core_drain_printer(msx_core *core);" in items


_TAPE_ITEMS = {
    "int32_t msx_core_mount_tape(msx_core *core, const uint8_t *edges, size_t len);",
    "int32_t msx_core_eject_tape(msx_core *core);",
    "int32_t msx_core_seek_tape(msx_core *core, int64_t position);",
    "void msx_core_tape_info(msx_core *core, msx_tape_info *out);",
    "typedef struct msx_tape_info { int32_t mounted; int32_t motor; "
    "int64_t position; int64_t length; } msx_tape_info;",
    "macro STATUS_NO_TAPE_PORT 8",
    "macro TRACE_EVENT_TAPE_MOTOR 12",
}


def test_the_s1985_mapper_port_form_is_the_fourth_minor_increase() -> None:
    # machine-c-abi "The S1985 mapper port form is the fourth minor increase":
    # MachineConfig format 5 adds a document key and no declaration.
    assert _version_macros() == (1, 4)


def test_the_cassette_additions_are_a_minor_change() -> None:
    # machine-c-abi "The additions are a minor change": the 1.1 items are the
    # 1.2 baseline (unchanged in 1.3 and 1.4) without the cassette items.
    version, items = _read_baseline()
    assert version == "version 1.4"
    assert _TAPE_ITEMS <= items
    assert _classify(items - _TAPE_ITEMS, _abi_items(_text())) == "minor"


def test_the_cassette_constants_match_the_boundary() -> None:
    # machine-c-abi "The constants match the boundary".
    macros = _macros(_code())
    assert macros["STATUS_NO_TAPE_PORT"] == boundary.STATUS_NO_TAPE_PORT == 8
    assert macros["TRACE_EVENT_TAPE_MOTOR"] == boundary.TRACE_EVENT_TAPE_MOTOR == 12


def test_the_header_matches_the_baseline() -> None:
    version, baseline = _read_baseline()
    kind = _classify(baseline, _abi_items(_text()))
    assert kind == "none", (
        f"a {kind} change to the header: raise the version as the header's Versions "
        "comment says, then record the items and the version in the baseline"
    )
    major, minor = _version_macros()
    assert version == f"version {major}.{minor}"


def test_the_baseline_holds_every_function_struct_and_constant() -> None:
    # A parse failure must not pass as an empty or short baseline.
    _version, items = _read_baseline()
    functions = [item for item in items if "msx_core_" in item and "(" in item]
    assert len(functions) == len(_core_api_members()) + len(_EXTRA_FUNCTIONS)
    assert sum(item.startswith("typedef struct") for item in items) == 18
    assert sum(item.startswith("macro ") for item in items) == len(_python_constants())


def test_an_added_declaration_raises_the_minor_version() -> None:
    # machine-c-abi "An added declaration raises the minor version".
    for added in ("int32_t msx_core_new(msx_core *core);\n", "#define MSX_MAPPER_NEW 99\n"):
        assert _classify(_abi_items(_text()), _abi_items(_text() + added)) == "minor"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("int32_t mouse_dx;", "int16_t mouse_dx;"),
        ("int32_t msx_core_attach_printer(msx_core *core);",
         "int32_t msx_core_attach_printer(msx_core *core, int32_t port);"),
        ("#define MSX_MAX_BREAKPOINTS 4", "#define MSX_MAX_BREAKPOINTS 8"),
    ],
)
def test_a_changed_declaration_is_a_major_change(old: str, new: str) -> None:
    # machine-c-abi "A changed declaration is a major change".
    assert old in _text()
    changed = _text().replace(old, new)
    assert _classify(_abi_items(_text()), _abi_items(changed)) == "major"


@pytest.mark.parametrize(
    "removed",
    ["int32_t msx_core_attach_printer(msx_core *core);", "#define MSX_STATUS_NO_PRINTER_PORT 7"],
)
def test_a_removed_declaration_is_a_major_change(removed: str) -> None:
    # machine-c-abi "A removed declaration is a major change".
    assert removed in _text()
    changed = _text().replace(removed, "")
    assert _classify(_abi_items(_text()), _abi_items(changed)) == "major"


def test_a_comment_change_keeps_the_version() -> None:
    # machine-c-abi "A comment change keeps the version".
    changed = "/* a new comment */\n" + _text().replace(";\n", ";  // note\n\n")
    assert _classify(_abi_items(_text()), _abi_items(changed)) == "none"


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


def _comment_words() -> str:
    # One line of words: each comment line's leading "*" dropped.
    lines = [re.sub(r"^\s*\*\s?", "", line) for line in _text().splitlines()]
    return " ".join(" ".join(lines).split())


def test_the_header_states_the_acceptance_rule() -> None:
    # machine-c-abi "The header states the acceptance rule".
    assert (
        "A host rejects a core with another major version or a lower minor version"
    ) in _comment_words()


def test_the_header_states_the_ownership_rule() -> None:
    # machine-c-abi "The header states the ownership rule".
    text = _comment_words()
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


def _accepts(version: int, major: int, minor: int) -> bool:
    """machine-c-abi "A host accepts a core by version", for a host built
    with major.minor."""
    return version >> 16 == major and version & 0xFFFF >= minor


@pytest.mark.skipif(shutil.which("cc") is None, reason="no cc on the path")
def test_the_compatibility_macro_follows_the_rule(tmp_path: Path) -> None:
    # machine-c-abi "The compatibility macro follows the rule".
    major, minor = _version_macros()
    versions = [
        (major << 16) | minor,
        (major << 16) | (minor + 1),
        (major << 16) | (minor - 1),
        ((major + 1) << 16) | minor,
        ((major - 1) << 16) | minor,
    ]
    lines = [f'#include "{_HEADER}"', "#include <stdio.h>", "int main(void) {"]
    for version in versions:
        lines.append(f"    printf(\"%d\\n\", MSX_CORE_ABI_COMPATIBLE({version}) ? 1 : 0);")
    lines += ["    return 0;", "}"]
    source = tmp_path / "compatible.c"
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    program = tmp_path / "compatible"
    build = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(program), str(source)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(program)], capture_output=True, text=True, check=True)
    results = [line == "1" for line in run.stdout.split()]
    assert results == [_accepts(v, major, minor) for v in versions]
    assert results == [True, True, False, False, False]
