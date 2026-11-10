"""Entry point: python . [--machine MACHINE_ID] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, NoReturn, TextIO, TypeVar

from host.app_config import VALID_EXTENSIONS, VALID_MAPPERS

if TYPE_CHECKING:
    from host.app_config import AppConfig
    from host.debugger.prompt import Debugger
    from host.media import DiskFiles
    from host.printer_output import PrinterOutput
    from host.rpc_server import DebugServer
    from host.tape import TapeDeck
    from host.trace import TraceOutput
    from msx.boundary import RtcTime
    from msx.core_api import CoreApi
    from msx.machine_spec import AnyExtensionOverlay, MachineSpec

_PROJECT_ROOT = Path(__file__).parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

_T = TypeVar("_T")


def _first_set(*candidates: _T | None, default: _T) -> _T:
    """Return the first non-None value (CLI < config file < built-in default).

    `default` is keyword-only and required (not `_T | None`) so a call site
    can't accidentally omit a concrete fallback and hit `StopIteration`.
    """
    return next((v for v in candidates if v is not None), default)


def _parse_breakpoints(spec: str | None) -> list[int]:
    """Parse a comma-separated hex breakpoint list into up to MAX_BREAKPOINTS
    (4) addresses."""
    from msx.boundary import MAX_BREAKPOINTS

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
    if len(addrs) > MAX_BREAKPOINTS:
        print(f"warning: more than {MAX_BREAKPOINTS} breakpoints given; "
              f"only first {MAX_BREAKPOINTS} will be used", file=sys.stderr)
        addrs = addrs[:MAX_BREAKPOINTS]
    return addrs


def _watch_summary(entries: list[tuple[int, int]]) -> str:
    """The startup line's list of watchpoints: "C000h[rw], ..."."""
    from host.watch import watch_text

    parts: list[str] = []
    for addr, kind in entries:
        parts.append(f"{addr:04X}h[{watch_text(kind)}]")
    return ", ".join(parts)


def _parse_watchpoints(spec: str | None) -> list[tuple[int, int]]:
    """Parse a comma-separated `addr[,mode]` watchpoint list (mode in r/w/rw,
    default rw) into up to MAX_WATCHPOINTS (4) (addr, kind) entries
    (host/watch.py)."""
    from host.watch import watch_kind_exact
    from msx.boundary import MAX_WATCHPOINTS, WATCH_READ, WATCH_WRITE

    entries: list[tuple[int, int]] = []
    if spec is None:
        return entries
    pending_addr: int | None = None
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        kind = watch_kind_exact(tok)
        if kind is not None:
            if pending_addr is not None:
                entries.append((pending_addr, kind))
                pending_addr = None
            else:
                print(f"error: mode {tok!r} without preceding address", file=sys.stderr)
                sys.exit(1)
        else:
            if pending_addr is not None:
                entries.append((pending_addr, WATCH_READ | WATCH_WRITE))
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
        entries.append((pending_addr, WATCH_READ | WATCH_WRITE))
    if len(entries) > MAX_WATCHPOINTS:
        print(f"warning: more than {MAX_WATCHPOINTS} watchpoints given; "
              f"only first {MAX_WATCHPOINTS} will be used", file=sys.stderr)
        entries = entries[:MAX_WATCHPOINTS]
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
                        help="Also append the --debug log to FILE (requires --debug)")
    parser.add_argument("--speed", type=float, default=None,
                        help="Emulation speed multiplier (default: 1.0)")
    parser.add_argument("--scale", type=int, default=None, metavar="N",
                        help="Integer window scale over the 256x212 base (default: 3)")
    parser.add_argument("--mapper", "--mapper1", dest="mapper1",
                        choices=list(VALID_MAPPERS),
                        default=None,
                        help="Slot 1 cartridge mapper type (default: auto — detect from ROM "
                             "database). '--mapper' is accepted as an alias for '--mapper1'")
    parser.add_argument("--slot", "--slot1", dest="slot1", default=None, metavar="ROM1",
                        help="Slot 1 cartridge ROM path (an alternative to the positional "
                             "cartridge argument; conflicts with it). '--slot' is accepted as "
                             "an alias for '--slot1'")
    parser.add_argument("--slot2", default=None, metavar="ROM2",
                        help="Slot 2 cartridge ROM path")
    parser.add_argument("--extension1", choices=[*VALID_EXTENSIONS, "none"], default=None,
                        help="Overlay a slot 1 extension device (same ids as --extension2; "
                             "conflicts with the positional cartridge argument/--slot1/"
                             "--mapper1). 'none' forces no extension overlay even when "
                             "py_emulator.yaml sets one, freeing --slot1/--mapper1 for CLI use")
    parser.add_argument("--extension", "--extension2", dest="extension2",
                        choices=[*VALID_EXTENSIONS, "none"], default=None,
                        help="Overlay a slot 2 extension device: 'fmpac' (MSX-MUSIC + 8 KB "
                             "SRAM), 'scc_plus' (an SCC-I / SCC+ cartridge), 'hbi_j1' "
                             "(Sony HBI-J1: Kanji-ROM + MSX-JE + Kanji driver/BASIC, expands "
                             "slot 2 into two sub-slots), 'memory_512k' (a 512 KB volatile "
                             "RAM-mapper memory-expansion cartridge; requires a machine with "
                             "no existing slot-3 memory mapper), 'msxdos2_512k' (memory_512k "
                             "plus an MSX-DOS2 kernel ROM), 'msxdos2_512k_kanjirom' "
                             "(msxdos2_512k plus a Kanji font ROM) or 'msxdos2_512k_viewfont' "
                             "(msxdos2_512k plus an ASCII8-mapped View font ROM) (conflicts with "
                             "--slot2/--mapper2). 'none' forces no extension overlay even when "
                             "py_emulator.yaml sets one, freeing --slot2/--mapper2 for CLI use. "
                             "'--extension' is accepted as an alias for '--extension2', "
                             "matching its pre-symmetrization meaning")
    parser.add_argument("--mapper2",
                        choices=list(VALID_MAPPERS),
                        default=None,
                        help="Slot 2 mapper type (default: auto; KonamiSCC builds its own "
                             "SCC chip, rejected if the other slot also resolves to "
                             "KonamiSCC or selects --extensionN scc_plus)")
    parser.add_argument("--fdd1", nargs="+", default=None, metavar="DSK",
                        help="Floppy disk image(s) (*.dsk) for drive A. The first is mounted "
                             "at startup; F6 steps through the list. Put a positional "
                             "cartridge before --fdd1 (or use --slot1), or it is read as "
                             "one more disk image")
    parser.add_argument("--fdd2", default=None, metavar="DSK",
                        help="Floppy disk image (*.dsk) to mount in drive B")
    parser.add_argument("--resume", nargs="?", const="", default=None, metavar="STATE_FILE",
                        help="Resume from a save state (default: saves/states/latest.state)")
    parser.add_argument("--mouse", nargs="?", const="2", default=None, choices=["1", "2"],
                        help="Plug an MSX mouse into a joystick port, driven by the host "
                             "mouse (default port 2 if bare). Overrides py_emulator.yaml's "
                             "mouse.enabled/mouse.port. Headless (--count-frame/--benchmark) "
                             "runs attach it too, with no host motion.")
    parser.add_argument("--tape", metavar="CAS", default=None,
                        help="Mount a CAS tape image at startup (WAV is not supported: "
                             "convert it to CAS with an external tool)")
    parser.add_argument("--tape-fast", action=argparse.BooleanOptionalAction,
                        default=True, dest="tape_fast",
                        help="Run as fast-forward while the tape motor is on "
                             "(default: on; --no-tape-fast loads at normal speed)")
    parser.add_argument("--printer-log", metavar="FILE", dest="printer_log", default=None,
                        help="Attach a printer to the printer port and write the raw bytes "
                             "it receives to FILE")
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
                        help="Comma-separated hex breakpoint addresses, max 4")
    parser.add_argument("--watch-point", metavar="ADDRS", default=None,
                        dest="watch_point",
                        help="Comma-separated watchpoints, each ADDR[,MODE], max 4 "
                             "(MODE is r, w or rw, default rw)")
    parser.add_argument("--rpc", action="store_const", const=True, default=None,
                        help="Enable the embedded Unix-socket JSON-RPC control server "
                             "(interactive SDL run mode only; off by default)")
    parser.add_argument("--rpc-socket", metavar="PATH", dest="rpc_socket", default=None,
                        help="Unix socket path for --rpc (default: /tmp/py_msx_emu.sock)")
    return parser


class _ResolvedPaths(NamedTuple):
    fdd1_paths: list[Path]
    fdd2_path: Path | None
    cartridge: bytes | None
    cartridge2: bytes | None


def _resolve_paths(
    args: argparse.Namespace, slot1: str | None, slot2: str | None, fdd1: list[str] | None
) -> _ResolvedPaths:
    """Validate the slot-1/slot-2/floppy paths and read the ROM bytes.

    `slot1` is the already-resolved effective slot 1 path (positional
    cartridge argument, `--slot1`, or `py_emulator.yaml`'s `slot1` key --
    see `_resolve_effective_options`), not `args.cartridge` directly. `fdd1` is
    likewise the resolved drive A disk list (`--fdd1` or the `fdd1` key).

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

    fdd1_paths = [Path(p) for p in fdd1] if fdd1 else []
    fdd2_path = Path(args.fdd2) if args.fdd2 else None
    fdd_checks = [("--fdd1", p) for p in fdd1_paths]
    if fdd2_path is not None:
        fdd_checks.append(("--fdd2", fdd2_path))
    for label, fdd_path in fdd_checks:
        if not fdd_path.exists():
            print(f"error: disk image not found ({label}): {fdd_path}", file=sys.stderr)
            sys.exit(1)

    cartridge = cart_path.read_bytes() if cart_path else None
    cartridge2 = slot2_path.read_bytes() if slot2_path else None
    return _ResolvedPaths(fdd1_paths, fdd2_path, cartridge, cartridge2)


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
        _fail("slot 1 and slot 2 resolve to the identical cartridge ROM -- "
              "loading the same cartridge into both slots is not supported")


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
    extension_overlay: "AnyExtensionOverlay | None",
    extension_id: str | None,
) -> None:
    if extension_overlay is None:
        return
    from msx.machine_spec import ExpandedExtensionOverlay
    if isinstance(extension_overlay, ExpandedExtensionOverlay):
        print(
            f"extension: {extension_id} (expanded slot {slot_num}: "
            f"{len(extension_overlay.subslots)} sub-slot(s)"
            + (" + Kanji-ROM I/O device" if extension_overlay.io_device is not None else "")
            + ")"
        )
    elif extension_overlay.rom_entry is not None:
        # rom_base_dir is always set together with rom_entry (see
        # ExtensionOverlay's docstring: both are None only for a device
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
    spec: "MachineSpec", display_mapper: str, fdd1_paths: list[Path], fdd2_path: Path | None,
    extension_overlay1: "AnyExtensionOverlay | None",
    extension_overlay2: "AnyExtensionOverlay | None",
    mouse_port: int | None, args: argparse.Namespace, tape: str | None = None,
) -> None:
    print(f"machine : {spec.name}")
    print(f"rom_base: {spec.rom_base_dir}")
    print(f"bios    : {spec.main_rom_entry.file}")
    if spec.generation == "msx2" and spec.sub_rom_entry is not None:
        print(f"ext     : {spec.sub_rom_entry.file}")
    if fdd1_paths:
        print(f"fdd1    : {', '.join(str(p) for p in fdd1_paths)}")
    if fdd2_path is not None:
        print(f"fdd2    : {fdd2_path}")
    _print_extension_summary_line(1, extension_overlay1, args.extension1)
    _print_extension_summary_line(2, extension_overlay2, args.extension2)
    if mouse_port is not None:
        print(f"mouse   : Joy{mouse_port + 1}")
    print(f"mapper  : {display_mapper}")
    if args.printer_log is not None:
        print(f"printer : {args.printer_log}")
    if tape is not None:
        print(f"tape    : {tape}")
    if args.vdp_trace:
        print(f"vdp-trace: {'stdout' if args.vdp_trace_out is None else args.vdp_trace_out}")
    if args.mapper_trace:
        print(f"map-trace: {'stdout' if args.mapper_trace_out is None else args.mapper_trace_out}")
    if args.count_frame is not None:
        print(f"frames  : {args.count_frame} (headless)")
    if args.benchmark is not None:
        print(f"benchmark: {args.benchmark} frames (headless)")


def _run_headless_frames(
    machine: "CoreApi", count_frame: int, resume: str | None, debugger: "Debugger"
) -> None:
    """Run exactly `count_frame` frames headlessly (no SDL window).

    No host input and no video: emulated state does not depend on rendering.
    A break opens the debugger; the stopped frame then continues and counts
    once.
    """
    if resume is not None:
        from host.state_files import resume_state
        resume_state(machine, resume)
    _run_frames(machine, count_frame, False, debugger)


def _run_frames(
    machine: "CoreApi", count: int, render_video: bool, debugger: "Debugger"
) -> None:
    """Run `count` complete frames with no host input. A break opens the
    debugger; the stopped frame then continues and counts once."""
    from host.debugger.stop import handle_stop, sigint_requests_break
    from msx.input import InputFrame

    no_input = InputFrame()
    done = 0
    with sigint_requests_break(machine):
        while done < count:
            out = machine.step_frame(no_input, render_video=render_video)
            debugger.drain_trace()
            debugger.drain_printer()
            if out.stop_reason:
                handle_stop(machine, out.stop_reason, debugger, None)
            if out.frame_complete:
                done += 1


def _host_rtc_time() -> "RtcTime":
    """The host's local time, handed to the core once to start the RTC."""
    import datetime

    from msx.boundary import RtcTime

    now = datetime.datetime.now()
    return RtcTime(now.year, now.month, now.day, now.hour, now.minute, now.second)


def _run_benchmark(
    machine: "CoreApi", frames: int, resume: str | None, debugger: "Debugger"
) -> None:
    """Run `frames` frames unthrottled and report average FPS (no SDL window).
    A break opens the debugger, and the time spent there counts."""
    import time

    from host.state_files import resume_state
    if resume is not None:
        resume_state(machine, resume)

    # Every frame renders RGB video, as interactive play does.
    start = time.perf_counter()
    _run_frames(machine, frames, True, debugger)
    elapsed = time.perf_counter() - start

    print(f"frames  : {frames}")
    print(f"elapsed : {elapsed:.2f}s")
    print(f"avg fps : {frames / elapsed:.2f}")


def _cleanup(
    machine: "CoreApi | None", rpc_server: "DebugServer | None", log_file: TextIO | None,
    trace_file: TextIO | None, mapper_trace_file: TextIO | None,
    sram_files: "dict[int, Path]", disk_files: "DiskFiles",
    printer_output: "PrinterOutput | None",
) -> None:
    """Tear down open resources and flush SRAM / disk writes on exit."""
    if rpc_server is not None:
        rpc_server.stop()
    if printer_output is not None:
        printer_output.close()
    if log_file is not None:
        log_file.close()
    if trace_file is not None:
        trace_file.close()
    if mapper_trace_file is not None:
        mapper_trace_file.close()
    if machine is not None:
        # Write every dirty medium (SRAM, RTC CMOS RAM, disk writes from
        # FORMAT / file save) back to its file.
        from host.media import persist_dirty_media
        persist_dirty_media(machine, sram_files, disk_files)
        from msx.machine_loader import release_machine
        release_machine(machine)


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
    fdd1: list[str] | None
    mapper2: str
    extension2: str | None
    frame_skip: str
    tape: str | None


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
    from host.app_config import DEFAULT_MAPPER, DEFAULT_SCALE, DEFAULT_SPEED

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
        # A CLI --fdd1 list replaces the config list whole, not merged.
        fdd1=_first_set(args.fdd1, app_cfg.fdd1, default=None),
        mapper2=_first_set(args.mapper2, default="auto"),
        extension2=_resolve_extension(args.extension2, app_cfg.extension2),
        frame_skip=_first_set(args.frame_skip, frame_skip_cfg, default="auto"),
        tape=_first_set(args.tape, app_cfg.tape, default=None),
    )


def _check(machine: "CoreApi", status: int) -> None:
    """Stop with the core's message when a setup call did not return
    STATUS_OK. The CLI checks most values first, so this mainly guards a core
    that disagrees. --printer-log on a machine with no port stops here."""
    from msx.boundary import STATUS_OK

    if status != STATUS_OK:
        _fail(machine.last_error_text().decode("utf-8"))


def _fail(msg: str) -> NoReturn:
    """Print a startup-validation error to stderr and exit (code 1)."""
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _validate_options(args: argparse.Namespace, opts: _EffectiveOptions) -> None:
    """Enforce mutual-exclusivity and range constraints on resolved options.

    Prints an error to stderr and exits the process (code 1) on violation.
    """
    if args.benchmark is not None and args.count_frame is not None:
        _fail("--benchmark and --count-frame are mutually exclusive")
    if args.benchmark is not None and args.printer_log is not None:
        _fail("--printer-log and --benchmark are mutually exclusive: "
              "the file output would change the measured time")
    if opts.scale < 1:
        _fail("--scale must be a positive integer")
    if args.cartridge and args.slot1:
        _fail("a positional cartridge argument and --slot1 are mutually "
              "exclusive (the positional argument is --slot1 shorthand)")
    if opts.extension1 is not None and (args.slot1 or args.cartridge):
        _fail("--extension1 and --slot1/a positional cartridge argument are "
              "mutually exclusive (the extension owns slot 1)")
    # Checked against args.mapperN, not opts.mapperN: opts.mapperN folds in
    # the "auto" default, so it can't distinguish an explicit --mapperN auto
    # from --mapperN never having been passed. --mapper1/--mapper2 have no
    # config-file equivalent (see app-config-file spec), so only the CLI
    # flag needs checking here.
    if opts.extension1 is not None and args.mapper1 is not None:
        _fail("--extension1 and --mapper1 are mutually exclusive (the "
              "extension owns slot 1)")
    if opts.extension2 is not None and opts.slot2:
        _fail("--extension2 and --slot2 are mutually exclusive (the "
              "extension owns slot 2)")
    if opts.extension2 is not None and args.mapper2 is not None:
        _fail("--extension2 and --mapper2 are mutually exclusive (the "
              "extension owns slot 2)")
    if (
        opts.extension1 is not None
        and opts.extension2 is not None
        and opts.extension1 == opts.extension2
    ):
        _fail(f"--extension1 and --extension2 both resolve to "
              f"{opts.extension1!r} -- the identical extension on both "
              "slots is not supported")


def _load_spec_and_overlays(
    machine_id: str, opts: _EffectiveOptions,
) -> tuple["MachineSpec", "AnyExtensionOverlay | None", "AnyExtensionOverlay | None"]:
    """The machine spec and the two extension overlays from the YAML, or
    stop with the loader's message."""
    from host.machine_config import (
        load_device_registry,
        load_extension_overlay,
        load_machine_spec,
    )
    from msx.machine_spec import MachineLoadError

    try:
        device_registry = load_device_registry(_CONFIG_DIR)
        spec = load_machine_spec(machine_id, _CONFIG_DIR, device_registry, _PROJECT_ROOT)
        extension_overlay1 = (
            load_extension_overlay(opts.extension1, _CONFIG_DIR, _PROJECT_ROOT)
            if opts.extension1 is not None else None
        )
        extension_overlay2 = (
            load_extension_overlay(opts.extension2, _CONFIG_DIR, _PROJECT_ROOT)
            if opts.extension2 is not None else None
        )
    except MachineLoadError as exc:
        _fail(str(exc))
    return spec, extension_overlay1, extension_overlay2


def _rom_names(spec: "MachineSpec") -> tuple[str, str]:
    """The main and sub ROM file names the debugger's sl/st print ("" for
    none): the core keeps no ROM name."""
    sub = spec.sub_rom_entry.file if spec.sub_rom_entry is not None else ""
    return spec.main_rom_entry.file, sub


def _disks_for(
    spec: "MachineSpec", fdd1_paths: list[Path], fdd2_path: Path | None,
) -> tuple[list[Path], Path | None]:
    """The disk images to mount: none, with a warning, on a machine without
    a floppy interface."""
    if (fdd1_paths or fdd2_path is not None) and spec.fdc is None:
        print("warning: --fdd1/--fdd2 given but machine has no floppy interface; "
              "ignoring", file=sys.stderr)
        return [], None
    return fdd1_paths, fdd2_path


def _set_break_points(
    machine: "CoreApi", breakpoint_addrs: list[int], watchpoint_entries: list[tuple[int, int]],
) -> None:
    """Set the --break-point and --watch-point lists and print them."""
    if breakpoint_addrs:
        _check(machine, machine.set_breakpoints(breakpoint_addrs))
        print(f"break   : {', '.join(f'{a:04X}h' for a in breakpoint_addrs)}")
    if watchpoint_entries:
        _check(machine, machine.set_watchpoints(watchpoint_entries))
        print(f"watch   : {_watch_summary(watchpoint_entries)}")


def _open_trace_outputs(
    args: argparse.Namespace,
) -> tuple["TraceOutput", TextIO | None, TextIO | None]:
    """The run's trace outputs, opened before the build: the VDP trace
    stream and, with --debug, the debug log (stderr, and with --log appended
    to FILE). Returns the outputs, the VDP trace file and the log file (None
    when not opened)."""
    from host.trace import TraceOutput

    trace_output = TraceOutput()
    trace_file = None
    if args.vdp_trace_out:
        # Opened without --vdp-trace too: the debugger's `te` writes here.
        trace_file = open(args.vdp_trace_out, "w", encoding="utf-8")
        trace_output.set_vdp_output(trace_file, args.vdp_trace_out)
    elif args.vdp_trace:
        trace_output.set_vdp_output(sys.stdout, "stdout")
    log_file = None
    if args.debug:
        if args.log:
            log_file = open(args.log, "a", encoding="utf-8")
        trace_output.set_debug_output(log_file)
    return trace_output, trace_file, log_file


def _open_printer_output(machine: "CoreApi", path: str) -> "PrinterOutput":
    """Attach a printer and open --printer-log's file. On a machine with no
    port it stops here, before the file exists."""
    from host.printer_output import PrinterOutput

    _check(machine, machine.attach_printer())
    try:
        return PrinterOutput(machine, Path(path))
    except OSError as exc:
        _fail(f"--printer-log: {exc}")


def _mount_tape(deck: "TapeDeck", path: str) -> None:
    """Convert and mount --tape's CAS file; stops with an error that names
    --tape when the file or the machine cannot take it."""
    from host.tape import TapeError

    try:
        warnings = deck.load(Path(path))
    except TapeError as exc:
        _fail(f"--tape: {exc}")
    for warning in warnings:
        print(f"warning: --tape: {warning}", file=sys.stderr)


def _start_traces(
    machine: "CoreApi", args: argparse.Namespace, trace_output: "TraceOutput"
) -> TextIO | None:
    """Turn on the trace kinds the flags ask for, after the build, and open
    the mapper trace output. Returns the mapper trace file (None when not
    opened)."""
    from msx.boundary import TRACE_DEBUG, TRACE_MAPPER, TRACE_VDP

    if args.debug:
        _check(machine, machine.set_trace_kinds(machine.trace_kinds() | TRACE_DEBUG))
    if args.vdp_trace:
        _check(machine, machine.set_trace_kinds(machine.trace_kinds() | TRACE_VDP))
    mapper_trace_file = None
    if args.mapper_trace_out:
        # Opened without --mapper-trace too: the debugger's `ce` writes here.
        mapper_trace_file = open(args.mapper_trace_out, "w", encoding="utf-8")
        trace_output.set_map_output(mapper_trace_file, args.mapper_trace_out)
    elif args.mapper_trace:
        trace_output.set_map_output(sys.stdout, "stdout")
    if args.mapper_trace:
        if machine.trace_sources() & TRACE_MAPPER:
            _check(machine, machine.set_trace_kinds(machine.trace_kinds() | TRACE_MAPPER))
        else:
            print("mapper-trace: no bank-switching ROM mapper present", file=sys.stderr)
    return mapper_trace_file


def main() -> None:
    args = _build_arg_parser().parse_args()

    from host.app_config import AppConfigError, load_app_config
    try:
        app_cfg = load_app_config(_PROJECT_ROOT)
    except AppConfigError as exc:
        _fail(str(exc))

    opts = _resolve_effective_options(args, app_cfg)
    _validate_options(args, opts)

    from host.romdb import lookup, lookup_system, lookup_title

    paths = _resolve_paths(args, opts.slot1, opts.slot2, opts.fdd1)
    fdd1_paths = paths.fdd1_paths
    fdd2_path = paths.fdd2_path
    cartridge = paths.cartridge
    cartridge2 = paths.cartridge2
    _check_identical_cartridges(cartridge, cartridge2)

    db_system = lookup_system(cartridge) if cartridge else None
    machine_id = _resolve_machine_id(args, app_cfg, db_system)

    # --- Load machine spec from YAML ---
    from host.machine_config import load_machine_config, sram_paths
    from msx.machine_loader import create_machine
    from msx.machine_spec import MachineLoadError

    spec, extension_overlay1, extension_overlay2 = _load_spec_and_overlays(machine_id, opts)

    fdd1_paths, fdd2_path = _disks_for(spec, fdd1_paths, fdd2_path)

    # --- Resolve display mapper for summary ---
    display_mapper = opts.mapper1
    if display_mapper == "auto" and cartridge:
        display_mapper = lookup(cartridge) or "auto"

    breakpoint_addrs = _parse_breakpoints(args.break_point)
    watchpoint_entries = _parse_watchpoints(args.watch_point)

    _print_startup_summary(
        spec, display_mapper, fdd1_paths, fdd2_path, extension_overlay1, extension_overlay2,
        opts.mouse_port, args, opts.tape,
    )

    # --- Trace outputs (the kinds are turned on after the build) ---
    trace_output, trace_file, log_file = _open_trace_outputs(args)
    mapper_trace_file = None

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    machine = None
    rpc_server = None
    printer_output = None
    sram_files: "dict[int, Path]" = {}
    from host.media import DiskFiles
    # The file of each drive's disk: one table for the debugger, the RPC
    # server, the frontend and the write-back on exit.
    disk_files = DiskFiles()
    try:
        try:
            config = load_machine_config(
                spec,
                cartridge1=cartridge,
                mapper1=opts.mapper1,
                cartridge2=cartridge2,
                mapper2=opts.mapper2,
                fdd1=fdd1_paths[0] if fdd1_paths else None,
                fdd2=fdd2_path,
                extension_overlay1=extension_overlay1,
                extension_overlay2=extension_overlay2,
                rtc_initial_time=_host_rtc_time(),
            )
            sram_files = sram_paths(config)
            disk_files = DiskFiles(config.disk_files)
        except MachineLoadError as exc:
            _fail(str(exc))
        result = create_machine(config.config)
        if result.machine is None:
            _fail(result.error_text.decode("utf-8"))
        machine = result.machine
        from host.tape import TapeDeck
        # The mounted tape file and its blocks, shared like disk_files.
        tape_deck = TapeDeck(machine)
        if opts.tape is not None:
            _mount_tape(tape_deck, opts.tape)
        if args.printer_log is not None:
            printer_output =_open_printer_output(machine, args.printer_log)

        # The interactive debugger for all machines, so Ctrl-C drops into the
        # REPL on MSX1 too (the slot/mapper tools are most useful there). The
        # host opens it when step_frame stops (host/debugger/stop.py).
        from host.debugger.prompt import Debugger
        debugger = Debugger(
            machine, disk_files, trace_output, _rom_names(spec), printer_output, tape_deck,
        )

        _set_break_points(machine, breakpoint_addrs, watchpoint_entries)

        mapper_trace_file = _start_traces(machine, args, trace_output)

        headless = args.count_frame is not None or args.benchmark is not None
        if headless and opts.mouse_port is not None:
            # The SDL2 run attaches in run(), with the host mouse. A headless
            # run attaches here, before its resume: a state saved with a mouse
            # loads only on a machine with one (machine-core "The host
            # attaches the mouse at run time").
            _check(machine, machine.attach_mouse(opts.mouse_port))
        if args.count_frame is not None:
            _run_headless_frames(machine, args.count_frame, args.resume, debugger)
        elif args.benchmark is not None:
            _run_benchmark(machine, args.benchmark, args.resume, debugger)
        else:
            if opts.rpc_enabled:
                from host.rpc_server import DEFAULT_SOCKET_PATH, DebugServer
                sock_path = args.rpc_socket or app_cfg.rpc_socket or DEFAULT_SOCKET_PATH
                rpc_server = DebugServer(
                    machine, sock_path=sock_path, disk_files=disk_files, tape_deck=tape_deck,
                )
                rpc_server.start()
                print(f"rpc     : {sock_path}")
            from frontend.sdl2_frontend import run
            from frontend.sdl2_joystick import resolve_gamepad_maps
            from frontend.sdl2_keyboard import resolve_hotkeys
            run(machine, scale=opts.scale, speed=opts.speed, game_title=game_title,
                resume=args.resume, frame_skip=opts.frame_skip, rpc_server=rpc_server,
                gamepad_map=resolve_gamepad_maps(app_cfg.gamepad_buttons_resolved()),
                turbo_period=app_cfg.turbo_period(),
                mouse_port=opts.mouse_port, fdd1_images=fdd1_paths,
                hotkey_bindings=resolve_hotkeys(app_cfg.hotkey_bindings()),
                keyboard_joystick=app_cfg.keyboard_joystick_buttons,
                debugger=debugger, disk_files=disk_files, tape_fast=args.tape_fast)
    finally:
        _cleanup(
            machine, rpc_server, log_file, trace_file, mapper_trace_file, sram_files, disk_files,
            printer_output,
        )


if __name__ == "__main__":
    main()
