# py-msx-emulator

A functionally accurate MSX1 emulator written in pure Python, driven by machine-readable component specifications.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-513%20passing-brightgreen)

[日本語版 README はこちら](README_ja.md)

---

## Goal

> **The primary goal of this emulator is to run [Salamander (沙羅曼蛇) by KONAMI](https://en.wikipedia.org/wiki/Salamander_(video_game)) on MSX1.**
>
> Compatibility notice: this emulator has only been tested against a physical ROM dump of Salamander owned by the author. There is no guarantee that all MSX1 ROMs will work correctly. Bug reports for other titles are welcome, but support is best-effort.

---

## Overview

py-msx-emulator is a functional MSX1 emulator targeting accurate hardware reproduction of the components required to run Salamander. It is written entirely in pure Python 3.10+ with no C extensions or native bindings beyond the SDL2 display and audio library.

**Design philosophy:**

- **Portability first.** Every component is pure Python. The only platform-specific dependency is pysdl2 for the display/audio frontend.
- **Spec-Driven Development.** Each hardware component is defined by a machine-readable specification before any implementation is written. Specs live under `openspec/specs/` and are used to drive test design, implementation, and change management.
- **Explicit over implicit.** Component wiring is done by hand in `make_machine()`; there is no reflection or magic dependency injection.

---

## Features

- **Zilog Z80 CPU** — full register file (AF, BC, DE, HL, IX, IY, SP, PC, I, R and shadow registers), all 252 documented opcodes, prefix tables CB/DD/ED/FD, undocumented IXH/IXL/IYH/IYL register-access opcodes, maskable (INT mode 1 and mode 2) and non-maskable (NMI) interrupts, T-state accurate stepping
- **TMS9918A VDP** — 16 KB VRAM, 8 control registers, Screen modes 0–3 (Text 40-col, Graphic 1, Graphic 2, Multicolor), sprite rendering with size/magnification, 5th-sprite and coincidence flags, VBlank interrupt
- **AY-3-8910 PSG** — 16 registers, 3 tone channels, noise channel, envelope generator with 8 waveform shapes, quasi-logarithmic amplitude table, 44100 Hz PCM sample output at 735 samples/frame
- **Konami SCC** — 5-channel wavetable synthesiser with 4 waveform banks (32 samples each), 12-bit frequency and 4-bit volume per channel, mixed into the audio output alongside PSG
- **i8255 PPI** — slot-select register (port 0xA8), 11-row × 8-bit MSX keyboard matrix (port 0xA9), row selection (port 0xAA)
- **MSX slot system** — 4-page × 4-slot dispatch, BIOS ROM in slot 0 (pages 0–1), companion logo ROM auto-loaded at slot 0 / page 2 (0x8000–0xBFFF) when `cbios_logo_msx1.rom` is present alongside the BIOS, cartridge in slot 1, optional second cartridge in slot 2, 32 KB RAM in slot 3
- **Cartridge mappers** — Flat (no bank switching), ASCII8, ASCII16, Konami, KonamiSCC; auto-detected from a SHA1-based ROM database
- **SDL2 frontend** — 768×576 window (256×192 × scale 3), TMS9918A hardware palette, mono audio at 44100 Hz, fullscreen toggle, screenshot, state save/load, automatic frame skip (VDP pixel render suppressed on late frames; VBlank interrupt still fires every frame)
- **Physical joystick** — SDL2 GameController and raw joystick APIs, hot-plug/unplug, keyboard joystick emulation (WASD + ZX/.,)
- **State save/load** — complete hardware snapshot (CPU, RAM, VDP, PSG, SCC, mapper banks) via pickle, PNG screenshot alongside each save, `saves/latest.*` symlinks for quick resume
- **ROM database** — SHA1 title lookup for automatic game title detection and mapper selection
- **Debug tooling** — opt-in structured logging, CPU instruction trace, I/O port trace, hang detector
- **Pure Python** — no C extensions; runs wherever Python 3.10 and SDL2 are available

---

## Spec-driven architecture

Every hardware component in this emulator is defined by a machine-readable specification written before any code is produced. This project was implemented using [Claude Code](https://claude.ai/code) and [OpenSpec](https://openspec.dev/).

### How it works

Specs live under `openspec/specs/<component>/spec.md`. Each spec file uses a structured prose format that interleaves natural-language requirements with concrete WHEN/THEN scenarios:

```markdown
### Requirement: Instruction fetch and execute
`Z80.step() -> int` SHALL fetch the opcode byte at PC, advance PC, decode and execute
the instruction, and return the number of T-states consumed.

#### Scenario: NOP executes in 4 T-states
- **WHEN** opcode 0x00 (NOP) is at PC and `step()` is called
- **THEN** the return value is 4 and PC is incremented by 1

#### Scenario: LD BC, nn loads a 16-bit immediate
- **WHEN** bytes [0x01, 0x34, 0x12] are at PC and `step()` is called
- **THEN** BC is 0x1234 and PC is incremented by 3
```

The scenarios map directly to unit tests, making it straightforward to verify that the implementation matches the specification. When a new feature is added or an existing component is changed, the spec is updated first and the implementation follows.

### Spec coverage

The following capabilities have specs defined:

`z80-cpu` · `vdp-core` · `vdp-renderer` · `vdp-sprites` · `vdp-interrupt` · `psg` · `psg-synthesis` · `scc-sound-chip` · `ppi` · `memory-bus` · `mega-rom-mapper` · `io-bus` · `keyboard-matrix` · `joystick-input` · `physical-joystick` · `machine` · `frame-timer` · `hang-detector` · `romdb` · `debug-logger` · `cpu-trace-buffer` · `io-trace` · `boot-diagnostic` · `sdl2-frontend` · `state-save-load`

> Note: the `openspec/` directory and `tests/` directory are not included in the public repository.

---

## Component reference

### CPU — Zilog Z80

| Item | Detail |
|------|--------|
| Implementation | `msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`, `msx/cpu/registers.py` |
| Register file | AF, BC, DE, HL, IX, IY, SP, PC, I, R; shadow registers AF′, BC′, DE′, HL′ |
| Instruction set | All 252 documented opcodes; CB, DD, ED, FD prefix tables |
| Undocumented opcodes | DD/FD prefix: `LD r, IXH/IXL/IYH/IYL`, `LD IXH/IXL/IYH/IYL, r`, arithmetic with IXH/IXL/IYH/IYL; 8 T-states, correct flag behaviour |
| Interrupts | Maskable INT (mode 1: jump to 0x0038; mode 2: I-register vector); NMI (push PC, jump to 0x0066) |
| Timing | `step()` returns T-states consumed; 59,659 T-states per NTSC frame |
| Known limitations | OTIR/INIR and similar block I/O instructions are not cycle-exact across page boundaries; R register increments only on opcode fetch |

### VDP — TMS9918A

| Item | Detail |
|------|--------|
| Implementation | `msx/vdp/vdp.py`, `msx/vdp/renderer.py` |
| VRAM | 16 KB |
| Control registers | 8 registers (R0–R7) via port 0x99 |
| Screen modes | Mode 0: Text 40-col (SCREEN 0); Mode 1: Graphic 1 (SCREEN 1); Mode 2: Graphic 2 (SCREEN 2); Mode 3: Multicolor (SCREEN 3) |
| Sprites | 32 sprites, size 8×8 or 16×16, ×1/×2 magnification; 4 sprites/line limit; 5th-sprite flag; coincidence flag |
| Output | 256×192 colour-index buffer per frame; frontend converts to RGB24 using TMS9918A palette |
| Interrupt | VBlank triggers INT callback; status register bit 7 cleared on read |
| Known limitations | Mid-frame register-change timing and undocumented sprite-overflow behaviour are not emulated |

### PSG — AY-3-8910

| Item | Detail |
|------|--------|
| Implementation | `msx/psg.py` |
| Registers | 16 registers via ports 0xA0 (address latch), 0xA1 (write), 0xA2 (read) |
| Tone channels | 3 channels (A, B, C), 12-bit period registers, square-wave |
| Noise channel | 17-bit LFSR |
| Envelope | 8 waveform shapes; quasi-logarithmic 16-step amplitude table |
| Audio output | 44100 Hz, signed 16-bit mono; 735 samples per frame via `generate_samples(735)` |

### SCC — Konami SCC (Sound Creative Chip)

| Item | Detail |
|------|--------|
| Implementation | `msx/scc.py` |
| Channels | 5 channels |
| Waveforms | 4 waveform banks, 32 signed bytes each; channels 4 and 5 share bank 3 |
| Frequency | 12-bit register per channel |
| Volume | 4-bit register per channel |
| Activation | KonamiSCC mapper activates SCC when 0x3F is written to 0x9000; registers appear at 0x9800 |
| Mixing | SCC samples added to PSG samples per-sample, clipped to [−32768, 32767] |

### PPI — Intel i8255

| Item | Detail |
|------|--------|
| Implementation | `msx/ppi.py` |
| Port 0xA8 | Primary slot register (read/write) |
| Port 0xA9 | Keyboard matrix row read (8-bit active-low) |
| Port 0xAA | Keyboard row selector (bits 0–3) |
| Known limitations | Cassette interface (port 0xAA bits 4–7) is not implemented |

### Memory bus / slot system

| Item | Detail |
|------|--------|
| Implementation | `msx/memory.py` |
| Address space | Flat 64 KB (0x0000–0xFFFF), four 16 KB pages |
| Slot 0 pages 0–1 | BIOS ROM (read-only, 0x0000–0x7FFF) |
| Slot 0 page 2 | Logo ROM (`cbios_logo_msx1.rom`) at 0x8000–0xBFFF; auto-loaded from same directory as BIOS; returns 0xFF if absent |
| Slot 1 | Cartridge ROM via mapper |
| Slot 2 | Second cartridge ROM via `_mapper2`; open bus (0xFF on read, writes ignored) when no slot 2 ROM is loaded |
| Slot 3 | 32 KB RAM at 0x8000–0xFFFF |

### Cartridge mappers

| Mapper | Description |
|--------|-------------|
| `FlatMapper` | No bank switching; mirrors ROM across the 32 KB cartridge region |
| `Ascii8Mapper` | Four 8 KB windows; control registers at 0x6000–0x7FFF |
| `Ascii16Mapper` | Two 16 KB windows; control registers at 0x6000–0x7FFF |
| `KonamiMapper` | Three 8 KB windows; bank register written to window base address |
| `KonamiSCCMapper` | Same as Konami; activates SCC when 0x3F is written to 0x9000 |

Mapper is auto-detected from a SHA1 ROM database. Override with `--mapper`.

Slot 2 uses a separate mapper controlled by `--mapper2` (auto-detected by default). `KonamiSCC` is not a valid mapper for slot 2; if the ROM database returns `KonamiSCC` for a slot 2 cartridge, the mapper automatically falls back to `Konami` with a warning on stderr.

### ROM database

| Item | Detail |
|------|--------|
| Implementation | `msx/romdb.py` |
| Lookup key | SHA1 hash of the cartridge ROM |
| Data | Game title and recommended mapper type per ROM |
| Source | Derived from the [openMSX software database](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml) |
| Fallback | If PyYAML is not installed, or the ROM is not found, the emulator continues without a title and falls back to `--mapper auto` heuristics |

### I/O bus

| Item | Detail |
|------|--------|
| Implementation | `msx/io.py` |
| Design | Range-based port registration; reads/writes dispatched to registered handler |

### Keyboard / joystick input

| Item | Detail |
|------|--------|
| Keyboard | `msx/input.py`; 11 rows × 8 bits, active-low, per MSX Technical Handbook |
| Key rows | Row 6: F1–F3, modifiers; Row 7: F4–F5, Tab, Return; Row 8: cursor keys, Space |
| Physical joystick | `msx/joystick.py`; SDL2 GameController (preferred) + raw joystick fallback; hot-plug |
| Keyboard emulation | WASD = Joy1 directions; Z/X or ,/. = Trigger A/B; arrow keys also mapped |

---

## Requirements

- **Python 3.10 or later**
- **SDL2 native library** — installed separately from pysdl2

| Package | Minimum version | Purpose |
|---------|----------------|---------|
| Pillow | 12.0 | PNG export for screenshots and state saves |
| pysdl2 | 0.9.16 | SDL2 bindings for the display/audio frontend |
| PyYAML | 6.0 | ROM database title lookup (graceful fallback if absent) |

Development dependencies (pytest, ruff, mypy) are in `requirements-dev.txt`. This project is not published to PyPI.

---

## Performance

Measured running Salamander (KonamiSCC mapper) at `--speed 1.0` (target: 60 fps).

| Platform | Runtime | Measured FPS | Notes |
|----------|---------|-------------|-------|
| Apple MacBook (M5 Pro) | Python 3.12 | ~60 fps | Real-time playable |
| Raspberry Pi 5 | Python 3.12 | ~16 fps | ~27% of real-time; game runs in slow motion |
| Raspberry Pi 5 | PyPy3 | ~35–45 fps | ~60–75% of real-time; significantly faster than CPython |

On platforms that cannot sustain 60 fps, the game runs in slow motion at a rate proportional to the achieved frame rate. Audio will degrade (clicks or silence) below 60 fps because samples are generated per-frame while the audio device always consumes at 44 100 Hz. PyPy3 is a drop-in alternative that substantially improves throughput on slower hardware and is the recommended way to get closer to real-time on a Raspberry Pi.

Automatic frame skip (`--frame-skip auto`, the default) suppresses VDP pixel rendering on frames that miss the deadline while still firing the VBlank interrupt every frame. This improves display smoothness on hosts near but below 60 fps — on Raspberry Pi 5 with PyPy3 (~35–45 fps emulation), the visible frame rate reaches ~25–35 fps instead of the raw throughput. Audio quality is unaffected by frame skip; underruns remain on any platform below 60 fps. Frame skip can be disabled with `--frame-skip none`.

`--speed` scales the target frame rate (e.g. `--speed 2.0` runs the game at 2× real time on a capable host). It does not compensate for insufficient host throughput and does not improve audio quality on slow hardware.

---

## BIOS setup

This emulator does not bundle a BIOS ROM. You must supply one yourself.

**C-BIOS** is a free, open-source MSX BIOS replacement and the recommended choice:

1. Download the latest release from [https://cbios.sourceforge.net/](https://cbios.sourceforge.net/)
2. Extract the archive and copy the following files into the `roms/` directory of this repository:
   - `cbios_main_msx1.rom` (required)
   - `cbios_logo_msx1.rom` (optional — enables the C-BIOS boot logo)
3. The CLI auto-selects `roms/cbios_main_msx1.rom` as the default BIOS. Use `--biosrom` to override.

When `cbios_logo_msx1.rom` is present in the same directory as the main BIOS, it is automatically mapped at slot 0 / page 2 (0x8000–0xBFFF) as the companion logo ROM. Without it, the emulator still runs but the boot logo is absent.

> **Legal note:** do not use a copyrighted MSX BIOS dump extracted from a commercial machine. C-BIOS is the recommended free and legal alternative. The `roms/` directory is excluded from version control by `.gitignore`.

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

> **Supported platforms:** macOS and Linux (Ubuntu). Windows is not tested.

---

## Usage

### Running the emulator

```bash
# MSX BASIC only (no cartridge); BIOS auto-detected from roms/
python .

# With a game cartridge
python . path/to/game.rom

# Override BIOS path
python . --biosrom path/to/custom_bios.rom

# With cartridge and custom BIOS
python . path/to/game.rom --biosrom path/to/custom_bios.rom

# Double emulation speed
python . path/to/game.rom --speed 2.0

# Dual cartridge (slot 1 and slot 2)
python . path/to/game1.rom --slot2 path/to/game2.rom

# Dual cartridge with explicit mapper types
python . path/to/game1.rom --mapper KonamiSCC --slot2 path/to/game2.rom --mapper2 Konami

# Force a specific mapper type
python . path/to/game.rom --mapper KonamiSCC

# Resume from the most recent save state
python . path/to/game.rom --resume

# Resume from a specific save file
python . path/to/game.rom --resume saves/salamander_20260605_120000.state

# Enable debug logging
python . path/to/game.rom --debug --log trace.log
```

### Command-line options

| Option | Default | Description |
|--------|---------|-------------|
| `cartridge` | _(none)_ | Path to the cartridge ROM |
| `--biosrom BIOS_PATH` | `roms/cbios_main_msx1.rom` | Main BIOS ROM path (overrides auto-selected default) |
| `--speed FLOAT` | `1.0` | Emulation speed multiplier |
| `--mapper TYPE` | `auto` | Slot 1 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `KonamiSCC` |
| `--slot2 ROM2` | _(none)_ | Path to the slot 2 cartridge ROM |
| `--mapper2 TYPE` | `auto` | Slot 2 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami` (KonamiSCC not supported in slot 2) |
| `--resume [FILE]` | _(none)_ | Resume from `saves/latest.state`, or a specific `.state` file |
| `--frame-skip MODE` | `auto` | Frame skip: `auto` skips VDP rendering on late frames; `none` disables |
| `--debug` | off | Enable structured diagnostic logging to stderr |
| `--log FILE` | _(none)_ | Write diagnostic log to a file (requires `--debug`) |

### In-emulator key bindings

| Key | Action |
|-----|--------|
| Esc | Quit |
| F8 | Save state to `saves/<title>_YYYYMMDD_HHMMSS.state`* |
| F9 | Load most recent save state |
| F10 | Save screenshot to `screenshot_YYYYMMDD_HHMMSS.png` |
| F11 | Toggle fullscreen |
| F1–F5 | Passed through to the MSX keyboard matrix |

\* `<title>` is the game title from the ROM database, or `"py-msx-emulator"` if the cartridge is not in the database.

**Keyboard joystick emulation (Joy 1):**

| Key | Action |
|-----|--------|
| W / ↑ | Up |
| S / ↓ | Down |
| A / ← | Left |
| D / → | Right |
| Z or , | Trigger A |
| X or . | Trigger B |

### Programmatic API

```python
from pathlib import Path
from msx.machine import make_machine

# Load ROMs
rom = Path("roms/cbios_main_msx1.rom").read_bytes()
cartridge = Path("game.rom").read_bytes()

# Create and wire the machine (CPU, VDP, PSG, PPI, memory, I/O all connected)
machine = make_machine(rom=rom, cartridge=cartridge)

# Step one CPU instruction; returns T-states consumed
t_states = machine.cpu.step()

# Read / write memory directly
value = machine.memory.read(0x0000)
machine.memory.write(0x8000, 0x42)

# Run one full NTSC frame (59,659 T-states)
# Returns a 49,152-byte (256×192) colour-index buffer (TMS9918A palette indices)
frame_buf = machine.run_frame()

# Inspect CPU state
print(hex(machine.cpu.registers.PC))
print(hex(machine.cpu.registers.A))
```

---

## Running tests

The test suite covers all major components with 513 tests spanning unit tests for individual opcodes and hardware registers, integration tests that wire multiple components together, and scenario-level tests whose conditions are derived directly from the component specs.

```bash
# Run all tests
python -m pytest

# Verbose output
python -m pytest -v

# Run tests matching a keyword
python -m pytest -k "psg"
```

> Note: the `tests/` directory is not included in the public repository.

---

## Project layout

```
py-msx-emulator/
├── __main__.py            # CLI entry point (python .)
├── frontend/
│   └── sdl2_frontend.py   # SDL2 window, audio, event loop
├── msx/                   # Core emulator package
│   ├── cpu/               # Z80 CPU (registers, flags, opcodes)
│   ├── vdp/               # TMS9918A VDP (core + renderer)
│   ├── machine.py         # Component wiring and frame loop
│   ├── memory.py          # Slot-based memory bus
│   ├── mapper.py          # Cartridge mappers (Flat, ASCII8/16, Konami, SCC)
│   ├── psg.py             # AY-3-8910 PSG + audio synthesis
│   ├── scc.py             # Konami SCC wavetable synthesiser
│   ├── ppi.py             # i8255 PPI (slot register, keyboard)
│   ├── io.py              # I/O bus (port dispatch)
│   ├── input.py           # Keyboard matrix + joystick input state
│   ├── joystick.py        # Physical joystick manager (SDL2)
│   ├── frame_timer.py     # 60 fps pacing + FPS measurement
│   ├── romdb.py           # SHA1-based ROM title/mapper database
│   ├── state.py           # Save/load machine state (pickle + PNG)
│   └── debug/             # DebugLogger, CPU/I/O trace, hang detector
├── roms/                  # Place C-BIOS ROM files here (not in version control)
├── saves/                 # Save states and screenshots (created at runtime)
├── openspec/
│   └── specs/             # Component specifications (not included in the public repository)
├── tests/                 # Test suite — 513 tests (not included in the public repository)
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Development dependencies
└── pyproject.toml         # Project metadata and tool configuration
```

---

## Contributing

### Spec-first rule

Every new hardware component or significant behaviour change must have a specification added or updated in `openspec/specs/<component>/spec.md` before any implementation code is written. The scenarios in the spec are the source of truth for test cases. A PR that adds implementation without a corresponding spec update will not be merged.

### Coding conventions

- **Pure Python only** — no C extensions, no Cython, no additional ctypes beyond what is already used in the SDL2 frontend
- **Python 3.10+** — use dataclasses, `match`/`case`, and modern type annotation syntax
- **Type hints everywhere** — the project is checked with mypy in strict mode
- **Linting** — ruff with `line-length = 99`; run `python -m ruff check .` before committing
- **Comments only for the non-obvious** — do not add comments that restate what the code already says

### Issues and pull requests

There is no formal CONTRIBUTING.md at present. Please open a GitHub issue to discuss significant changes before submitting a PR. Bug reports for ROMs other than Salamander are welcome; compatibility fixes will be considered on a best-effort basis.

---

## Acknowledgements

- **[openMSX](https://openmsx.org/)** — the ROM title and mapper database (`msx/romdb.py`) is derived from the [openMSX software database](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml). openMSX is released under the GNU GPL v2.
- **[C-BIOS](https://cbios.sourceforge.net/)** — recommended free MSX BIOS replacement used for testing.

---

## License

MIT — see [LICENSE](LICENSE).
