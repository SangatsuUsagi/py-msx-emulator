"""Regression guard for msx/memory.py's Memory class.

Memory has no __setattr__ hook (removed by the memory-explicit-setters
OpenSpec change): writing directly to one of its 8 page-routing-affecting
fields (slot_register, sub_slot_reg, ram_mapper, sub0_rom, fdc,
flat_ram_subslot, _mapper, _mapper2) from outside Memory's own class body
no longer raises or invalidates anything -- it just silently leaves the
page-routing cache stale. 5 of the 8 have a matching set_*() method
(set_slot_register, set_ram_mapper, ...) callers must use instead; the
remaining 3 (flat_ram_subslot, _mapper, _mapper2) have no setter at all --
they're only ever set once, as Memory(...) constructor kwargs, so any
assignment to them outside __init__ is itself the bug, not just a missed
setter call.

This is a static, regex-based scan (not an AST-based one -- see
openspec/changes/archive/*-memory-explicit-setters/design.md's Decision 3),
scoped to the same directories that change's proposal covers. It
deliberately only flags assignments whose base expression looks like a
Memory instance (`mem`, `memory`, or anything ending in `.memory`) -- the 3
field names that collide with other classes (`fdc` on Machine, and
`_mapper`/`_mapper2` on the test-only `_ReferenceMemory` oracle in
tests/test_memory_dispatch_cache.py) are real, legitimate, unrelated fields
on those other classes, not a Memory instance, and must not be flagged.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("msx", "tests", "frontend", "tools")
_MEMORY_FIELDS = (
    "slot_register",
    "sub_slot_reg",
    "ram_mapper",
    "sub0_rom",
    "fdc",
    "flat_ram_subslot",
    "_mapper",
    "_mapper2",
)
# Base expressions that plausibly hold a Memory instance, per this
# codebase's naming convention (confirmed by grep across the whole repo
# when this test was written: Memory instances are always bound to `mem`,
# `memory`, or reached via `<something>.memory` -- never a bare `m`/`self`
# the way Machine/other classes coincidentally sharing a field name are).
_BASE_PATTERN = r"(?:\bmem|\bmemory|\w+\.memory)"
_ASSIGN_PATTERN = re.compile(
    r"^\s*" + _BASE_PATTERN + r"\.(" + "|".join(_MEMORY_FIELDS) + r")\s*=(?!=)"
)
_EXEMPT_FILES = {
    # Memory's own class body; its setters legitimately do
    # `self.slot_register = value` etc. internally.
    _REPO_ROOT / "msx" / "memory.py",
    # Every `mem`/`m.memory` here is a MagicMock() test double, never a
    # real Memory instance (verified: zero `Memory(` construction anywhere
    # in this file) -- these assignments never went through Memory's
    # __setattr__/setters in the first place, and calling a real setter on
    # a MagicMock would be semantically wrong (it wouldn't leave a
    # `.slot_register` attribute readable the way plain assignment does).
    _REPO_ROOT / "tests" / "test_debugger_prompt.py",
}


def _offending_lines() -> list[str]:
    offenses = []
    for scan_dir in _SCAN_DIRS:
        for path in (_REPO_ROOT / scan_dir).rglob("*.py"):
            if path in _EXEMPT_FILES:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _ASSIGN_PATTERN.match(line):
                    rel = path.relative_to(_REPO_ROOT)
                    offenses.append(f"{rel}:{lineno}: {line.strip()}")
    return offenses


def test_no_direct_assignment_to_memory_cache_invalidating_fields() -> None:
    offenses = _offending_lines()
    assert not offenses, (
        "Direct assignment to a Memory page-routing field bypasses cache "
        "invalidation (Memory has no __setattr__ hook to catch this "
        "anymore) -- use the matching set_*() method instead (slot_register, "
        "sub_slot_reg, ram_mapper, sub0_rom, fdc), or, for flat_ram_subslot/"
        "_mapper/_mapper2 (no setter exists), pass it as a Memory(...) "
        "constructor kwarg instead of reassigning post-construction:\n"
        + "\n".join(offenses)
    )
