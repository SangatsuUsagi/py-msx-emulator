"""--scc-plus tests: CLI/config wiring (patched filesystem, no SDL window) and
direct build_machine wiring (SCCICart in slot 1, coexistence with --fmpac).
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from msx.machine_loader import (
    MachineSpec,
    _FmPacOverlay,
    _RomEntry,
    build_machine,
)
from msx.mapper import SCCICart

_MAIN_PATH = Path(__file__).parent.parent / "__main__.py"


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def fake_read_bytes(self: Path) -> bytes:
        return b"\x00" * 32768

    def fake_read_text(self: Path, encoding: str | None = None) -> str:
        # Isolate from the developer's own (git-ignored) py_emulator.yaml,
        # the only real caller of Path.read_text in this codebase
        # (msx/app_config.py's load_app_config) -- an empty string parses
        # to an all-unset AppConfig, the same as a genuinely absent file.
        return ""

    with patch.object(sys, "argv", [".", *argv]), \
         patch("builtins.print", side_effect=lambda *a, **kw: (
             stdout_buf.write(" ".join(str(x) for x in a) + "\n")
             if kw.get("file") is None else
             stderr_buf.write(" ".join(str(x) for x in a) + "\n")
         )), \
         patch.object(Path, "exists", lambda self: True), \
         patch.object(Path, "read_bytes", fake_read_bytes), \
         patch.object(Path, "read_text", fake_read_text), \
         patch("frontend.sdl2_frontend.run"):
        try:
            spec = importlib.util.spec_from_file_location("_emulator_main_scc_plus", _MAIN_PATH)
            assert spec is not None and spec.loader is not None
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            m.main()
            return 0, stdout_buf.getvalue(), stderr_buf.getvalue()
        except SystemExit as exc:
            return int(exc.code or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# CLI-level conflicts
# ---------------------------------------------------------------------------

def test_scc_plus_and_cartridge_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--scc-plus", "game.rom"])
    assert code != 0
    assert "--scc-plus" in err


def test_scc_plus_and_mapper_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--scc-plus", "--mapper", "KonamiSCC"])
    assert code != 0
    assert "--scc-plus" in err and "--mapper" in err


def test_scc_plus_alone_connects_cartridge() -> None:
    code, out, _err = _run_main(["--scc-plus", "--count-frame", "1"])
    assert code == 0
    assert "scc-plus" in out


def test_scc_plus_with_fmpac_boots() -> None:
    code, out, _err = _run_main(["--scc-plus", "--fmpac", "--count-frame", "1"])
    assert code == 0
    assert "scc-plus" in out
    assert "fmpac" in out


# ---------------------------------------------------------------------------
# py_emulator.yaml scc_plus key
# ---------------------------------------------------------------------------

def test_config_scc_plus_conflicts_with_config_mapper(tmp_path: Path) -> None:
    from msx.app_config import load_app_config
    (tmp_path / "py_emulator.yaml").write_text(
        "scc_plus: true\nmapper: KonamiSCC\n", encoding="utf-8"
    )
    cfg = load_app_config(tmp_path)
    assert cfg.scc_plus is True
    assert cfg.mapper == "KonamiSCC"
    # __main__.py's own validation (args.mapper is None or app_cfg.mapper is
    # not None) is exercised at the CLI level by
    # test_scc_plus_and_mapper_conflict_exits_nonzero above; this test only
    # confirms the config loader surfaces both values for that check to see.


def _fake_app_config(**overrides: object) -> object:
    from msx.app_config import AppConfig
    cfg = AppConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_config_scc_plus_connects_cartridge_when_flag_omitted() -> None:
    with patch("msx.app_config.load_app_config",
               return_value=_fake_app_config(scc_plus=True)):
        code, out, _err = _run_main(["--count-frame", "1"])
    assert code == 0
    assert "scc-plus" in out


def test_cli_scc_plus_overrides_config_false() -> None:
    with patch("msx.app_config.load_app_config",
               return_value=_fake_app_config(scc_plus=False)):
        code, out, _err = _run_main(["--scc-plus", "--count-frame", "1"])
    assert code == 0
    assert "scc-plus" in out


def test_config_scc_plus_and_config_mapper_conflict_exits_nonzero() -> None:
    with patch("msx.app_config.load_app_config",
               return_value=_fake_app_config(scc_plus=True, mapper="KonamiSCC")):
        code, _out, err = _run_main(["--count-frame", "1"])
    assert code != 0
    assert "--scc-plus" in err and "--mapper" in err


# ---------------------------------------------------------------------------
# build_machine wiring
# ---------------------------------------------------------------------------

def _msx1_spec(tmp_path: Path) -> MachineSpec:
    (tmp_path / "main.rom").write_bytes(bytes(32768))
    return MachineSpec(
        name="test_msx1",
        generation="msx1",
        rom_base_dir=tmp_path,
        main_rom_entry=_RomEntry(file="main.rom", size_kb=32, pages=[0, 1]),
        logo_rom_entry=None,
        sub_rom_entry=None,
        has_ram_mapper=False,
        ram_size_kb=64,
        has_v9938=False,
        has_rtc=False,
    )


def _make_fmpac_overlay(tmp_path: Path) -> _FmPacOverlay:
    (tmp_path / "fmpac.rom").write_bytes(bytes(65536))
    return _FmPacOverlay(
        rom_base_dir=tmp_path,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        slot=2,
        sram_save_path=tmp_path / "fmpac.sram",
    )


def test_build_machine_installs_scci_cart_in_slot1(tmp_path: Path) -> None:
    machine = build_machine(_msx1_spec(tmp_path), scc_plus=True)
    assert isinstance(machine.memory._mapper, SCCICart)
    assert machine.scc is not None
    assert machine.scc is machine.memory._mapper.scc


def test_build_machine_scc_plus_false_preserves_normal_resolution(tmp_path: Path) -> None:
    machine = build_machine(_msx1_spec(tmp_path))
    assert not isinstance(machine.memory._mapper, SCCICart)


def test_build_machine_scc_plus_and_fmpac_compose(tmp_path: Path) -> None:
    overlay = _make_fmpac_overlay(tmp_path)
    machine = build_machine(_msx1_spec(tmp_path), scc_plus=True, fmpac_overlay=overlay)
    assert isinstance(machine.memory._mapper, SCCICart)
    assert machine.fmpac is not None
    assert machine.memory._mapper2 is machine.fmpac


@pytest.mark.parametrize("addr", [0x9800, 0xB800])
def test_scci_cart_dispatches_at_slot1(tmp_path: Path, addr: int) -> None:
    machine = build_machine(_msx1_spec(tmp_path), scc_plus=True)
    mem = machine.memory
    # Default slot_register already routes page 1 (0x4000-0x7FFF) and page 2
    # (0x8000-0xBFFF) to slot 1 at power-on -- no explicit slot switch needed.
    # Just confirm dispatch reaches the SCCICart without raising; the SCC
    # window itself is inactive at power-on (see test_scc_i_cart.py).
    mem.read(addr)
