"""load_state parses and validates the whole snapshot before restoring anything.

Covers openspec machine-state "The whole snapshot is parsed and validated
before anything is restored": every top-level value is checked for type,
range and length, and a rejected file leaves the running machine unchanged.
"""
from __future__ import annotations

import base64
import json

import pytest

from msx.state import StateLoadError, load_state, save_state
from tests.factories import make_machine, make_machine_msx2

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000
_RGB = bytearray(256 * 192 * 3)


def _blob(n: int) -> dict[str, str]:
    return {"__b64__": base64.b64encode(bytes(n)).decode("ascii")}


def _edited(tmp_path, monkeypatch, machine, edit):
    monkeypatch.chdir(tmp_path)
    state_path = save_state(machine, _RGB, "test")
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    edit(data)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return state_path


def _fingerprint(machine) -> tuple:
    r = machine.cpu.registers
    return (
        r.A, r.PC, r.SP, bytes(machine.memory.ram), bytes(machine.vdp.vram),
        list(machine.vdp.regs), list(machine.psg.regs),
    )


def _disturb(machine) -> None:
    machine.cpu.registers.A = 0x42
    machine.cpu.registers.PC = 0x1234
    machine.memory.ram[0] = 0x99
    machine.vdp.vram[0] = 0x77
    machine.vdp.regs[7] = 0x5A
    machine.psg.regs[0] = 0x33


def _set(key, value):
    return lambda d: d.__setitem__(key, value)


def _set_in(outer, key, value):
    return lambda d: d[outer].__setitem__(key, value)


_MSX1_CASES = [
    ("vdp_vram", _set("vdp_vram", _blob(10))),
    ("vdp_regs", _set("vdp_regs", [0, 0, 0])),
    ("vdp_regs", _set("vdp_regs", [0] * 7 + [0x100])),
    ("psg_regs", _set("psg_regs", [0] * 5)),
    ("psg_latch", _set("psg_latch", 16)),
    ("cpu_regs", lambda d: d["cpu_regs"].pop("A")),
    ("cpu_regs", _set_in("cpu_regs", "A", "x")),
    ("cpu_regs", _set_in("cpu_regs", "PC", 0x12345)),
    ("cpu_regs", _set_in("cpu_regs", "extra", 0)),
    ("cpu_im", _set("cpu_im", 3)),
    ("cpu_halted", _set("cpu_halted", 1)),
    ("slot_register", _set("slot_register", 0x100)),
    ("vdp_addr", _set("vdp_addr", 0x4000)),
    ("vdp_status", _set("vdp_status", -1)),
    ("vdp_frame_count", _set("vdp_frame_count", -1)),
    ("mapper_kind", _set("mapper_kind", "no-such-mapper")),
    ("mapper_state", _set("mapper_state", [1, 2])),
    ("psg_synth", _set("psg_synth", [1, 2])),
    ("ram", _set("ram", "not a blob")),
    ("machine_type", _set("machine_type", 5)),
    ("vdp9938", _set("vdp9938", {})),        # an MSX1 file carries no V9938 object
]


@pytest.mark.parametrize(("field", "edit"), _MSX1_CASES)
def test_malformed_msx1_value_is_rejected_before_restoring(
    tmp_path, monkeypatch, field, edit
) -> None:
    machine = make_machine(_ROM)
    state_path = _edited(tmp_path, monkeypatch, machine, edit)
    _disturb(machine)
    before = _fingerprint(machine)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)

    assert exc_info.value.producer == field
    assert _fingerprint(machine) == before


def test_cpu_register_error_names_the_register_path(tmp_path, monkeypatch) -> None:
    machine = make_machine(_ROM)
    state_path = _edited(tmp_path, monkeypatch, machine, _set_in("cpu_regs", "PC", 0x12345))
    with pytest.raises(StateLoadError, match=r"cpu_regs\.PC"):
        load_state(machine, state_path)


_MSX2_CASES = [
    ("vdp_vram", _set("vdp_vram", _blob(0x4000))),     # MSX1 size in an MSX2 file
    ("vdp_regs", _set("vdp_regs", [0] * 8)),
    ("vdp_addr", _set("vdp_addr", 0x20000)),
    ("ram_mapper_banks", _set("ram_mapper_banks", [0, 0, 0])),
    ("sub_slot_reg", _set("sub_slot_reg", 0x100)),
    ("vdp9938", _set("vdp9938", None)),
]


@pytest.mark.parametrize(("field", "edit"), _MSX2_CASES)
def test_malformed_msx2_value_is_rejected_before_restoring(
    tmp_path, monkeypatch, field, edit
) -> None:
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = _edited(tmp_path, monkeypatch, machine, edit)
    _disturb(machine)
    before = _fingerprint(machine)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)

    assert exc_info.value.producer == field
    assert _fingerprint(machine) == before


# ---------------------------------------------------------------------------
# Checks that need the running machine
# ---------------------------------------------------------------------------

def test_ram_of_the_wrong_size_for_the_machine_is_rejected(tmp_path, monkeypatch) -> None:
    machine = make_machine(_ROM)
    ram_len = len(machine.memory.ram)
    state_path = _edited(tmp_path, monkeypatch, machine, _set("ram", _blob(ram_len // 2)))
    _disturb(machine)
    before = _fingerprint(machine)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)

    assert exc_info.value.producer == "ram"
    assert len(machine.memory.ram) == ram_len
    assert _fingerprint(machine) == before


def test_ram_mapper_blob_of_the_wrong_size_is_rejected(tmp_path, monkeypatch) -> None:
    machine = make_machine_msx2(_ROM, _EXTROM)
    rm = machine.memory.ram_mapper
    assert rm is not None
    size = len(rm.ram)
    state_path = _edited(
        tmp_path, monkeypatch, machine, _set("ram_mapper_ram", _blob(size // 2))
    )
    _disturb(machine)
    before = _fingerprint(machine)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)

    assert exc_info.value.producer == "ram_mapper_ram"
    assert len(rm.ram) == size
    assert _fingerprint(machine) == before


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_set("fdc_state", {}), "FDC wiring mismatch"),
        (
            lambda d: d.update(
                mapper2_subslot_kinds=[None] * 4, mapper2_subslot_states=[{}] * 4
            ),
            "slot 2 expansion wiring mismatch",
        ),
    ],
)
def test_wiring_mismatch_leaves_the_machine_unchanged(
    tmp_path, monkeypatch, edit, message
) -> None:
    machine = make_machine(_ROM)
    state_path = _edited(tmp_path, monkeypatch, machine, edit)
    _disturb(machine)
    before = _fingerprint(machine)

    with pytest.raises(ValueError, match=message):
        load_state(machine, state_path)

    assert _fingerprint(machine) == before


def test_subslot_lists_must_come_in_pairs(tmp_path, monkeypatch) -> None:
    machine = make_machine(_ROM)
    state_path = _edited(
        tmp_path, monkeypatch, machine, _set("mapper2_subslot_kinds", [None] * 4)
    )
    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)
    assert exc_info.value.producer == "mapper2_subslot_states"


@pytest.mark.parametrize("factory", ["msx1", "msx2"])
def test_unmodified_file_round_trips(tmp_path, monkeypatch, factory) -> None:
    if factory == "msx1":
        machine = make_machine(_ROM)
    else:
        machine = make_machine_msx2(_ROM, _EXTROM)
    machine.cpu.registers.A = 0x5A
    machine.memory.ram[3] = 0x44
    state_path = _edited(tmp_path, monkeypatch, machine, lambda d: None)
    saved = _fingerprint(machine)
    _disturb(machine)

    load_state(machine, state_path)

    assert _fingerprint(machine) == saved
