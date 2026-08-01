"""Entry point: python . [--machine MACHINE_ID] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, TypeVar

_PROJECT_ROOT = Path(__file__).parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# Hardware debug register count (MSX2): caps the breakpoint/watchpoint lists.
_MAX_BREAKPOINTS = 4

_T = TypeVar("_T")


def _first_set(*candidates: _T | None) -> _T:
    """Return the first non-None value (CLI < config file < built-in default)."""
    return next(v for v in candidates if v is not None)


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
    parser.add_argument("--mapper",
                        choices=["auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
                                 "Konami", "KonamiSCC", "Majutsushi",
                                 "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
                                 "R-Type"],
                        default=None,
                        help="Cartridge mapper type (default: auto — detect from ROM database)")
    parser.add_argument("--slot2", default=None, metavar="ROM2",
                        help="Slot 2 cartridge ROM path")
    parser.add_argument("--fmpac", action="store_const", const=True, default=None,
                        help="Overlay an FM-PAC (MSX-MUSIC + 8 KB SRAM) cartridge in slot 2 "
                             "(conflicts with --slot2)")
    parser.add_argument("--mapper2",
                        choices=["auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
                                 "Konami", "Majutsushi"],
                        default="auto",
                        help="Slot 2 mapper type (default: auto; KonamiSCC not supported)")
    parser.add_argument("--fdd1", default=None, metavar="DSK",
                        help="Floppy disk image (*.dsk) to mount in drive A")
    parser.add_argument("--fdd2", default=None, metavar="DSK",
                        help="Floppy disk image (*.dsk) to mount in drive B")
    parser.add_argument("--resume", nargs="?", const="", default=None, metavar="STATE_FILE",
                        help="Resume from a save state (default: saves/states/latest.state)")
    parser.add_argument("--frame-skip", choices=["auto", "none"], default="auto",
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


def _resolve_paths(
    args: argparse.Namespace,
) -> tuple[Path | None, Path | None, Path | None, Path | None, bytes | None, bytes | None]:
    """Validate the cartridge/slot-2/floppy paths and read the ROM bytes.

    Exits the process with an error if any given path does not exist. Returns
    (cart_path, slot2_path, fdd1_path, fdd2_path, cartridge, cartridge2).
    """
    cart_path = Path(args.cartridge) if args.cartridge else None
    slot2_path = Path(args.slot2) if args.slot2 else None
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
    return cart_path, slot2_path, fdd1_path, fdd2_path, cartridge, cartridge2


def _resolve_machine_id(args: argparse.Namespace, app_cfg: Any, db_system: str | None) -> str:
    """Pick the machine ID: --machine > config file > ROM DB (MSX1) > MSX2 default."""
    if args.machine is not None:
        return str(args.machine)
    if app_cfg.machine is not None:
        return str(app_cfg.machine)
    if db_system == "MSX":
        return "cbios_msx1_jp"
    return "cbios_msx2_jp"


def _print_startup_summary(
    spec: Any, display_mapper: str, fdd1_path: Path | None, fdd2_path: Path | None,
    fmpac_overlay: Any, args: argparse.Namespace,
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
    if fmpac_overlay is not None:
        print(f"fmpac   : {fmpac_overlay.rom_base_dir / fmpac_overlay.rom_entry.file}")
    print(f"mapper  : {display_mapper}")
    if args.vdp_trace:
        print(f"vdp-trace: {'stdout' if args.vdp_trace_out is None else args.vdp_trace_out}")
    if args.mapper_trace:
        print(f"map-trace: {'stdout' if args.mapper_trace_out is None else args.mapper_trace_out}")
    if args.count_frame is not None:
        print(f"frames  : {args.count_frame} (headless)")
    if args.benchmark is not None:
        print(f"benchmark: {args.benchmark} frames (headless)")


def _run_headless_frames(machine: Any, count_frame: int, resume: str | None) -> None:
    """Run exactly `count_frame` frames headlessly (no SDL window)."""
    if resume is not None:
        from msx.state import load_state
        load_state(machine, path=Path(resume) if resume else None)
    for _ in range(count_frame):
        machine.run_frame()


def _run_benchmark(machine: Any, frames: int, resume: str | None) -> None:
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


def _cleanup(
    machine: Any, rpc_server: Any, logger: Any, trace_file: Any, mapper_trace_file: Any,
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
    if machine is not None and machine.sram_save_path is not None:
        mapper = machine.memory._mapper
        if hasattr(mapper, "save_sram"):
            machine.sram_save_path.parent.mkdir(parents=True, exist_ok=True)
            mapper.save_sram(machine.sram_save_path)
    if (
        machine is not None
        and machine.fmpac is not None
        and machine.fmpac_sram_save_path is not None
    ):
        machine.fmpac_sram_save_path.parent.mkdir(parents=True, exist_ok=True)
        machine.fmpac.save_sram(machine.fmpac_sram_save_path)
    # Flush any disk writes (FORMAT / file save) back to the *.dsk on exit.
    if machine is not None and machine.fdc is not None:
        machine.fdc.flush()


def main() -> None:
    args = _build_arg_parser().parse_args()

    # Load py_emulator.yaml and resolve effective options with the precedence
    # built-in default < config file < CLI argument. A CLI flag left unset is
    # None (see the sentinel defaults above), so it never masks a config value.
    from msx.app_config import (
        DEFAULT_MAPPER,
        DEFAULT_SPEED,
        AppConfigError,
        load_app_config,
    )
    try:
        app_cfg = load_app_config(_PROJECT_ROOT)
    except AppConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    speed_eff = _first_set(args.speed, app_cfg.speed, DEFAULT_SPEED)
    mapper_eff = _first_set(args.mapper, app_cfg.mapper, DEFAULT_MAPPER)
    fmpac_eff = _first_set(args.fmpac, app_cfg.fmpac, False)
    rpc_enabled_eff = _first_set(args.rpc, app_cfg.rpc_enabled, False)

    if args.benchmark is not None and args.count_frame is not None:
        print("error: --benchmark and --count-frame are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    if fmpac_eff and args.slot2:
        print("error: --fmpac and --slot2 are mutually exclusive (FM-PAC owns slot 2)",
              file=sys.stderr)
        sys.exit(1)

    from msx.romdb import lookup, lookup_system, lookup_title

    (cart_path, slot2_path, fdd1_path, fdd2_path,
     cartridge, cartridge2) = _resolve_paths(args)

    db_system = lookup_system(cartridge) if cartridge else None
    machine_id = _resolve_machine_id(args, app_cfg, db_system)

    # --- Load machine spec from YAML ---
    from msx.machine_loader import (
        MachineLoadError,
        build_machine,
        load_device_registry,
        load_fmpac_overlay,
        load_machine_spec,
    )
    try:
        device_registry = load_device_registry(_CONFIG_DIR)
        spec = load_machine_spec(machine_id, _CONFIG_DIR, device_registry, _PROJECT_ROOT)
        fmpac_overlay = load_fmpac_overlay(_CONFIG_DIR, _PROJECT_ROOT) if fmpac_eff else None
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
    if mapper_eff != "auto":
        display_mapper = mapper_eff
    elif cartridge:
        display_mapper = lookup(cartridge) or "auto"
    else:
        display_mapper = "auto"

    breakpoint_addrs = _parse_breakpoints(args.break_point)
    watchpoint_entries = _parse_watchpoints(args.watch_point)

    _print_startup_summary(spec, display_mapper, fdd1_path, fdd2_path, fmpac_overlay, args)

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
                mapper=mapper_eff,
                cartridge2=cartridge2,
                mapper2=args.mapper2,
                logger=logger,
                tracer=tracer,
                fdd1=fdd1_path,
                fdd2=fdd2_path,
                fmpac_overlay=fmpac_overlay,
            )
        except MachineLoadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        # Attach the interactive debugger for all machines so Ctrl-C drops into
        # the REPL on MSX1 too (the slot/mapper tools are most useful there).
        from msx.debugger.prompt import Debugger
        machine._debugger = Debugger(machine)

        if spec.generation == "msx2":
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
            if rpc_enabled_eff:
                from msx.rpc_server import DEFAULT_SOCKET_PATH, DebugServer
                sock_path = args.rpc_socket or app_cfg.rpc_socket or DEFAULT_SOCKET_PATH
                rpc_server = DebugServer(machine, sock_path=sock_path)
                machine.set_pause_hook(rpc_server.on_pause)
                rpc_server.start()
                print(f"rpc     : {sock_path}")
            from frontend.sdl2_frontend import run
            run(machine, speed=speed_eff, game_title=game_title, resume=args.resume,
                frame_skip=args.frame_skip, rpc_server=rpc_server,
                gamepad_map=app_cfg.gamepad_maps(), turbo_period=app_cfg.turbo_period())
    finally:
        _cleanup(machine, rpc_server, logger, _trace_file, _mapper_trace_file)


if __name__ == "__main__":
    main()
