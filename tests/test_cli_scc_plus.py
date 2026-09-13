"""--extension scc_plus tests: CLI/config wiring (patched filesystem, no SDL
window) and direct build_machine wiring (SCCICart in slot 2, combinable with a
slot-1 cartridge).
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
    _ExtensionOverlay,
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

def test_extension_scc_plus_and_slot2_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--extension", "scc_plus", "--slot2", "game2.rom"])
    assert code != 0
    assert "--extension" in err and "--slot2" in err


def test_extension_scc_plus_and_mapper2_conflict_exits_nonzero() -> None:
    code, _out, err = _run_main(["--extension", "scc_plus", "--mapper2", "Konami"])
    assert code != 0
    assert "--extension" in err and "--mapper2" in err


def test_extension_scc_plus_alone_boots() -> None:
    code, out, _err = _run_main(["--extension", "scc_plus", "--count-frame", "1"])
    assert code == 0
    assert "scc_i_cart" in out


def test_extension_scc_plus_with_cartridge_boots() -> None:
    """Unlike the old --scc-plus (slot 1), --extension scc_plus (slot 2) does
    not conflict with a slot-1 cartridge argument or --mapper."""
    code, out, _err = _run_main(
        ["--extension", "scc_plus", "--mapper", "KonamiSCC", "game.rom", "--count-frame", "1"]
    )
    assert code == 0
    assert "scc_i_cart" in out


# ---------------------------------------------------------------------------
# py_emulator.yaml extension key
# ---------------------------------------------------------------------------

def _fake_app_config(**overrides: object) -> object:
    from msx.app_config import AppConfig
    cfg = AppConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_config_extension_connects_cartridge_when_flag_omitted() -> None:
    with patch("msx.app_config.load_app_config",
               return_value=_fake_app_config(extension="scc_plus")):
        code, out, _err = _run_main(["--count-frame", "1"])
    assert code == 0
    assert "scc_i_cart" in out


def test_cli_extension_overrides_config_extension() -> None:
    with patch("msx.app_config.load_app_config",
               return_value=_fake_app_config(extension="fmpac")):
        code, out, _err = _run_main(["--extension", "scc_plus", "--count-frame", "1"])
    assert code == 0
    assert "scc_i_cart" in out
    assert "fmpac" not in out


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


def _scc_plus_overlay() -> _ExtensionOverlay:
    return _ExtensionOverlay(device="scc_i_cart")


def _make_fmpac_overlay(tmp_path: Path) -> _ExtensionOverlay:
    (tmp_path / "fmpac.rom").write_bytes(bytes(65536))
    return _ExtensionOverlay(
        device="fmpac",
        rom_base_dir=tmp_path,
        rom_entry=_RomEntry(file="fmpac.rom", size_kb=64, pages=[]),
        sram_save_path=tmp_path / "fmpac.sram",
    )


def test_build_machine_installs_scci_cart_in_slot2(tmp_path: Path) -> None:
    machine = build_machine(_msx1_spec(tmp_path), extension_overlay=_scc_plus_overlay())
    assert isinstance(machine.memory._mapper2, SCCICart)
    assert not isinstance(machine.memory._mapper, SCCICart)
    assert machine.scc is not None
    assert machine.scc is machine.memory._mapper2.scc


def test_machine_reset_resyncs_scci_plus_mode(tmp_path: Path) -> None:
    """Machine.reset() resets the carried SCC chip directly, clearing its
    _plus_mode -- without resyncing, SCCICart's own (unreset) mode/bank
    registers would keep forwarding the Plus-mode window address (0xB800)
    into a chip now decoding Compatible offsets. Regression guard for the
    scc.allium reset-desync fix."""
    machine = build_machine(_msx1_spec(tmp_path), extension_overlay=_scc_plus_overlay())
    cart = machine.memory._mapper2
    assert isinstance(cart, SCCICart)

    cart.write(0xBFFE, 0x20)  # mode register: select Plus mode
    cart.write(0xB000, 0x80)  # window 3 bank register (0xB000-0xB7FF): high bit -> Plus window
    assert cart._scc_window_base == 0xB800
    assert machine.scc._plus_mode is True

    machine.reset()

    assert machine.scc._plus_mode is True  # re-derived from cart's own mode register
    assert cart._scc_window_base == 0xB800  # still consistent with the chip's mode


def test_build_machine_no_extension_preserves_normal_slot2_resolution(tmp_path: Path) -> None:
    machine = build_machine(_msx1_spec(tmp_path))
    assert not isinstance(machine.memory._mapper2, SCCICart)


def test_build_machine_scc_plus_with_slot1_cartridge(tmp_path: Path) -> None:
    """--extension scc_plus (slot 2) no longer forces slot 1 -- a normal
    cartridge/mapper resolves in slot 1 independently."""
    machine = build_machine(
        _msx1_spec(tmp_path), cartridge=bytes(65536), mapper="ASCII8",
        extension_overlay=_scc_plus_overlay(),
    )
    assert isinstance(machine.memory._mapper2, SCCICart)
    assert not isinstance(machine.memory._mapper, SCCICart)


@pytest.mark.parametrize("addr", [0x9800, 0xB800])
def test_scci_cart_dispatches_at_slot2(tmp_path: Path, addr: int) -> None:
    machine = build_machine(_msx1_spec(tmp_path), extension_overlay=_scc_plus_overlay())
    mem = machine.memory
    # Just confirm dispatch reaches the SCCICart without raising; the SCC
    # window itself is inactive at power-on (see test_scc_i_cart.py).
    mem.read(addr)
