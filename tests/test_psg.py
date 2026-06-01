from msx.input import InputState
from msx.psg import PSG


def test_registers_init_zero() -> None:
    psg = PSG()
    assert psg.regs == [0] * 16


def test_address_latch() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    assert psg.latch == 7


def test_register_write() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    psg.write_port(0xA1, 0x38)
    assert psg.regs[7] == 0x38


def test_register_read() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    psg.write_port(0xA1, 0x38)
    assert psg.read_port(0xA2) == 0x38


def test_latch_masked_to_4_bits() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x1F)  # 0x1F & 0x0F = 0x0F = 15
    assert psg.latch == 15


def test_unmapped_read_returns_ff() -> None:
    psg = PSG()
    assert psg.read_port(0xA0) == 0xFF


def test_reg14_all_released_returns_ff() -> None:
    # All bits = 1 (released) when no buttons pressed, JOY_SELECT=0 (default)
    psg = PSG(_input=InputState())
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) == 0xFF


def test_reg14_joy1_up_pressed_clears_bit0() -> None:
    state = InputState()
    state.joystick_button_down(0, 0)  # Joy1 Up
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & 0x01 == 0   # bit 0 cleared
    assert result & 0xFE == 0xFE  # other bits unaffected → 0xFE


def test_reg14_joy1_trigger_a_clears_bit4() -> None:
    state = InputState()
    state.joystick_button_down(0, 4)  # Joy1 Trigger A
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) & (1 << 4) == 0


def test_reg14_joy1_trigger_b_clears_bit5() -> None:
    state = InputState()
    state.joystick_button_down(0, 5)  # Joy1 Trigger B
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) & (1 << 5) == 0


def test_reg14_joy2_trigger_a_clears_bit6() -> None:
    state = InputState()
    state.joystick_button_down(1, 4)  # Joy2 Trigger A
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) & (1 << 6) == 0


def test_reg14_joy2_trigger_b_clears_bit7() -> None:
    state = InputState()
    state.joystick_button_down(1, 5)  # Joy2 Trigger B
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) & (1 << 7) == 0


def test_reg14_joy_select_0_reads_joy1_directions() -> None:
    # reg15 default=0 → bit6=0 → Joy1 directions on bits 0-3
    state = InputState()
    state.joystick_button_down(0, 0)  # Joy1 Up
    state.joystick_button_down(1, 0)  # Joy2 Up (should NOT appear at bit 0)
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & 0x01 == 0   # Joy1 Up visible


def test_reg14_joy_select_1_reads_joy2_directions() -> None:
    state = InputState()
    state.joystick_button_down(1, 0)  # Joy2 Up
    psg = PSG(_input=state)
    # Set JOY_SELECT=1 via reg15 bit6
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x40)  # bit 6 = 1
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & 0x01 == 0   # Joy2 Up visible on bit 0


def test_reg14_joy_select_1_hides_joy1_directions() -> None:
    state = InputState()
    state.joystick_button_down(0, 0)  # Joy1 Up pressed
    psg = PSG(_input=state)
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x40)  # JOY_SELECT=1
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & 0x01 != 0   # Joy1 Up NOT visible (Joy2 Up not pressed)


def test_reg14_returns_register_value_when_no_input() -> None:
    psg = PSG()
    psg.write_port(0xA0, 14)
    psg.write_port(0xA1, 0x00)
    assert psg.read_port(0xA2) == 0x00


def test_reg14_not_overridden_for_other_regs() -> None:
    state = InputState()
    state.joystick_button_down(0, 0)
    psg = PSG(_input=state)
    psg.write_port(0xA0, 7)
    psg.write_port(0xA1, 0x38)
    assert psg.read_port(0xA2) == 0x38


def test_sequential_register_writes() -> None:
    psg = PSG()
    for i in range(16):
        psg.write_port(0xA0, i)
        psg.write_port(0xA1, i * 2)
    for i in range(16):
        psg.write_port(0xA0, i)
        assert psg.read_port(0xA2) == i * 2
