"""V9938.snapshot_state() / parse_v9938_state() / restore_state() and the
shared first-byte latch.

Covers openspec machine-state "MSX2 snapshot carries V9938 state as one
validated object" at the VDP level (parse_v9938_state validates the whole
object; load_state rejects a bad one before restoring anything), and
allium/v9938.allium's first_byte / control_byte_pending /
palette_byte_pending model behind the read-only `latch` accessor.
"""
from __future__ import annotations

import copy
import json

import pytest

from msx.state import StateLoadError, load_state, save_state
from msx.vdp.v9938 import V9938, V9938StateError, parse_v9938_state
from tests.factories import make_machine_msx2

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000
_RGB_MSX2 = bytearray(256 * 192 * 3)


def _busy_vdp() -> V9938:
    """A V9938 with every V9938-only field away from its reset value."""
    vdp = V9938()
    vdp.palette[:] = [(i * 37) & 0x1FF for i in range(16)]
    vdp.cmd_regs[:] = [(0x10 + i) & 0xFF for i in range(15)]
    vdp._status1 = 0x01
    vdp._status2 = 0x81
    vdp._status8 = 0x34
    vdp._status9 = 0xFE
    vdp._cmd_remaining = 123
    vdp.collision_x = 0x1AB
    vdp.collision_y = 0x0CD
    vdp._first_byte = 0x75
    vdp._pal_pending = True
    vdp._cmd_active = True
    vdp._cmd_transfer = True
    vdp._cmd_code = 0x0F
    vdp._cmd_log = 0x03
    vdp._cmd_sx = 10
    vdp._cmd_sy = 20
    vdp._cmd_dx = 30
    vdp._cmd_dy = 40
    vdp._cmd_nx = 50
    vdp._cmd_ny = 60
    vdp._cmd_x = 11
    vdp._cmd_y = 21
    vdp._cmd_xstep = -1
    vdp._cmd_ystep = 1
    vdp._cmd_bpl = 128
    vdp._cmd_ppb = 2
    vdp._cmd_bpp = 4
    vdp._tr_delay = 7
    return vdp


# ---------------------------------------------------------------------------
# snapshot_state / restore_state
# ---------------------------------------------------------------------------

def test_snapshot_restore_round_trips_every_field_into_a_fresh_vdp() -> None:
    state = _busy_vdp().snapshot_state()
    fresh = V9938()

    fresh.restore_state(parse_v9938_state(json.loads(json.dumps(state))))  # as on disk

    assert fresh.snapshot_state() == state


def test_snapshot_is_detached_from_the_live_lists() -> None:
    vdp = _busy_vdp()
    state = vdp.snapshot_state()
    vdp.palette[1] = 0
    vdp.cmd_regs[0] = 0
    assert state["palette"][1] == 37
    assert state["cmd_regs"][0] == 0x10


def _corrupt(state: dict, path: tuple[str, ...], value: object) -> dict:
    bad = copy.deepcopy(state)
    target = bad
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return bad


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("cmd_engine", "tr_delay"), "x"),       # last engine field, wrong type
        (("cmd_engine", "bpp"), 0),              # below range
        (("cmd_engine", "ystep"), 2),            # above range
        (("cmd_engine", "active"), 1),           # int where a bool is expected
        (("cmd_engine", "ny"), KeyError),        # missing engine key
        (("cmd_engine", "sx"), 0x200),           # SX is 9 bits
        (("cmd_engine", "sy"), 0x400),           # SY is 10 bits
        (("cmd_engine", "nx"), 0x201),           # clipped NX <= 512 dots
        (("cmd_engine", "y"), 0x401),            # cursor <= 1024 lines
        (("cmd_engine", "tr_delay"), 1 << 31),   # above i32
        (("palette_pending",), KeyError),        # missing top-level key
        (("control_pending",), 0),               # int where a bool is expected
        (("cmd_regs",), [0] * 14),               # wrong list length
        (("collision_x",), 0x400),               # out of range
    ],
)
def test_parse_rejects_a_malformed_object(path, value) -> None:
    bad = _corrupt(dict(_busy_vdp().snapshot_state()), path, value)
    with pytest.raises(V9938StateError):
        parse_v9938_state(bad)


@pytest.mark.parametrize("where", [(), ("cmd_engine",)])
def test_parse_rejects_unknown_keys(where) -> None:
    bad = copy.deepcopy(dict(_busy_vdp().snapshot_state()))
    target = bad
    for key in where:
        target = target[key]
    target["extra"] = 0
    with pytest.raises(ValueError, match="unknown keys"):
        parse_v9938_state(bad)


@pytest.mark.parametrize("value", [[1, 2, 3], None, "x"])
def test_parse_rejects_a_non_object(value) -> None:
    bad = dict(_busy_vdp().snapshot_state())
    bad["cmd_engine"] = value
    with pytest.raises(ValueError):
        parse_v9938_state(bad)
    with pytest.raises(ValueError):
        parse_v9938_state(value)


def test_parse_accepts_the_snapshot_of_a_busy_vdp() -> None:
    state = _busy_vdp().snapshot_state()
    assert parse_v9938_state(json.loads(json.dumps(state))) == state


def test_restore_state_leaves_the_irq_level_to_the_caller() -> None:
    # restore_state documents that the caller re-derives the /INT level.
    state = _busy_vdp().snapshot_state()     # S#1 FH set
    vdp = V9938()
    vdp.regs[0] = 0x10                        # IE1
    vdp.restore_state(state)
    assert vdp.irq is False
    vdp.update_irq()
    assert vdp.irq is True


def test_msx2_file_with_null_vdp9938_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["vdp9938"] = None
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(StateLoadError, match="vdp9938"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# Shared first-byte latch: `latch` accessor
# ---------------------------------------------------------------------------

def test_latch_reads_none_until_a_control_first_byte() -> None:
    vdp = V9938()
    assert vdp.latch is None
    vdp.write_port(0x9A, 0x75)            # palette first byte only
    assert vdp.latch is None
    vdp.write_port(0x99, 0x34)
    assert vdp.latch == 0x34


def test_latch_is_read_only() -> None:
    vdp = V9938()
    with pytest.raises(AttributeError):
        vdp.latch = 0x12  # type: ignore[misc]


def test_status_read_drops_only_the_control_pair() -> None:
    vdp = V9938()
    vdp.write_port(0x9A, 0x75)            # palette pair pending
    vdp.write_port(0x99, 0x34)            # control pair pending, shares 0x34
    vdp.read_port(0x99)

    assert vdp.latch is None
    assert vdp._pal_pending is True
    assert vdp._first_byte == 0x34        # the shared byte is kept
    vdp.write_port(0x9A, 0x02)            # palette completes from 0x34
    entry = vdp.palette[0]
    assert ((entry >> 6) & 7, (entry >> 3) & 7, entry & 7) == (3, 2, 4)


def test_palette_first_byte_while_control_pending_changes_latch_value() -> None:
    vdp = V9938()
    vdp.write_port(0x99, 0x34)
    vdp.write_port(0x9A, 0x75)
    assert vdp.latch == 0x75
    assert vdp._ctrl_pending and vdp._pal_pending


def test_state_restore_of_latch_and_first_byte_agree(tmp_path, monkeypatch) -> None:
    # vdp9938 holds the whole latch; the common vdp_latch is its port-0x99 view.
    monkeypatch.chdir(tmp_path)
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    vdp.write_port(0x9A, 0x75)
    vdp.write_port(0x99, 0x34)
    save_state(machine, _RGB_MSX2, "test")

    vdp.reset()
    load_state(machine)

    assert vdp.latch == 0x34
    assert vdp._pal_pending is True
    assert vdp._first_byte == 0x34


def _edited_msx2_file(tmp_path, monkeypatch, edit):
    monkeypatch.chdir(tmp_path)
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.write_port(0x99, 0x34)    # control pair pending
    state_path = save_state(machine, _RGB_MSX2, "test")
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    edit(data)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return machine, state_path


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.__setitem__("vdp_latch", 0x35),                  # differs from first_byte
        lambda d: d.__setitem__("vdp_latch", None),                  # control_pending is true
        lambda d: d["vdp9938"].__setitem__("control_pending", False),  # vdp_latch is 0x34
    ],
)
def test_disagreeing_port_latch_is_rejected(tmp_path, monkeypatch, edit) -> None:
    machine, state_path = _edited_msx2_file(tmp_path, monkeypatch, edit)
    with pytest.raises(StateLoadError, match="vdp9938"):
        load_state(machine, state_path)


@pytest.mark.parametrize("value", [0x100, -1, "x", True])
def test_out_of_range_vdp_latch_is_rejected(tmp_path, monkeypatch, value) -> None:
    machine, state_path = _edited_msx2_file(
        tmp_path, monkeypatch, lambda d: d.__setitem__("vdp_latch", value)
    )
    with pytest.raises(StateLoadError, match="vdp"):
        load_state(machine, state_path)


def test_rejected_vdp9938_object_restores_nothing(tmp_path, monkeypatch) -> None:
    machine, state_path = _edited_msx2_file(
        tmp_path, monkeypatch, lambda d: d["vdp9938"]["cmd_engine"].__setitem__("bpp", 0)
    )
    machine.vdp.vram[0:4] = b"\x11\x22\x33\x44"
    machine.vdp.regs[7] = 0x5A
    machine.memory.ram[0] = 0x99
    with pytest.raises(StateLoadError, match="vdp9938"):
        load_state(machine, state_path)
    assert bytes(machine.vdp.vram[0:4]) == b"\x11\x22\x33\x44"
    assert machine.vdp.regs[7] == 0x5A
    assert machine.memory.ram[0] == 0x99


@pytest.mark.parametrize(
    "edit",
    [lambda d: d.__setitem__("extra_key", 1), lambda d: d.pop("vdp_regs")],
)
def test_other_top_level_layout_is_refused(tmp_path, monkeypatch, edit) -> None:
    machine, state_path = _edited_msx2_file(tmp_path, monkeypatch, edit)
    with pytest.raises(ValueError, match="incompatible state file layout"):
        load_state(machine, state_path)


@pytest.mark.parametrize(("where", "key"), [((), "palette"), (("cmd_engine",), "sx")])
def test_parse_names_a_missing_key_and_its_object(where, key) -> None:
    bad = copy.deepcopy(dict(_busy_vdp().snapshot_state()))
    target = bad
    for part in where:
        target = target[part]
    del target[key]
    label = where[-1] if where else "vdp9938"
    with pytest.raises(V9938StateError, match=rf"{label} is missing keys \['{key}'\]"):
        parse_v9938_state(bad)


def test_layout_is_checked_before_mapper_kinds_are_decoded(tmp_path, monkeypatch) -> None:
    def edit(d):
        d["extra_key"] = 1
        d["mapper_kind"] = "no-such-mapper"
    machine, state_path = _edited_msx2_file(tmp_path, monkeypatch, edit)
    with pytest.raises(ValueError, match="incompatible state file layout"):
        load_state(machine, state_path)
