"""Entry point: python . [--machine MACHINE_ID] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


def main() -> None:
    parser = argparse.ArgumentParser(description="py-msx-emulator")
    parser.add_argument("cartridge", nargs="?", default=None,
                        help="Cartridge ROM path")
    parser.add_argument("--machine", metavar="MACHINE_ID", default=None,
                        help="Machine configuration ID (e.g. cbios_msx1, cbios_msx2_jp)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable diagnostic logging")
    parser.add_argument("--log", metavar="FILE",
                        help="Write diagnostic log to FILE (requires --debug)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Emulation speed multiplier (default: 1.0)")
    parser.add_argument("--mapper",
                        choices=["auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
                                 "Konami", "KonamiSCC", "Majutsushi",
                                 "ASCII8SRAM2", "ASCII8SRAM8", "ASCII16SRAM2", "ASCII16SRAM8",
                                 "R-Type"],
                        default="auto",
                        help="Cartridge mapper type (default: auto — detect from ROM database)")
    parser.add_argument("--slot2", default=None, metavar="ROM2",
                        help="Slot 2 cartridge ROM path")
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
    parser.add_argument("--benchmark", nargs="?", type=float, const=10.0, default=None,
                        metavar="SECONDS",
                        help="Run headlessly, unthrottled, for SECONDS (default: 10) and "
                             "report average FPS (no SDL window)")
    parser.add_argument("--break-point", metavar="ADDRS", default=None,
                        dest="break_point",
                        help="Comma-separated hex breakpoint addresses, max 4 (MSX2 only)")
    parser.add_argument("--watch-point", metavar="ADDRS", default=None,
                        dest="watch_point",
                        help="Comma-separated watchpoint addresses, max 4 (MSX2 only; "
                             "breaks on read or write)")
    parser.add_argument("--rpc", action="store_true",
                        help="Enable the embedded Unix-socket JSON-RPC control server "
                             "(interactive SDL run mode only; off by default)")
    parser.add_argument("--rpc-socket", metavar="PATH", dest="rpc_socket", default=None,
                        help="Unix socket path for --rpc (default: /tmp/py_msx_emu.sock)")
    args = parser.parse_args()

    if args.benchmark is not None and args.count_frame is not None:
        print("error: --benchmark and --count-frame are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    from msx.romdb import lookup, lookup_system, lookup_title

    # --- Load cartridge bytes early (needed for DB lookups) ---
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

    cartridge: bytes | None = cart_path.read_bytes() if cart_path else None
    cartridge2: bytes | None = slot2_path.read_bytes() if slot2_path else None

    # --- Resolve machine ID ---
    # Priority: --machine > ROM DB (MSX1 → cbios_msx1_jp) > default cbios_msx2_jp
    db_system = lookup_system(cartridge) if cartridge else None
    if args.machine is not None:
        machine_id = args.machine
    elif db_system == "MSX":
        machine_id = "cbios_msx1_jp"
    else:
        machine_id = "cbios_msx2_jp"

    # --- Load machine spec from YAML ---
    from msx.machine_loader import (
        MachineLoadError,
        build_machine,
        load_device_registry,
        load_machine_spec,
    )
    try:
        device_registry = load_device_registry(_CONFIG_DIR)
        spec = load_machine_spec(machine_id, _CONFIG_DIR, device_registry, _PROJECT_ROOT)
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
    if args.mapper != "auto":
        display_mapper = args.mapper
    elif cartridge:
        display_mapper = lookup(cartridge) or "auto"
    else:
        display_mapper = "auto"

    # --- Parse --break-point ---
    breakpoint_addrs: list[int] = []
    if args.break_point is not None:
        for tok in args.break_point.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                breakpoint_addrs.append(int(tok, 16) & 0xFFFF)
            except ValueError:
                print(f"error: invalid breakpoint address: {tok!r} (expected hex)",
                      file=sys.stderr)
                sys.exit(1)
        if len(breakpoint_addrs) > 4:
            print("warning: more than 4 breakpoints given; only first 4 will be used",
                  file=sys.stderr)
            breakpoint_addrs = breakpoint_addrs[:4]

    # --- Parse --watch-point ---
    watchpoint_entries: list[tuple[int, str]] = []
    if args.watch_point is not None:
        _pending_addr: int | None = None
        for tok in args.watch_point.split(","):
            tok = tok.strip().lower()
            if not tok:
                continue
            if tok in ("r", "w", "rw"):
                if _pending_addr is not None:
                    watchpoint_entries.append((_pending_addr, tok))
                    _pending_addr = None
                else:
                    print(f"error: mode {tok!r} without preceding address", file=sys.stderr)
                    sys.exit(1)
            else:
                if _pending_addr is not None:
                    watchpoint_entries.append((_pending_addr, "rw"))
                try:
                    _pending_addr = int(tok, 16) & 0xFFFF
                except ValueError:
                    print(
                        f"error: invalid watchpoint token: {tok!r} "
                        f"(hex address or r/w/rw expected)",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        if _pending_addr is not None:
            watchpoint_entries.append((_pending_addr, "rw"))
        if len(watchpoint_entries) > 4:
            print("warning: more than 4 watchpoints given; only first 4 will be used",
                  file=sys.stderr)
            watchpoint_entries = watchpoint_entries[:4]

    # --- Startup summary ---
    print(f"machine : {spec.name}")
    print(f"rom_base: {spec.rom_base_dir}")
    print(f"bios    : {spec.main_rom_entry.file}")
    if spec.generation == "msx2" and spec.sub_rom_entry is not None:
        print(f"ext     : {spec.sub_rom_entry.file}")
    if fdd1_path is not None:
        print(f"fdd1    : {fdd1_path}")
    if fdd2_path is not None:
        print(f"fdd2    : {fdd2_path}")
    print(f"mapper  : {display_mapper}")
    if args.vdp_trace:
        print(f"vdp-trace: {'stdout' if args.vdp_trace_out is None else args.vdp_trace_out}")
    if args.mapper_trace:
        print(f"map-trace: {'stdout' if args.mapper_trace_out is None else args.mapper_trace_out}")
    if args.count_frame is not None:
        print(f"frames  : {args.count_frame} (headless)")
    if args.benchmark is not None:
        print(f"benchmark: {args.benchmark}s (headless)")

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
                mapper=args.mapper,
                cartridge2=cartridge2,
                mapper2=args.mapper2,
                logger=logger,
                tracer=tracer,
                fdd1=fdd1_path,
                fdd2=fdd2_path,
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
            for _ in range(args.count_frame):
                machine.run_frame()
        elif args.benchmark is not None:
            import time

            from msx.state import load_state
            if args.resume is not None:
                load_state(machine, path=Path(args.resume) if args.resume else None)

            frame_count = 0
            start = time.perf_counter()
            deadline = start + args.benchmark
            while time.perf_counter() < deadline:
                machine.run_frame()
                frame_count += 1
            elapsed = time.perf_counter() - start

            print(f"frames  : {frame_count}")
            print(f"elapsed : {elapsed:.2f}s")
            print(f"avg fps : {frame_count / elapsed:.2f}")
        else:
            if args.rpc:
                from msx.rpc_server import DEFAULT_SOCKET_PATH, DebugServer
                sock_path = args.rpc_socket or DEFAULT_SOCKET_PATH
                rpc_server = DebugServer(machine, sock_path=sock_path)
                machine.set_pause_hook(rpc_server.on_pause)
                rpc_server.start()
                print(f"rpc     : {sock_path}")
            from frontend.sdl2_frontend import run
            run(machine, speed=args.speed, game_title=game_title, resume=args.resume,
                frame_skip=args.frame_skip, rpc_server=rpc_server)
    finally:
        if rpc_server is not None:
            rpc_server.stop()
        if logger is not None:
            logger.close()
        if _trace_file is not None:
            _trace_file.close()
        if _mapper_trace_file is not None:
            _mapper_trace_file.close()
        if machine is not None and machine.sram_save_path is not None:
            mapper = machine.memory._mapper
            if hasattr(mapper, "save_sram"):
                machine.sram_save_path.parent.mkdir(parents=True, exist_ok=True)
                mapper.save_sram(machine.sram_save_path)
        # Flush any disk writes (FORMAT / file save) back to the *.dsk on exit.
        if machine is not None and machine.fdc is not None:
            machine.fdc.flush()


if __name__ == "__main__":
    main()
