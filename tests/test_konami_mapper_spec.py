"""KonamiCartridge / KonamiSccCartridge / MajutsushiCartridge obligations
from allium/konami-mapper.allium.

History: this file originally covered only Step 2's ground-truth-confirmed
match facets for KonamiCartridge and KonamiSccCartridge, deliberately
excluding what were then four open `open question` entries (page-select
arithmetic, read mirroring, SCC-enable/bank-select exclusivity) and all of
MajutsushiCartridge (out of that pass's entity-name-filtered scope). All
four open questions were since resolved -- openMSX's actual behaviour was
adopted, msx/mapper.py was updated to match, and the once-excluded facets
are exercised directly in tests/test_mapper.py and
tests/test_konami_scc_mapper.py now (see those files' own coverage, e.g.
test_konami_bank_select_out_of_range_value_masks_then_opens_bus and
test_konami_read_below_window_mirrors_windows_0_and_1). MajutsushiCartridge
was also confirmed identical to KonamiCartridge for bank-select and reads:
openMSX's RomMajutsushi subclasses RomKonami and overrides only reset(),
writeMem() (the DAC intercept) and getWriteCacheLine() -- no readMem/
peekMem override, and RomKonami's own bankSwitch() is not virtual -- so
there is nothing left for a "separate pass" to check. msx/mapper.py's
MajutsushiMapper mirrors this: it only overrides write(), inheriting
KonamiMapper's read() and bank-select arithmetic unchanged.

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
    a legitimate window (0x4000-0xBFFF) -- distinct from what was then the
    mirror-related open question (addresses *outside* that range).
    Untested anywhere: the existing "short ROM" tests
    (test_konami_read_above_window_mirrors_windows_2_and_3 and its
    KonamiSCC twin) exercise addresses above 0xBFFF, a different case.
  - KonamiSccBankSelect / KonamiSccWindow2BankSelect: no existing test
    writes to the *far end* of a 2 KB register zone (only the base address
    is exercised); the @guidance's "2 KB zone, not just the base" claim is
    otherwise only proven for the *non*-register remainder of a window.
  - MajutsushiBankSelect / MajutsushiReadByte / MajutsushiWindowZeroIsFixed:
    the confirmed-identical-to-Konami facts above have no test of their
    own -- tests/test_mapper.py's Majutsushi section covers the DAC
    intercept and inherited bank switching but nothing about the mirror or
    the far-end-of-window register zone, mirroring the gaps this file
    already fills for KonamiCartridge.

Gaps this session fills (the four resolved open questions' own edge
cases -- none of the tests above happen to exercise a value or page count
where the old and new algorithms disagree):
  - KonamiBankSelect / MajutsushiBankSelect: no existing test used a
    written value large enough (>= 32) for the fixed 5-bit mask to
    actually change the outcome -- every prior test's value was already
    below the mask width, so masking was a no-op. Added a case where
    masking reduces the value to a *different, valid* page (not the raw
    value, not open bus).
  - KonamiSccBankSelect: no existing test used a non-power-of-two page
    count, the only case where `pages - 1` differs from a true modulo.
    Added one proving the actual result (bit_and) against what modulo
    would have given, on a 5-page ROM.
  - KonamiSccWindow2BankSelect: the enable-code write's bank-register
    update was only exercised indirectly (via a read whose primary
    purpose is testing something else); added a test naming the
    obligation directly.
  - KonamiSccReadByte: no existing test set scc_mode before probing a
    mirrored address, so the claim that the mirror is pure ROM and never
    SCC-routed -- regardless of scc_mode -- was entirely untested,
    including the strongest case (a mirrored address whose
    effective_address lands exactly on the SCC zone's first byte).
  - MajutsushiReadByte: only the below-window mirror direction had a
    test; added the above-window (0xC000-0xFFFF) counterpart.
"""
from __future__ import annotations

from msx.mapper import KonamiMapper, KonamiSCCMapper, MajutsushiMapper
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
    # unlike the mirror cases above, this address (0x8000) is squarely
    # inside a legitimate window, not a mirrored range.
    m = KonamiMapper(_rom_8k_pages(2))
    assert m.read(0x8000) == 0xFF


def test_konami_bank_select_masks_down_to_a_different_valid_page() -> None:
    # Formerly Open Question 1 (now a Note): the fixed 5-bit mask (31) only
    # changes the outcome once the written value exceeds it. On a 5-page
    # ROM, writing 36 is not less than 5 pages, so it is masked: 36 & 31 ==
    # 4, which *is* less than 5 -- a real, different page, not the raw
    # value and not open bus.
    m = KonamiMapper(_rom_8k_pages(5))
    m.write(0x6000, 36)
    assert m.read(0x6000) == 4


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
# KonamiSccWindow2BankSelect -- register zone is the window's low 2 KB,
# non-enable-code branch
# ---------------------------------------------------------------------------

def test_konami_scc_switch_window2_at_zone_far_end() -> None:
    m = _konami_scc()
    m.write(0x97FF, 6)  # last address of window 2's 2 KB zone; 6 != 0x3F low 6 bits
    assert m.read(0x8000) == 6


def test_konami_scc_enable_code_write_also_updates_window2_bank() -> None:
    # Formerly Open Question 3 (now a Note): openMSX's page-selection check
    # is not gated on the write being the enable code, so 0x3F both enables
    # SCC mode and updates window 2's bank register in the same write --
    # the register is not left as it was.
    m = _konami_scc()
    m.write(0x9000, 0x3F)
    assert m._scc_mode is True
    assert m._banks[2] == 7  # 0x3F (63) is not < 8 pages; 63 & (8 - 1) == 7


def test_konami_scc_bank_select_uses_bit_and_not_modulo() -> None:
    # Formerly Open Question 2 (now a Note): the mask is (pages - 1) via
    # bitwise AND, not a true modulo -- the two only coincide when pages is
    # a power of two. On a 5-page ROM, writing 6 is not less than 5 pages,
    # so it is masked: 6 & (5 - 1) == 6 & 4 == 4, selecting page 4. A true
    # modulo would give 6 % 5 == 1 instead -- a different, real page.
    m = _konami_scc(num_pages=5)
    m.write(0x7000, 6)  # window 1's register
    assert m.read(0x6000) == 4


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


def test_konami_scc_mirror_is_never_scc_routed_even_when_scc_mode_on() -> None:
    # Formerly Open Question 3's territory (now a Note): the SCC-forward
    # guard tests the *raw* trigger address, never the mirrored one. 0x1800
    # mirrors to effective_address 0x9800 -- the very first byte of the
    # SCC's own register zone -- yet the raw address is nowhere near
    # 0x9800-0x9FFF, so this must resolve as a plain ROM mirror read,
    # never routed to the chip, however scc_mode is set.
    rom = _rom_8k_pages_distinct(8)
    m = KonamiSCCMapper(rom=rom, scc=SCC())
    m.write(0x9000, 0x3F)  # enables SCC mode; also sets window 2's bank to 7
    assert m._scc_mode is True
    assert m.read(0x1800) == rom[7 * _PAGE + 0x1800]


# ---------------------------------------------------------------------------
# MajutsushiBankSelect / MajutsushiReadByte -- confirmed identical to
# KonamiCartridge (openMSX's RomMajutsushi overrides nothing but writeMem's
# DAC intercept); same three facets as the KonamiBankSelect/KonamiReadByte
# section above, for the inherited behaviour.
# ---------------------------------------------------------------------------

def test_majutsushi_switch_window1_at_non_base_offset() -> None:
    m = MajutsushiMapper(_rom_8k_pages(8))
    m.write(0x7500, 4)  # anywhere in 0x6000-0x7FFF, not just 0x6000
    assert m.read(0x6000) == 4


def test_majutsushi_window0_ignored_across_its_full_range() -> None:
    m = MajutsushiMapper(_rom_8k_pages(8))
    m.write(0x5FFF, 5)  # last address of window 0's range, not just 0x4000
    # (0x5FFF is below 0x6000, so it is not diverted to the DAC either)
    assert m.read(0x4000) == 0  # still page 0


def test_majutsushi_bank_select_out_of_range_value_opens_bus() -> None:
    # Same fixed 5-bit mask as KonamiBankSelect (inherited write()): 9 is
    # not less than this ROM's 8 pages, masked with 31 stays 9, still not
    # less than 8, so it resolves to open bus rather than aliasing.
    m = MajutsushiMapper(_rom_8k_pages(8))
    m.write(0x6000, 9)
    assert m.read(0x6000) == 0xFF


def test_majutsushi_bank_select_masks_down_to_a_different_valid_page() -> None:
    # Same fixed 5-bit mask as KonamiBankSelect: on a 5-page ROM, writing
    # 36 is not less than 5 pages, so it is masked (36 & 31 == 4), landing
    # on a real, different page rather than open bus.
    m = MajutsushiMapper(_rom_8k_pages(5))
    m.write(0x6000, 36)
    assert m.read(0x6000) == 4


def test_majutsushi_read_below_window_mirrors_windows_0_and_1() -> None:
    # Real hardware mirrors windows 0/1 into 0x0000-0x3FFF here, the same
    # direction as plain KonamiCartridge (RomMajutsushi has no readMem
    # override, so this is literally RomKonami's own read path).
    m = MajutsushiMapper(_rom_8k_pages(8))
    assert m.read(0x0000) == m.read(0x4000)  # window 0's first byte, mirrored
    assert m.read(0x3FFF) == m.read(0x7FFF)  # window 1's last byte, mirrored


def test_majutsushi_read_above_window_mirrors_windows_2_and_3() -> None:
    # And the other direction, 0xC000-0xFFFF mirroring windows 2/3 -- the
    # counterpart to the below-window test above, exercising the other
    # branch of KonamiMapper's inherited mirror.
    m = MajutsushiMapper(_rom_8k_pages(8))
    assert m.read(0xC000) == m.read(0x8000)  # window 2's first byte, mirrored
    assert m.read(0xFFFF) == m.read(0xBFFF)  # window 3's last byte, mirrored


def test_majutsushi_read_open_bus_when_bank_exceeds_short_rom() -> None:
    # A 2-page ROM: window 2's power-on bank is 2, which this ROM does not
    # have, so the window resolves to open bus without any write at all.
    m = MajutsushiMapper(_rom_8k_pages(2))
    assert m.read(0x8000) == 0xFF
