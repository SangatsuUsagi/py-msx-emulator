from msx.cpu.z80 import Z80
from msx.io import IOBus
from msx.mapper import FlatMapper
from msx.memory import Memory


def test_unregistered_read_returns_ff() -> None:
    bus = IOBus()
    assert bus.read_port(0x00) == 0xFF


def test_unregistered_write_is_noop() -> None:
    bus = IOBus()
    bus.write_port(0x00, 0xFF)  # must not raise


def test_read_handler_registered_and_dispatched() -> None:
    bus = IOBus()
    called: list[int] = []
    bus.register_read(0x98, 0x99, lambda p: called.append(p) or 0x42)  # type: ignore[func-returns-value]
    result = bus.read_port(0x98)
    assert called == [0x98]
    assert result == 0x42


def test_write_handler_registered_and_dispatched() -> None:
    bus = IOBus()
    calls: list[tuple[int, int]] = []
    bus.register_write(0x98, 0x99, lambda p, v: calls.append((p, v)))
    bus.write_port(0x99, 0x80)
    assert calls == [(0x99, 0x80)]


def test_multiple_devices_dispatched_independently() -> None:
    bus = IOBus()
    vdp_reads: list[int] = []
    psg_reads: list[int] = []
    bus.register_read(0x98, 0x99, lambda p: vdp_reads.append(p) or 0x11)  # type: ignore[func-returns-value]
    bus.register_read(0xA0, 0xA2, lambda p: psg_reads.append(p) or 0x22)  # type: ignore[func-returns-value]

    r1 = bus.read_port(0x98)
    r2 = bus.read_port(0xA2)

    assert r1 == 0x11 and vdp_reads == [0x98]
    assert r2 == 0x22 and psg_reads == [0xA2]


def test_port_outside_all_ranges_returns_ff() -> None:
    bus = IOBus()
    bus.register_read(0x98, 0x99, lambda p: 0x00)
    assert bus.read_port(0xA0) == 0xFF


def test_first_registered_handler_wins() -> None:
    bus = IOBus()
    bus.register_read(0x98, 0x99, lambda p: 0xAA)
    bus.register_read(0x98, 0x99, lambda p: 0xBB)
    assert bus.read_port(0x98) == 0xAA


def test_write_stops_at_first_matching_range() -> None:
    bus = IOBus()
    calls: list[int] = []
    bus.register_write(0x98, 0x99, lambda p, v: calls.append(1))
    bus.register_write(0x98, 0x99, lambda p, v: calls.append(2))
    bus.write_port(0x98, 0x00)
    assert calls == [1]


def test_16bit_port_read_masked_to_low_byte() -> None:
    # The Z80 drives a 16-bit port; the bus masks to 8 bits for device decode.
    bus = IOBus()
    seen: list[int] = []
    bus.register_read(0x98, 0x99, lambda p: seen.append(p) or 0x42)  # type: ignore[func-returns-value]
    assert bus.read_port(0x1298) == 0x42
    assert seen == [0x98]


def test_16bit_port_write_masked_to_low_byte() -> None:
    bus = IOBus()
    calls: list[tuple[int, int]] = []
    bus.register_write(0x98, 0x99, lambda p, v: calls.append((p, v)))
    bus.write_port(0x5099, 0x80)
    assert calls == [(0x99, 0x80)]


def test_register_read_with_backwards_range_never_matches() -> None:
    # start > end is never validated or rejected, but the range can then never
    # cover any port (start <= port <= end is unsatisfiable), so it is a dead
    # registration rather than an error.
    bus = IOBus()
    bus.register_read(0x99, 0x98, lambda p: 0x11)
    assert bus.read_port(0x98) == 0xFF
    assert bus.read_port(0x99) == 0xFF


def test_register_accepts_out_of_bounds_values_without_raising() -> None:
    # Neither register_read nor register_write validates that start/end lie
    # within [0, 255].
    bus = IOBus()
    bus.register_read(-10, -5, lambda p: 0x11)
    bus.register_write(-10, -5, lambda p, v: None)
    assert bus.read_port(0x00) == 0xFF
    bus.write_port(0x00, 0x00)  # must not raise


def test_read_only_registration_does_not_affect_write_dispatch() -> None:
    bus = IOBus()
    bus.register_read(0x98, 0x99, lambda p: 0xAA)
    bus.write_port(0x98, 0x00)  # no write handler registered: silent no-op
    assert bus.read_port(0x98) == 0xAA


def test_write_only_registration_does_not_affect_read_dispatch() -> None:
    bus = IOBus()
    calls: list[int] = []
    bus.register_write(0x98, 0x99, lambda p, v: calls.append(v))
    assert bus.read_port(0x98) == 0xFF  # no read handler registered: open bus
    bus.write_port(0x98, 0x55)
    assert calls == [0x55]


def test_io_bus_wired_to_z80_dispatches_in_and_out() -> None:
    # openspec/specs/io-bus/spec.md's "IOBus assigned to Z80" scenario:
    # cpu.read_port/write_port assigned to a real IOBus's own methods, and a
    # real Z80 IN/OUT instruction dispatches through to a registered handler.
    bus = IOBus()
    bus.register_read(0x98, 0x99, lambda p: 0x77)
    writes: list[int] = []
    bus.register_write(0x98, 0x99, lambda p, v: writes.append(v))

    rom = bytes([0xDB, 0x98, 0xD3, 0x99]) + bytes(32768 - 4)  # IN A,(0x98); OUT (0x99),A
    mem = Memory(rom=rom, ram=bytearray(32768), _mapper=FlatMapper(None))
    cpu = Z80(read_byte=mem.read, write_byte=mem.write)
    cpu.read_port = bus.read_port
    cpu.write_port = bus.write_port

    cpu.step()  # IN A,(0x98)
    assert cpu.registers.A == 0x77

    cpu.step()  # OUT (0x99),A
    assert writes == [0x77]
