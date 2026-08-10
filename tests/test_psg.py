from msx.input import InputState
from msx.psg import PSG, PSG_CLOCK, SAMPLE_RATE, JoystickPort, MouseSlot


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


def test_reg14_joy2_trigger_a_selected_clears_bit4() -> None:
    # Corrected behaviour (Phase 2): PORT A returns the *selected* port's 6
    # signals; Joy2 Trigger A appears on bit 4 when JOY_SELECT=1, and bits 6-7
    # are always 1 (not joystick lines). (Previously Joy2 triggers were on 6-7.)
    state = InputState()
    state.joystick_button_down(1, 4)  # Joy2 Trigger A
    psg = PSG(_input=state)
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x40)  # JOY_SELECT=1
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & (1 << 4) == 0    # Joy2 Trigger A on bit 4
    assert result & 0xC0 == 0xC0     # bits 6-7 pulled high


def test_reg14_joy2_trigger_b_selected_clears_bit5() -> None:
    # Corrected behaviour (Phase 2): Joy2 Trigger B is on bit 5 when JOY_SELECT=1.
    state = InputState()
    state.joystick_button_down(1, 5)  # Joy2 Trigger B
    psg = PSG(_input=state)
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x40)  # JOY_SELECT=1
    psg.write_port(0xA0, 14)
    result = psg.read_port(0xA2)
    assert result & (1 << 5) == 0    # Joy2 Trigger B on bit 5
    assert result & 0xC0 == 0xC0     # bits 6-7 pulled high


def test_reg14_joy_select_0_selects_joy1_triggers() -> None:
    # With JOY_SELECT=0 the selected port is Joy1; a Joy2 trigger must not show.
    state = InputState()
    state.joystick_button_down(1, 4)  # Joy2 Trigger A (should be hidden)
    psg = PSG(_input=state)
    psg.write_port(0xA0, 14)          # JOY_SELECT defaults to 0 → Joy1
    result = psg.read_port(0xA2)
    assert result & (1 << 4) != 0     # Joy2 not visible on the Joy1 view
    assert result & 0xC0 == 0xC0      # bits 6-7 pulled high


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


class _FakeMouse:
    """Test double recording write_pin8 calls; read() returns a fixed value."""

    def __init__(self) -> None:
        self.pin8_calls: list[int] = []
        self.read_value: int = 0

    def write_pin8(self, value: int) -> None:
        self.pin8_calls.append(value)

    def read(self) -> int:
        return self.read_value


def test_reg15_write_forwards_pin8_bit_for_mouse_port() -> None:
    psg = PSG(_input=InputState())
    mouse = _FakeMouse()
    psg._mouse = MouseSlot(mouse, JoystickPort.JOY2)  # bit 5
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x20)  # bit 5 = 1
    assert mouse.pin8_calls[-1] == 1
    psg.write_port(0xA1, 0x00)  # bit 5 = 0
    assert mouse.pin8_calls[-1] == 0


def test_reg15_other_port_pin8_bit_does_not_affect_mouse() -> None:
    psg = PSG(_input=InputState())
    mouse = _FakeMouse()
    psg._mouse = MouseSlot(mouse, JoystickPort.JOY2)  # bit 5
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x10)  # bit 4 (Joy1 pin 8) = 1, bit 5 = 0
    assert mouse.pin8_calls[-1] == 0  # derived from bit 5 only


def test_reg14_delegates_to_mouse_when_its_port_is_selected() -> None:
    psg = PSG(_input=InputState())
    mouse = _FakeMouse()
    mouse.read_value = 0x05
    psg._mouse = MouseSlot(mouse, JoystickPort.JOY2)
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x40)  # JOY_SELECT=1 (Joy2 selected)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) == (0x05 & 0x3F) | 0xC0


def test_reg14_reads_input_state_when_other_port_selected() -> None:
    state = InputState()
    state.joystick_button_down(0, 0)  # Joy1 Up pressed
    psg = PSG(_input=state)
    mouse = _FakeMouse()
    mouse.read_value = 0xFF  # would show up if wrongly delegated
    psg._mouse = MouseSlot(mouse, JoystickPort.JOY2)  # mouse attached to Joy2
    psg.write_port(0xA0, 15)
    psg.write_port(0xA1, 0x00)  # JOY_SELECT=0 (Joy1 selected)
    psg.write_port(0xA0, 14)
    assert psg.read_port(0xA2) & 0x01 == 0  # Joy1 Up bit from InputState, not mouse


# ---------------------------------------------------------------------------
# Reset (allium/psg.allium rule Reset)
# ---------------------------------------------------------------------------

def test_reset_restores_register_defaults() -> None:
    psg = PSG()
    for i in range(16):
        psg.write_port(0xA0, i)
        psg.write_port(0xA1, 0xAB)
    psg.reset()
    assert psg.regs == [0] * 16
    assert psg.latch == 0


def test_reset_clears_pending_events() -> None:
    psg = PSG()
    psg._get_cycle = lambda: 5
    psg.write_port(0xA0, 8)
    psg.write_port(0xA1, 0x0F)
    assert psg._events
    psg.reset()
    assert psg._events == []


# ---------------------------------------------------------------------------
# Unaddressed ports (allium/psg.allium rule ReadUnaddressedPort)
# ---------------------------------------------------------------------------

def test_unmapped_data_write_port_read_returns_ff() -> None:
    """Port 0xA1 (data write) is write-only; a read returns open bus, same as
    0xA0 (test_unmapped_read_returns_ff already covers the address-latch port)."""
    psg = PSG()
    assert psg.read_port(0xA1) == 0xFF


# ---------------------------------------------------------------------------
# Hardware constants (allium/psg.allium config block)
# ---------------------------------------------------------------------------

def test_psg_clock_constant() -> None:
    assert PSG_CLOCK == 223722


def test_sample_rate_constant() -> None:
    assert SAMPLE_RATE == 44100


def test_max_events_constant() -> None:
    from msx.psg import _MAX_EVENTS
    assert _MAX_EVENTS == 4096


# ---------------------------------------------------------------------------
# Register-value masking (allium/psg.allium invariant RegistersAreEightBit)
# ---------------------------------------------------------------------------

def test_register_write_masks_out_of_range_value_to_eight_bits() -> None:
    psg = PSG()
    psg.write_port(0xA0, 8)
    psg.write_port(0xA1, 0x1FF)  # out-of-range int; write_port masks with & 0xFF
    assert psg.regs[8] == 0xFF
