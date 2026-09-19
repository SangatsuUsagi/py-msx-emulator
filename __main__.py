"""Entry point: python . [--machine MACHINE_ID] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, TextIO, TypeVar

from msx.app_config import VALID_EXTENSIONS, VALID_MAPPERS

if TYPE_CHECKING:
    from msx.app_config import AppConfig
    from msx.diagnostics.logger import DebugLogger
    from msx.machine import Machine
    from msx.machine_loader import (
        MachineSpec,
        _ExpandedExtensionOverlay,
        _ExtensionOverlay,
    )
    from msx.rpc_server import DebugServer

_PROJECT_ROOT = Path(__file__).parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# Hardware debug register count (MSX2): caps the breakpoint/watchpoint lists.
_MAX_BREAKPOINTS = 4

_T = TypeVar("_T")


def _first_set(*candidates: _T | None, default: _T) -> _T:
    """Return the first non-None value (CLI < config file < built-in default).

    `default` is keyword-only and required (not `_T | None`) so a call site
    can't accidentally omit a concrete fallback and hit `StopIteration`.
    """
    return next((v for v in candidates if v is not None), default)


def _parse_breakpoints(spec: str | None) -> list[int]:
    """Parse a comma-separated hex breakpoint list into up to 4 addresses."""
    addrs: list[int] = []
    if spec is None:
        return addrs
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            addrs.append(int(tok, 16) & 0xFFFF)
        except ValueError:
            print(f"error: invalid breakpoint address: {tok!r} (expected hex)",
                  file=sys.stderr)
            sys.exit(1)
    if len(addrs) > _MAX_BREAKPOINTS:
        print(f"warning: more than {_MAX_BREAKPOINTS} breakpoints given; "
              f"only first {_MAX_BREAKPOINTS} will be used", file=sys.stderr)
        addrs = addrs[:_MAX_BREAKPOINTS]
    return addrs


def _parse_watchpoints(spec: str | None) -> list[tuple[int, str]]:
    """Parse a comma-separated `addr[,mode]` watchpoint list (mode in r/w/rw,
    default rw) into up to 4 (addr, mode) entries."""
    entries: list[tuple[int, str]] = []
    if spec is None:
        return entries
    pending_addr: int | None = None
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok in ("r", "w", "rw"):
            if pending_addr is not None:
                entries.append((pending_addr, tok))
                pending_addr = None
            else:
                print(f"error: mode {tok!r} without preceding address", file=sys.stderr)
                sys.exit(1)
        else:
            if pending_addr is not None:
                entries.append((pending_addr, "rw"))
            try:
                pending_addr = int(tok, 16) & 0xFFFF
            except ValueError:
                print(
                    f"error: invalid watchpoint token: {tok!r} "
                    f"(hex address or r/w/rw expected)",
                    file=sys.stderr,
                )
                sys.exit(1)
    if pending_addr is not None:
        entries.append((pending_addr, "rw"))
    if len(entries) > _MAX_BREAKPOINTS:
        print(f"warning: more than {_MAX_BREAKPOINTS} watchpoints given; "
              f"only first {_MAX_BREAKPOINTS} will be used", file=sys.stderr)
        entries = entries[:_MAX_BREAKPOINTS]
    return entries


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (see `python . --help` for the options)."""
    parser = argparse.ArgumentParser(description="py-msx-emulator")
    parser.add_argument("cartridge", nargs="?", default=None,
                        help="Cartridge ROM path")
    parser.add_argument("--machine", metavar="MACHINE_ID", default=None,
                        help="Machine configuration ID (e.g. cbios_msx1, cbios_msx2_jp)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable diagnostic logging")
    parser.add_argument("--log", metavar="FILE",
                        help="Write diagnostic log to FILE (requires --debug)")
    parser.add_argument("--speed", type=float, default=None,
                        help="Emulation speed multiplier (default: 1.0)")
    parser.add_argument("--scale", type=int, default=None, metavar="N",
                        help="Integer window scale over the 256x212 base (default: 3)")
    parser.add_argument("--mapper", "--mapper1", dest="mapper1",
                        choices=list(VALID_MAPPERS),
                        default=None,
                        help="Slot 1 cartridge mapper type (default: auto — detect from ROM "
                             "database). '--mapper' is accepted as an alias for '--mapper1'")
    parser.add_argument("--slot1", default=None, metavar="ROM1",
                        help="Slot 1 cartridge ROM path (an alternative to the positional "
                             "cartridge argument; conflicts with it)")
    parser.add_argument("--slot2", default=None, metavar="ROM2",
                        help="Slot 2 cartridge ROM path")
    parser.add_argument("--extension1", choices=[*VALID_EXTENSIONS, "none"], default=None,
                        help="Overlay a slot 1 extension device (same ids as --extension2; "
                             "conflicts with the positional cartridge argument/--slot1/"
                             "--mapper1). 'none' forces no extension overlay even when "
                             "py_emulator.yaml sets one, freeing --slot1/--mapper1 for CLI use")
    parser.add_argument("--extension2", choices=[*VALID_EXTENSIONS, "none"], default=None,
                        help="Overlay a slot 2 extension device: 'fmpac' (MSX-MUSIC + 8 KB "
                             "SRAM), 'scc_plus' (an SCC-I / SCC+ cartridge), 'hbi_j1' "
                             "(Sony HBI-J1: Kanji-ROM + MSX-JE + Kanji driver/BASIC, expands "
                             "slot 2 into two sub-slots), or 'memory_512k' (a 512 KB volatile "
                             "RAM-mapper memory-expansion cartridge; requires a machine with "
                             "no existing slot-3 memory mapper) (conflicts with "
                             "--slot2/--mapper2). 'none' forces no extension overlay even when "
                             "py_emulator.yaml sets one, freeing --slot2/--mapper2 for CLI use")
    parser.add_argument("--mapper2",
                        choices=list(VALID_MAPPERS),
                        default=None,
                        help="Slot 2 mapper type (default: auto; KonamiSCC builds its own "
                             "SCC chip, rejected if the other slot also resolves to "
                             "KonamiSCC or selects --extensionN scc_plus)")
    parser.add_argument("--fdd1", default=None, metavar="DSK",
                        help="Floppy disk image (*.dsk) to mount in drive A")
    parser.add_argument("--fdd2", default=None, metavar="DSK",
                        help="Floppy disk image (*.dsk) to mount in drive B")
    parser.add_argument("--resume", nargs="?", const="", default=None, metavar="STATE_FILE",
                        help="Resume from a save state (default: saves/states/latest.state)")
    parser.add_argument("--mouse", nargs="?", const="2", default=None, choices=["1", "2"],
                        help="Plug an MSX mouse into a joystick port, driven by the host "
                             "mouse (default port 2 if bare). Overrides py_emulator.yaml's "
                             "mouse.enabled/mouse.port. Ignored in headless "
                             "(--count-frame/--benchmark) runs — no SDL window/event loop.")
    parser.add_argument("--frame-skip", choices=["auto", "none"], default=None,
                        dest="frame_skip",
                        help="Frame skip mode: auto (default) or none to disable")
    parser.add_argument("--vdp-trace", action="store_true", dest="vdp_trace",
                        help="Enable VDP register write tracing (VDP Trace Log Format)")
    parser.add_argument("--vdp-trace-out", metavar="FILE", dest="vdp_trace_out", default=None,
                        help="Write VDP trace to FILE instead of stdout")
    parser.add_argument("--mapper-trace", action="store_true", dest="mapper_trace",
                        help="Enable cartridge mapper bank-switch tracing (MAP_BANK records)")
    parser.add_argument("--mapper-trace-out", metavar="FILE", dest="mapper_trace_out",
                        default=None,
                        help="Write mapper trace to FILE instead of stdout")
    parser.add_argument("--count-frame", type=int, default=None, metavar="N",
                        dest="count_frame",
                        help="Run exactly N frames headlessly and exit (no SDL window)")
    parser.add_argument("--benchmark", nargs="?", type=int, const=10000, default=None,
                        metavar="FRAMES",
                        help="Run headlessly, unthrottled, for FRAMES frames (default: 10000) "
                             "and report average FPS (no SDL window)")
    parser.add_argument("--break-point", metavar="ADDRS", default=None,
                        dest="break_point",
                        help="Comma-separated hex breakpoint addresses, max 4 (MSX2 only)")
    parser.add_argument("--watch-point", metavar="ADDRS", default=None,
                        dest="watch_point",
                        help="Comma-separated watchpoint addresses, max 4 (MSX2 only; "
                             "breaks on read or write)")
    parser.add_argument("--rpc", action="store_const", const=True, default=None,
                        help="Enable the embedded Unix-socket JSON-RPC control server "
                             "(interactive SDL run mode only; off by default)")
    parser.add_argument("--rpc-socket", metavar="PATH", dest="rpc_socket", default=None,
                        help="Unix socket path for --rpc (default: /tmp/py_msx_emu.sock)")
    return parser


class _ResolvedPaths(NamedTuple):
    cart_path: Path | None
    slot2_path: Path | None
    fdd1_path: Path | None
    fdd2_path: Path | None
    cartridge: bytes | None
    cartridge2: bytes | None


def _resolve_paths(
    args: argparse.Namespace, slot1: str | None, slot2: str | None
) -> _ResolvedPaths:
    """Validate the slot-1/slot-2/floppy paths and read the ROM bytes.

    `slot1` is the already-resolved effective slot 1 path (positional
    cartridge argument, `--slot1`, or `py_emulator.yaml`'s `slot1` key --
    see `_resolve_effective_options`), not `args.cartridge` directly.

    Exits the process with an error if any given path does not exist.
    """
    cart_path = Path(slot1) if slot1 else None
    slot2_path = Path(slot2) if slot2 else None
    if cart_path is not None and not cart_path.exists():
        print(f"error: cartridge not found: {cart_path}", file=sys.stderr)
        sys.exit(1)
    if slot2_path is not None and not slot2_path.exists():
        print(f"error: slot 2 ROM not found: {slot2_path}", file=sys.stderr)
        sys.exit(1)

    fdd1_path = Path(args.fdd1) if args.fdd1 else None
    fdd2_path = Path(args.fdd2) if args.fdd2 else None
    for label, fdd_path in (("--fdd1", fdd1_path), ("--fdd2", fdd2_path)):
        if fdd_path is not None and not fdd_path.exists():
            print(f"error: disk image not found ({label}): {fdd_path}", file=sys.stderr)
            sys.exit(1)

    cartridge = cart_path.read_bytes() if cart_path else None
    cartridge2 = slot2_path.read_bytes() if slot2_path else None
    return _ResolvedPaths(cart_path, slot2_path, fdd1_path, fdd2_path, cartridge, cartridge2)


def _check_identical_cartridges(cartridge: bytes | None, cartridge2: bytes | None) -> None:
    """Reject identical (by content, not path) cartridge ROMs in both slots.

    SRAM persistence and save-state identity both key off which cartridge
    occupies a given slot, and there is no supported use case for the same
    ROM in both slots at once -- see the app-cli spec's "Identical ROM or
    extension across both slots is rejected" Requirement. Compares content
    (SHA1), not path, so two different files that happen to be
    byte-identical are treated the same as the same file given twice.
    """
    if cartridge is None or cartridge2 is None:
        return
    import hashlib
    if hashlib.sha1(cartridge, usedforsecurity=False).digest() == hashlib.sha1(
        cartridge2, usedforsecurity=False
    ).digest():
        print("error: slot 1 and slot 2 resolve to the identical cartridge ROM "
              "-- loading the same cartridge into both slots is not supported",
              file=sys.stderr)
        sys.exit(1)


def _resolve_machine_id(
    args: argparse.Namespace, app_cfg: "AppConfig", db_system: str | None
) -> str:
    """Pick the machine ID: --machine > config file > ROM DB (MSX1) > MSX2 default."""
    if args.machine is not None:
        return str(args.machine)
    if app_cfg.machine is not None:
        return str(app_cfg.machine)
    if db_system == "MSX":
        return "cbios_msx1_jp"
    return "cbios_msx2_jp"


def _print_extension_summary_line(
    slot_num: int,
    extension_overlay: "_ExtensionOverlay | _ExpandedExtensionOverlay | None",
    extension_id: str | None,
) -> None:
    if extension_overlay is None:
        return
    from msx.machine_loader import _ExpandedExtensionOverlay
    if isinstance(extension_overlay, _ExpandedExtensionOverlay):
        print(
            f"extension: {extension_id} (expanded slot {slot_num}: "
            f"{len(extension_overlay.subslots)} sub-slot(s)"
            + (" + Kanji-ROM I/O device" if extension_overlay.io_device is not None else "")
            + ")"
        )
    elif extension_overlay.rom_entry is not None:
        # rom_base_dir is always set together with rom_entry (see
        # _ExtensionOverlay's docstring: both are None only for a device
        # with no ROM file, e.g. scc_i_cart) -- asserted here only to
        # narrow the type for mypy, not a runtime possibility.
        assert extension_overlay.rom_base_dir is not None
        print(
            f"extension: {extension_overlay.device} "
            f"({extension_overlay.rom_base_dir / extension_overlay.rom_entry.file}, "
            f"slot {slot_num})"
        )
    else:
        print(f"extension: {extension_overlay.device} (slot {slot_num})")


def _print_startup_summary(
    spec: "MachineSpec", display_mapper: str, fdd1_path: Path | None, fdd2_path: Path | None,
    extension_overlay1: "_ExtensionOverlay | _ExpandedExtensionOverlay | None",
    extension_overlay: "_ExtensionOverlay | _ExpandedExtensionOverlay | None",
    mouse_port: int | None, args: argparse.Namespace,
) -> None:
    print(f"machine : {spec.name}")
    print(f"rom_base: {spec.rom_base_dir}")
    print(f"bios    : {spec.main_rom_entry.file}")
    if spec.generation == "msx2" and spec.sub_rom_entry is not None:
        print(f"ext     : {spec.sub_rom_entry.file}")
    if fdd1_path is not None:
        print(f"fdd1    : {fdd1_path}")
    if fdd2_path is not None:
        print(f"fdd2    : {fdd2_path}")
    _print_extension_summary_line(1, extension_overlay1, args.extension1)
    _print_extension_summary_line(2, extension_overlay, args.extension2)
    if mouse_port is not None:
        print(f"mouse   : Joy{mouse_port + 1}")
    print(f"mapper  : {display_mapper}")
    if args.vdp_trace:
        print(f"vdp-trace: {'stdout' if args.vdp_trace_out is None else args.vdp_trace_out}")
    if args.mapper_trace:
        print(f"map-trace: {'stdout' if args.mapper_trace_out is None else args.mapper_trace_out}")
    if args.count_frame is not None:
        print(f"frames  : {args.count_frame} (headless)")
    if args.benchmark is not None:
        print(f"benchmark: {args.benchmark} frames (headless)")


def _run_headless_frames(machine: "Machine", count_frame: int, resume: str | None) -> None:
    """Run exactly `count_frame` frames headlessly (no SDL window)."""
    if resume is not None:
        from msx.state import load_state
        load_state(machine, path=Path(resume) if resume else None)
    for _ in range(count_frame):
        machine.run_frame()


def _run_benchmark(machine: "Machine", frames: int, resume: str | None) -> None:
    """Run `frames` frames unthrottled and report average FPS (no SDL window)."""
    import time

    from msx.state import load_state
    if resume is not None:
        load_state(machine, path=Path(resume) if resume else None)

    start = time.perf_counter()
    for _ in range(frames):
        machine.run_frame()
    elapsed = time.perf_counter() - start

    print(f"frames  : {frames}")
    print(f"elapsed : {elapsed:.2f}s")
    print(f"avg fps : {frames / elapsed:.2f}")


class _SramSource(Protocol):
    def save_sram(self, path: Path) -> None: ...


def _save_sram_if_present(source: "_SramSource | None", save_path: "Path | None") -> None:
    """Save `source`'s SRAM to `save_path` on exit, if both are present."""
    if source is not None and save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        source.save_sram(save_path)


def _cleanup(
    machine: "Machine | None", rpc_server: "DebugServer | None", logger: "DebugLogger | None",
    trace_file: TextIO | None, mapper_trace_file: TextIO | None,
) -> None:
    """Tear down open resources and flush SRAM / disk writes on exit."""
    if rpc_server is not None:
        rpc_server.stop()
    if logger is not None:
        logger.close()
    if trace_file is not None:
        trace_file.close()
    if mapper_trace_file is not None:
        mapper_trace_file.close()
    if machine is not None:
        mapper = machine.memory._mapper
        mapper2 = machine.memory._mapper2
        _save_sram_if_present(mapper if mapper.has_sram else None, machine.sram_save_path)
        _save_sram_if_present(mapper2 if mapper2.has_sram else None, machine.sram_save_path2)
        _save_sram_if_present(machine.fmpac, machine.fmpac_sram_save_path)
        _save_sram_if_present(machine.halnote_cart, machine.halnote_sram_save_path)
        _save_sram_if_present(machine.rtc, machine.rtc_sram_save_path)
        # Flush any disk writes (FORMAT / file save) back to the *.dsk on exit.
        if machine.fdc is not None:
            machine.fdc.flush()


class _EffectiveOptions(NamedTuple):
    """Resolved run options, after the built-in default < config file <
    CLI argument precedence (see _first_set)."""

    speed: float
    scale: int
    mapper1: str
    extension1: str | None
    rpc_enabled: bool
    mouse_port: int | None
    slot1: str | None
    slot2: str | None
    mapper2: str
    extension2: str | None
    frame_skip: str


def _resolve_extension(cli_value: str | None, config_value: str | None) -> str | None:
    """Resolve one slot's extensionN option. "none" is a CLI-only sentinel
    (not in VALID_EXTENSIONS, never valid in py_emulator.yaml): it forces no
    extension overlay even when the config file sets one, since a plain
    omitted --extensionN can't be told apart from "let the config file
    decide" -- see cart-extension-overlay's "--extension1/--extension2 none
    forces no extension overlay on that slot" Requirement."""
    if cli_value == "none":
        return None
    return _first_set(cli_value, config_value, default=None)


def _resolve_effective_options(
    args: argparse.Namespace, app_cfg: "AppConfig"
) -> _EffectiveOptions:
    """Merge CLI arguments with py_emulator.yaml at built-in default < config
    file < CLI argument precedence. A CLI flag left unset is None (see the
    sentinel defaults in _build_arg_parser), so it never masks a config value.
    """
    from msx.app_config import DEFAULT_MAPPER, DEFAULT_SCALE, DEFAULT_SPEED

    if app_cfg.frame_skip is None:
        frame_skip_cfg = None
    elif app_cfg.frame_skip:
        frame_skip_cfg = "auto"
    else:
        frame_skip_cfg = "none"

    return _EffectiveOptions(
        speed=_first_set(args.speed, app_cfg.speed, default=DEFAULT_SPEED),
        scale=_first_set(args.scale, app_cfg.scale, default=DEFAULT_SCALE),
        mapper1=_first_set(args.mapper1, default=DEFAULT_MAPPER),
        extension1=_resolve_extension(args.extension1, app_cfg.extension1),
        rpc_enabled=_first_set(args.rpc, app_cfg.rpc_enabled, default=False),
        mouse_port=int(args.mouse) - 1 if args.mouse else app_cfg.mouse_port_index(),
        # slot1/slot2's built-in default is "no cartridge" (None), unlike the
        # concrete defaults above -- _first_set's keyword-only `default`
        # accepts that directly. The positional `cartridge` argument is
        # slot 1's third source, lowest CLI-side priority after --slot1 (the
        # two are mutually exclusive in practice -- see _validate_options).
        slot1=_first_set(args.slot1, args.cartridge, app_cfg.slot1, default=None),
        slot2=_first_set(args.slot2, app_cfg.slot2, default=None),
        mapper2=_first_set(args.mapper2, default="auto"),
        extension2=_resolve_extension(args.extension2, app_cfg.extension2),
        frame_skip=_first_set(args.frame_skip, frame_skip_cfg, default="auto"),
    )


def _validate_options(args: argparse.Namespace, opts: _EffectiveOptions) -> None:
    """Enforce mutual-exclusivity and range constraints on resolved options.

    Prints an error to stderr and exits the process (code 1) on violation.
    """
    if args.benchmark is not None and args.count_frame is not None:
        print("error: --benchmark and --count-frame are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    if opts.scale < 1:
        print("error: --scale must be a positive integer", file=sys.stderr)
        sys.exit(1)
    if args.cartridge and args.slot1:
        print("error: a positional cartridge argument and --slot1 are "
              "mutually exclusive (the positional argument is --slot1 "
              "shorthand)", file=sys.stderr)
        sys.exit(1)
    if opts.extension1 is not None and (args.slot1 or args.cartridge):
        print("error: --extension1 and --slot1/a positional cartridge "
              "argument are mutually exclusive (the extension owns slot 1)",
              file=sys.stderr)
        sys.exit(1)
    # Checked against args.mapperN, not opts.mapperN: opts.mapperN folds in
    # the "auto" default, so it can't distinguish an explicit --mapperN auto
    # from --mapperN never having been passed. --mapper1/--mapper2 have no
    # config-file equivalent (see app-config-file spec), so only the CLI
    # flag needs checking here.
    if opts.extension1 is not None and args.mapper1 is not None:
        print("error: --extension1 and --mapper1 are mutually exclusive "
              "(the extension owns slot 1)", file=sys.stderr)
        sys.exit(1)
    if opts.extension2 is not None and opts.slot2:
        print("error: --extension2 and --slot2 are mutually exclusive "
              "(the extension owns slot 2)", file=sys.stderr)
        sys.exit(1)
    if opts.extension2 is not None and args.mapper2 is not None:
        print("error: --extension2 and --mapper2 are mutually exclusive "
              "(the extension owns slot 2)", file=sys.stderr)
        sys.exit(1)
    if (
        opts.extension1 is not None
        and opts.extension2 is not None
        and opts.extension1 == opts.extension2
    ):
        print(f"error: --extension1 and --extension2 both resolve to "
              f"{opts.extension1!r} -- the identical extension on both "
              "slots is not supported", file=sys.stderr)
        sys.exit(1)
    # PHASE-3-TEMPORARY (openspec/changes/slot1-slot2-symmetrization):
    # build_machine's extension_overlay1 now wires a *flat* overlay
    # (fmpac/scc_plus) onto slot 1 correctly, but an *expanded* overlay
    # (hbi_j1/memory_512k/msxdos2_512k*) on slot 1 needs Memory's slot-1
    # sub-slot expansion, which lands in a later phase. Refuse the
    # expanded-shape ids on slot 1 here rather than letting
    # build_machine's own (correct, but less CLI-friendly) MachineLoadError
    # surface. Remove this set (and this comment) once every id is
    # symmetric on slot 1.
    _EXPANDED_SHAPE_EXTENSION_IDS = frozenset({
        "hbi_j1", "memory_512k", "msxdos2_512k",
        "msxdos2_512k_kanjirom", "msxdos2_512k_viewfont",
    })
    if opts.extension1 in _EXPANDED_SHAPE_EXTENSION_IDS:
        print(f"error: --extension1 {opts.extension1} is not implemented yet "
              "(expanded-shape extensions on slot 1 need Memory support "
              "landing in a later phase of the slot1-slot2-symmetrization "
              "change); use --extension2 for now", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    args = _build_arg_parser().parse_args()

    from msx.app_config import AppConfigError, load_app_config
    try:
        app_cfg = load_app_config(_PROJECT_ROOT)
    except AppConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    opts = _resolve_effective_options(args, app_cfg)
    _validate_options(args, opts)

    from msx.romdb import lookup, lookup_system, lookup_title

    paths = _resolve_paths(args, opts.slot1, opts.slot2)
    fdd1_path = paths.fdd1_path
    fdd2_path = paths.fdd2_path
    cartridge = paths.cartridge
    cartridge2 = paths.cartridge2
    _check_identical_cartridges(cartridge, cartridge2)

    db_system = lookup_system(cartridge) if cartridge else None
    machine_id = _resolve_machine_id(args, app_cfg, db_system)

    # --- Load machine spec from YAML ---
    from msx.machine_loader import (
        MachineLoadError,
        build_machine,
        load_device_registry,
        load_extension_overlay,
        load_machine_spec,
    )
    try:
        device_registry = load_device_registry(_CONFIG_DIR)
        spec = load_machine_spec(machine_id, _CONFIG_DIR, device_registry, _PROJECT_ROOT)
        # opts.extension1, if set, is always a flat-shape id here --
        # _validate_options's PHASE-3-TEMPORARY guard rejects the
        # expanded-shape ids before main() reaches this point.
        extension_overlay1 = (
            load_extension_overlay(opts.extension1, _CONFIG_DIR, _PROJECT_ROOT)
            if opts.extension1 is not None else None
        )
        extension_overlay = (
            load_extension_overlay(opts.extension2, _CONFIG_DIR, _PROJECT_ROOT)
            if opts.extension2 is not None else None
        )
    except MachineLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Disk image only applies to machines with a floppy interface ---
    if (fdd1_path is not None or fdd2_path is not None) and spec.fdc is None:
        print("warning: --fdd1/--fdd2 given but machine has no floppy interface; "
              "ignoring", file=sys.stderr)
        fdd1_path = None
        fdd2_path = None

    # --- Resolve display mapper for summary ---
    if opts.mapper1 != "auto":
        display_mapper = opts.mapper1
    elif cartridge:
        display_mapper = lookup(cartridge) or "auto"
    else:
        display_mapper = "auto"

    breakpoint_addrs = _parse_breakpoints(args.break_point)
    watchpoint_entries = _parse_watchpoints(args.watch_point)

    _print_startup_summary(
        spec, display_mapper, fdd1_path, fdd2_path, extension_overlay1, extension_overlay,
        opts.mouse_port, args,
    )

    from msx.diagnostics.logger import DebugLogger
    from msx.vdp.tracer import Tracer

    # --- Build tracer ---
    tracer: Tracer | None = None
    _trace_file = None
    _mapper_trace_file = None
    if args.vdp_trace:
        if args.vdp_trace_out:
            _trace_file = open(args.vdp_trace_out, "w", encoding="utf-8")
        tracer = Tracer(enabled=True, output=_trace_file if _trace_file else sys.stdout)

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    logger = DebugLogger(log_path=args.log) if args.debug else None
    machine = None
    rpc_server = None
    try:
        try:
            machine = build_machine(
                spec,
                cartridge=cartridge,
                mapper=opts.mapper1,
                cartridge2=cartridge2,
                mapper2=opts.mapper2,
                logger=logger,
                tracer=tracer,
                fdd1=fdd1_path,
                fdd2=fdd2_path,
                extension_overlay=extension_overlay,
                extension_overlay1=extension_overlay1,
                joy_map=app_cfg.keyboard_joy_map(),
            )
        except MachineLoadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        # Attach the interactive debugger for all machines so Ctrl-C drops into
        # the REPL on MSX1 too (the slot/mapper tools are most useful there).
        from msx.debugger.prompt import Debugger
        machine._debugger = Debugger(machine)

        if breakpoint_addrs:
            machine.set_breakpoints(breakpoint_addrs)
            print(f"break   : {', '.join(f'{a:04X}h' for a in breakpoint_addrs)}")
        if watchpoint_entries:
            machine.set_watchpoints(watchpoint_entries)
            print(f"watch   : {', '.join(f'{a:04X}h[{m}]' for a, m in watchpoint_entries)}")

        if args.mapper_trace:
            from msx.mapper_tracer import attach_to_machine
            if args.mapper_trace_out:
                _mapper_trace_file = open(args.mapper_trace_out, "w", encoding="utf-8")
            if attach_to_machine(machine, output=_mapper_trace_file or sys.stdout) is None:
                print("mapper-trace: no bank-switching ROM mapper present", file=sys.stderr)

        if args.count_frame is not None:
            _run_headless_frames(machine, args.count_frame, args.resume)
        elif args.benchmark is not None:
            _run_benchmark(machine, args.benchmark, args.resume)
        else:
            if opts.rpc_enabled:
                from msx.rpc_server import DEFAULT_SOCKET_PATH, DebugServer
                sock_path = args.rpc_socket or app_cfg.rpc_socket or DEFAULT_SOCKET_PATH
                rpc_server = DebugServer(machine, sock_path=sock_path)
                machine.set_pause_hook(rpc_server.on_pause)
                rpc_server.start()
                print(f"rpc     : {sock_path}")
            from frontend.sdl2_frontend import run
            run(machine, scale=opts.scale, speed=opts.speed, game_title=game_title,
                resume=args.resume, frame_skip=opts.frame_skip, rpc_server=rpc_server,
                gamepad_map=app_cfg.gamepad_maps(), turbo_period=app_cfg.turbo_period(),
                mouse_port=opts.mouse_port)
    finally:
        _cleanup(machine, rpc_server, logger, _trace_file, _mapper_trace_file)


if __name__ == "__main__":
    main()
