"""Correctness tests for Memory's per-page dispatch cache.

Covers the scenarios in
openspec/changes/memory-dispatch-cache/specs/memory-bus/spec.md: routing
must always reflect the current slot_register/sub_slot_reg, for every
slot-3 RAM strategy (MSX1 flat, legacy RamMapper, data-driven
flat_ram_subslot). Also includes a differential sweep (task 3.3 of
openspec/changes/memory-dispatch-cache/tasks.md) against a frozen copy of
the pre-cache read()/write() decode logic, across every slot_register and
sub_slot_reg value.
"""
from __future__ import annotations

from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ram_mapper import RamMapper

_ALL_SLOT3 = 0xFF


class _StubFdc:
    """Minimal FloppyDisk stand-in (see tests/test_memory_flat_ram.py)."""

    def __init__(self) -> None:
        self.reads: list[int] = []
        self.writes: list[tuple[int, int]] = []

    def read_mem(self, addr: int) -> int:
        self.reads.append(addr)
        return 0x99

    def write_mem(self, addr: int, value: int) -> None:
        self.writes.append((addr, value))


# ---------------------------------------------------------------------------
# Scenario: A page's routing updates immediately after a primary slot switch
# ---------------------------------------------------------------------------

def test_primary_slot_switch_msx1_flat() -> None:
    rom = bytes([0xAA] * 0x10000)
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None), slot_register=0x00)
    assert mem.read(0xC000) == 0xAA  # page3 -> slot0 (ROM)
    mem.slot_register = 0xC0  # page3 -> slot3 (MSX1 flat RAM)
    mem.write(0xC000, 0x11)
    assert mem.read(0xC000) == 0x11  # immediate, not stale ROM


def test_primary_slot_switch_ram_mapper() -> None:
    rom = bytes([0xAA] * 0x10000)
    rm = RamMapper()
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0x00, ram_mapper=rm)
    assert mem.read(0xC000) == 0xAA  # page3 -> slot0 (ROM)
    mem.slot_register = 0xC0  # page3 -> slot3 (RAM mapper)
    mem.write(0xC000, 0x22)
    assert mem.read(0xC000) == 0x22  # immediate, not stale ROM
    assert rm.read(0xC000) == 0x22


def test_primary_slot_switch_flat_subslot() -> None:
    rom = bytes([0xAA] * 0x10000)
    mem = Memory(rom=rom, ram=bytearray(65536), _mapper=FlatMapper(None),
                 slot_register=0x00, sub_slot_enabled=True, flat_ram_subslot=3,
                 sub_slot_reg=0xC0)  # page3 -> sub-slot 3 once slot3 is selected
    assert mem.read(0xC000) == 0xAA  # page3 -> slot0 (ROM)
    mem.slot_register = 0xC0  # page3 -> slot3 (flat RAM)
    mem.write(0xC000, 0x33)
    assert mem.read(0xC000) == 0x33  # immediate, not stale ROM


# ---------------------------------------------------------------------------
# Scenario: A page's routing updates immediately after a secondary slot switch
# (direct sub_slot_reg write)
# ---------------------------------------------------------------------------

def test_secondary_slot_switch_ram_mapper() -> None:
    sub_rom = bytes([0x41] * 0x4000)
    rm = RamMapper()
    rm.write(0x0000, 0x52)
    mem = Memory(rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0xC3,  # page0 -> slot3
                 ram_mapper=rm, sub0_rom=sub_rom, sub_slot_enabled=True, sub_slot_reg=0x00)
    assert mem.read(0x0000) == 0x41  # sub-slot 0 -> SUB-ROM
    mem.sub_slot_reg = 0x02  # page0 -> sub-slot 2 (RAM mapper)
    assert mem.read(0x0000) == 0x52  # immediate, not stale SUB-ROM byte


def test_secondary_slot_switch_flat_subslot() -> None:
    mem = Memory(rom=bytes(32768), ram=bytearray(65536), _mapper=FlatMapper(None),
                 slot_register=_ALL_SLOT3, sub_slot_enabled=True,
                 flat_ram_subslot=3, sub_slot_reg=0x00)
    mem.ram[0x0000] = 0x63
    assert mem.read(0x0000) == 0xFF  # sub-slot 0, no sub0_rom -> open bus
    mem.sub_slot_reg = 0x03  # page0 -> sub-slot 3 (flat RAM)
    assert mem.read(0x0000) == 0x63  # immediate, not stale open bus


def test_secondary_slot_switch_msx1_flat_with_sub0_rom() -> None:
    # No ram_mapper, no flat_ram_subslot: legacy "MSX1 flat" fallback branch.
    # A 32 KB flat RAM's base is 0x8000, so page0 falls outside it -> 0xFF.
    sub_rom = bytes([0x41] * 0x4000)
    mem = Memory(rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0xC3, sub0_rom=sub_rom, sub_slot_enabled=True, sub_slot_reg=0x00)
    assert mem.read(0x0000) == 0x41  # sub-slot 0 -> SUB-ROM
    mem.sub_slot_reg = 0x02  # page0 -> sub-slot 2 -> MSX1 flat RAM fallback (out of range here)
    assert mem.read(0x0000) == 0xFF  # immediate, not stale SUB-ROM byte


# ---------------------------------------------------------------------------
# Scenario: Writing sub_slot_reg via the 0xFFFF intercept also invalidates
# routing
# ---------------------------------------------------------------------------

def test_intercept_write_ffff_switches_ram_mapper() -> None:
    sub_rom = bytes([0x41] * 0x4000)
    rm = RamMapper()
    rm.write(0xC000, 0x66)
    mem = Memory(rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0xC0, ram_mapper=rm, sub0_rom=sub_rom,
                 sub_slot_enabled=True, sub_slot_reg=0x00)
    # page3, sub-slot 0: sub0_rom only ever serves page0 -> open bus here
    assert mem.read(0xC000) == 0xFF
    mem.write(0xFFFF, 0x80)  # intercept: sub_slot_reg = 0x80 -> page3 sub-slot 2
    assert mem.sub_slot_reg == 0x80
    assert mem.read(0xC000) == 0x66  # immediate, via the intercept path, not stale open bus


def test_intercept_write_ffff_switches_flat_subslot() -> None:
    mem = Memory(rom=bytes(32768), ram=bytearray(65536), _mapper=FlatMapper(None),
                 slot_register=_ALL_SLOT3, sub_slot_enabled=True, flat_ram_subslot=3,
                 sub_slot_reg=0x00)
    mem.ram[0xC000] = 0x55
    assert mem.read(0xC000) == 0xFF  # page3, sub-slot 0, no sub0_rom/fdc -> open bus
    mem.write(0xFFFF, 0xC0)  # intercept: page3 -> sub-slot 3 (flat RAM)
    assert mem.read(0xC000) == 0x55  # immediate, not stale open bus


def test_intercept_write_ffff_switches_msx1_flat() -> None:
    sub_rom = bytes([0x41] * 0x4000)
    mem = Memory(rom=bytes(0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0xC0, sub0_rom=sub_rom, sub_slot_enabled=True, sub_slot_reg=0x00)
    mem.ram[0x4000] = 0x77  # MSX1 flat RAM offset for 0xC000 (32 KB RAM, base 0x8000)
    assert mem.read(0xC000) == 0xFF  # page3, sub-slot 0: sub0_rom only serves page0 -> open bus
    mem.write(0xFFFF, 0x80)  # intercept: page3 -> sub-slot 2 -> MSX1 flat RAM
    assert mem.read(0xC000) == 0x77  # immediate, not stale open bus


# ---------------------------------------------------------------------------
# Scenario: Repeated reads between slot changes return identical results
# ---------------------------------------------------------------------------

def test_repeated_reads_between_switches_are_stable() -> None:
    mem = Memory(rom=bytes([0xAB] * 0x8000), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=0xD4)
    first = mem.read(0x0000)
    for _ in range(5):
        assert mem.read(0x0000) == first
    mem.write(0xC000, 0x99)
    second = mem.read(0xC000)
    for _ in range(5):
        assert mem.read(0xC000) == second


# ---------------------------------------------------------------------------
# Differential sweep: cached Memory vs a frozen copy of the pre-cache decode
# logic, across every slot_register / sub_slot_reg value.
# ---------------------------------------------------------------------------

class _ReferenceMemory:
    """Frozen copy of Memory.read()/write()'s pre-cache decode logic (full
    re-derivation on every call, no page cache). Used only to differentially
    verify the cached implementation never changes observable behavior --
    not for production use.
    """

    def __init__(
        self,
        rom: bytes,
        ram: bytearray,
        mapper: FlatMapper,
        mapper2: FlatMapper,
        slot_register: int = 0xD4,
        extrom: bytes | None = None,
        ram_mapper: RamMapper | None = None,
        sub_slot_reg: int = 0x00,
        sub_slot_enabled: bool = False,
        sub0_rom: bytes | None = None,
        flat_ram_subslot: int | None = None,
        fdc: object | None = None,
    ) -> None:
        self.rom = rom
        self.ram = ram
        self._mapper = mapper
        self._mapper2 = mapper2
        self.slot_register = slot_register
        self.extrom = extrom
        self.ram_mapper = ram_mapper
        self.sub_slot_reg = sub_slot_reg
        self.sub_slot_enabled = sub_slot_enabled
        self.sub0_rom = sub0_rom
        self.flat_ram_subslot = flat_ram_subslot
        self.fdc = fdc
        self._rom_len = len(rom)
        self._extrom_len = len(extrom) if extrom is not None else 0

    def _page3_is_slot3(self) -> bool:
        return self.sub_slot_enabled and ((self.slot_register >> 6) & 0x03) == 3

    def read(self, addr: int) -> int:
        addr = addr & 0xFFFF
        slot = (self.slot_register >> ((addr >> 14) * 2)) & 0x03
        if slot == 0:
            if self.extrom is not None and 0x8000 <= addr <= 0xBFFF:
                off = addr - 0x8000
                return self.extrom[off] if off < self._extrom_len else 0xFF
            return self.rom[addr] if addr < self._rom_len else 0xFF
        if slot == 1:
            return self._mapper.read(addr)
        if slot == 2:
            return self._mapper2.read(addr)
        if addr == 0xFFFF and self._page3_is_slot3():
            return (~self.sub_slot_reg) & 0xFF
        page = (addr >> 14) & 0x03
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        flat_sub = self.flat_ram_subslot
        if flat_sub is not None:
            if sub == 0:
                if page == 0 and self.sub0_rom is not None:
                    return self.sub0_rom[addr] if addr < len(self.sub0_rom) else 0xFF
                if page == 1 and self.fdc is not None:
                    return self.fdc.read_mem(addr)  # type: ignore[attr-defined]
                return 0xFF
            if sub == flat_sub:
                ram = self.ram
                return ram[addr] if addr < len(ram) else 0xFF
            return 0xFF
        if sub == 0:
            if self.sub0_rom is not None:
                if page == 0:
                    return self.sub0_rom[addr] if addr < len(self.sub0_rom) else 0xFF
                return 0xFF
        elif sub == 1:
            return 0xFF
        if self.ram_mapper is not None:
            return self.ram_mapper.read(addr)
        off = addr - (0x10000 - len(self.ram))
        return self.ram[off] if 0 <= off < len(self.ram) else 0xFF

    def write(self, addr: int, value: int) -> None:
        addr = addr & 0xFFFF
        value = value & 0xFF
        slot = (self.slot_register >> ((addr >> 14) * 2)) & 0x03
        if slot == 0:
            return
        if slot == 1:
            self._mapper.write(addr, value)
            return
        if slot == 2:
            self._mapper2.write(addr, value)
            return
        if addr == 0xFFFF and self._page3_is_slot3():
            self.sub_slot_reg = value & 0xFF
            return
        page = (addr >> 14) & 0x03
        sub = (self.sub_slot_reg >> (page * 2)) & 0x03
        flat_sub = self.flat_ram_subslot
        if flat_sub is not None:
            if sub == 0:
                if page == 1 and self.fdc is not None:
                    self.fdc.write_mem(addr, value)  # type: ignore[attr-defined]
                return
            if sub == flat_sub:
                ram = self.ram
                if addr < len(ram):
                    ram[addr] = value
            return
        if sub == 1:
            return
        if sub == 0 and self.sub0_rom is not None:
            return
        if self.ram_mapper is not None:
            self.ram_mapper.write(addr, value)
            return
        off = addr - (0x10000 - len(self.ram))
        if 0 <= off < len(self.ram):
            self.ram[off] = value


_ADDRS = [0x0000, 0x3FFF, 0x4000, 0x7FFF, 0x8000, 0xBFFF, 0xC000, 0xFFFF]
_ROM = b"\x11" * 0x8000
_CART = b"\x22" * 0x8000
_SUB_ROM = b"\x44" * 0x4000
_EXTROM = b"\x55" * 0x4000


def _assert_read_matches(new: Memory, ref: _ReferenceMemory) -> None:
    for addr in _ADDRS:
        assert new.read(addr) == ref.read(addr), f"read mismatch at addr=0x{addr:04X}"


def _assert_write_matches(new: Memory, ref: _ReferenceMemory) -> None:
    for addr in _ADDRS:
        value = (addr ^ 0x5A) & 0xFF
        new.write(addr, value)
        ref.write(addr, value)
        assert new.read(addr) == ref.read(addr), f"post-write mismatch at addr=0x{addr:04X}"


def test_differential_msx1_flat_across_all_slot_registers() -> None:
    for slot_register in range(256):
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 32768), _mapper=FlatMapper(_CART),
                     slot_register=slot_register)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 32768), mapper=FlatMapper(_CART),
                                mapper2=FlatMapper(None), slot_register=slot_register)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)


def test_differential_msx1_flat_across_all_sub_slot_regs() -> None:
    for sub_slot_reg in range(256):
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 32768), _mapper=FlatMapper(None),
                     slot_register=_ALL_SLOT3, sub_slot_enabled=True, sub_slot_reg=sub_slot_reg)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 32768), mapper=FlatMapper(None),
                                mapper2=FlatMapper(None), slot_register=_ALL_SLOT3,
                                sub_slot_enabled=True, sub_slot_reg=sub_slot_reg)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)


def test_differential_ram_mapper_across_all_slot_registers() -> None:
    for slot_register in range(256):
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 32768), _mapper=FlatMapper(None),
                     slot_register=slot_register, ram_mapper=RamMapper(), sub0_rom=_SUB_ROM,
                     sub_slot_enabled=True, sub_slot_reg=0b01_10_11_00)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 32768), mapper=FlatMapper(None),
                                mapper2=FlatMapper(None), slot_register=slot_register,
                                ram_mapper=RamMapper(), sub0_rom=_SUB_ROM, sub_slot_enabled=True,
                                sub_slot_reg=0b01_10_11_00)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)


def test_differential_ram_mapper_across_all_sub_slot_regs() -> None:
    for sub_slot_reg in range(256):
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 32768), _mapper=FlatMapper(None),
                     slot_register=_ALL_SLOT3, ram_mapper=RamMapper(), sub0_rom=_SUB_ROM,
                     sub_slot_enabled=True, sub_slot_reg=sub_slot_reg)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 32768), mapper=FlatMapper(None),
                                mapper2=FlatMapper(None), slot_register=_ALL_SLOT3,
                                ram_mapper=RamMapper(), sub0_rom=_SUB_ROM, sub_slot_enabled=True,
                                sub_slot_reg=sub_slot_reg)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)


def test_differential_flat_subslot_across_all_slot_registers() -> None:
    for slot_register in range(256):
        fdc_new, fdc_ref = _StubFdc(), _StubFdc()
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 65536), _mapper=FlatMapper(None),
                     slot_register=slot_register, sub_slot_enabled=True, flat_ram_subslot=3,
                     sub0_rom=_SUB_ROM, fdc=fdc_new, sub_slot_reg=0b11_10_01_00)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 65536), mapper=FlatMapper(None),
                                mapper2=FlatMapper(None), slot_register=slot_register,
                                sub_slot_enabled=True, flat_ram_subslot=3, sub0_rom=_SUB_ROM,
                                fdc=fdc_ref, sub_slot_reg=0b11_10_01_00)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)
        assert fdc_new.reads == fdc_ref.reads
        assert fdc_new.writes == fdc_ref.writes


def test_differential_flat_subslot_across_all_sub_slot_regs() -> None:
    for sub_slot_reg in range(256):
        fdc_new, fdc_ref = _StubFdc(), _StubFdc()
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 65536), _mapper=FlatMapper(None),
                     slot_register=_ALL_SLOT3, sub_slot_enabled=True, flat_ram_subslot=3,
                     sub0_rom=_SUB_ROM, fdc=fdc_new, sub_slot_reg=sub_slot_reg)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 65536), mapper=FlatMapper(None),
                                mapper2=FlatMapper(None), slot_register=_ALL_SLOT3,
                                sub_slot_enabled=True, flat_ram_subslot=3, sub0_rom=_SUB_ROM,
                                fdc=fdc_ref, sub_slot_reg=sub_slot_reg)
        _assert_read_matches(new, ref)
        _assert_write_matches(new, ref)
        assert fdc_new.reads == fdc_ref.reads
        assert fdc_new.writes == fdc_ref.writes


def test_differential_extrom_across_all_slot_registers() -> None:
    for slot_register in range(256):
        new = Memory(rom=_ROM, ram=bytearray(b"\x33" * 32768), _mapper=FlatMapper(_CART),
                     slot_register=slot_register, extrom=_EXTROM)
        ref = _ReferenceMemory(rom=_ROM, ram=bytearray(b"\x33" * 32768), mapper=FlatMapper(_CART),
                                mapper2=FlatMapper(None), slot_register=slot_register,
                                extrom=_EXTROM)
        _assert_read_matches(new, ref)
