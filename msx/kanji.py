"""JIS Kanji font ROM device (Sony HBI-J1's `hbi-j1_kanjifont.rom`), ports
0xD8-0xDB.

Address/counter/level layout confirmed against openMSX's `MSXKanji`
(references/openMSX/src/MSXKanji.cc) and cross-checked against
`references/docs/Kanji display - MSX Wiki.md`: a 32-byte glyph is addressed
by a 6-bit column, a 6-bit row, and a level bit (JIS1/JIS2), with a 5-bit
read counter auto-incrementing on each data read and wrapping back to byte 0
of the same glyph -- not advancing to the next glyph -- every 32 reads. Any
row/column register write resets the counter. Hangul-variant Kanji ROMs (a
7-bit row address) are out of scope; HBI-J1 is JIS-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_ROW_SHIFT = 11
_COLUMN_SHIFT = 5
_ROW_MASK = 0x3F << _ROW_SHIFT
_COLUMN_MASK = 0x3F << _COLUMN_SHIFT
_COUNTER_MASK = 0x1F
_LEVEL_SHIFT = 17

_ROM_SIZE_LEVEL1_ONLY = 131072  # 128 KB
_ROM_SIZE_LEVEL1_AND_2 = 262144  # 256 KB


@dataclass
class KanjiRom:
    """JIS Kanji font ROM: ports base+0..base+3 (base 0xD8), decoded by the
    low 2 bits: bit 1 selects JIS level (0 = level 1, 1 = level 2), bit 0
    selects the written register (0 = column, 1 = row)."""

    rom: bytes
    _adr: int = field(default=0, init=False, repr=False)
    _rom_len: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.rom) not in (_ROM_SIZE_LEVEL1_ONLY, _ROM_SIZE_LEVEL1_AND_2):
            raise ValueError(
                f"KanjiRom: expected a {_ROM_SIZE_LEVEL1_ONLY}- or "
                f"{_ROM_SIZE_LEVEL1_AND_2}-byte ROM, got {len(self.rom)}"
            )
        self._rom_len = len(self.rom)

    def reset(self) -> None:
        self._adr = 0

    def write_port(self, port: int, value: int) -> None:
        register = port & 3
        if (register & 1) == 0:
            # Column write: clears the read counter and the column field,
            # sets the column from `value`, preserves the row field.
            self._adr = (self._adr & _ROW_MASK) | ((value & 0x3F) << _COLUMN_SHIFT)
        else:
            # Row write: clears the read counter and the row field, sets the
            # row from `value`, preserves the column field.
            self._adr = (self._adr & _COLUMN_MASK) | ((value & 0x3F) << _ROW_SHIFT)

    def read_port(self, port: int) -> int:
        register = port & 3
        read_level = 1 if (register & 2) else 0
        address = self._adr | (read_level << _LEVEL_SHIFT)
        value = self.rom[address] if address < self._rom_len else 0xFF
        self._adr = (self._adr & ~_COUNTER_MASK) | ((self._adr + 1) & _COUNTER_MASK)
        return value
