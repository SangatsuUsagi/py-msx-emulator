"""MSX2 system RAM mapper (configurable, whole 16 KB banks).

Ports 0xFC–0xFF select which 16 KB bank is visible in each of the four
16 KB pages of the Z80 address space when slot 3 is active.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, TypedDict, cast

from msx.mapper import MapperKind

_BANK_SIZE = 0x4000  # 16 KB
_DEFAULT_SIZE_KB = 128  # 8 banks, matching every machine before size became configurable


class RamMapperState(TypedDict):
    """Save-state schema for RamMapper.snapshot()/restore()."""

    banks: list[int]
    ram: bytes


@dataclass
class RamMapper:
    """Banked RAM for MSX2, sized in whole 16 KB banks.

    Attributes:
        size_kb: Configured buffer size in KB. Must be a positive multiple of
            16; defaults to 128 KB (8 banks), matching every machine
            configured before this became configurable.
        ram: Backing store, `size_kb * 1024` bytes.
        banks: Four bank-register values (one per 16 KB page). Power-on/reset
            value is [0, 0, 0, 0] -- all pages start aliased to bank 0
            (openMSX MSXMemoryMapperBase::reset(): "Most mappers initialize
            to segment 0 for all pages"). It is the MSX2 BIOS boot routine,
            not the hardware, that subsequently writes [3, 2, 1, 0] (top of
            physical RAM in the lowest logical page) via ports 0xFC-0xFF.
        bank_mask: Mask applied to bank register values, derived from the
            configured bank count (`bit_ceil(num_banks) - 1`, matching
            openMSX's `MSXMemoryMapperBase` segment masking) rather than a
            bit width fixed at 3 bits.
    """

    kind: ClassVar[MapperKind] = MapperKind.RAM_MAPPER
    # Persisted entirely through the generic slot-2 save-state mechanism
    # (snapshot()/restore()), never through a standalone .sram file.
    has_sram: ClassVar[bool] = False
    size_kb: int = _DEFAULT_SIZE_KB
    ram: bytearray = field(init=False, repr=False)
    banks: list[int] = field(init=False, default_factory=lambda: [0, 0, 0, 0])
    bank_mask: int = field(init=False, default=0)
    # ~bank_mask & 0xFF, precomputed for read_port (constant for the instance's
    # lifetime; avoids recomputing the inverse mask on every port read).
    _read_port_or_mask: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.size_kb <= 0 or self.size_kb % 16 != 0:
            raise ValueError(
                f"RamMapper size must be a positive multiple of 16 KB, got {self.size_kb}"
            )
        num_banks = self.size_kb // 16
        self.ram = bytearray(num_banks * _BANK_SIZE)
        # Smallest mask covering num_banks: bit_ceil(num_banks) - 1 (see class
        # docstring's bank_mask entry).
        self.bank_mask = (1 << (num_banks - 1).bit_length()) - 1
        self._read_port_or_mask = ~self.bank_mask & 0xFF

    def reset(self) -> None:
        """Restore power-on/reset bank state. RAM contents are retained,
        matching Machine.reset()'s "Memory/VRAM contents are retained"."""
        self.banks[:] = [0, 0, 0, 0]

    def save_sram(self, path: Path) -> None:
        """No-op: RamMapper has no separate .sram file, see has_sram."""
        pass

    def _phys(self, addr: int) -> int:
        page = (addr & 0xFFFF) >> 14
        bank = self.banks[page] & self.bank_mask
        return bank * _BANK_SIZE + (addr & 0x3FFF)

    def read(self, addr: int) -> int:
        return self.ram[self._phys(addr)]

    def write(self, addr: int, value: int) -> None:
        self.ram[self._phys(addr)] = value & 0xFF

    def read_port(self, port: int) -> int:
        """Return bank register for the page corresponding to port 0xFC–0xFF.

        Only the bits addressing the configured bank count are meaningful;
        the unused high bits read back as 1, not 0 (openMSX
        MSXMemoryMapperBase::peekIO, `registers[page] | ~bankMask` — real
        hardware sets them, it doesn't clear them).
        """
        return (self.banks[(port - 0xFC) & 0x03] & self.bank_mask) | self._read_port_or_mask

    def write_port(self, port: int, value: int) -> None:
        """Set bank register for the page corresponding to port 0xFC–0xFF."""
        self.banks[(port - 0xFC) & 0x03] = value & self.bank_mask

    def snapshot(self) -> RamMapperState:
        return {"banks": list(self.banks), "ram": bytes(self.ram)}

    def restore(self, state: dict[str, object]) -> None:
        typed_state = cast(RamMapperState, state)
        banks = typed_state["banks"]
        if len(banks) != 4 or any(b < 0 for b in banks):
            raise ValueError("RamMapperState.banks must have 4 non-negative entries")
        ram = typed_state["ram"]
        if len(ram) != len(self.ram):
            raise ValueError(
                f"RamMapperState.ram must have {len(self.ram)} bytes, got {len(ram)}"
            )
        self.banks[:] = banks
        self.ram[:] = ram

    def debug_bank_info(self, page: int) -> str | None:
        """Describe the bank visible at CPU page (0-3, i.e. addr >> 14).

        One bank register maps directly to one 16 KB page here -- unlike
        ASCII8/Konami's two-8KB-windows-per-page pairing (`_format_bank_info`
        in msx/mapper.py), which does not apply to this mapper.
        """
        if not 0 <= page <= 3:
            return None
        bank = self.banks[page] & self.bank_mask
        start = bank * _BANK_SIZE
        return f"bank {bank} @{start:05X}-{start + _BANK_SIZE - 1:05X}"
