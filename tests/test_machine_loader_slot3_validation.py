"""Regression tests for allium/machine-config-loader.allium's
RejectSubRomSubslotOutOfRange / RejectFdcSubslotOutOfRange /
RejectFlatRamSubslotOutOfRange / RejectRamMapperAndFlatRamBothDeclared rules.

Generated via `/allium:propagate` from those four rules (previously Open
Questions #3 and #5 in that spec, now resolved). `_parse_slot3_msx2` does not
implement any of this validation yet -- these tests are EXPECTED TO FAIL
(red) until that follow-up lands. See the four rules' own @guidance in
allium/machine-config-loader.allium for the full rationale (in particular
RejectRamMapperAndFlatRamBothDeclared's guidance, which states this
not-yet-implemented status explicitly).
"""
from __future__ import annotations

import pytest

from msx.machine_loader import MachineLoadError, _parse_slot3_msx2

# allium/machine-config-loader.allium's config.max_sub_slot_index (= 3):
# derived from the secondary slot register's 2-bit-per-page sub-slot field
# (openspec/specs/msx2-subslot/spec.md's "Secondary slot register field" /
# "Slot 3 sub-slot dispatch" Requirements), not picked by this test.
_OUT_OF_RANGE_SUBSLOT = 4


def _fdc_block() -> dict:
    return {
        "rom": {"file": "disk.rom", "size_kb": 16, "pages": [1]},
        "controller": "wd2793",
        "connection_style": "sony",
    }


# --- RejectSubRomSubslotOutOfRange ----------------------------------------

def test_out_of_range_sub_rom_subslot_rejected() -> None:
    """A `secondary:` key naming a sub-slot beyond the register's 2-bit field
    width (0-3) for the SUB ROM role must be rejected, not silently resolved
    to an unreachable sub_rom_subslot."""
    slot3 = {
        "expanded": True,
        "secondary": {
            _OUT_OF_RANGE_SUBSLOT: {
                "content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}],
            },
        },
    }
    with pytest.raises(MachineLoadError, match="out of range"):
        _parse_slot3_msx2(slot3, "test")


# --- RejectFdcSubslotOutOfRange --------------------------------------------

def test_out_of_range_fdc_subslot_rejected() -> None:
    """Same as above, for a sub-slot declaring only an `fdc:` block."""
    slot3 = {
        "expanded": True,
        "secondary": {
            _OUT_OF_RANGE_SUBSLOT: {"fdc": _fdc_block()},
        },
    }
    with pytest.raises(MachineLoadError, match="out of range"):
        _parse_slot3_msx2(slot3, "test")


# --- RejectFlatRamSubslotOutOfRange -----------------------------------------

def test_out_of_range_flat_ram_subslot_rejected() -> None:
    """Same as above, for a sub-slot declaring flat (non-mapper) RAM."""
    slot3 = {
        "expanded": True,
        "secondary": {
            _OUT_OF_RANGE_SUBSLOT: {"type": "ram", "size_kb": 64},
        },
    }
    with pytest.raises(MachineLoadError, match="out of range"):
        _parse_slot3_msx2(slot3, "test")


def test_in_range_subslot_3_still_accepted() -> None:
    """Boundary check: sub-slot 3 (the largest value the 2-bit field can
    produce) is valid and must NOT be rejected -- only indices beyond it.
    This one already passes today; it pins the upper boundary so a future
    off-by-one in the range-check implementation is caught."""
    slot3 = {
        "expanded": True,
        "secondary": {
            3: {"content": [{"rom": {"file": "sub.rom", "size_kb": 16, "pages": [0]}}]},
        },
    }
    result = _parse_slot3_msx2(slot3, "test")
    assert result.sub_rom is not None
    assert result.sub_rom_subslot == 3


# --- RejectRamMapperAndFlatRamBothDeclared ---------------------------------

def test_ram_mapper_and_flat_ram_both_declared_rejected() -> None:
    """A slot 3 declaring `mapper: standard` in one sub-slot and `type: ram`
    (flat, non-mapper) in another names both mutually-exclusive MSX2 RAM
    strategies at once -- allium/slots.allium's SlotThreeStrategyIsExclusive
    invariant on Memory. Must be rejected at parse time rather than silently
    resolved by BuildMsx2Machine's mapper-wins precedence."""
    slot3 = {
        "expanded": True,
        "secondary": {
            2: {"mapper": "standard", "type": "ram", "size_kb": 128},
            3: {"type": "ram", "size_kb": 64},
        },
    }
    with pytest.raises(MachineLoadError, match="mapper"):
        _parse_slot3_msx2(slot3, "test")
