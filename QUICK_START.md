# Quick Start

Requirements, installation, and BIOS setup for py-msx-emulator. See
[README.md](README.md) for emulated hardware details, full usage, and
everything else.

[日本語版はこちら](QUICK_START_ja.md)

---

## Requirements

- **Python 3.10 or later**
- **SDL2 native library** — installed separately from pysdl2

| Package | Minimum version | Purpose |
| --- | --- | --- |
| Pillow | 12.0 | PNG export for screenshots and state saves |
| pysdl2 | 0.9.16 | SDL2 bindings for the display/audio frontend |
| PyYAML | 6.0 | ROM database title lookup and machine YAML loading (graceful fallback if absent) |

Development dependencies (pytest, ruff, mypy) are in `requirements-dev.txt`.
This project is not published to PyPI.

---

## Installation

```bash
git clone https://github.com/SangatsuUsagi/py-msx-emulator.git
cd py-msx-emulator

# Install SDL2 native library
# macOS:
brew install sdl2
# Ubuntu / Debian:
sudo apt install libsdl2-2.0-0

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Supported platforms are macOS and Linux (Ubuntu). Windows is untested; if you
try it, add `pysdl2-dll` to the installed packages in place of the system SDL2
library.

---

## BIOS setup

This emulator does not bundle a BIOS ROM. You must supply one yourself.

**C-BIOS** is a free, open-source MSX BIOS replacement and the recommended
choice:

1. Download the latest release from
   [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/)
2. Extract the archive and copy the relevant files into `roms/cbios/` in this
   repository.

For MSX1 (`cbios_msx1_jp`, the default when a known MSX1 cartridge is detected):

- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

For MSX2 (`cbios_msx2_jp`, the default when no cartridge or an MSX2 cartridge is
given):

- `cbios_main_msx2_jp.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

The required filenames for each machine ID are listed in the corresponding YAML
under `config/machines/`.

> **Legal note:** MSX BIOS files are copyrighted. Only use a file you dumped
> yourself, from a real MSX machine you own of the corresponding model.
> C-BIOS is the recommended free and legal alternative. The `roms/` directory is excluded from version
> control by `.gitignore`.

---

## Next steps

Run `python .` — a window should open showing C-BIOS's `No cartridge found`
message (C-BIOS has no built-in BASIC interpreter, so it never boots to a
BASIC prompt on its own). To confirm the emulator actually runs software, try
it with a legally obtained ROM/disk image or a homebrew ROM. See
[Usage](README.md#usage) in the main README for cartridges, floppy disks, and
the rest of the command-line options.
