"""Tests for make_machine_msx2 factory and MSX1 regression guard."""
from msx.input import InputState
from msx.machine import Machine, make_machine, make_machine_msx2
from msx.ram_mapper import RamMapper
from msx.vdp.v9938 import V9938

_DUMMY_ROM = bytes(32768)
_DUMMY_EXTROM = bytes(16384)


# ---------------------------------------------------------------------------
# make_machine_msx2: VDP is V9938
# ---------------------------------------------------------------------------

def test_msx2_vdp_is_v9938() -> None:
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    assert isinstance(machine.vdp, V9938)


def test_msx2_memory_has_ram_mapper() -> None:
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    assert isinstance(machine.memory.ram_mapper, RamMapper)


def test_msx2_input_is_input_state() -> None:
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    assert isinstance(machine.input, InputState)


def test_msx2_returns_machine_instance() -> None:
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    assert isinstance(machine, Machine)


# ---------------------------------------------------------------------------
# Ext ROM accessible via memory (slot 0 page 2 = 0x8000)
# ---------------------------------------------------------------------------

def test_msx2_extrom_accessible_at_0x8000() -> None:
    """With slot_register=0x00 (all pages slot 0), ext ROM is at 0x8000–0xBFFF."""
    extrom = bytes([0xAB] + [0x00] * (16384 - 1))
    machine = make_machine_msx2(_DUMMY_ROM, extrom)
    assert machine.memory.read(0x8000) == 0xAB


def test_msx2_extrom_write_is_noop() -> None:
    extrom = bytes([0xCC] + [0x00] * (16384 - 1))
    machine = make_machine_msx2(_DUMMY_ROM, extrom)
    machine.memory.write(0x8000, 0xFF)
    assert machine.memory.read(0x8000) == 0xCC


# ---------------------------------------------------------------------------
# RamMapper ports wired to IO bus
# ---------------------------------------------------------------------------

def test_msx2_ram_mapper_write_port_updates_bank() -> None:
    """Writing to port 0xFF via IOBus changes the page-3 bank register."""
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    machine.io.write_port(0xFF, 4)
    assert machine.memory.ram_mapper.banks[3] == 4


def test_msx2_ram_mapper_read_port_returns_bank() -> None:
    """Reading port 0xFD via IOBus returns the current page-1 bank register."""
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    machine.memory.ram_mapper.banks[1] = 6
    assert machine.io.read_port(0xFD) == 6


def test_msx2_ram_mapper_all_ports_wired() -> None:
    """All four mapper ports 0xFC–0xFF set the corresponding bank."""
    machine = make_machine_msx2(_DUMMY_ROM, _DUMMY_EXTROM)
    for page, port in enumerate([0xFC, 0xFD, 0xFE, 0xFF]):
        machine.io.write_port(port, page + 1)
    for page in range(4):
        assert machine.memory.ram_mapper.banks[page] == page + 1


# ---------------------------------------------------------------------------
# MSX1 make_machine regression: unchanged
# ---------------------------------------------------------------------------

def test_msx1_make_machine_still_works() -> None:
    machine = make_machine(_DUMMY_ROM)
    assert isinstance(machine, Machine)
    assert not isinstance(machine.vdp, V9938)
    assert machine.memory.ram_mapper is None


def test_msx1_input_is_input_state() -> None:
    machine = make_machine(_DUMMY_ROM)
    assert isinstance(machine.input, InputState)
