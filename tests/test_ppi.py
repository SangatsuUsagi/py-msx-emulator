from msx.memory import Memory
from msx.ppi import PPI


def make_ppi(slot_register: int = 0) -> PPI:
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None,
                 slot_register=slot_register)
    return PPI(memory=mem)


def test_slot_register_write_and_read() -> None:
    ppi = make_ppi()
    ppi.write_port(0xA8, 0x09)
    assert ppi.read_port(0xA8) == 0x09


def test_slot_register_initial_value() -> None:
    ppi = make_ppi(slot_register=0)
    assert ppi.read_port(0xA8) == 0x00


def test_slot_register_propagates_to_memory() -> None:
    mem = Memory(rom=bytes(32768), ram=bytearray(16384), cartridge=None)
    ppi = PPI(memory=mem)
    ppi.write_port(0xA8, 0xD4)
    assert mem.slot_register == 0xD4


def test_keyboard_read_returns_ff() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xA9) == 0xFF


def test_port_aa_read_returns_ff() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xAA) == 0xFF


def test_port_aa_write_is_noop() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x55)  # must not raise


def test_port_ab_read_returns_ff() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xAB) == 0xFF


def test_port_ab_write_is_noop() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0xFF)  # must not raise
