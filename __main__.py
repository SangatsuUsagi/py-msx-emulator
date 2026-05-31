"""Entry point: python -m py_msx_emulator [rom_path] [cartridge_path]"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    rom_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("roms/cbios_main_msx1.rom")
    cart_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    if not rom_path.exists():
        print(f"error: ROM not found: {rom_path}", file=sys.stderr)
        print("Download CBIOS from http://cbios.sourceforge.net/ and place in roms/",
              file=sys.stderr)
        sys.exit(1)

    rom = rom_path.read_bytes()
    cartridge = cart_path.read_bytes() if cart_path else None

    from msx.machine import make_machine
    from frontend.sdl2_frontend import run

    machine = make_machine(rom=rom, cartridge=cartridge)
    run(machine)


if __name__ == "__main__":
    main()
