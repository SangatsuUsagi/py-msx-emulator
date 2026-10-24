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


# Debug logging writes its own log file; it stays out of scope until a
# DebugControl contract exists (allium/core-frontend.allium open question).
_IO_EXEMPT_DIR = _MSX_DIR / "diagnostics"
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
    # machine-core "No file I/O or host clock in the core".
    offenders = []
    for path in sorted(_MSX_DIR.rglob("*.py")):
        if _IO_EXEMPT_DIR in path.parents:
            continue
        rel = path.relative_to(_MSX_DIR.parent)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _file_calls(tree):
            offenders.append(f"{rel}:{lineno}: {name}()")
        for root in _imported_roots(path):
            if root in _CLOCK_ROOTS:
                offenders.append(f"{rel}: import {root}")
    assert offenders == []
