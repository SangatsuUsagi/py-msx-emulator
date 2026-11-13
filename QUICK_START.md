# Quick Start

This page gives the requirements, installation steps, and BIOS setup for
py-msx-emulator. For emulated hardware details, full usage, and all other
topics, see [README.md](README.md).

[日本語版はこちら](QUICK_START_ja.md)

---

## Requirements

- **Python 3.10 or later**
- **SDL2 native library**, installed separately from pysdl2

| Package | Minimum version | Purpose |
| --- | --- | --- |
| Pillow | 12.0 | PNG export for screenshots and state saves |
| pysdl2 | 0.9.16 | SDL2 bindings for the display/audio frontend |
| PyYAML | 6.0 | Loading of `py_emulator.yaml` and the machine YAML files |

`requirements-dev.txt` lists the development dependencies (pytest, ruff,
mypy). Only `tools/mcp_server.py` needs one more package, the optional `mcp`
extra (see [tools/README_tools.md](tools/README_tools.md#mcp_serverpy)). This
project is not on PyPI.

---

## Installation

```bash
git clone https://github.com/SangatsuUsagi/py-msx-emulator.git
cd py-msx-emulator

# Install SDL2 native library
# macOS:
brew install sdl2
# Ubuntu / Debian:
sudo apt install libsdl2-2.0-0 python3-venv

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

The supported platforms are macOS and Linux (Ubuntu). Windows is untested. If
you try Windows, install `pysdl2-dll` in place of the system SDL2 library.

---

## BIOS setup

This emulator does not include a BIOS ROM. You must supply one.

**C-BIOS** is a free, open-source MSX BIOS replacement, and it is the
recommended choice:

1. Download the latest release from
   [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/).
2. Extract the archive.
3. Copy the related files into `roms/cbios/` in this repository.

For MSX1 (`cbios_msx1_jp`, the default for a cartridge that the ROM database
lists as MSX1):

- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

For MSX2 (`cbios_msx2_jp`, the default in all other cases, including a run
with no cartridge):

- `cbios_main_msx2_jp.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

These defaults apply only when you give no `--machine` option and set no
`machine:` in `py_emulator.yaml`. The YAML file for each machine ID in
`config/machines/` lists the necessary filenames for that machine.

> **Legal note:** MSX BIOS files are copyrighted. Use only a file that you
> dumped yourself, from a real MSX machine of the same model that you own.
> C-BIOS is the recommended free and legal alternative. `.gitignore` excludes
> the `roms/` directory from version control.

---

## Next steps

1. Run `python .`.
2. Make sure that a window opens and shows the C-BIOS message
   `No cartridge found`. The C-BIOS machines have no BASIC, so they do not
   boot to a BASIC prompt.
3. To confirm that the emulator runs software, run it with a ROM or disk image
   that you got legally, or with a homebrew ROM.

For cartridges, floppy disks, and the other command-line options, see
[Usage](README.md#usage) in the main README.
