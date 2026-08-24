from msx.mouse import (
    PHASE_XHIGH1,
    PHASE_XHIGH2,
    PHASE_XLOW1,
    PHASE_YHIGH1,
    PHASE_YLOW1,
    PHASE_YLOW2,
    MouseDevice,
)


def _drive(device: MouseDevice, *values: int) -> None:
    """Apply a sequence of pin-8 values, one write_pin8 call each."""
    for value in values:
        device.write_pin8(value)


def test_main_cycle_reports_real_delta() -> None:
    device = MouseDevice(_scale=1)
    device.add_motion(5, -1)
    # YLOW2 -> XHIGH1 (rising edge) latches the accumulated delta.
    device.write_pin8(1)
    assert device._phase == PHASE_XHIGH1
    high, low = (5 >> 4) & 0xF, 5 & 0xF
    assert device.read() & 0x0F == high
    device.write_pin8(0)  # XHIGH1 -> XLOW1 (falling edge)
    assert device.read() & 0x0F == low


def test_alternate_cycle_reports_zero_regardless_of_new_motion() -> None:
    device = MouseDevice(_scale=1)
    device.add_motion(10, 20)
    _drive(device, 1, 0, 1, 0)  # YLOW2 -> XHIGH1 -> XLOW1 -> YHIGH1 -> YLOW1
    assert device._phase == PHASE_YLOW1
    device.add_motion(99, 99)  # motion during the alternate cycle must be ignored
    device.write_pin8(1)  # YLOW1 -> XHIGH2 (forces zero delta)
    assert device._phase == PHASE_XHIGH2
    assert device.read() & 0x0F == 0
    device.write_pin8(0)  # XHIGH2 -> XLOW2
    assert device.read() & 0x0F == 0
    device.write_pin8(1)  # XLOW2 -> YHIGH2
    assert device.read() & 0x0F == 0
    device.write_pin8(0)  # YHIGH2 -> YLOW2
    assert device.read() & 0x0F == 0


def test_clamp_beyond_range_carries_remainder() -> None:
    device = MouseDevice(_scale=1)
    device.add_motion(200, 0)
    device.write_pin8(1)  # YLOW2 -> XHIGH1, latches clamped delta
    assert device._x_rel == 127
    assert device._cur_x_rel == 73  # 200 - 127 carried to next scan round


def test_negative_delta_encodes_two_complement() -> None:
    device = MouseDevice(_scale=1)
    device.add_motion(-1, 0)
    device.write_pin8(1)  # YLOW2 -> XHIGH1
    assert device.read() & 0x0F == 0xF
    device.write_pin8(0)  # XHIGH1 -> XLOW1
    assert device.read() & 0x0F == 0xF


def test_button_bits_independent_of_phase() -> None:
    device = MouseDevice(_scale=1)
    assert device.read() & 0x30 == 0x30  # both released
    device.set_button(4, True)  # left pressed
    assert device.read() & 0x30 == 0x20
    device.set_button(5, True)  # right pressed
    assert device.read() & 0x30 == 0x00
    device.set_button(4, False)  # left released
    assert device.read() & 0x30 == 0x10


def test_timeout_resync_before_processing_new_value() -> None:
    cycle = [0]
    device = MouseDevice(_scale=1)
    device.write_pin8(1, cycle[0])  # YLOW2 -> XHIGH1
    device.write_pin8(0, cycle[0])  # XHIGH1 -> XLOW1
    assert device._phase == PHASE_XLOW1
    cycle[0] = 100_000  # far beyond the ~5369-cycle timeout
    # Without resync this would advance XLOW1 -> YHIGH1; with resync the
    # phase first resets to YLOW2, then this same rising edge advances it
    # to XHIGH1 instead.
    device.write_pin8(1, cycle[0])
    assert device._phase == PHASE_XHIGH1


def test_normal_inter_nibble_timing_does_not_resync() -> None:
    cycle = [0]
    device = MouseDevice(_scale=1)
    device.write_pin8(1, cycle[0])  # YLOW2 -> XHIGH1
    cycle[0] = 100
    device.write_pin8(0, cycle[0])  # XHIGH1 -> XLOW1 (well within timeout)
    assert device._phase == PHASE_XLOW1
    cycle[0] = 200
    device.write_pin8(1, cycle[0])  # XLOW1 -> YHIGH1
    assert device._phase == PHASE_YHIGH1


def test_repeated_identical_pin8_value_is_noop() -> None:
    device = MouseDevice(_scale=1)
    device.write_pin8(0)  # matches initial _last_pin8 (0) — no-op
    assert device._phase == PHASE_YLOW2
    device.write_pin8(1)
    phase_after_first = device._phase
    device.write_pin8(1)  # repeated identical value
    assert device._phase == phase_after_first


def test_same_value_writes_refresh_the_idle_timer() -> None:
    # A same-value register-15 write still refreshes _last_cycle (openMSX
    # refreshes lastTime on every write()), so a train of same-value writes
    # spaced within the timeout -- even though the *cumulative* span since
    # the original edge exceeds it -- must not resync.
    cycle = [0]
    device = MouseDevice(_scale=1)
    device.write_pin8(1, cycle[0])  # YLOW2 -> XHIGH1
    assert device._phase == PHASE_XHIGH1
    for _ in range(3):
        cycle[0] += 3_000  # each gap within the ~5369-cycle timeout
        device.write_pin8(1, cycle[0])  # same value -- refreshes the timer only
    assert device._phase == PHASE_XHIGH1, "no resync: each gap was refreshed in time"


def test_add_motion_subpixel_remainder_accumulates() -> None:
    device = MouseDevice(_scale=3)
    device.add_motion(1, 0)
    assert device._cur_x_rel == 0
    device.add_motion(1, 0)
    assert device._cur_x_rel == 0
    device.add_motion(1, 0)
    assert device._cur_x_rel == 1


def test_noop_write_still_triggers_resync() -> None:
    # The timeout check runs unconditionally on every write_pin8 call,
    # before the same-value early return (matches openMSX's Mouse.cc write():
    # elapsed time is checked before the value is inspected), so a repeated
    # value after a long gap still resyncs to PHASE_YLOW2.
    cycle = [0]
    device = MouseDevice(_scale=1)
    device.write_pin8(1, cycle[0])  # YLOW2 -> XHIGH1
    assert device._phase == PHASE_XHIGH1
    cycle[0] = 100_000  # far beyond the timeout
    device.write_pin8(1, cycle[0])  # same value as last write -- resync still applies
    assert device._phase == PHASE_YLOW2


def test_resync_from_every_phase_lands_on_ylow2() -> None:
    # Completes transition-edge coverage: test_timeout_resync_before_processing_new_value
    # only exercises the resync edge starting from XLOW1. Every other phase
    # resyncs to YLOW2 the same way -- and since the resync forces phase to
    # YLOW2 *before* this same write's edge is evaluated, a rising edge
    # (value=1) immediately completes YLOW2's own edge too, landing one step
    # further at XHIGH1 in the same call; a falling edge (value=0) does not.
    from msx.mouse import PHASE_XLOW2, PHASE_YHIGH2

    cases = [
        (PHASE_XHIGH1, (1,), PHASE_YLOW2),                        # last write=1 -> next=0
        (PHASE_YHIGH1, (1, 0, 1), PHASE_YLOW2),                   # last write=1 -> next=0
        (PHASE_YLOW1, (1, 0, 1, 0), PHASE_XHIGH1),                # last write=0 -> next=1
        (PHASE_XHIGH2, (1, 0, 1, 0, 1), PHASE_YLOW2),             # last write=1 -> next=0
        (PHASE_XLOW2, (1, 0, 1, 0, 1, 0), PHASE_XHIGH1),          # last write=0 -> next=1
        (PHASE_YHIGH2, (1, 0, 1, 0, 1, 0, 1), PHASE_YLOW2),       # last write=1 -> next=0
    ]
    for start_phase, pin8_values, expected_after_resync in cases:
        cycle = [0]
        device = MouseDevice(_scale=1)
        for v in pin8_values:
            device.write_pin8(v, cycle[0])
        assert device._phase == start_phase

        cycle[0] = 100_000  # exceed the timeout
        next_value = 1 - pin8_values[-1]
        device.write_pin8(next_value, cycle[0])
        assert device._phase == expected_after_resync


def test_add_motion_negative_delta_uses_floor_division() -> None:
    # Locks in Python's flooring divmod semantics (quotient toward -inf,
    # remainder in [0, scale)) — a naive truncating '/'/'%' port would give
    # (-2, -1) here instead of (-3, 2), corrupting the sub-pixel carry.
    device = MouseDevice(_scale=3)
    device.add_motion(-7, 0)
    assert device._cur_x_rel == -3
    assert device._frac_x == 2
