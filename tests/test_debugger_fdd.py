"""Tests for the debugger fdd1/fdd2 disk-swap REPL commands."""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from msx.debugger.prompt import Debugger
from msx.fdc.disk_drive import DiskDrive
from msx.fdc.disk_image import DskDiskImage
from msx.fdc.interface import SonyPhilipsInterface
from msx.fdc.wd2793 import WD2793

_2DD = 737280


def _dbg(drives: int = 1) -> tuple[Debugger, SonyPhilipsInterface]:
    iface = SonyPhilipsInterface(
        WD2793(), [DiskDrive() for _ in range(drives)], disk_rom=bytes(16384)
    )
    return Debugger(types.SimpleNamespace(fdc=iface)), iface  # type: ignore[arg-type]


def _dsk(path: Path) -> Path:
    path.write_bytes(bytes(_2DD))
    return path


def test_fdd1_mounts_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dbg, iface = _dbg()
    p = _dsk(tmp_path / "g.dsk")
    dbg._cmd_fdd([str(p)], 0)
    assert iface.drives[0].image is not None
    assert "mounted" in capsys.readouterr().out


def test_fdd1_status_empty(capsys: pytest.CaptureFixture[str]) -> None:
    dbg, _ = _dbg()
    dbg._cmd_fdd([], 0)
    assert "empty" in capsys.readouterr().out


def test_fdd1_status_shows_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dbg, iface = _dbg()
    p = _dsk(tmp_path / "g.dsk")
    iface.mount(DskDiskImage(p), 0)
    dbg._cmd_fdd([], 0)
    assert str(p) in capsys.readouterr().out


def test_fdd1_eject(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dbg, iface = _dbg()
    iface.mount(DskDiskImage(_dsk(tmp_path / "g.dsk")), 0)
    dbg._cmd_fdd(["-"], 0)
    assert iface.drives[0].image is None
    assert "eject" in capsys.readouterr().out.lower()


def test_fdd2_on_single_drive_errors(capsys: pytest.CaptureFixture[str]) -> None:
    dbg, _ = _dbg(drives=1)
    dbg._cmd_fdd(["x.dsk"], 1)
    assert "only 1 drive" in capsys.readouterr().out.lower()


def test_fdd_no_fdc_errors(capsys: pytest.CaptureFixture[str]) -> None:
    dbg = Debugger(types.SimpleNamespace(fdc=None))  # type: ignore[arg-type]
    dbg._cmd_fdd(["x.dsk"], 0)
    assert "no floppy" in capsys.readouterr().out.lower()


def test_fdd1_missing_file_keeps_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dbg, iface = _dbg()
    iface.mount(DskDiskImage(_dsk(tmp_path / "g.dsk")), 0)
    dbg._cmd_fdd([str(tmp_path / "nope.dsk")], 0)
    assert iface.drives[0].image is not None  # previous disk kept
    assert "not found" in capsys.readouterr().out.lower()
