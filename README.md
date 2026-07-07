# py-msx-emulator

A functionally accurate MSX1/MSX2 emulator written in pure Python, driven by machine-readable component specifications.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-1245%20passing-brightgreen)

[日本語版 README はこちら](README_ja.md)

---

## Goal

> **The primary goal of this emulator is to run specific titles accurately on their target hardware generation.**
>
> **MSX1:** [Salamander (沙羅曼蛇) by KONAMI](https://en.wikipedia.org/wiki/Salamander_(video_game)) · [Nemesis 2 (グラディウス2) by KONAMI](https://en.wikipedia.org/wiki/Nemesis_2_(MSX)) · [Penguin Adventure (夢大陸アドベンチャー) by KONAMI](https://en.wikipedia.org/wiki/Penguin_Adventure)
>
> **MSX2:** [Legacy of the Wizard (ドラゴンスレイヤーIV ドラスレファミリー) by Falcom](https://en.wikipedia.org/wiki/Legacy_of_the_Wizard) · [Romancia (ロマンシア) by Falcom](https://en.wikipedia.org/wiki/Romancia)
>
> Compatibility notice: this emulator has only been tested against physical ROM dumps owned by the author. There is no guarantee that other MSX1 or MSX2 ROMs will work correctly. Bug reports for other titles are welcome, but support is best-effort.

---

## Overview

py-msx-emulator is a functional MSX1/MSX2 emulator targeting accurate hardware reproduction of the components required to run its target titles. It is written entirely in pure Python 3.10+ with no C extensions or native bindings beyond the SDL2 display and audio library.

**Design philosophy:**

- **Portability first.** Every component is pure Python. The only platform-specific dependency is pysdl2 for the display/audio frontend.
- **Spec-Driven Development.** Each hardware component is defined by a machine-readable specification before any implementation is written. Specs live under `openspec/specs/` and are used to drive test design, implementation, and change management.
- **Explicit over implicit.** Component wiring is done by hand in `build_machine()`; there is no reflection or magic dependency injection.

---

## Features

- **Zilog Z80 CPU** — full register file (AF, BC, DE, HL, IX, IY, SP, PC, I, R and shadow registers), all 252 documented opcodes, prefix tables CB/DD/ED/FD, undocumented IXH/IXL/IYH/IYL register-access opcodes, maskable (INT mode 1 and mode 2) and non-maskable (NMI) interrupts, T-state accurate stepping
- **TMS9918A VDP** — 16 KB VRAM, 8 control registers, Screen modes 0–3 (Text 40-col, Graphic 1, Graphic 2, Multicolor), sprite rendering with size/magnification, 5th-sprite and coincidence flags, VBlank interrupt
- **Yamaha V9938 VDP** — 128 KB VRAM, 28 control registers, programmable 16-colour palette (9-bit GRB333), Screen modes 0–8 (SCREEN 0 through SCREEN 8), hardware command engine (HMMV, HMMM, HMMC, LMMV, LMMM, LMCM, LMMC, YMMM, LINE, PSET, POINT, SRCH), horizontal line interrupt (R#19/R#23, IE1), banded renderer for mid-frame register and palette changes
- **AY-3-8910 PSG** — 16 registers, 3 tone channels, noise channel, envelope generator with 8 waveform shapes, quasi-logarithmic amplitude table, 44100 Hz PCM sample output at 735 samples/frame
- **Konami SCC** — 5-channel wavetable synthesiser with 4 waveform banks (32 samples each), 12-bit frequency and 4-bit volume per channel, mixed into the audio output alongside PSG
- **i8255 PPI** — slot-select register (port 0xA8), 11-row × 8-bit MSX keyboard matrix (port 0xA9), row selection (port 0xAA)
- **MSX1 slot system** — 4-page × 4-slot dispatch, BIOS ROM in slot 0, cartridge in slot 1, optional second cartridge in slot 2, 32 KB RAM in slot 3
- **MSX2 sub-slot system** — primary slot 3 expanded into 4 secondary slots; sub-ROM in sub-slot 3-0, 128 KB RAM mapper in sub-slot 3-2
- **RAM mapper** — 128 KB main RAM (8 × 16 KB segments), segment registers at ports 0xFC–0xFF
- **RTC** — RP5C01 real-time clock, ports 0xB4–0xB5
- **Cartridge mappers** — Flat (no bank switching), ASCII8, ASCII16, Konami, KonamiSCC, Majutsushi (DAC), ASCII8SRAM2/8, ASCII16SRAM2/8, R-Type; auto-detected from a SHA1-based ROM database
- **SDL2 frontend** — 768×576 window by default (256×192 × scale 3; SCREEN 6/7 resize to maintain aspect ratio), hardware palette, mono audio at 44100 Hz, fullscreen toggle, screenshot, state save/load, automatic frame skip (VDP pixel render suppressed on late frames; VBlank interrupt still fires every frame)
- **Physical joystick** — SDL2 GameController and raw joystick APIs, hot-plug/unplug, keyboard joystick emulation (WASD + ZX/.,)
- **State save/load** — complete hardware snapshot (CPU, RAM, VDP, PSG, SCC, mapper banks) via pickle, PNG screenshot alongside each save, `saves/latest.*` symlinks for quick resume
- **ROM database** — SHA1 title lookup for automatic game title detection and mapper selection
- **Interactive debugger** — REPL accessible via Ctrl+C or breakpoint hit; breakpoints/watchpoints, step execution, register/VRAM dump, disassembly, VDP trace, mapper trace, slot inspector
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


---

## Component reference

### CPU — Zilog Z80

| Item | Detail |
|------|--------|
| Implementation | `msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`, `msx/cpu/registers.py` |
| Known limitations | OTIR/INIR and similar block I/O instructions are not cycle-exact across page boundaries; R register increments only on opcode fetch |

### VDP — TMS9918A (MSX1)

| Item | Detail |
|------|--------|
| Implementation | `msx/vdp/vdp.py`, `msx/vdp/renderer.py` |
| Known limitations | Mid-frame register-change timing and undocumented sprite-overflow behaviour are not emulated |

### VDP — Yamaha V9938 (MSX2)

| Item | Detail |
|------|--------|
| Implementation | `msx/vdp/v9938.py`, `msx/vdp/v9938_renderer.py` |
| Known limitations | Command timing is approximate, not cycle-accurate; beam-raced blits and double-buffered VRAM updates within a single frame are not reproduced faithfully |

### PSG — AY-3-8910

Implementation: `msx/psg.py`

### SCC — Konami SCC (Sound Creative Chip)

| Item | Detail |
|------|--------|
| Implementation | `msx/scc.py` |
| Activation | KonamiSCC mapper activates SCC when 0x3F is written to 0x9000; registers appear at 0x9800 |

### PPI — Intel i8255

| Item | Detail |
|------|--------|
| Implementation | `msx/ppi.py` |
| Known limitations | Cassette interface (port 0xAA bits 4–7) is not implemented |

### RAM mapper

Implementation: `msx/ram_mapper.py`

### RTC — RP5C01

| Item | Detail |
|------|--------|
| Implementation | `msx/rtc.py` |
| Known limitations | Clock reads reflect host system time; no alarm or timer output |

### Memory bus / slot system

| Item | Detail |
|------|--------|
| Implementation | `msx/memory.py` |
| Address space | Flat 64 KB (0x0000–0xFFFF), four 16 KB pages |
| Slot 0 pages 0–1 | BIOS ROM (read-only, 0x0000–0x7FFF) |
| Slot 0 page 2 | Logo ROM (`cbios_logo_msx1.rom`) at 0x8000–0xBFFF; auto-loaded from same directory as BIOS; returns 0xFF if absent |
| Slot 1 | Cartridge ROM via mapper |
| Slot 2 | Second cartridge ROM via `_mapper2`; open bus (0xFF on read, writes ignored) when no slot 2 ROM is loaded |
| Slot 3 (MSX1) | 32 KB RAM at 0x8000–0xFFFF |
| Slot 3 (MSX2) | Expanded into 4 secondary slots; sub-ROM in 3-0, 128 KB RAM mapper in 3-2 |

### Cartridge mappers

| Mapper | Description |
|--------|-------------|
| `FlatMapper` | No bank switching; mirrors ROM across the 32 KB cartridge region |
| `Ascii8Mapper` | Four 8 KB windows; control registers at 0x6000–0x7FFF |
| `Ascii16Mapper` | Two 16 KB windows; control registers at 0x6000–0x7FFF |
| `KonamiMapper` | Three 8 KB windows; bank register written to window base address |
| `KonamiSCCMapper` | Same as Konami; activates SCC when 0x3F is written to 0x9000 |
| `MajutsushiMapper` | ASCII8 variant with DAC output on writes to 0x9000 |
| `ASCII8SRAM2`, `ASCII8SRAM8` | ASCII8 with 2 KB or 8 KB battery-backed SRAM |
| `ASCII16SRAM2`, `ASCII16SRAM8` | ASCII16 with 2 KB or 8 KB battery-backed SRAM |
| `RTypeMapper` | 8 KB windows; bank-0 fixed at ROM start |

Mapper is auto-detected from a SHA1 ROM database. Override with `--mapper`.

Slot 2 uses a separate mapper controlled by `--mapper2` (auto-detected by default). `KonamiSCC` is not a valid mapper for slot 2; if the ROM database returns `KonamiSCC` for a slot 2 cartridge, the mapper automatically falls back to `Konami` with a warning on stderr.

### ROM database

| Item | Detail |
|------|--------|
| Implementation | `msx/romdb.py` |
| Source | [openMSX software database](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml) (referenced; all entries are independently compiled factual data) |
| Fallback | If PyYAML is not installed, or the ROM is not found, the emulator continues without a title and falls back to `--mapper auto` heuristics |

### I/O bus

Implementation: `msx/io.py` — range-based port registration; reads/writes dispatched to registered handler.

### Keyboard / joystick input

| Item | Detail |
|------|--------|
| Keyboard | `msx/input.py`; 11 rows × 8 bits, active-low, per MSX Technical Handbook |
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
| PyYAML | 6.0 | ROM database title lookup and machine YAML loading (graceful fallback if absent) |

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
2. Extract the archive and copy the relevant files into `roms/cbios/` in this repository.

For MSX1 (`cbios_msx1_jp`, the default when a known MSX1 cartridge is detected):
- `cbios_main_msx1_jp.rom`
- `cbios_logo_msx1.rom`

For MSX2 (`cbios_msx2_jp`, the default when no cartridge or an MSX2 cartridge is given):
- `cbios_main_msx2.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

The required filenames for each machine ID are listed in the corresponding YAML under `config/machines/`.

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
# MSX BASIC only — default machine (cbios_msx2_jp, no cartridge)
python .

# With a cartridge — machine auto-detected from ROM database
python . path/to/game.rom

# Explicitly select MSX1 (Japan)
python . path/to/game.rom --machine cbios_msx1_jp

# Explicitly select MSX2 (Japan)
python . path/to/game.rom --machine cbios_msx2_jp

# Double emulation speed
python . path/to/game.rom --speed 2.0

# Dual cartridge (slot 1 and slot 2)
python . path/to/game1.rom --slot2 path/to/game2.rom

# Force a specific mapper type
python . path/to/game.rom --mapper KonamiSCC

# Resume from the most recent save state
python . path/to/game.rom --resume

# Resume from a specific save file
python . path/to/game.rom --resume saves/game_20260605_120000.state

# Enable debug logging
python . path/to/game.rom --debug --log trace.log

# Set breakpoints at startup
python . path/to/game.rom --break-point C000,D000

# Run 300 frames headlessly and capture VDP trace (no SDL window)
python . path/to/game.rom --count-frame 300 --vdp-trace --vdp-trace-out trace.log
```

### Command-line options

| Option | Default | Description |
|--------|---------|-------------|
| `cartridge` | _(none)_ | Path to the cartridge ROM |
| `--machine MACHINE_ID` | _(auto)_ | Machine configuration ID (e.g. `cbios_msx2_jp`). Auto-detected from ROM database when omitted |
| `--speed FLOAT` | `1.0` | Emulation speed multiplier |
| `--mapper TYPE` | `auto` | Slot 1 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `KonamiSCC`, `Majutsushi`, `ASCII8SRAM2`, `ASCII8SRAM8`, `ASCII16SRAM2`, `ASCII16SRAM8`, `R-Type` |
| `--slot2 ROM2` | _(none)_ | Path to the slot 2 cartridge ROM |
| `--mapper2 TYPE` | `auto` | Slot 2 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `Majutsushi` (KonamiSCC not supported in slot 2) |
| `--resume [FILE]` | _(none)_ | Resume from `saves/latest.state`, or a specific `.state` file |
| `--frame-skip MODE` | `auto` | Frame skip: `auto` skips VDP rendering on late frames; `none` disables |
| `--debug` | off | Enable structured diagnostic logging to stderr |
| `--log FILE` | _(none)_ | Write diagnostic log to a file (requires `--debug`) |
| `--vdp-trace` | off | Enable VDP register-write tracing to stdout |
| `--vdp-trace-out FILE` | stdout | Write VDP trace to FILE instead of stdout |
| `--mapper-trace` | off | Enable cartridge mapper bank-switch tracing (MAP\_BANK records) |
| `--mapper-trace-out FILE` | stdout | Write mapper trace to FILE instead of stdout |
| `--count-frame N` | _(none)_ | Run exactly N frames headlessly and exit (no SDL window) |
| `--break-point ADDRS` | _(none)_ | Comma-separated hex breakpoint addresses, max 4 (MSX2 only) |
| `--watch-point ADDRS` | _(none)_ | Watchpoint addresses, max 4 (MSX2 only); append `,r`, `,w`, or `,rw` after each address to restrict to read, write, or both (default: `rw`). Example: `C000,rw,D000,r` |

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

---

## Machine configuration

Hardware topology — VDP type, RAM size, slot wiring, ROM files — is declared in YAML files under `config/machines/`. The `--machine` flag selects a configuration by ID; when omitted, the ROM database determines the generation automatically (MSX1 ROM → `cbios_msx1_jp`; MSX2 ROM or no cartridge → `cbios_msx2_jp`).

### Available machine IDs

| ID | Generation | Region | VDP |
|----|-----------|--------|-----|
| `cbios_msx1` | MSX1 | International | TMS9918A |
| `cbios_msx1_jp` | MSX1 | Japan | TMS9918A |
| `cbios_msx1_eu` | MSX1 | Europe | TMS9918A |
| `cbios_msx1_br` | MSX1 | Brazil | TMS9918A |
| `cbios_msx2` | MSX2 | International | V9938 |
| `cbios_msx2_jp` | MSX2 | Japan (default) | V9938 |
| `cbios_msx2_eu` | MSX2 | Europe | V9938 |
| `cbios_msx2_br` | MSX2 | Brazil | V9938 |

### Machine YAML structure

A machine file declares the CPU, slot wiring, and built-in devices. Device definitions live separately under `config/devices/` and are referenced by `ref:`.

```yaml
schema_version: 1
id: cbios_msx2
name: "Generic MSX2 (C-BIOS, International)"
generation: msx2
video_standard: ntsc
cpu:
  type: z80a
  clock_mhz: 3.579545

slots:
  primary:
    0:
      content:
        - rom:
            file: cbios_main_msx2.rom
            size_kb: 32
            pages: [0, 1]
            sha1: null
        - rom:
            file: cbios_logo_msx2.rom
            size_kb: 16
            pages: [2]
            sha1: null
    1: {type: cartridge}
    2: {type: cartridge}
    3:
      expanded: true
      secondary:
        0:
          content:
            - rom:
                file: cbios_sub.rom
                size_kb: 32
                pages: [0, 1]
                sha1: null
        2:
          type: ram
          size_kb: 128
          mapper: standard

builtin_devices:
  - ref: ppi8255
  - ref: vdp_v9938
    overrides: {vram_kb: 128}
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

Key fields:

| Field | Description |
|-------|-------------|
| `generation` | `msx1` or `msx2`; determines VDP class and memory model |
| `cpu.clock_mhz` | Z80A clock frequency (3.579545 MHz for NTSC MSX) |
| `slots.primary.N` | Primary slot N: `{type: cartridge}`, `{type: ram, ...}`, or inline ROM `content` |
| `slots.primary.N.expanded` | Set `true` to expand into 4 secondary slots |
| `builtin_devices` | Devices wired directly (not slot-based): VDP, PSG, PPI, RTC, RAM mapper |
| `overrides` | Shallow merge over a device's defaults (e.g. `vram_kb: 128` for V9938) |
| `sha1` | `null` means load without hash verification |

To use a custom machine definition, add a new YAML file to `config/machines/` and pass its `id` as `--machine`. Device entries with `implemented: false` in their device YAML are skipped at load time with a warning.

---

## Running tests

The test suite covers all major components with 1245 tests spanning unit tests for individual opcodes and hardware registers, integration tests that wire multiple components together, and scenario-level tests whose conditions are derived directly from the component specs.

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
│   ├── vdp/               # VDP (TMS9918A + V9938 core, renderers, tracer)
│   ├── debug/             # DebugLogger, CPU/I/O trace, hang detector
│   ├── debugger/          # Interactive REPL (prompt, disassembler)
│   ├── machine.py         # Component wiring and frame loop
│   ├── machine_loader.py  # YAML-based machine configuration loader
│   ├── memory.py          # Slot-based memory bus
│   ├── mapper.py          # Cartridge mappers (Flat, ASCII8/16, Konami, SCC, ...)
│   ├── mapper_tracer.py   # Cartridge bank-switch tracer
│   ├── ram_mapper.py      # MSX2 RAM mapper (128 KB, 8 segments)
│   ├── rtc.py             # RP5C01 real-time clock
│   ├── psg.py             # AY-3-8910 PSG + audio synthesis
│   ├── scc.py             # Konami SCC wavetable synthesiser
│   ├── ppi.py             # i8255 PPI (slot register, keyboard)
│   ├── io.py              # I/O bus (port dispatch)
│   ├── input.py           # Keyboard matrix + joystick input state
│   ├── joystick.py        # Physical joystick manager (SDL2)
│   ├── frame_timer.py     # 60 fps pacing + FPS measurement
│   ├── romdb.py           # SHA1-based ROM title/mapper database
│   └── state.py           # Save/load machine state (pickle + PNG)
├── config/
│   ├── devices/           # Device YAML definitions (VDP, PSG, PPI, RTC, ...)
│   └── machines/          # Machine YAML definitions (cbios_msx1_jp, cbios_msx2_jp, ...)
├── roms/
│   └── cbios/             # C-BIOS ROM files (not in version control)
├── saves/                 # Save states and screenshots (created at runtime)
├── openspec/
│   └── specs/             # Component specifications (not included in the public repository)
├── tests/                 # Test suite — 1245 tests (not included in the public repository)
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

There is no formal CONTRIBUTING.md at present. Please open a GitHub issue to discuss significant changes before submitting a PR. Bug reports for ROMs other than the listed target titles are welcome; compatibility fixes will be considered on a best-effort basis.

---

## Acknowledgements

- **[openMSX](https://openmsx.org/)** — ROM identification data references openMSX softwaredb.xml (https://github.com/openMSX/openMSX), but all entries are independently compiled factual data. openMSX is released under the GNU GPL v2.
- **[C-BIOS](https://cbios.sourceforge.net/)** — recommended free MSX BIOS replacement used for testing.

---

## License

MIT — see [LICENSE](LICENSE).
