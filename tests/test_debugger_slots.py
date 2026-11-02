"""host/debugger/slots.py: the sl/st text from the core API's slot
description record (flatten-slot-info design "D2", one test per row)."""
from __future__ import annotations

import pytest

from host.debugger.slots import MAPPER_DISPLAY_NAMES, slot_bank_text, slot_content_text
from msx.boundary import (
    BANK_NONE,
    BANK_SEGMENT,
    BANK_SRAM_PAGE,
    BANK_WINDOW_16K,
    BANK_WINDOWS_8K,
    SLOT_CONTENT_CARTRIDGE,
    SLOT_CONTENT_CARTRIDGE_EMPTY,
    SLOT_CONTENT_EMPTY,
    SLOT_CONTENT_FDC,
    SLOT_CONTENT_MAIN_ROM,
    SLOT_CONTENT_RAM,
    SLOT_CONTENT_RAM_MAPPER,
    SLOT_CONTENT_SUB_ROM,
    WINDOW_NONE,
    WINDOW_OPEN,
    WINDOW_PAGE,
    WINDOW_SRAM,
    MapperId,
    SlotInfo,
)

_NAMES = ("main.rom", "sub.rom")


def _content(content: int, mapper: int = -1, names: tuple[str, str] = _NAMES) -> str:
    return slot_content_text(SlotInfo(content, mapper, 0), names)


def _bank(bank: int, kinds: tuple[int, int], values: tuple[int, int], page: int,
          subs: tuple[int, int] | None = None, offsets: tuple[int, int] = (0, 0)) -> str:
    info = SlotInfo(
        SLOT_CONTENT_CARTRIDGE, MapperId.HALNOTE, 0, bank, kinds, values,
        2 if subs is not None else 0, subs or (0, 0), offsets,
    )
    return slot_bank_text(info, page)


def test_empty() -> None:
    assert _content(SLOT_CONTENT_EMPTY) == "empty"


def test_main_and_sub_rom_with_and_without_a_name() -> None:
    assert _content(SLOT_CONTENT_MAIN_ROM) == "ROM main.rom"
    assert _content(SLOT_CONTENT_SUB_ROM) == "ROM sub.rom"
    assert _content(SLOT_CONTENT_MAIN_ROM, names=("", "")) == "ROM"
    assert _content(SLOT_CONTENT_SUB_ROM, names=("", "")) == "ROM"


@pytest.mark.parametrize("mapper", list(MapperId))
def test_cartridge_names_its_mapper(mapper: MapperId) -> None:
    assert _content(SLOT_CONTENT_CARTRIDGE, int(mapper)) == (
        f"Cartridge {MAPPER_DISPLAY_NAMES[mapper]}")


def test_every_mapper_has_a_display_name() -> None:
    assert sorted(MAPPER_DISPLAY_NAMES) == list(MapperId)


def test_cartridge_empty() -> None:
    assert _content(SLOT_CONTENT_CARTRIDGE_EMPTY) == "Cartridge (empty)"


def test_ram_ram_mapper_and_fdc() -> None:
    assert _content(SLOT_CONTENT_RAM) == "RAM"
    assert _content(SLOT_CONTENT_RAM_MAPPER) == "RAM (mapper:standard)"
    assert _content(SLOT_CONTENT_FDC) == "FDC"


def test_no_bank() -> None:
    assert _bank(BANK_NONE, (WINDOW_NONE, WINDOW_NONE), (0, 0), 1) == "-"


def test_segment() -> None:
    assert _bank(BANK_SEGMENT, (WINDOW_PAGE, WINDOW_NONE), (2, 0), 3) == "seg=2"


def test_sram_page() -> None:
    assert _bank(BANK_SRAM_PAGE, (WINDOW_NONE, WINDOW_NONE), (0, 0), 0) == "SRAM@0000-3FFF"


def test_one_16k_window() -> None:
    assert _bank(BANK_WINDOW_16K, (WINDOW_PAGE, WINDOW_NONE), (23, 0), 1) == (
        "bank 23 @5C000-5FFFF")
    assert _bank(BANK_WINDOW_16K, (WINDOW_SRAM, WINDOW_NONE), (0, 0), 1) == "bank SRAM@0000"
    assert _bank(BANK_WINDOW_16K, (WINDOW_OPEN, WINDOW_NONE), (0, 0), 2) == "bank open"


def test_two_8k_windows_are_numbered_by_the_page() -> None:
    assert _bank(BANK_WINDOWS_8K, (WINDOW_PAGE, WINDOW_PAGE), (1, 2), 1) == (
        "w0=b1@02000  w1=b2@04000")
    assert _bank(BANK_WINDOWS_8K, (WINDOW_PAGE, WINDOW_SRAM), (2, 0x1000), 2) == (
        "w2=b2@04000  w3=SRAM@1000")
    assert _bank(BANK_WINDOWS_8K, (WINDOW_OPEN, WINDOW_PAGE), (0, 3), 2) == (
        "w2=open  w3=b3@06000")


def test_sub_banks_follow_the_windows() -> None:
    text = _bank(BANK_WINDOWS_8K, (WINDOW_PAGE, WINDOW_PAGE), (5, 6), 1,
                 subs=(0x12, 0x34), offsets=(0x89000, 0x9A000))
    assert text == "w0=b5@0A000  w1=b6@0C000  sub=b18@89000,b52@9A000"


def test_an_unknown_mapper_id_has_a_fallback_text() -> None:
    assert slot_content_text(SlotInfo(SLOT_CONTENT_CARTRIDGE, 999, 0), ("", "")) == (
        "Cartridge mapper 999"
    )


def test_an_unknown_slot_content_shows_its_number() -> None:
    # machine-c-abi "An unknown slot content".
    assert _content(99) == "content 99"


def test_an_unknown_bank_kind_shows_no_bank() -> None:
    assert _bank(99, (WINDOW_NONE, WINDOW_NONE), (0, 0), 1) == "-"


def test_an_unknown_window_kind_shows_its_number() -> None:
    # machine-c-abi "An unknown window kind".
    assert _bank(BANK_WINDOW_16K, (99, WINDOW_NONE), (3, 0), 1) == "bank kind 99"
    assert _bank(BANK_WINDOWS_8K, (99, WINDOW_PAGE), (3, 4), 1) == "w0=kind 99  w1=b4@08000"
