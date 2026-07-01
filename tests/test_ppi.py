from msx.input import InputState
from msx.mapper import FlatMapper
from msx.memory import Memory
from msx.ppi import PPI


def make_ppi(slot_register: int = 0, input_state: InputState | None = None) -> PPI:
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None),
                 slot_register=slot_register)
    return PPI(memory=mem, _input=input_state)


def test_slot_register_write_and_read() -> None:
    ppi = make_ppi()
    ppi.write_port(0xA8, 0x09)
    assert ppi.read_port(0xA8) == 0x09


def test_slot_register_initial_value() -> None:
    ppi = make_ppi(slot_register=0)
    assert ppi.read_port(0xA8) == 0x00


def test_slot_register_propagates_to_memory() -> None:
    mem = Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))
    ppi = PPI(memory=mem)
    ppi.write_port(0xA8, 0xD4)
    assert mem.slot_register == 0xD4


def test_keyboard_read_returns_ff() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xA9) == 0xFF


def test_port_aa_read_returns_last_written_port_c() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xAA) == 0x00  # default Port C
    ppi.write_port(0xAA, 0x47)          # CAPS LED (bit6) set, row 7
    assert ppi.read_port(0xAA) == 0x47


def test_port_aa_upper_nibble_reflected() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x40)  # CAPS LED (bit 6) set, row 0
    assert ppi.read_port(0xAA) & 0x40


def test_port_aa_write_stores_row() -> None:
    state = InputState()
    state.matrix[3] = 0xFE
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x03)
    assert ppi.read_port(0xA9) == 0xFE


def test_port_aa_row_out_of_range_returns_ff() -> None:
    state = InputState()
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x0F)  # row 15, out of range
    assert ppi.read_port(0xA9) == 0xFF


def test_keyboard_read_matrix_row2() -> None:
    state = InputState()
    state.matrix[2] = 0xFE
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x02)
    assert ppi.read_port(0xA9) == 0xFE


def test_keyboard_read_returns_ff_no_input() -> None:
    ppi = make_ppi()  # no InputState
    assert ppi.read_port(0xA9) == 0xFF


def test_port_ab_read_returns_ff() -> None:
    ppi = make_ppi()
    assert ppi.read_port(0xAB) == 0xFF


def test_port_ab_bit_set_sets_single_port_c_bit() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x0D)  # bit7=0, index 6 (bits 3-1), value 1 → set bit 6
    assert ppi.read_port(0xAA) & (1 << 6)


def test_port_ab_bit_reset_clears_single_port_c_bit() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x40)  # bit 6 set
    ppi.write_port(0xAB, 0x0C)  # index 6, value 0 → clear bit 6
    assert not (ppi.read_port(0xAA) & (1 << 6))


def test_port_ab_mode_word_preserves_row() -> None:
    state = InputState()
    state.matrix[3] = 0xFE
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x03)   # row 3
    ppi.write_port(0xAB, 0x82)   # bit7=1 mode-set word
    assert ppi.read_port(0xA9) == 0xFE  # row 3 still selected
