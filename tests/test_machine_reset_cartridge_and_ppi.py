"""Tests for Machine.reset() wiring added by
openspec/changes/wire-cartridge-mapper-and-ppi-reset: the two unexpanded
top-level cartridge-slot mappers (memory._mapper/memory._mapper2), and the
PPI. Both were previously never reset at all -- see that change's design.md
for the audit that found the gap.
"""
from __future__ import annotations

from msx.mapper import KonamiMapper
from tests.factories import make_machine

_NOP_ROM = bytes(32768)


def test_machine_reset_restores_unexpanded_slot1_cartridge_bank_state() -> None:
    machine = make_machine(rom=_NOP_ROM, mapper="Konami")
    cart = machine.memory._mapper
    assert isinstance(cart, KonamiMapper)

    cart.write(0x6000, 1)
    cart.write(0x8000, 2)
    cart.write(0xA000, 3)
    assert cart._banks == [0, 1, 2, 3]  # already power-on values for a 4-page ROM
    cart.write(0x6000, 0)
    assert cart._banks == [0, 0, 2, 3]

    machine.reset()

    assert cart._banks == [0, 1, 2, 3]


def test_machine_reset_restores_unexpanded_slot2_cartridge_bank_state() -> None:
    machine = make_machine(rom=_NOP_ROM, cartridge2=_NOP_ROM, mapper2="Konami")
    cart = machine.memory._mapper2
    assert isinstance(cart, KonamiMapper)

    cart.write(0x6000, 0)
    assert cart._banks == [0, 0, 2, 3]

    machine.reset()

    assert cart._banks == [0, 1, 2, 3]


def test_machine_reset_restores_ppi_control_word_and_port_c() -> None:
    machine = make_machine(rom=_NOP_ROM)
    assert machine.ppi is not None

    machine.ppi.write_port(0xAB, 0x82)  # mode-set word, not the 0x9B default
    machine.ppi.write_port(0xAA, 0x40)  # Port C: CAPS LED bit set, row 0

    machine.reset()

    assert machine.ppi.read_port(0xAB) == 0x9B
    assert machine.ppi.read_port(0xAA) == 0x00
