"""The core package (msx/) must not depend on SDL2, the frontend or the host,
open files or read the host clock (machine-core "Core package has no frontend
dependency")."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_MSX_DIR = Path(__file__).resolve().parent.parent / "msx"
_FORBIDDEN_ROOTS = ("sdl2", "frontend", "host")

# Imports every msx module in a fresh interpreter where `import sdl2` fails, so
# a module already imported by another test cannot hide an SDL2 dependency.
_IMPORT_ALL = """
import importlib, pkgutil, sys
sys.modules["sdl2"] = None
import msx
for info in pkgutil.walk_packages(msx.__path__, "msx."):
    importlib.import_module(info.name)
"""


def _imported_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.append(node.module.split(".")[0])
    return roots


def test_every_core_module_imports_without_sdl2() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_ALL],
        cwd=_MSX_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_no_core_module_names_the_frontend() -> None:
    offenders = []
    for path in sorted(_MSX_DIR.rglob("*.py")):
        for root in _imported_roots(path):
            if root in _FORBIDDEN_ROOTS:
                offenders.append(f"{path.relative_to(_MSX_DIR.parent)}: {root}")
    assert offenders == []


# SDL keycodes for non-ASCII keys are 0x40000000 | scancode, scancode < 512
# (SDL_NUM_SCANCODES). Wider masks such as 0xFFFFFFFF are not key codes.
_SDL_KEYCODE_FIRST = 0x40000000
_SDL_KEYCODE_LAST = 0x400001FF


def test_no_sdl_key_codes_in_the_core() -> None:
    # machine-core "No SDL key codes in the core": no SDL key names in the
    # source text, and no integer literal in the scancode-keycode range.
    offenders = []
    for path in sorted(_MSX_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_MSX_DIR.parent)
        for name in ("SDLK", "SDL_SCANCODE"):
            if name in text:
                offenders.append(f"{rel}: {name}")
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if (isinstance(node, ast.Constant) and type(node.value) is int
                    and _SDL_KEYCODE_FIRST <= node.value <= _SDL_KEYCODE_LAST):
                offenders.append(f"{rel}:{node.lineno}: {node.value:#x}")
    assert offenders == []


def test_no_wall_clock_pacing_in_the_core() -> None:
    # machine-core "No wall-clock pacing in the core".
    assert list(_MSX_DIR.rglob("frame_timer.py")) == []


_FILE_CALLS = ("open", "read_bytes", "write_bytes", "read_text", "write_text")
_CLOCK_ROOTS = ("datetime", "time", "PIL")


def _file_calls(tree: ast.AST) -> list[tuple[int, str]]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _FILE_CALLS:
            calls.append((node.lineno, func.id))
        elif isinstance(func, ast.Attribute) and func.attr in _FILE_CALLS:
            calls.append((node.lineno, func.attr))
    return calls


def test_no_file_io_or_host_clock_in_the_core() -> None:
    # machine-core "No file I/O or host clock in the core": every module
    # under msx/, with no directory excepted.
    offenders = []
    for path in sorted(_MSX_DIR.rglob("*.py")):
        rel = path.relative_to(_MSX_DIR.parent)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _file_calls(tree):
            offenders.append(f"{rel}:{lineno}: {name}()")
        for root in _imported_roots(path):
            if root in _CLOCK_ROOTS:
                offenders.append(f"{rel}: import {root}")
    assert offenders == []


# machine-core "Host and frontend reach the core only through the core API":
# code outside msx/ uses the machine only through msx/core_api.py's CoreApi.
_REPO = _MSX_DIR.parent
_HOST_SIDE_DIRS = ("host", "frontend", "tools")
# Bare-Z80 conformance runners: they drive msx.cpu.z80.Z80, not a machine.
_HOST_SIDE_SKIP = ("tools/run_z80_exercisers.py", "tools/run_singlestep_tests.py")
_CORE_COMPONENTS = frozenset({
    "cpu", "vdp", "memory", "fdc", "psg", "scc", "ppi", "rtc", "fmpac", "dac",
    "io", "input", "audio", "kanji",
})
# Names a machine handle goes by on the host side.
_MACHINE_RECEIVERS = frozenset({"machine", "_machine", "m", "core"})

def _host_side_files() -> list[Path]:
    files = [_REPO / "__main__.py"]
    for name in _HOST_SIDE_DIRS:
        files.extend(sorted((_REPO / name).rglob("*.py")))
    return [f for f in files if f.relative_to(_REPO).as_posix() not in _HOST_SIDE_SKIP]


def _is_machine_receiver(node: ast.expr) -> bool:
    """A name or attribute called like a machine handle (machine, self._machine,
    server.machine, m, core), or a call to a function named for the machine
    (e.g. self._get_machine())."""
    if isinstance(node, ast.Name):
        return node.id in _MACHINE_RECEIVERS
    if isinstance(node, ast.Attribute):
        return node.attr in _MACHINE_RECEIVERS
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return "machine" in name
    return False


def _machine_internal_accesses(path: Path) -> list[tuple[str, int, str]]:
    """(enclosing function, line, attribute) for each read of a core component
    or a private field on a machine handle in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Attribute) and _is_machine_receiver(node.value):
            if node.attr in _CORE_COMPONENTS or node.attr.startswith("_"):
                found.append((function, node.lineno, node.attr))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


def test_no_host_code_reads_a_core_component() -> None:
    # machine-core "No host code reads a core component" and "The allowlist
    # names only diagnostics": no host function is excepted from the scan.
    offenders = []
    for path in _host_side_files():
        rel = path.relative_to(_REPO).as_posix()
        for function, lineno, attr in _machine_internal_accesses(path):
            offenders.append(f"{rel}:{lineno}: {function}: .{attr}")
    assert offenders == []


def test_the_trace_commands_read_no_core_component() -> None:
    # machine-core "The allowlist names only the trace commands": te, td, ce
    # and cd use only the core API.
    prompt = _REPO / "host" / "debugger" / "prompt.py"
    trace_commands = {
        "_cmd_trace_enable", "_cmd_trace_disable",
        "_cmd_mapper_trace_enable", "_cmd_mapper_trace_disable",
    }
    tree = ast.parse(prompt.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert trace_commands <= defined
    accesses = [a for a in _machine_internal_accesses(prompt) if a[0] in trace_commands]
    assert accesses == []


def _calls_named(path: Path, name: str) -> list[int]:
    """Line numbers of calls to `name`, as a bare name or an attribute."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(node.lineno)
    return lines


def test_no_host_code_builds_through_the_raising_wrapper() -> None:
    # machine-core "No host code builds through the raising wrapper": the
    # host builds through create_machine, which a native core also offers.
    offenders = []
    for path in _host_side_files():
        rel = path.relative_to(_REPO).as_posix()
        for lineno in _calls_named(path, "build_machine"):
            offenders.append(f"{rel}:{lineno}")
    assert offenders == []
