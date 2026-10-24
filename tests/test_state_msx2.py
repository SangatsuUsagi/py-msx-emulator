"""Tests for MSX2 save/load state functionality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.state_files import load_state, save_state
from msx.boundary import RgbFrame
from msx.state import StateLoadError
from tests.factories import make_machine, make_machine_msx2


def _load_state_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _rewrite_state_version(path: Path, version: int) -> None:
    data = _load_state_json(path)
    data["format_version"] = version
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

_ROM = b"\x00" * 0x8000
_EXTROM = b"\x00" * 0x4000
_RGB_MSX1 = RgbFrame(256, 192, bytes(256 * 192 * 3))
_RGB_MSX2 = RgbFrame(256, 192, bytes(256 * 192 * 3))


@pytest.fixture()
def saves_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "saves"


# ---------------------------------------------------------------------------
# MSX2 round-trip
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_vram(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.vram[0x100] = 0xAB
    machine.vdp.vram[0x1FFFF] = 0xCD
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.vram[0x100] = 0x00
    machine.vdp.vram[0x1FFFF] = 0x00
    load_state(machine)

    assert machine.vdp.vram[0x100] == 0xAB
    assert machine.vdp.vram[0x1FFFF] == 0xCD


def test_msx2_roundtrip_palette(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.palette[3] = 0b101_011_110
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.palette[3] = 0
    load_state(machine)

    assert machine.vdp.palette[3] == 0b101_011_110


def test_msx2_roundtrip_ram_mapper_banks(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.ram_mapper.banks[0] = 5
    machine.memory.ram_mapper.banks[2] = 7
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.ram_mapper.banks[0] = 0
    machine.memory.ram_mapper.banks[2] = 0
    load_state(machine)

    assert machine.memory.ram_mapper.banks[0] == 5
    assert machine.memory.ram_mapper.banks[2] == 7


def test_msx2_roundtrip_ram_mapper_ram(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.ram_mapper.ram[0x8000] = 0x77
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.ram_mapper.ram[0x8000] = 0x00
    load_state(machine)

    assert machine.memory.ram_mapper.ram[0x8000] == 0x77


# ---------------------------------------------------------------------------
# machine_type mismatch raises ValueError
# ---------------------------------------------------------------------------

def test_msx1_load_into_msx2_raises(saves_dir):
    msx1 = make_machine(_ROM)
    save_state(msx1, _RGB_MSX1, "test")

    msx2 = make_machine_msx2(_ROM, _EXTROM)
    with pytest.raises(ValueError, match="machine type"):
        load_state(msx2)


def test_msx2_load_into_msx1_raises(saves_dir):
    msx2 = make_machine_msx2(_ROM, _EXTROM)
    save_state(msx2, _RGB_MSX2, "test")

    msx1 = make_machine(_ROM)
    with pytest.raises(ValueError, match="machine type"):
        load_state(msx1)


# ---------------------------------------------------------------------------
# format_version 1 rejected
# ---------------------------------------------------------------------------

def test_old_format_version_rejected(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    _rewrite_state_version(state_path, 1)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# sub_slot_reg round-trip
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_sub_slot_reg(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.set_sub_slot_reg(0xA5)
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.set_sub_slot_reg(0x00)
    load_state(machine)

    assert machine.memory.sub_slot_reg == 0xA5


def test_msx1_sub_slot_reg_none_in_snapshot(saves_dir):
    """MSX1 snapshots store no slot 3 secondary slot register."""
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    data = _load_state_json(state_path)
    assert data["slots"]["slot3"]["secondary_slot_register"] is None


# ---------------------------------------------------------------------------
# slot2_sub_slot_reg round-trip (independent of sub_slot_reg; gated on
# memory.slot2_sub_slot_enabled, not MSX1/MSX2-ness -- no machine-loader
# wiring exists yet to build one via make_machine_msx2, so the flag is set
# directly for test purposes, matching how it has no setter in msx/memory.py)
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_slot2_sub_slot_reg(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.slot2_sub_slot_enabled = True
    machine.memory.set_slot2_sub_slot_reg(0x5A)
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.set_slot2_sub_slot_reg(0x00)
    load_state(machine)

    assert machine.memory.slot2_sub_slot_reg == 0x5A


def test_slot2_sub_slot_disabled_none_in_snapshot(saves_dir):
    """A machine without an expanded slot 2 (every machine without an
    HBI-J1-style extension, the default) stores no slot 2 secondary slot
    register and no sub-slots."""
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")

    data = _load_state_json(state_path)
    assert data["slots"]["slot2"]["secondary_slot_register"] is None
    assert data["slots"]["slot2"]["subslots"] is None


# ---------------------------------------------------------------------------
# slot1_sub_slot_reg round-trip (openspec/changes/slot1-slot2-symmetrization
# -- the identical mechanism the slot2 section above exercises, applied
# independently to slot 1; no machine-loader wiring exists yet to build one
# end-to-end via --extension1, so the flag/sub-slot mappers are set directly
# for test purposes, matching the slot2 tests' own precedent)
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_slot1_sub_slot_reg(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.slot1_sub_slot_enabled = True
    machine.memory.set_slot1_sub_slot_reg(0x5A)
    save_state(machine, _RGB_MSX2, "test")

    machine.memory.set_slot1_sub_slot_reg(0x00)
    load_state(machine)

    assert machine.memory.slot1_sub_slot_reg == 0x5A


def test_slot1_sub_slot_disabled_none_in_snapshot(saves_dir):
    """A machine without an expanded slot 1 (every machine today, since
    --extension1 does not yet support expanded-shape overlays) stores
    no slot 1 secondary slot register and no sub-slots."""
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")

    data = _load_state_json(state_path)
    assert data["slots"]["slot1"]["secondary_slot_register"] is None
    assert data["slots"]["slot1"]["subslots"] is None


def test_msx2_roundtrip_mapper1_subslot_content(saves_dir):
    """mapper1_subslot_kinds/mapper1_subslot_states round-trip real
    sub-slot mapper bank-register state, not just the raw register."""
    from msx.mapper import Ascii8Mapper

    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.slot1_sub_slot_enabled = True
    original = Ascii8Mapper(bytes(16 * 8192))
    original.write(0x6000, 5)
    machine.memory._mapper1_subslots[0] = original
    save_state(machine, _RGB_MSX2, "test")

    machine.memory._mapper1_subslots[0] = Ascii8Mapper(bytes(16 * 8192))
    load_state(machine)

    restored = machine.memory._mapper1_subslots[0]
    assert isinstance(restored, Ascii8Mapper)
    assert restored._banks[0] == 5


def test_loading_state_with_mismatched_slot1_subslot_kind_raises(saves_dir):
    from msx.mapper import Ascii8Mapper, FlatMapper

    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.memory.slot1_sub_slot_enabled = True
    machine.memory._mapper1_subslots[0] = FlatMapper(bytes(32768))
    save_state(machine, _RGB_MSX2, "test")

    machine.memory._mapper1_subslots[0] = Ascii8Mapper(bytes(16384))
    with pytest.raises(ValueError, match="slot 1 sub-slot 0"):
        load_state(machine)


# ---------------------------------------------------------------------------
# format_version 2 rejected (version bump to 3)
# ---------------------------------------------------------------------------

def test_format_version_2_rejected(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    _rewrite_state_version(state_path, 2)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# format_version 3 rejected (version bump to 4)
# ---------------------------------------------------------------------------

def test_format_version_3_rejected(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    _rewrite_state_version(state_path, 3)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


# ---------------------------------------------------------------------------
# cmd_regs / status2 round-trip
# ---------------------------------------------------------------------------

def test_msx2_roundtrip_cmd_regs(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.cmd_regs[0] = 0x12   # SX low
    machine.vdp.cmd_regs[4] = 0xAB   # DX low
    machine.vdp.cmd_regs[14] = 0xF0  # CMR
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.cmd_regs[0] = 0
    machine.vdp.cmd_regs[4] = 0
    machine.vdp.cmd_regs[14] = 0
    load_state(machine)

    assert machine.vdp.cmd_regs[0] == 0x12
    assert machine.vdp.cmd_regs[4] == 0xAB
    assert machine.vdp.cmd_regs[14] == 0xF0


def test_msx2_roundtrip_status2(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp._status2 = 0x81  # CE=1, TR=1
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp._status2 = 0
    load_state(machine)

    assert machine.vdp._status2 == 0x81


def test_msx1_snapshot_has_no_vdp9938_object(saves_dir):
    machine = make_machine(_ROM)
    state_path = save_state(machine, _RGB_MSX1, "test")

    assert _load_state_json(state_path)["vdp"]["v9938"] is None


def test_msx2_roundtrip_cmd_remaining(saves_dir):
    machine = make_machine_msx2(_ROM, _ROM)
    machine.vdp._cmd_remaining = 1000
    machine.vdp._status2 = 0x01  # CE=1

    save_state(machine, _RGB_MSX2, "test")
    machine.vdp._cmd_remaining = 0
    machine.vdp._status2 = 0
    load_state(machine)

    assert machine.vdp._cmd_remaining == 1000
    assert machine.vdp._status2 & 0x01


# ---------------------------------------------------------------------------
# MSX1 save/load unaffected
# ---------------------------------------------------------------------------

def test_msx1_roundtrip_unaffected(saves_dir):
    machine = make_machine(_ROM)
    machine.cpu.registers.A = 0x55
    machine.memory.ram[10] = 0x99
    save_state(machine, _RGB_MSX1, "test")

    machine.cpu.registers.A = 0x00
    machine.memory.ram[10] = 0x00
    load_state(machine)

    assert machine.cpu.registers.A == 0x55
    assert machine.memory.ram[10] == 0x99


def test_msx2_roundtrip_vdp_port_state(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.write_port(0x99, 0x56)  # control pair pending
    machine.vdp.addr = 0x1A256          # 17-bit pointer
    machine.vdp.read_buf = 0xAB
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.read_port(0x99)         # drops the pending pair
    machine.vdp.addr = 0
    machine.vdp.read_buf = 0
    load_state(machine)

    assert machine.vdp.latch == 0x56
    assert machine.vdp.addr == 0x1A256
    assert machine.vdp.read_buf == 0xAB

def test_msx2_roundtrip_pending_palette_byte(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.regs[16] = 0
    machine.vdp.write_port(0x9A, 0x75)  # palette first byte pending
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp._first_byte = 0x11
    machine.vdp._pal_pending = False
    load_state(machine)

    assert machine.vdp._pal_pending and machine.vdp._first_byte == 0x75
    machine.vdp.write_port(0x9A, 0x03)
    entry = machine.vdp.palette[0]
    assert ((entry >> 6) & 7, (entry >> 3) & 7, entry & 7) == (7, 3, 5)


def test_msx2_load_clears_stale_pending_palette_byte(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    save_state(machine, _RGB_MSX2, "test")  # no palette pair pending

    machine.vdp.write_port(0x9A, 0x75)
    load_state(machine)

    assert machine.vdp._pal_pending is False


def test_msx2_v9938_state_is_one_object(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    data = _load_state_json(save_state(machine, _RGB_MSX2, "test"))

    assert isinstance(data["vdp"]["v9938"], dict)
    for key in ("palette", "cmd_regs", "status2", "cmd_remaining", "collision_x",
                "status1", "cmd_engine"):
        assert key not in data["vdp"]


def test_new_save_uses_format_version_18(saves_dir):
    # machine-state "New save uses the current format version 18".
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")

    assert _load_state_json(state_path)["format_version"] == 18


def test_format_version_17_rejected(saves_dir):
    # machine-state "An earlier format version is rejected".
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")

    _rewrite_state_version(state_path, 17)

    with pytest.raises(ValueError, match="17"):
        load_state(machine, state_path)


def test_format_version_13_rejected(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")

    _rewrite_state_version(state_path, 13)

    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


def test_msx2_roundtrip_collision_coordinates(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    machine.vdp.collision_x = 0x123      # 9-bit X
    machine.vdp.collision_y = 0x2AB      # 10-bit Y
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.collision_x = 0
    machine.vdp.collision_y = 0
    load_state(machine)

    assert (machine.vdp.collision_x, machine.vdp.collision_y) == (0x123, 0x2AB)
    machine.vdp.regs[15] = 3
    assert machine.vdp.read_port(0x99) == 0x23   # S#3: X low


def _start_hmmc_8x1(vdp) -> None:
    """HMMC to (0,0), 8x1 dots in GRAPHIC4 = 4 CPU bytes, no CLR preload."""
    vdp.regs[0] = 0x06
    vdp.regs[1] = 0x40
    vdp.cmd_regs[4:12] = [0, 0, 0, 0, 8, 0, 1, 0]   # DX, DY, NX, NY
    vdp.cmd_regs[13] = 0                               # ARG
    vdp.write_port(0x99, 0xF0)                         # R#46 := HMMC
    vdp.write_port(0x99, 0x80 | 46)


def _hmmc_byte(vdp, value: int) -> None:
    vdp.write_port(0x99, value)                        # R#44 := value
    vdp.write_port(0x99, 0x80 | 44)
    vdp.tick(8)                                        # engine re-asserts TR


def test_msx2_hmmc_transfer_resumes_after_save_load(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    _start_hmmc_8x1(vdp)
    _hmmc_byte(vdp, 0x11)
    _hmmc_byte(vdp, 0x22)
    save_state(machine, _RGB_MSX2, "test")

    vdp.reset()                                        # disturb the live engine
    load_state(machine)
    assert vdp._status2 & 0x81 == 0x81                 # CE and TR still set
    _hmmc_byte(vdp, 0x33)
    assert vdp._status2 & 0x80, "TR must come back between bytes"
    _hmmc_byte(vdp, 0x44)

    assert bytes(vdp.vram[0:4]) == b"\x11\x22\x33\x44"
    assert not (vdp._status2 & 0x01), "CE clears after the last byte"


def test_msx2_roundtrip_status1_and_srch_result(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    vdp.regs[0] = 0x10                                 # IE1
    vdp._status1 = 0x01                                # FH pending
    vdp._status8 = 0x34
    vdp._status9 = 0xFF
    save_state(machine, _RGB_MSX2, "test")

    vdp._status1 = 0
    vdp._status8 = 0
    vdp._status9 = 0
    vdp.irq = False
    load_state(machine)

    assert vdp.irq is True
    vdp.regs[15] = 8
    assert vdp.read_port(0x99) == 0x34
    vdp.regs[15] = 9
    assert vdp.read_port(0x99) == 0xFF
    vdp.regs[15] = 1
    assert vdp.read_port(0x99) & 0x01


def test_msx2_lmmc_transfer_resumes_with_saved_logop_and_bpp(saves_dir):
    # GRAPHIC5 (2 bpp, 4 dots per byte), LMMC 4x1 at (0,0) with LOG = OR.
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    vdp.regs[0] = 0x08
    vdp.regs[1] = 0x40
    vdp.vram[0] = 0x55                                 # every dot = 1
    vdp.cmd_regs[4:12] = [0, 0, 0, 0, 4, 0, 1, 0]      # DX, DY, NX, NY
    vdp.cmd_regs[13] = 0
    vdp.write_port(0x99, 0xB2)                         # R#46 := LMMC | OR
    vdp.write_port(0x99, 0x80 | 46)
    _hmmc_byte(vdp, 0xFE)                              # 0xFE & 3 = 2; 2 OR 1 = 3
    _hmmc_byte(vdp, 0xFE)
    save_state(machine, _RGB_MSX2, "test")

    vdp.reset()
    vdp._cmd_log = 0                                   # IMP
    vdp._cmd_bpl, vdp._cmd_ppb, vdp._cmd_bpp = 256, 1, 8  # GRAPHIC7 geometry
    vdp._cmd_nx = 1
    load_state(machine)
    assert vdp._status2 & 0x81 == 0x81
    _hmmc_byte(vdp, 0xFE)
    _hmmc_byte(vdp, 0xFE)

    assert vdp.vram[0] == 0xFF, "OR and the 2-bpp mask must survive the load"
    assert vdp.vram[1] == 0x00
    assert not (vdp._status2 & 0x01), "CE clears after the last dot"


def test_msx2_lmcm_transfer_resumes_from_saved_source_cursor(saves_dir):
    # GRAPHIC4, LMCM 2x2 from (2,1): dots 1, 2 on row 1 and 3, 4 on row 2.
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    vdp.regs[0] = 0x06
    vdp.regs[1] = 0x40
    vdp.vram[128 + 1] = 0x12
    vdp.vram[256 + 1] = 0x34
    vdp.cmd_regs[0:4] = [2, 0, 1, 0]                   # SX, SY
    vdp.cmd_regs[8:12] = [2, 0, 2, 0]                  # NX, NY
    vdp.cmd_regs[13] = 0
    vdp.write_port(0x99, 0xA0)                         # R#46 := LMCM
    vdp.write_port(0x99, 0x80 | 46)
    vdp.regs[15] = 7
    assert vdp.read_port(0x99) == 1
    save_state(machine, _RGB_MSX2, "test")

    vdp.reset()
    vdp._cmd_sx = 0
    vdp._cmd_sy = 0
    vdp._cmd_nx = 1
    vdp._cmd_ny = 1
    load_state(machine)
    assert vdp.regs[15] == 7
    assert vdp._status2 & 0x01

    assert [vdp.read_port(0x99) for _ in range(3)] == [2, 3, 4]
    assert not (vdp._status2 & 0x01), "CE clears after the last dot"
    assert vdp._cmd_active is False


def _start_hmmv_g4(vdp) -> None:
    """HMMV 8x2 dots (4 byte columns x 2 rows) at (0,0) in GRAPHIC4."""
    vdp.regs[0] = 0x06
    vdp.regs[1] = 0x40
    vdp.cmd_regs[4:12] = [0, 0, 0, 0, 8, 0, 2, 0]
    vdp.cmd_regs[12] = 0x77
    vdp.cmd_regs[13] = 0
    vdp.write_port(0x99, 0xC0)                         # R#46 := HMMV
    vdp.write_port(0x99, 0x80 | 46)


def test_msx2_timed_command_mid_countdown_keeps_ce_until_expiry(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    vdp = machine.vdp
    _start_hmmv_g4(vdp)
    vdp.tick(4)
    remaining = vdp._cmd_remaining
    assert remaining > 1
    save_state(machine, _RGB_MSX2, "test")

    vdp.reset()
    load_state(machine)

    assert vdp._cmd_remaining == remaining
    assert vdp._cmd_active is True
    assert vdp._status2 & 0x01
    vdp.tick(remaining - 1)
    assert vdp._status2 & 0x01, "CE holds until the saved countdown runs out"
    vdp.tick(1)
    assert not (vdp._status2 & 0x01)
    assert vdp._cmd_active is False


def _corrupt_vdp9938(state_path, corrupt: str) -> None:
    data = _load_state_json(state_path)
    v = data["vdp"]["v9938"]
    if corrupt == "missing_key":
        del v["status1"]
    elif corrupt == "engine_missing_key":
        del v["cmd_engine"]["sx"]
    elif corrupt == "engine_not_a_dict":
        v["cmd_engine"] = 5
    elif corrupt == "wrong_type":
        v["status2"] = "3"
    elif corrupt == "out_of_range":
        v["palette"][0] = 0x200
    else:  # bool where an int is expected
        v["cmd_engine"]["code"] = True
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.mark.parametrize(
    "corrupt",
    ["missing_key", "engine_missing_key", "engine_not_a_dict", "wrong_type",
     "out_of_range", "bool_for_int"],
)
def test_malformed_vdp9938_state_raises_state_load_error(saves_dir, corrupt):
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    _corrupt_vdp9938(state_path, corrupt)

    with pytest.raises(StateLoadError) as exc_info:
        load_state(machine, state_path)
    assert "vdp.v9938" in str(exc_info.value)


def test_malformed_vdp9938_state_leaves_vdp_untouched(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    _corrupt_vdp9938(state_path, "out_of_range")
    machine.vdp._status1 = 0x01

    with pytest.raises(StateLoadError):
        load_state(machine, state_path)
    assert machine.vdp._status1 == 0x01            # validated before any write


def test_other_layout_of_the_same_version_is_rejected_cleanly(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    data = _load_state_json(state_path)
    del data["vdp"]
    data["vdp_status1"] = 0                       # a key this layout does not define
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(ValueError, match="layout"):
        load_state(machine, state_path)


def test_vram_blob_roundtrips_byte_for_byte(saves_dir):
    machine = make_machine_msx2(_ROM, _EXTROM)
    # Write a recognisable pattern across the 128 KB VRAM.
    for i in range(0, len(machine.vdp.vram), 997):
        machine.vdp.vram[i] = (i * 7) & 0xFF
    expected = bytes(machine.vdp.vram)
    save_state(machine, _RGB_MSX2, "test")

    for i in range(len(machine.vdp.vram)):
        machine.vdp.vram[i] = 0
    load_state(machine)
    assert bytes(machine.vdp.vram) == expected


def _make_msx2_no_ram_mapper():
    """Build an MSX2 machine (V9938) that has no RAM mapper (ram_mapper is None)."""
    from host.machine_config import build_machine_from_spec
    from msx.machine_spec import MachineSpec, _RomEntry
    spec = MachineSpec(
        name="test_msx2_nomap",
        generation="msx2",
        rom_base_dir=Path("."),
        main_rom_entry=_RomEntry(file="", size_kb=0, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=_RomEntry(file="", size_kb=0, pages=[]),
        has_ram_mapper=False,
        ram_size_kb=32,
        has_v9938=True,
        has_rtc=True,
    )
    return build_machine_from_spec(spec, bios_override=_ROM, extrom_override=_EXTROM)


def test_msx2_roundtrip_without_ram_mapper(saves_dir):
    # An MSX2 machine with no RAM mapper must save/load without AttributeError.
    machine = _make_msx2_no_ram_mapper()
    assert machine.memory.ram_mapper is None
    machine.vdp.vram[0x100] = 0xAB
    machine.memory.ram[10] = 0x99
    save_state(machine, _RGB_MSX2, "test")

    machine.vdp.vram[0x100] = 0x00
    machine.memory.ram[10] = 0x00
    load_state(machine)

    assert machine.vdp.vram[0x100] == 0xAB
    assert machine.memory.ram[10] == 0x99


def test_load_wrong_format_version_raises_before_expand(saves_dir):
    # A wrong format_version is reported as such (validated before the layout
    # check, so it is not reported as unknown/missing keys).
    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    _rewrite_state_version(state_path, 999)
    with pytest.raises(ValueError, match="version"):
        load_state(machine, state_path)


def test_msx2_snapshot_without_vdp9938_is_rejected_before_restoring(saves_dir):
    from msx.state import StateLoadError

    machine = make_machine_msx2(_ROM, _EXTROM)
    state_path = save_state(machine, _RGB_MSX2, "test")
    data = _load_state_json(state_path)
    data["vdp"]["v9938"] = None
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    machine.memory.ram[0] = 0x5A           # must survive the rejected load

    with pytest.raises(StateLoadError, match="vdp.v9938"):
        load_state(machine, state_path)
    assert machine.memory.ram[0] == 0x5A
