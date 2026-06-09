"""Entry point: python . [--debug] [--log FILE] [--msx2 --extrom PATH] [rom_path] [cartridge_path]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="py-msx-emulator")
    parser.add_argument("rom", nargs="?", default="roms/cbios_main_msx1.rom",
                        help="ROM image path (default: roms/cbios_main_msx1.rom)")
    parser.add_argument("cartridge", nargs="?", default=None,
                        help="Cartridge ROM path")
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
                        help="Enable MSX2 mode (requires --extrom)")
    parser.add_argument("--extrom", metavar="EXTROM_PATH", default=None,
                        help="Extension BIOS ROM path (required with --msx2)")
    args = parser.parse_args()

    rom_path = Path(args.rom)
    cart_path = Path(args.cartridge) if args.cartridge else None
    slot2_path = Path(args.slot2) if args.slot2 else None

    if not rom_path.exists():
        print(f"error: ROM not found: {rom_path}", file=sys.stderr)
        print("Download CBIOS from http://cbios.sourceforge.net/ and place in roms/",
              file=sys.stderr)
        sys.exit(1)

    if slot2_path is not None and not slot2_path.exists():
        print(f"error: slot 2 ROM not found: {slot2_path}", file=sys.stderr)
        sys.exit(1)

    extrom: bytes | None = None
    if args.msx2:
        if args.extrom is None:
            print("error: --extrom is required with --msx2", file=sys.stderr)
            sys.exit(1)
        extrom_path = Path(args.extrom)
        if not extrom_path.exists():
            print(f"error: ext ROM not found: {extrom_path}", file=sys.stderr)
            sys.exit(1)
        extrom = extrom_path.read_bytes()

    rom = rom_path.read_bytes()
    cartridge = cart_path.read_bytes() if cart_path else None
    cartridge2 = slot2_path.read_bytes() if slot2_path else None

    from frontend.sdl2_frontend import run
    from msx.debug.logger import DebugLogger
    from msx.machine import make_machine, make_machine_msx2
    from msx.romdb import lookup_title

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    logger = DebugLogger(log_path=args.log) if args.debug else None
    try:
        if args.msx2:
            machine = make_machine_msx2(rom=rom, extrom=extrom,  # type: ignore[arg-type]
                                        cartridge=cartridge, logger=logger, mapper=args.mapper,
                                        cartridge2=cartridge2, mapper2=args.mapper2)
        else:
            machine = make_machine(rom=rom, cartridge=cartridge, logger=logger, mapper=args.mapper,
                                   cartridge2=cartridge2, mapper2=args.mapper2)
        run(machine, speed=args.speed, game_title=game_title, resume=args.resume,
            frame_skip=args.frame_skip)
    finally:
        if logger is not None:
            logger.close()


if __name__ == "__main__":
    main()
