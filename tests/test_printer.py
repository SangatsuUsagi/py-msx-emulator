"""msx/printer.py (io-printer) and the printer port in the saved state
(machine-state "The printer port state is saved")."""
from __future__ import annotations

import dataclasses
import json

import pytest

from host.machine_config import build_machine_from_spec
from msx import state
from msx.boundary import PRINTER_BUFFER_SIZE, STATUS_NO_PRINTER_PORT, STATUS_OK
from msx.machine import Machine
from msx.machine_spec import PrinterPortForm
from msx.printer import PrinterPort
from msx.state import StateLoadError
from tests.factories import _msx1_spec, make_machine

_ROM = bytes(0x8000)


def _port(form: PrinterPortForm = PrinterPortForm.PLAIN) -> PrinterPort:
    port = PrinterPort(form)
    port.attach()
    return port


def _pulse(port: PrinterPort, status_port: int = 0x90) -> None:
    port.write_port(status_port, 0x00)
    port.write_port(status_port, 0x01)


def _send(port: PrinterPort, value: int) -> None:
    port.write_port(0x91, value)
    _pulse(port)


def _machine(form: PrinterPortForm = PrinterPortForm.PLAIN) -> Machine:
    spec = dataclasses.replace(_msx1_spec(), printer_port=form)
    return build_machine_from_spec(spec, bios_override=_ROM)


def _printer(machine: Machine) -> PrinterPort:
    assert machine.printer is not None
    return machine.printer


# -- decode ---------------------------------------------------------------


def test_the_s1985_form_mirrors_the_port() -> None:
    machine = _machine(PrinterPortForm.S1985)
    _printer(machine).attach()
    machine.io.write_port(0x95, 0x41)
    machine.io.write_port(0x94, 0x00)
    machine.io.write_port(0x94, 0x01)
    assert _printer(machine).drain() == b"\x41"


def test_the_plain_form_has_no_mirror() -> None:
    machine = _machine(PrinterPortForm.PLAIN)
    _printer(machine).attach()
    machine.io.write_port(0x91, 0x41)
    machine.io.write_port(0x94, 0x00)
    assert _printer(machine).drain() == b""
    assert machine.io.read_port(0x94) == 0xFF


def test_a_machine_with_no_port() -> None:
    machine = _machine(PrinterPortForm.NONE)
    machine.io.write_port(0x91, 0x41)
    machine.io.write_port(0x90, 0x00)
    assert machine.printer is None
    assert machine.io.read_port(0x90) == 0xFF


def test_a_data_write_delivers_nothing() -> None:
    port = _port()
    port.write_port(0x91, 0x41)
    assert port.drain() == b""


@pytest.mark.parametrize("form", [PrinterPortForm.PLAIN, PrinterPortForm.S1985])
def test_a_data_port_read(form: PrinterPortForm) -> None:
    assert _port(form).read_port(0x91) == 0xFF


def test_the_s1985_upper_ports_do_nothing() -> None:
    port = _port(PrinterPortForm.S1985)
    port.write_port(0x91, 0x41)
    port.write_port(0x92, 0x42)
    port.write_port(0x93, 0x00)
    assert port.drain() == b""
    _pulse(port)
    assert port.drain() == b"\x41"


# -- STROBE ---------------------------------------------------------------


def test_one_byte_per_pulse() -> None:
    port = _port()
    _send(port, 0x41)
    assert port.drain() == b"\x41"


def test_a_repeated_low_write_does_not_repeat_the_byte() -> None:
    port = _port()
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x00)
    port.write_port(0x90, 0x00)
    assert port.drain() == b"\x41"


def test_the_other_status_bits_are_ignored() -> None:
    port = _port()
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0xFF)
    assert port.drain() == b""
    port.write_port(0x90, 0xFE)
    assert port.drain() == b"\x41"


def test_bytes_keep_their_order() -> None:
    port = _port()
    _send(port, 0x1B)
    _send(port, 0x4B)
    assert port.drain() == b"\x1B\x4B"


def test_the_data_latch_is_eight_bits() -> None:
    port = _port()
    _send(port, 0x141)
    assert port.drain() == b"\x41"


# -- status ---------------------------------------------------------------


def test_plain_form_with_no_printer() -> None:
    assert PrinterPort(PrinterPortForm.PLAIN).read_port(0x90) == 0xFF


def test_plain_form_with_a_printer() -> None:
    assert _port(PrinterPortForm.PLAIN).read_port(0x90) == 0xFD


@pytest.mark.parametrize("status_port", [0x90, 0x94])
def test_s1985_form_with_no_printer(status_port: int) -> None:
    assert PrinterPort(PrinterPortForm.S1985).read_port(status_port) == 0x02


def test_s1985_form_with_a_printer() -> None:
    assert _port(PrinterPortForm.S1985).read_port(0x90) == 0x00


# -- buffer ---------------------------------------------------------------


def test_a_full_buffer_reads_busy_and_recovers() -> None:
    port = _port()
    for _ in range(PRINTER_BUFFER_SIZE):
        _send(port, 0x41)
    assert port.read_port(0x90) == 0xFF
    port.drain()
    assert port.read_port(0x90) == 0xFD


def test_a_byte_over_the_capacity_is_dropped() -> None:
    port = _port()
    for _ in range(PRINTER_BUFFER_SIZE):
        _send(port, 0x41)
    _send(port, 0x42)
    assert port.drain() == b"\x41" * PRINTER_BUFFER_SIZE


def test_a_byte_with_no_printer_is_dropped() -> None:
    port = PrinterPort(PrinterPortForm.PLAIN)
    _send(port, 0x41)
    port.attach()
    assert port.drain() == b""


# -- reset ----------------------------------------------------------------


def test_a_reset_keeps_the_queued_bytes() -> None:
    machine = _machine()
    port = _printer(machine)
    port.attach()
    _send(port, 0x41)
    machine.reset()
    _send(port, 0x42)
    assert port.drain() == b"\x41\x42"


def test_a_reset_does_not_deliver_a_byte() -> None:
    machine = _machine()
    port = _printer(machine)
    port.attach()
    port.write_port(0x91, 0x41)
    port.write_port(0x90, 0x00)
    port.drain()
    machine.reset()
    assert port.drain() == b""
    # STROBE is 1 again and the latch is 0, so the next low write delivers 0.
    port.write_port(0x90, 0x00)
    assert port.drain() == b"\x00"


# -- saved state ----------------------------------------------------------


def test_the_lines_are_restored() -> None:
    source = _machine()
    source.io.write_port(0x91, 0x41)
    source.io.write_port(0x90, 0x00)
    target = _machine()
    _printer(target).attach()
    assert target.load_state(source.save_state()) == 0
    target.io.write_port(0x90, 0x01)
    assert _printer(target).drain() == b""
    target.io.write_port(0x90, 0x00)
    assert _printer(target).drain() == b"\x41"


def test_the_attachment_does_not_change_the_state() -> None:
    plain = _machine()
    attached = _machine()
    _printer(attached).attach()
    for machine in (plain, attached):
        machine.io.write_port(0x91, 0x41)
        machine.io.write_port(0x90, 0x00)
    assert plain.save_state() == attached.save_state()


def test_a_state_loads_without_a_printer() -> None:
    source = _machine()
    _printer(source).attach()
    target = _machine()
    assert target.load_state(source.save_state()) == 0
    assert target.io.read_port(0x90) == 0xFF


def test_a_state_load_keeps_the_buffer() -> None:
    machine = _machine()
    port = _printer(machine)
    saved = machine.save_state()
    port.attach()
    _send(port, 0x41)
    assert machine.load_state(saved) == 0
    assert port.drain() == b"\x41"


@pytest.mark.parametrize(("field", "value"), [("strobe", 2), ("data", 256)])
def test_a_value_out_of_range_is_rejected(field: str, value: int) -> None:
    machine = _machine()
    decoded = json.loads(machine.save_state())
    decoded["printer"][field] = value
    before = machine.save_state()
    with pytest.raises(StateLoadError, match=f"printer.{field}"):
        state.load_state(machine, json.dumps(decoded).encode("utf-8"))
    assert machine.save_state() == before


def test_a_machine_with_no_port_saves_null() -> None:
    decoded = json.loads(make_machine(_ROM).save_state())
    assert decoded["printer"] is None


# -- core API (machine-core) ----------------------------------------------


def _strobe(machine: Machine, value: int) -> None:
    machine.io.write_port(0x91, value)
    machine.io.write_port(0x90, 0x00)
    machine.io.write_port(0x90, 0x01)


def test_attach_on_a_machine_with_a_port() -> None:
    machine = _machine()
    assert machine.attach_printer() == STATUS_OK
    assert machine.io.read_port(0x90) == 0xFD


def test_a_second_attach_keeps_the_queued_bytes() -> None:
    machine = _machine()
    assert machine.attach_printer() == STATUS_OK
    _strobe(machine, 0x41)
    assert machine.attach_printer() == STATUS_OK
    assert machine.drain_printer() == b"\x41"


def test_attach_on_a_machine_with_no_port() -> None:
    machine = _machine(PrinterPortForm.NONE)
    assert machine.attach_printer() == STATUS_NO_PRINTER_PORT
    assert machine.last_error_text() != b""
    assert machine.drain_printer() == b""


def test_a_drain_empties_the_buffer() -> None:
    machine = _machine()
    machine.attach_printer()
    _strobe(machine, 0x41)
    _strobe(machine, 0x42)
    assert machine.drain_printer() == b"\x41\x42"
    assert machine.drain_printer() == b""


def test_the_full_buffer_through_the_core_api() -> None:
    machine = _machine()
    machine.attach_printer()
    for _ in range(PRINTER_BUFFER_SIZE + 1):
        _strobe(machine, 0x41)
    assert machine.io.read_port(0x90) == 0xFF
    assert machine.drain_printer() == b"\x41" * PRINTER_BUFFER_SIZE
    assert machine.io.read_port(0x90) == 0xFD
