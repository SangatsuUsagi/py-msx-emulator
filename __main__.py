"""Entry point: python . [--debug] [--log FILE] [rom_path] [cartridge_path]"""
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
    parser.add_argument("--resume", nargs="?", const="", default=None, metavar="STATE_FILE",
                        help="Resume from a save state (default: saves/latest.state)")
    args = parser.parse_args()

    rom_path = Path(args.rom)
    cart_path = Path(args.cartridge) if args.cartridge else None

    if not rom_path.exists():
        print(f"error: ROM not found: {rom_path}", file=sys.stderr)
        print("Download CBIOS from http://cbios.sourceforge.net/ and place in roms/",
              file=sys.stderr)
        sys.exit(1)

    rom = rom_path.read_bytes()
    cartridge = cart_path.read_bytes() if cart_path else None

    from frontend.sdl2_frontend import run
    from msx.debug.logger import DebugLogger
    from msx.machine import make_machine
    from msx.romdb import lookup_title

    game_title = (lookup_title(cartridge) if cartridge else None) or "py-msx-emulator"
    logger = DebugLogger(log_path=args.log) if args.debug else None
    try:
        machine = make_machine(rom=rom, cartridge=cartridge, logger=logger, mapper=args.mapper)
        run(machine, speed=args.speed, game_title=game_title, resume=args.resume)
    finally:
        if logger is not None:
            logger.close()


if __name__ == "__main__":
    main()
