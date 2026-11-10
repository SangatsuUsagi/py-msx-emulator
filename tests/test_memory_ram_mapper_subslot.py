"""Tests for a RAM mapper placed in one slot-3 sub-slot (ram_mapper_subslot).

memory-slot-bus "Slot 3 sub-slot dispatch" and "MSX2 slot 3 sub-slot layout
is data-driven": a mapper with a configured sub-slot answers there only, and
every sub-slot without a role reads open bus and ignores writes.

slot_register 0xFF selects slot 3 for every page, so sub_slot_reg alone
drives the sub-slot dispatch. sub_slot_reg bit pairs: page0=1:0, page1=3:2,
page2=5:4, page3=7:6.
"""
from pathlib import Path

import pytest

from host.machine_config import build_machine_from_spec, load_device_registry, load_machine_spec
from msx.boundary import BANK_NONE, SLOT_CONTENT_EMPTY, SLOT_CONTENT_RAM_MAPPER
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper

_ALL_SLOT3 = 0xFF  # every page -> slot 3
_PROJECT = Path(__file__).resolve().parent.parent
_CONFIG = _PROJECT / "config"


class _StubFdc:
    """Minimal FloppyDisk stand-in: answers 0x99 and records writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []

    def read_mem(self, addr: int) -> int:
        return 0x99

    def write_mem(self, addr: int, value: int) -> None:
        self.writes.append((addr, value))


def _memory(ram_mapper_subslot: int, fdc: object | None = None) -> Memory:
    return Memory(
        rom=bytes(32768),
        ram=bytearray(32768),
        mapper1=FlatMapper(None),
        slot_register=_ALL_SLOT3,
        sub_slot_enabled=True,
        sub0_rom=bytes([0x41]) * 0x4000,
        ram_mapper=RamMapper(),
        ram_mapper_subslot=ram_mapper_subslot,
        fdc=fdc,  # type: ignore[arg-type]
    )


def _select(mem: Memory, sub: int) -> None:
    mem.set_sub_slot_reg(sub * 0x55)  # every page -> `sub`


def test_a_memory_mapper_responds_only_in_its_own_subslot() -> None:
    # memory-slot-bus "A memory mapper responds only in its own sub-slot".
    mem = _memory(2)
    _select(mem, 2)
    mem.write(0xC000, 0x5A)
    assert mem.read(0xC000) == 0x5A
    _select(mem, 3)
    assert mem.read(0xC000) == 0xFF
    mem.write(0xC000, 0x11)
    _select(mem, 2)
    assert mem.read(0xC000) == 0x5A


@pytest.mark.parametrize("sub", [1, 3])
def test_other_subslots_read_open_bus(sub: int) -> None:
    mem = _memory(2)
    _select(mem, sub)
    for addr in (0x0000, 0x4000, 0x8000, 0xC000):
        assert mem.read(addr) == 0xFF


def test_fdc_composes_with_a_ram_mapper() -> None:
    # memory-slot-bus "FDC composes with a RAM mapper".
    fdc = _StubFdc()
    mem = _memory(3, fdc=fdc)
    _select(mem, 0)
    assert mem.read(0x0000) == 0x41  # SUB ROM, page 0
    assert mem.read(0x4000) == 0x99  # FDC, pages 1-2
    assert mem.read(0xC000) == 0xFF  # page 3 of sub-slot 0: no role
    mem.write(0xC000, 0x22)
    assert fdc.writes == []
    for sub in (1, 2):
        _select(mem, sub)
        assert mem.read(0x8000) == 0xFF
    _select(mem, 3)
    mem.write(0x8000, 0x33)
    assert mem.read(0x8000) == 0x33


def test_a_mapper_subslot_without_a_mapper_is_rejected() -> None:
    with pytest.raises(ValueError, match="ram_mapper_subslot requires a ram_mapper"):
        Memory(
            rom=bytes(32768), ram=bytearray(32768), mapper1=FlatMapper(None),
            sub_slot_enabled=True, ram_mapper_subslot=2,
        )


def test_cbios_msx2_slot3_subslot3_is_empty() -> None:
    # The mapper is in sub-slot 2, as in openMSX's C-BIOS_MSX2.xml; sub-slot 3
    # holds nothing.
    spec = load_machine_spec("cbios_msx2", _CONFIG, load_device_registry(_CONFIG), _PROJECT)
    assert spec.ram_mapper_subslot == 2
    machine = build_machine_from_spec(
        spec, bios_override=bytes(32768), extrom_override=bytes(32768),
        logo_override=bytes(16384),
    )
    assert machine.memory.ram_mapper_subslot == 2
    assert machine.slot_info(3, 2, -1).content == SLOT_CONTENT_RAM_MAPPER
    assert machine.slot_info(3, 3, -1).content == SLOT_CONTENT_EMPTY
    assert machine.slot_info(3, 3, -1).size_kb == 0
    assert machine.slot_info(3, 2, 3).bank != machine.slot_info(3, 3, 3).bank
    assert machine.slot_info(3, 3, 3).bank == BANK_NONE
