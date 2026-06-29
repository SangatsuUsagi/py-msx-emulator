"""Entry point: python . [--machine MACHINE_ID] [--msx2] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# Logo ROM filename map — used only when --biosrom overrides the default BIOS path.
# Keyed by BIOS filename; value is the companion logo ROM in the same directory.
_LOGO_ROM_MAP: dict[str, str] = {
    "cbios_main_msx1.rom":    "cbios_logo_msx1.rom",
    "cbios_main_msx1_jp.rom": "cbios_logo_msx1.rom",
    "cbios_main_msx1_eu.rom": "cbios_logo_msx1.rom",
    "cbios_main_msx1_br.rom": "cbios_logo_msx1.rom",
    "cbios_main_msx2.rom":    "cbios_logo_msx2.rom",
    "cbios_main_msx2_jp.rom": "cbios_logo_msx2.rom",
    "cbios_main_msx2_eu.rom": "cbios_logo_msx2.rom",
    "cbios_main_msx2_br.rom": "cbios_logo_msx2.rom",
}


def _find_logo_rom(bios_path: Path) -> bytes | None:
    """Return companion logo ROM bytes for a given BIOS path, or None."""
    logo_name = _LOGO_ROM_MAP.get(bios_path.name)
    if logo_name is None:
        return None
    logo_path = bios_path.parent / logo_name
    return logo_path.read_bytes() if logo_path.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="py-msx-emulator")
    parser.add_argument("cartridge", nargs="?", default=None,
                        help="Cartridge ROM path")
    parser.add_argument("--machine", metavar="MACHINE_ID", default=None,
                        help="Machine configuration ID (e.g. cbios_msx1, cbios_msx2_jp)")
    parser.add_argument("--biosrom", metavar="BIOS_PATH", default=None,
                        help="Main BIOS ROM path (overrides machine YAML ROM file)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable diagnostic logging")
    parser.add_argument("--log", metavar="FILE",
                        help="Write diagnostic log to FILE (requires --debug)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Emulation speed multiplier (default: 1.0)")
    parser.add_argument("--mapper",
                        choices=["auto", "Mirrored", "Normal", "ASCII8", "ASCII16",
                                 "Konami", "KonamiSCC"],
                        default="auto",
                        help="Cartridge mapper type (default: auto — detect from ROM database)")
    parser.add_argument("--slot2", default=None, metavar="ROM2",
                        help="Slot 2 cartridge ROM path")
    parser.add_argument("--mapper2",
                        choices=["auto", "Mirrored", "Normal", "ASCII8", "ASCII16", "Konami"],
                        default="auto",
                        help="Slot 2 mapper type (default: auto; KonamiSCC not supported)")
    parser.add_argument("--resume", nargs="?", const="", default=None, metavar="STATE_FILE",
                        help="Resume from a save state (default: saves/latest.state)")
    parser.add_argument("--frame-skip", choices=["auto", "none"], default="auto",
                        dest="frame_skip",
                        help="Frame skip mode: auto (default) or none to disable")
    parser.add_argument("--msx2", action="store_true",
                        help="Use MSX2 machine (alias for --machine cbios_msx2)")
    parser.add_argument("--extrom", metavar="EXTROM_PATH", default=None,
                        help="MSX2 extension BIOS ROM path (overrides machine YAML sub ROM)")
    parser.add_argument("--vdp-trace", action="store_true", dest="vdp_trace",
                        help="Enable VDP register write tracing (VDP Trace Log Format)")
    parser.add_argument("--vdp-trace-out", metavar="FILE", dest="vdp_trace_out", default=None,
                        help="Write VDP trace to FILE instead of stdout")
    parser.add_argument("--count", type=int, default=None, metavar="N",
                        help="Run exactly N CPU T-states headlessly and exit (no SDL window)")
    parser.add_argument("--break-point", metavar="ADDRS", default=None,
                        dest="break_point",
                        help="Comma-separated hex breakpoint addresses, max 4 (MSX2 only)")
    parser.add_argument("--watch-point", metavar="ADDRS", default=None,
                        dest="watch_point",
                        help="Comma-separated watchpoint addresses, max 4 (MSX2 only; "
                             "breaks on read or write)")
    args = parser.parse_args()

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

    cartridge: bytes | None = cart_path.read_bytes() if cart_path else None
    cartridge2: bytes | None = slot2_path.read_bytes() if slot2_path else None

    # --- Resolve machine ID ---
    # Priority: --machine > --msx2 > ROM DB auto-detect > default cbios_msx1
    db_system = lookup_system(cartridge) if cartridge else None
    if args.machine is not None:
        machine_id = args.machine
    elif args.msx2:
        machine_id = "cbios_msx2"
    elif db_system == "MSX2":
        machine_id = "cbios_msx2"
    else:
        machine_id = "cbios_msx1"

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

    # --- ROM overrides from --biosrom / --extrom ---
    bios_override: bytes | None = None
    logo_override: bytes | None = None
    extrom_override: bytes | None = None

    if args.biosrom is not None:
        bios_path = Path(args.biosrom)
        if not bios_path.exists():
            print(f"error: BIOS ROM not found: {bios_path}", file=sys.stderr)
            sys.exit(1)
        bios_override = bios_path.read_bytes()
        logo_override = _find_logo_rom(bios_path)

    if args.extrom is not None:
        if spec.generation != "msx2":
            print("warning: --extrom ignored (machine is not MSX2)", file=sys.stderr)
        else:
            extrom_path = Path(args.extrom)
            if not extrom_path.exists():
                print(f"error: ext ROM not found: {extrom_path}", file=sys.stderr)
                sys.exit(1)
            extrom_override = extrom_path.read_bytes()

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
    if bios_override is not None:
        print(f"bios    : {args.biosrom} (override)")
    else:
        print(f"bios    : {spec.main_rom_entry.file}")
    if extrom_override is not None:
        print(f"ext     : {args.extrom} (override)")
    elif spec.generation == "msx2" and spec.sub_rom_entry is not None:
        print(f"ext     : {spec.sub_rom_entry.file}")
    print(f"mapper  : {display_mapper}")
    if args.vdp_trace:
        print(f"vdp-trace: {'stdout' if args.vdp_trace_out is None else args.vdp_trace_out}")
    if args.count is not None:
        print(f"count   : {args.count} T-states (headless)")

    from msx.debug.logger import DebugLogger
    from msx.vdp.tracer import Tracer

    # --- Build tracer ---
    tracer: Tracer | None = None
    _trace_file = None
    if args.vdp_trace:
        if args.vdp_trace_out:
            _trace_file = open(args.vdp_trace_out, "w", encoding="utf-8")
        tracer = Tracer(enabled=True, output=_trace_file if _trace_file else sys.stdout)

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    logger = DebugLogger(log_path=args.log) if args.debug else None
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
                bios_override=bios_override,
                logo_override=logo_override,
                extrom_override=extrom_override,
            )
        except MachineLoadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        if spec.generation == "msx2":
            from msx.debugger.prompt import Debugger
            dbg = Debugger(machine)
            machine._debugger = dbg
            if breakpoint_addrs:
                machine.set_breakpoints(breakpoint_addrs)
                print(f"break   : {', '.join(f'{a:04X}h' for a in breakpoint_addrs)}")
            if watchpoint_entries:
                machine.set_watchpoints(watchpoint_entries)
                print(f"watch   : {', '.join(f'{a:04X}h[{m}]' for a, m in watchpoint_entries)}")

        if args.count is not None:
            while machine.cycle_count < args.count:
                machine.run_frame()
        else:
            from frontend.sdl2_frontend import run
            run(machine, speed=args.speed, game_title=game_title, resume=args.resume,
                frame_skip=args.frame_skip)
    finally:
        if logger is not None:
            logger.close()
        if _trace_file is not None:
            _trace_file.close()


if __name__ == "__main__":
    main()
