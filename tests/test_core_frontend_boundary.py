"""The core package (msx/) must not depend on SDL2 or the frontend package."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_MSX_DIR = Path(__file__).resolve().parent.parent / "msx"
_FORBIDDEN_ROOTS = ("sdl2", "frontend")

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
