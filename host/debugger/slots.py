"""The text of the debugger's `sl` and `st` slot views, built from the core
API's slot description record (msx/boundary.py SlotInfo, integers only)."""
from __future__ import annotations

from msx.boundary import (
    BANK_SEGMENT,
    BANK_SRAM_PAGE,
    BANK_WINDOW_16K,
    BANK_WINDOWS_8K,
    SLOT_CONTENT_CARTRIDGE,
    SLOT_CONTENT_CARTRIDGE_EMPTY,
    SLOT_CONTENT_FDC,
    SLOT_CONTENT_MAIN_ROM,
    SLOT_CONTENT_RAM,
    SLOT_CONTENT_RAM_MAPPER,
    SLOT_CONTENT_SUB_ROM,
    WINDOW_OPEN,
    WINDOW_SRAM,
    MapperId,
    SlotInfo,
)

# The `Cartridge <name>` text of each mapper.
MAPPER_DISPLAY_NAMES: dict[int, str] = {
    MapperId.FLAT: "Flat",
    MapperId.FIXED_PAGE: "FixedPage",
    MapperId.ASCII8: "Ascii8",
    MapperId.ASCII16: "Ascii16",
    MapperId.ASCII8_SRAM2: "Ascii8Sram2",
    MapperId.ASCII8_SRAM8: "Ascii8Sram8",
    MapperId.KOEI_SRAM32: "KoeiSRAM32",
    MapperId.GAME_MASTER2: "GameMaster2",
    MapperId.ASCII16_SRAM2: "Ascii16Sram2",
    MapperId.ASCII16_SRAM8: "Ascii16Sram8",
    MapperId.R_TYPE: "RType",
    MapperId.KONAMI: "Konami",
    MapperId.MAJUTSUSHI: "Majutsushi",
    MapperId.KONAMI_SCC: "KonamiSCC",
    MapperId.SCC_I_CART: "SCCICart",
    MapperId.HALNOTE: "Halnote",
    MapperId.FMPAC: "FmPac",
    MapperId.RAM_MAPPER: "RamMapper",
}

_FIXED_CONTENT: dict[int, str] = {
    SLOT_CONTENT_CARTRIDGE_EMPTY: "Cartridge (empty)",
    SLOT_CONTENT_RAM: "RAM",
    SLOT_CONTENT_RAM_MAPPER: "RAM (mapper:standard)",
    SLOT_CONTENT_FDC: "FDC",
}


def _rom_text(name: str) -> str:
    """`ROM <name>`, or the bare `ROM` for a ROM with no name."""
    return f"ROM {name}" if name else "ROM"


def slot_content_text(info: SlotInfo, rom_names: tuple[str, str]) -> str:
    """The content column of a slot: `rom_names` are the main and sub ROM
    file names the host loaded ("" for none)."""
    content = info.content
    if content == SLOT_CONTENT_MAIN_ROM:
        return _rom_text(rom_names[0])
    if content == SLOT_CONTENT_SUB_ROM:
        return _rom_text(rom_names[1])
    if content == SLOT_CONTENT_CARTRIDGE:
        return f"Cartridge {MAPPER_DISPLAY_NAMES[info.mapper]}"
    return _FIXED_CONTENT.get(content, "empty")


def _window_16k(kind: int, value: int) -> str:
    if kind == WINDOW_SRAM:
        return f"bank SRAM@{value:04X}"
    if kind == WINDOW_OPEN:
        return "bank open"
    start = value * 0x4000
    return f"bank {value} @{start:05X}-{start + 0x3FFF:05X}"


def _window_8k(number: int, kind: int, value: int) -> str:
    if kind == WINDOW_SRAM:
        return f"w{number}=SRAM@{value:04X}"
    if kind == WINDOW_OPEN:
        return f"w{number}=open"
    return f"w{number}=b{value}@{value * 0x2000:05X}"


def slot_bank_text(info: SlotInfo, page: int) -> str:
    """The bank column of a slot at CPU page `page` (0-3), `-` for none.
    Two 8 KB windows are numbered by the page: w0/w1 at page 1, w2/w3 at
    page 2."""
    bank = info.bank
    kinds = info.window_kinds
    values = info.window_values
    if bank == BANK_SEGMENT:
        return f"seg={values[0]}"
    if bank == BANK_SRAM_PAGE:
        return "SRAM@0000-3FFF"
    if bank == BANK_WINDOW_16K:
        text = _window_16k(kinds[0], values[0])
    elif bank == BANK_WINDOWS_8K:
        first = (page - 1) * 2
        text = (f"{_window_8k(first, kinds[0], values[0])}  "
                f"{_window_8k(first + 1, kinds[1], values[1])}")
    else:
        return "-"
    if info.sub_bank_count:
        parts = []
        for n in range(info.sub_bank_count):
            parts.append(f"b{info.sub_banks[n]}@{info.sub_offsets[n]:05X}")
        text = f"{text}  sub={','.join(parts)}"
    return text
