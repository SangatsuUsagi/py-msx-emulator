"""KonamiCartridge / KonamiSccCartridge obligations from allium/konami-mapper.allium.

Scope: only the rule facets that Step 2's ground-truth cross-check
(msx-wiki / bifi / references/openmsx) confirmed as Verdict: match. This
file does NOT test anything tied to the four `open question` entries in
allium/konami-mapper.allium (and allium/scc.allium's matching fifth one) --
those record confirmed spec/code-vs-hardware divergences still awaiting a
code decision, and a test asserting on them would lock in behaviour that
may change:
  - KonamiBankSelect's / KonamiSccBankSelect's / KonamiSccWindow2BankSelect's
    page-select arithmetic (power-of-two mask vs modulo vs openMSX's actual
    two-tier direct-then-mask algorithm) for a ROM whose page count is not a
    power of two, or for a written value only meaningful under one specific
    interpretation of that arithmetic.
  - KonamiReadByte's / KonamiSccReadByte's open-bus fallback for any address
    outside 0x4000-0xBFFF (openMSX/real hardware mirrors windows 0/1 and 2/3
    there instead; msx/mapper.py does not implement the mirror).
  - KonamiSccWindow2BankSelect's assumption that writing the SCC-enable code
    (0x3F) leaves window 2's bank register untouched (openMSX's writeMem
    does not gate the page-select check on this being a non-enable value).

MajutsushiCartridge (MajutsushiBankSelect, MajutsushiReadByte,
MajutsushiWindowZeroIsFixed, MajutsushiBanksAreOnePerWindow) is excluded
for a different reason: Step 2's cross-check batches were scoped by entity
name ("contains Konami, not Scc" / "contains KonamiScc") and Majutsushi
matched neither filter, so it was never assigned a Verdict at all -- not
confirmed match, not an open question. Excluded here pending its own pass.

Rule-by-rule coverage map (built by reading tests/test_mapper.py's Konami
section and tests/test_konami_scc_mapper.py in full before writing
anything here):

Already covered, no new test added for these facets:
  - KonamiBankSelect: initial banks [0,1,2,3] -- test_mapper.py::
    test_konami_initial_banks. Switching windows 1-3 via each register's
    base address -- test_konami_switch_window_1/2/3. Window 0 ignoring a
    write at its base address -- test_konami_window_0_is_fixed.
  - KonamiReadByte: reading back a just-switched window --
    test_konami_switch_window_1/2/3 (implicitly, via read-after-write).
  - KonamiSccBankSelect: initial banks [0,1,2,3], all four switchable --
    test_konami_scc_mapper.py::test_initial_bank_state,
    test_bank_switch_window0 (proves window 0 is switchable here, unlike
    plain Konami). Switching windows 0/1/2/3 via each register's base
    address -- test_bank_switch_window0/1/2/3. Writes outside a window's
    2 KB zone (but inside the window) not switching it --
    test_window0_write_outside_register_ignored,
    test_writes_outside_register_zones_ignored.
  - KonamiSccWindow2BankSelect: a non-enable value written to window 2's
    zone updating its bank -- test_bank_switch_window2 (value 5).
  - KonamiSccReadByte: SCC-mode-on/off routing at and around 0x9800-0x9FFF
    -- test_scc_read_routed_when_scc_mode,
    test_rom_read_when_scc_mode_false, and the four
    test_scc_*_forward_only_applies_within_9800_9fff tests (this facet
    belongs to allium/scc.allium's ForwardReadToScc/ForwardWriteToScc, not
    to this file, but the ROM-fallback branch KonamiSccReadByte owns is
    exercised by the same tests).
  - KonamiWindowZeroIsFixed -- test_konami_window_0_is_fixed (base address
    only; this file adds the full-range case).
  - KonamiBanksAreOnePerWindow -- true by construction (banks is a
    4-element list literal in both entities' __post_init__); not
    independently meaningful to unit-test on its own.

Gaps this file fills:
  - KonamiBankSelect's register zone is documented as the window's *entire*
    address range, not just the base address (its @guidance: "any write
    anywhere in 0x6000-0x7FFF switches window 1..."), and window 0's
    ignore is documented as covering all of 0x4000-0x5FFF -- no existing
    test writes to a non-base address in either case.
  - KonamiBankSelect / KonamiSccBankSelect: no existing test confirms that
    switching one window leaves the other three untouched (the entity's
    `with_bank` semantics: only the addressed window's slot changes).
  - KonamiReadByte / KonamiSccReadByte: every existing ROM builder only
    gives page 0 of each page a distinguishing byte (the rest is 0), so no
    existing test can tell "correct offset within a page" apart from "any
    offset, coincidentally reading 0". This file adds a ROM where every
    byte is distinct.
  - KonamiReadByte / KonamiSccReadByte: open bus for a bank register that
    points past the actual (short) ROM's content, while still resolving to
    a legitimate window (0x4000-0xBFFF) -- distinct from the mirror-related
    open question, which is only about addresses *outside* that range.
    Untested anywhere: the existing "short ROM" tests
    (test_konami_read_above_window_falls_back_to_bank_arithmetic and its
    KonamiSCC twin) exercise addresses above 0xBFFF, i.e. the open-question
    territory, not this.
  - KonamiSccBankSelect / KonamiSccWindow2BankSelect: no existing test
    writes to the *far end* of a 2 KB register zone (only the base address
    is exercised); the @guidance's "2 KB zone, not just the base" claim is
    otherwise only proven for the *non*-register remainder of a window.
"""
from __future__ import annotations

from msx.mapper import KonamiMapper, KonamiSCCMapper
from msx.scc import SCC

_PAGE = 8192


def _rom_8k_pages(n: int) -> bytes:
    """ROM of n 8 KB pages where page P's first byte is P, rest 0."""
    return bytes([(p if i == 0 else 0) for p in range(n) for i in range(_PAGE)])


def _rom_8k_pages_distinct(n: int) -> bytes:
    """ROM of n 8 KB pages where every byte is distinguishable from every
    other offset within its own page, so an offset-computation bug can't
    hide behind an all-zero page body (unlike _rom_8k_pages above)."""
    return bytes([(offset & 0xFF) ^ (offset >> 8) for _ in range(n) for offset in range(_PAGE)])


# ---------------------------------------------------------------------------
# KonamiBankSelect -- register zone is the window's entire address range
# ---------------------------------------------------------------------------

def test_konami_switch_window1_at_non_base_offset() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    m.write(0x7500, 4)  # anywhere in 0x6000-0x7FFF, not just 0x6000
    assert m.read(0x6000) == 4


def test_konami_switch_window2_at_non_base_offset() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    m.write(0x9FFF, 6)  # last address of window 2's range
    assert m.read(0x8000) == 6


def test_konami_switch_window3_at_non_base_offset() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    m.write(0xBFFF, 7)  # last address of window 3's range
    assert m.read(0xA000) == 7


def test_konami_window0_ignored_across_its_full_range() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    m.write(0x5FFF, 5)  # last address of window 0's range, not just 0x4000
    assert m.read(0x4000) == 0  # still page 0


def test_konami_switching_one_window_leaves_others_unchanged() -> None:
    m = KonamiMapper(_rom_8k_pages(8))
    m.write(0x6000, 4)  # switch window 1 only
    assert m.read(0x8000) == 2  # window 2 still its initial page
    assert m.read(0xA000) == 3  # window 3 still its initial page


# ---------------------------------------------------------------------------
# KonamiReadByte -- offset-within-window arithmetic, and open bus for a
# bank pointing past a short ROM's actual content
# ---------------------------------------------------------------------------

def test_konami_read_offset_within_window_is_correct() -> None:
    rom = _rom_8k_pages_distinct(4)
    m = KonamiMapper(rom)
    # Window 1 (0x6000-0x7FFF) starts at page 1: check three distinct
    # offsets, not just the page's first byte.
    assert m.read(0x6000) == rom[1 * _PAGE + 0]
    assert m.read(0x6500) == rom[1 * _PAGE + 0x500]
    assert m.read(0x7FFF) == rom[1 * _PAGE + 0x1FFF]


def test_konami_read_open_bus_when_bank_exceeds_short_rom() -> None:
    # A 2-page ROM: window 2's power-on bank is 2, which this ROM does not
    # have, so the window resolves to open bus without any write at all --
    # unlike the open-question mirror cases, this address (0x8000) is
    # squarely inside a legitimate window.
    m = KonamiMapper(_rom_8k_pages(2))
    assert m.read(0x8000) == 0xFF


# ---------------------------------------------------------------------------
# KonamiSccBankSelect -- register zone is the window's low 2 KB, not just
# its base address
# ---------------------------------------------------------------------------

def _konami_scc(num_pages: int = 8) -> KonamiSCCMapper:
    return KonamiSCCMapper(rom=_rom_8k_pages(num_pages), scc=SCC())


def test_konami_scc_switch_window0_at_zone_far_end() -> None:
    m = _konami_scc()
    m.write(0x57FF, 5)  # last address of window 0's 2 KB zone
    assert m.read(0x4000) == 5


def test_konami_scc_switch_window1_at_zone_far_end() -> None:
    m = _konami_scc()
    m.write(0x77FF, 4)  # last address of window 1's 2 KB zone
    assert m.read(0x6000) == 4


def test_konami_scc_switch_window3_at_zone_far_end() -> None:
    m = _konami_scc()
    m.write(0xB7FF, 6)  # last address of window 3's 2 KB zone
    assert m.read(0xA000) == 6


def test_konami_scc_switching_one_window_leaves_others_unchanged() -> None:
    m = _konami_scc()
    m.write(0x5000, 5)  # switch window 0 only
    assert m.read(0x6000) == 1  # window 1 still its initial page
    assert m.read(0x8000) == 2  # window 2 still its initial page
    assert m.read(0xA000) == 3  # window 3 still its initial page


# ---------------------------------------------------------------------------
# KonamiSccWindow2BankSelect -- register zone is the window's low 2 KB
# (non-enable-code branch only; the enable-code branch is the open
# question and is deliberately not tested here)
# ---------------------------------------------------------------------------

def test_konami_scc_switch_window2_at_zone_far_end() -> None:
    m = _konami_scc()
    m.write(0x97FF, 6)  # last address of window 2's 2 KB zone; 6 != 0x3F low 6 bits
    assert m.read(0x8000) == 6


# ---------------------------------------------------------------------------
# KonamiSccReadByte -- offset-within-window arithmetic, and open bus for a
# bank pointing past a short ROM's actual content
# ---------------------------------------------------------------------------

def test_konami_scc_read_offset_within_window_is_correct() -> None:
    rom = _rom_8k_pages_distinct(4)
    m = KonamiSCCMapper(rom=rom, scc=SCC())
    # Window 2 (0x8000-0x9FFF, below the SCC's own 0x9800 sub-range) starts
    # at page 2: check three distinct offsets, not just the page's first byte.
    assert m.read(0x8000) == rom[2 * _PAGE + 0]
    assert m.read(0x8500) == rom[2 * _PAGE + 0x500]
    assert m.read(0x97FF) == rom[2 * _PAGE + 0x17FF]


def test_konami_scc_read_open_bus_when_bank_exceeds_short_rom() -> None:
    # A 2-page ROM: window 3's power-on bank is 3, which this ROM does not
    # have, so the window resolves to open bus without any write at all.
    m = KonamiSCCMapper(rom=_rom_8k_pages(2), scc=SCC())
    assert m.read(0xA000) == 0xFF
