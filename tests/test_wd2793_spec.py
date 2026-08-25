"""WD2793 obligations from allium/fdc-wd2793-core.allium.

Rule-by-rule coverage map (built by reading every test file under tests/
that touches msx/fdc/wd2793.py, directly or through SonyPhilipsInterface,
before writing anything here):

Already covered, no new test added for these:
  - WriteTrackRegister, WriteSectorRegister -- tests/test_wd2793.py::
    test_track_sector_register_round_trip
  - RestoreCommand -- test_wd2793.py::test_restore_seeks_to_track0 (also
    tests/test_fdc_interface.py::test_irq_status_byte_active_low)
  - SeekCommand -- test_wd2793.py::test_seek_moves_to_data_register_track
  - ReadSectorCommand, both failure branches -- test_wd2793.py::
    test_read_sector_streams_bytes_and_raises_intrq,
    test_record_not_found_for_missing_sector,
    test_no_disk_reports_not_ready,
    test_multi_sector_flag_decoded_as_single (also
    tests/test_fdc_acceptance.py, tests/test_fdc_interface.py)
  - WriteSectorCommand's write-protect branch, WriteDataRegisterDuring-
    SectorWrite -- test_wd2793.py::test_write_sector_persists_and_round_trips,
    test_write_protect_rejects_write_sector (also test_fdc_acceptance.py)
  - WriteTrackCommand's write-protect branch, WriteDataRegisterDuring-
    TrackWrite -- test_wd2793.py::test_write_track_blanks_track_to_e5,
    test_write_protect_rejects_write_track (also test_fdc_acceptance.py)
  - ReadDataRegisterDuringTransfer -- test_wd2793.py::
    test_read_sector_streams_bytes_and_raises_intrq
  - ReadStatusRegister (both the Type I/IV frozen-byte path and the Type
    II/III live DRQ patch), and INTRQ-clear-on-read -- test_wd2793.py::
    test_status_read_clears_intrq, test_read_sector_streams_bytes_...
  - ForceInterruptCommand's non-immediate case (partial: BUSY/mode only)
    -- test_wd2793.py::test_force_interrupt_aborts_busy_command
  - AbortTransfer (partial: BUSY/mode only, exercised through
    FloppyDisk.swap -> WD2793.abort()) -- tests/test_fdc_disk_swap.py::
    test_swap_aborts_in_progress_transfer
  - PositioningCommandCompleted, CommandEnded -- the spec's two internal
    chained rules, exercised transitively by every test above that reaches
    a Type I completion or a Type II/III end-of-command
  - Mutual exclusion among the eleven WriteCommandRegister-triggered rules
    (each rule's own rule-failure obligation) -- the source dispatches on
    disjoint high-nibble ranges, so the full set of command bytes already
    exercised across both files (0x00, 0x10, 0x20/0x40/0x60 below, 0x80,
    0x90, 0xA0, 0xC0/0xE0/0xF0 below, 0xD0/0xD8 below) is simultaneously a
    negative test for every other rule in the dispatch

Gaps this file fills:
  - StepCommand / StepInCommand / StepOutCommand -- untested anywhere,
    including the StepCommand Open-Questions wrap-at-track-0 quirk
  - WriteSectorCommand's not-ready branch -- test_no_disk_reports_not_ready
    only exercises READ SECTOR (0x80) with no disk, never WRITE SECTOR
    (0xA0); test_write_protect_rejects_write_sector covers a *mounted*
    write-protected disk, a different branch
  - WriteTrackCommand's not-ready branch -- same gap, for WRITE TRACK
  - ReadAddressCommand -- untested anywhere
  - ReadTrackCommand's stub behaviour -- untested anywhere
  - ForceInterruptCommand's immediate-interrupt case (0xD8) and its
    "every other status bit unchanged" property -- untested anywhere;
    the existing test only issues 0xD0 and only checks BUSY/mode
  - AbortTransfer's track/sector-preservation and INTRQ-untouched
    postconditions -- test_swap_aborts_in_progress_transfer checks BUSY
    and mode only
  - Reset -- no test anywhere calls WD2793.reset() (confirmed by grep
    across tests/); includes its as-built command_register divergence
    from GT/openMSX (0, not the Hex-03 latch)
  - ReadDataRegisterIdle -- both branches (idle, and reading DATA mid-write)
  - WriteDataRegisterIdle -- the "reading" branch (write mid-read-transfer
    must not disturb it); the "idle" branch is incidentally exercised
    wherever an existing test calls set_data() before issuing a command
    (e.g. SeekCommand's setup), but never asserted as its own round-trip
  - entity-optional.WD2793.drive's null case -- every existing WD2793()
    construction in tests/ is immediately wired into a SonyPhilipsInterface
    that connects a drive; drive=None is never observed as a lasting state
    (test_wd2793.py's "no disk" tests all pass a real DiskDrive() object)
  - config-default.* -- no existing test asserts the bit-position/size
    constants directly
  - invariant.RegistersAreBytes, invariant.StepDirectionIsSignum -- no
    property-based tests exist for either
"""
from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.wd2793 import (
    BUSY,
    NOT_READY,
    RECORD_NOT_FOUND,
    S_DRQ,
    SECTOR_SIZE,
    TRACK00,
    TRACK_BYTES,
    WD2793,
    WRITE_PROTECTED,
    Mode,
)

_2DD = 737280


def _ctrl(tmp_path: Path, *, fill: int = 0x00, write_protected: bool = False):
    p = tmp_path / "d.dsk"
    p.write_bytes(bytes([fill]) * _2DD)
    image = DskDiskImage(p, write_protected=write_protected)
    drive = DiskDrive(image)
    return WD2793(drive=drive), drive, image


# ---------------------------------------------------------------------------
# config-default.* -- named status-bit masks and transfer-size constants
# (read_address_size_code and force_interrupt_immediate_bit have no named
# source constant; they are covered behaviourally below by
# test_read_address_reports_synthesized_id_field_and_track_into_sector_register
# and test_force_interrupt_immediate_bit_raises_intrq)
# ---------------------------------------------------------------------------

def test_config_defaults_match_declared_status_bit_positions() -> None:
    assert BUSY == 1 << 0
    assert S_DRQ == 1 << 1
    assert TRACK00 == 1 << 2
    assert RECORD_NOT_FOUND == 1 << 4
    assert WRITE_PROTECTED == 1 << 6
    assert NOT_READY == 1 << 7


def test_config_defaults_match_declared_transfer_sizes() -> None:
    assert SECTOR_SIZE == 512
    assert TRACK_BYTES == 6250


# ---------------------------------------------------------------------------
# StepCommand / StepInCommand / StepOutCommand
# ---------------------------------------------------------------------------

def test_step_in_increments_track_and_latches_direction(tmp_path: Path) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(10)
    wd.set_command(0x40)  # STEP-IN
    assert wd.get_track() == 11
    assert drive.track == 11
    wd.set_command(0x20)  # STEP: repeats the latched "in" direction
    assert wd.get_track() == 12


def test_step_out_decrements_track_and_clamps_at_zero(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(1)
    wd.set_command(0x60)  # STEP-OUT
    assert wd.get_track() == 0
    wd.set_command(0x60)  # STEP-OUT again at track 0
    assert wd.get_track() == 0  # clamped, does not go negative


def test_step_repeat_out_clamps_at_zero_same_as_dedicated_step_out(
    tmp_path: Path,
) -> None:
    """Was asymmetric with StepOutCommand (STEP wrapped to 255 instead of
    clamping) -- see allium/fdc-wd2793-core.allium's StepCommand guidance.
    Fixed so STEP's "out" repeat uses the same floor as STEP-OUT itself."""
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(0)
    wd.set_command(0x60)  # STEP-OUT: clamps, track stays 0
    assert wd.get_track() == 0
    wd.set_command(0x20)  # STEP: repeats "out" -- now clamps too
    assert wd.get_track() == 0


def test_step_default_direction_before_any_step_in_or_out_is_in(
    tmp_path: Path,
) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_track(5)
    wd.set_command(0x20)  # STEP with no prior STEP-IN/STEP-OUT this session
    assert wd.get_track() == 6  # last_step_direction defaults to +1 ("in")


# ---------------------------------------------------------------------------
# ReadAddressCommand
# ---------------------------------------------------------------------------

def test_read_address_reports_synthesized_id_field_and_track_into_sector_register(
    tmp_path: Path,
) -> None:
    wd, drive, _ = _ctrl(tmp_path)
    wd.set_track(5)
    drive.side = 1
    wd.set_command(0xC0)  # READ ADDRESS
    assert wd.get_status() & (BUSY | S_DRQ) == (BUSY | S_DRQ)
    id_field = [wd.get_data() for _ in range(6)]
    assert id_field == [5, 1, 1, 2, 0, 0]  # track, side, sector=1, N=2, CRC=0,0
    assert wd.get_sector() == 5  # quirk: track address is written into the sector register
    assert wd.get_irq() is True


def test_read_address_not_ready_without_disk() -> None:
    wd = WD2793(drive=DiskDrive())  # drive present, no disk mounted
    wd.set_command(0xC0)
    assert wd.get_status() & NOT_READY


# ---------------------------------------------------------------------------
# ReadTrackCommand -- as-built stub
# ---------------------------------------------------------------------------

def test_read_track_is_a_stub_that_completes_with_no_transfer(
    tmp_path: Path,
) -> None:
    """READ TRACK (0xE0) decodes but msx/fdc/wd2793.py implements no real
    behaviour -- see ReadTrackCommand's guidance in the spec. Characterizes
    the as-built stub, not the datasheet's real READ TRACK."""
    wd, _, _ = _ctrl(tmp_path)
    wd.set_command(0xE0)
    assert wd.get_irq() is True
    assert wd.get_drq() is False
    assert wd.get_status() == 0  # reading status also clears INTRQ
    assert wd.get_irq() is False


# ---------------------------------------------------------------------------
# WriteSectorCommand / WriteTrackCommand -- not-ready branch (the
# write-protect branch of each is already covered by tests/test_wd2793.py's
# test_write_protect_rejects_write_sector / _write_track)
# ---------------------------------------------------------------------------

def test_write_sector_not_ready_without_disk() -> None:
    wd = WD2793(drive=DiskDrive())
    wd.set_command(0xA0)
    assert wd.get_status() & NOT_READY


def test_write_track_not_ready_without_disk() -> None:
    wd = WD2793(drive=DiskDrive())
    wd.set_command(0xF0)
    assert wd.get_status() & NOT_READY


# ---------------------------------------------------------------------------
# ForceInterruptCommand -- immediate-interrupt case, and the "other status
# bits unchanged" property
# ---------------------------------------------------------------------------

def test_force_interrupt_immediate_bit_raises_intrq(tmp_path: Path) -> None:
    wd, _, image = _ctrl(tmp_path)
    image.write_sector(0, b"\x11" * SECTOR_SIZE)
    wd.set_command(0x80)  # start a read: BUSY|DRQ, INTRQ false
    assert wd.get_irq() is False
    wd.set_command(0xD8)  # FORCE INTERRUPT with the immediate-interrupt bit set
    assert wd.get_irq() is True
    assert not (wd.get_status() & BUSY)


def test_force_interrupt_preserves_status_bits_other_than_busy_and_drq(
    tmp_path: Path,
) -> None:
    wd, _, _ = _ctrl(tmp_path, write_protected=True)
    wd.set_command(0xA0)  # WRITE SECTOR on a write-protected disk -> WRITE_PROTECTED, ends
    assert wd.get_irq() is True
    wd.set_command(0xD0)  # FORCE INTERRUPT, no immediate bit
    assert wd.get_status() & WRITE_PROTECTED  # unchanged by the force interrupt
    assert wd.get_irq() is False  # not immediate -> no INTRQ raised this time


# ---------------------------------------------------------------------------
# AbortTransfer -- host-level, not a WD279X command. tests/test_fdc_disk_
# swap.py::test_swap_aborts_in_progress_transfer already exercises this rule
# through FloppyDisk.swap() -> WD2793.abort(), but only checks that BUSY
# clears and mode becomes IDLE. This adds the postconditions that
# distinguish AbortTransfer from Reset (which this rule deliberately is
# not): track/sector preserved, and no INTRQ raised.
# ---------------------------------------------------------------------------

def test_abort_clears_transfer_state_but_preserves_head_position(
    tmp_path: Path,
) -> None:
    wd, _, image = _ctrl(tmp_path)
    image.write_sector(0, b"\x22" * SECTOR_SIZE)
    wd.set_track(7)
    wd.set_sector(3)
    wd.set_command(0x80)  # start a read: BUSY|DRQ
    assert wd.get_drq() is True

    wd.abort()

    assert wd.get_drq() is False
    assert not (wd.get_status() & (BUSY | S_DRQ))
    assert wd.get_track() == 7  # head position preserved -- unlike Reset
    assert wd.get_sector() == 3
    assert wd._mode is Mode.IDLE
    assert wd.get_irq() is False  # abort does not raise INTRQ, unlike CommandEnded


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def test_reset_returns_to_power_on_state(tmp_path: Path) -> None:
    wd, _, image = _ctrl(tmp_path)
    image.write_sector(0, b"\x33" * SECTOR_SIZE)
    wd.set_track(9)
    wd.set_sector(4)
    wd.set_command(0x80)  # get into a busy/DRQ transfer state

    wd.reset()

    assert wd.get_track() == 0
    assert wd.get_sector() == 1
    assert wd.get_data() == 0
    assert wd.get_status() == 0
    assert wd.get_irq() is False
    assert wd.get_drq() is False
    assert wd._mode is Mode.IDLE
    # As-built divergence from GT/openMSX (see Reset's @guidance in the
    # spec): a real MR pulse latches Hex 03 (RESTORE) into the command
    # register; this model leaves it at 0 (idle, no command dispatched).
    assert wd.command_reg == 0


def test_reset_does_not_disconnect_the_selected_drive(tmp_path: Path) -> None:
    """Unlike every other field, `drive` is intentionally excluded from
    reset() -- a soft reset must not un-select the drive the connection-style
    layer wired up."""
    wd, drive, _ = _ctrl(tmp_path)
    assert wd.drive is drive
    wd.reset()
    assert wd.drive is drive


# ---------------------------------------------------------------------------
# ReadDataRegisterIdle / WriteDataRegisterIdle
# ---------------------------------------------------------------------------

def test_data_register_round_trips_when_idle(tmp_path: Path) -> None:
    wd, _, _ = _ctrl(tmp_path)
    wd.set_data(0x5A)
    assert wd.get_data() == 0x5A


def test_read_data_register_during_write_transfer_echoes_register_not_buffer(
    tmp_path: Path,
) -> None:
    """ReadDataRegisterIdle's guard is "not reading", which also covers a
    read mid-write: get_data() must return the held register value, not
    whatever has been accumulated into the write buffer so far."""
    wd, _, _ = _ctrl(tmp_path)
    wd.set_command(0xA0)  # WRITE SECTOR
    wd.set_data(0x7C)
    assert wd.get_data() == 0x7C  # register echo, not a buffer read


def test_write_data_register_during_read_transfer_does_not_disturb_transfer(
    tmp_path: Path,
) -> None:
    wd, _, image = _ctrl(tmp_path)
    payload = bytes(range(256)) * 2
    image.write_sector(0, payload)
    wd.set_command(0x80)  # READ SECTOR
    assert wd.get_data() == payload[0]
    wd.set_data(0xFF)  # write mid-read: stored, but no effect on the transfer
    assert wd.data_reg == 0xFF
    assert wd.get_data() == payload[1]  # transfer resumes exactly where it left off


# ---------------------------------------------------------------------------
# entity-optional.WD2793.drive -- the fully-disconnected case (drive=None,
# distinct from DiskDrive() with no disk mounted, which the other tests use)
# ---------------------------------------------------------------------------

def test_no_drive_connected_at_all_reports_not_ready_but_type1_still_runs() -> None:
    wd = WD2793()  # no drive connected -- entity-optional.WD2793.drive, null case
    wd.set_command(0x80)  # READ SECTOR
    assert wd.get_status() & NOT_READY

    wd.set_command(0x00)  # RESTORE -- Type I commands run regardless of Ready
    assert wd.get_track() == 0
    assert wd.get_status() & NOT_READY  # still reported, just doesn't block Type I


def test_restore_with_no_disk_reports_track00_and_not_ready_together() -> None:
    """STATUS bits compose independently, not as a priority ladder: TRACK00
    and NOT_READY must both be set, not one masking the other out. This is
    the first command every MSX2 DISK ROM issues at boot with no disk
    mounted. Regression guard for allium's PositioningCommandCompleted
    composition fix (fdc-wd2793-core.allium)."""
    wd = WD2793()  # no drive connected
    wd.set_command(0x00)  # RESTORE
    assert wd.get_status() == (TRACK00 | NOT_READY)  # exactly 0x84, not 0x80


# ---------------------------------------------------------------------------
# invariant.RegistersAreBytes / invariant.StepDirectionIsSignum
# ---------------------------------------------------------------------------

@given(
    commands=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=40),
    data_bytes=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=40),
)
@settings(max_examples=200)
def test_registers_stay_within_byte_range(
    commands: list[int], data_bytes: list[int]
) -> None:
    wd = WD2793(drive=DiskDrive())  # no disk: exercises every not-ready/error path too
    for i, cmd in enumerate(commands):
        wd.set_command(cmd)
        wd.set_data(data_bytes[i % len(data_bytes)])
        wd.set_track(data_bytes[i % len(data_bytes)])
        wd.set_sector(data_bytes[i % len(data_bytes)])
        for reg in (wd.command_reg, wd.track_reg, wd.sector_reg, wd.data_reg, wd.status_reg):
            assert 0 <= reg <= 255


@given(commands=st.lists(st.integers(min_value=0, max_value=255), min_size=1, max_size=40))
@settings(max_examples=200)
def test_step_direction_is_always_signum(commands: list[int]) -> None:
    wd = WD2793(drive=DiskDrive())
    for cmd in commands:
        wd.set_command(cmd)
        assert wd._step_dir in (1, -1)
