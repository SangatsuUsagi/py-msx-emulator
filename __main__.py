"""Entry point: python . [--biosrom PATH] [--extrom PATH] [--msx2] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CBIOS_URL = "http://cbios.sourceforge.net/"
_DEFAULT_MSX1_BIOS = Path("roms/cbios_main_msx1.rom")
_DEFAULT_MSX2_BIOS = Path("roms/cbios_main_msx2.rom")
_DEFAULT_MSX2_EXT  = Path("roms/cbios_sub.rom")


def main() -> None:
    parser = argparse.ArgumentParser(description="py-msx-emulator")
    parser.add_argument("cartridge", nargs="?", default=None,
                        help="Cartridge ROM path")
    parser.add_argument("--biosrom", metavar="BIOS_PATH", default=None,
                        help="Main BIOS ROM path (overrides auto-selected default)")
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
                        help="Force MSX2 mode (auto-detected from ROM DB when cartridge known)")
    parser.add_argument("--extrom", metavar="EXTROM_PATH", default=None,
                        help="Extension BIOS ROM path (auto-selected for MSX2; overrides default)")
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

    # --- Resolve machine type ---
    db_system = lookup_system(cartridge) if cartridge else None
    is_msx2: bool = args.msx2 or (db_system == "MSX2")

    # --- Warn: --extrom given but machine is MSX1 ---
    if args.extrom is not None and not is_msx2:
        print("warning: --extrom ignored (machine type resolved to MSX1)", file=sys.stderr)

    # --- Resolve BIOS paths ---
    if args.biosrom is not None:
        bios_path = Path(args.biosrom)
        if not bios_path.exists():
            print(f"error: BIOS ROM not found: {bios_path}", file=sys.stderr)
            sys.exit(1)
    else:
        bios_path = _DEFAULT_MSX2_BIOS if is_msx2 else _DEFAULT_MSX1_BIOS
        if not bios_path.exists():
            print(f"error: BIOS ROM not found: {bios_path}", file=sys.stderr)
            print(f"Download CBIOS from {_CBIOS_URL} and place in roms/", file=sys.stderr)
            sys.exit(1)

    extrom_path: Path | None = None
    if is_msx2:
        extrom_path = Path(args.extrom) if args.extrom else _DEFAULT_MSX2_EXT
        if not extrom_path.exists():
            print(f"error: ext ROM not found: {extrom_path}", file=sys.stderr)
            print(f"Download CBIOS from {_CBIOS_URL} and place in roms/", file=sys.stderr)
            sys.exit(1)

    # --- Resolve display mapper (for summary; actual resolution happens in make_machine) ---
    if args.mapper != "auto":
        display_mapper = args.mapper
    elif cartridge:
        display_mapper = lookup(cartridge) or "auto"
    else:
        display_mapper = "auto"

    # --- Startup configuration summary ---
    print(f"machine : {'MSX2' if is_msx2 else 'MSX1'}")
    print(f"bios    : {bios_path}")
    if is_msx2:
        print(f"ext     : {extrom_path}")
    print(f"mapper  : {display_mapper}")

    # --- Load ROM bytes ---
    bios = bios_path.read_bytes()
    extrom: bytes | None = extrom_path.read_bytes() if extrom_path else None

    from frontend.sdl2_frontend import run
    from msx.debug.logger import DebugLogger
    from msx.machine import make_machine, make_machine_msx2

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    logger = DebugLogger(log_path=args.log) if args.debug else None
    try:
        if is_msx2:
            machine = make_machine_msx2(rom=bios, extrom=extrom,  # type: ignore[arg-type]
                                        cartridge=cartridge, logger=logger, mapper=args.mapper,
                                        cartridge2=cartridge2, mapper2=args.mapper2)
        else:
            machine = make_machine(rom=bios, cartridge=cartridge, logger=logger,
                                   mapper=args.mapper, cartridge2=cartridge2,
                                   mapper2=args.mapper2)
        run(machine, speed=args.speed, game_title=game_title, resume=args.resume,
            frame_skip=args.frame_skip)
    finally:
        if logger is not None:
            logger.close()


if __name__ == "__main__":
    main()
