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


def test_port_ab_read_returns_reset_default_before_any_write() -> None:
    # 0x9B: Intel 8255 post-reset control word (mode 0, ports A/B/C all
    # input), matching openMSX's I8255::reset before MSX firmware's
    # start-up OUT &HAB,&H82.
    ppi = make_ppi()
    assert ppi.read_port(0xAB) == 0x9B


def test_port_ab_read_returns_last_mode_set_word() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x82)   # bit7=1 mode-set word
    assert ppi.read_port(0xAB) == 0x82


def test_port_ab_bit_set_reset_does_not_change_control_readback() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x82)   # mode-set word
    ppi.write_port(0xAB, 0x0D)   # bit set/reset command, bit7=0
    assert ppi.read_port(0xAB) == 0x82


def test_port_ab_bit_set_sets_single_port_c_bit() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x0D)  # bit7=0, index 6 (bits 3-1), value 1 → set bit 6
    assert ppi.read_port(0xAA) & (1 << 6)


def test_port_ab_bit_reset_clears_single_port_c_bit() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x40)  # bit 6 set
    ppi.write_port(0xAB, 0x0C)  # index 6, value 0 → clear bit 6
    assert not (ppi.read_port(0xAA) & (1 << 6))


def test_port_ab_mode_word_leaves_port_c_unchanged() -> None:
    # Real 8255 datasheets disagree on whether a mode-set control word
    # (bit 7 = 1) resets output latches; this follows openMSX's I8255 core
    # (shared across its MSX/SC-3000/SVI machines), which leaves them alone.
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x43)   # CAPS LED (bit 6) + row 3 on Port C
    ppi.write_port(0xAB, 0x82)   # bit7=1 mode-set word → latch unchanged
    assert ppi.read_port(0xAA) == 0x43


# --- allium/ppi.allium: rows 9-10 are real matrix rows (numeric keypad), not
# out-of-range like rows 11-15 (see "Rows 9-10" open question) -----------------

def test_port_aa_row9_reads_matrix_row9() -> None:
    state = InputState()
    state.matrix[9] = 0xAA
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x09)  # row 9
    assert ppi.read_port(0xA9) == 0xAA


def test_port_aa_row10_reads_matrix_row10() -> None:
    state = InputState()
    state.matrix[10] = 0x55
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x0A)  # row 10
    assert ppi.read_port(0xA9) == 0x55


def test_port_aa_row11_out_of_range_returns_ff() -> None:
    # Boundary complement to test_port_aa_row_out_of_range_returns_ff (row 15):
    # row 11 is the first row past the real 0-10 matrix.
    state = InputState()
    ppi = make_ppi(input_state=state)
    ppi.write_port(0xAA, 0x0B)  # row 11
    assert ppi.read_port(0xA9) == 0xFF


# --- allium/ppi.allium: PPI.cassette_motor_on / cassette_out_level /
# key_click_level derived values -- only Port C bit 6 (CAPS LED) had coverage
# before; bits 4, 5 and 7 are the other three control lines Port C carries ----

def test_port_c_bit4_cassette_motor_reflected() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x10)  # bit 4 set (0 = motor relay closed/on)
    assert ppi.read_port(0xAA) & 0x10


def test_port_c_bit5_cassette_out_reflected() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x20)  # bit 5 set (software-toggled MIC output level)
    assert ppi.read_port(0xAA) & 0x20


def test_port_c_bit7_key_click_reflected() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x80)  # bit 7 set (software-toggled 1-bit audio output)
    assert ppi.read_port(0xAA) & 0x80


# --- allium/ppi.allium: invariant PortCIsEightBit -- write_port masks every
# value to 8 bits before storing, regardless of port ---------------------------

def test_write_port_c_masks_value_above_8_bits() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAA, 0x143)  # 0x143 & 0xFF == 0x43
    assert ppi.read_port(0xAA) == 0x43


def test_write_control_port_masks_before_decoding() -> None:
    ppi = make_ppi()
    ppi.write_port(0xAB, 0x10D)  # 0x10D & 0xFF == 0x0D -> bit-set, index 6, value 1
    assert ppi.read_port(0xAA) & (1 << 6)
