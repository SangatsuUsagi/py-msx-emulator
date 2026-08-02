# py-msx-emulator

A functionally accurate MSX1/MSX2 emulator written in pure Python 3.10+, driven
by machine-readable component specifications.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-1607%20passing-brightgreen)

[日本語版 README はこちら](README_ja.md)

---

The goal is to run specific titles accurately on their target hardware
generation:

- **MSX1:**
  [Salamander (沙羅曼蛇) by KONAMI](<https://en.wikipedia.org/wiki/Salamander_(video_game)>)
  ·
  [Nemesis 2 (グラディウス2) by KONAMI](<https://en.wikipedia.org/wiki/Nemesis_2_(MSX)>)
  ·
  [Penguin Adventure (夢大陸アドベンチャー) by KONAMI](https://en.wikipedia.org/wiki/Penguin_Adventure)
- **MSX2:**
  [Legacy of the Wizard (ドラゴンスレイヤーIV ドラスレファミリー) by Falcom](https://en.wikipedia.org/wiki/Legacy_of_the_Wizard)
  · [Romancia (ロマンシア) by Falcom](https://en.wikipedia.org/wiki/Romancia)

It has only been tested against physical ROM dumps owned by the author, so other
MSX1 or MSX2 ROMs are not guaranteed to work. Bug reports for other titles are
welcome, but support is best-effort.

Every hardware component is pure Python and is defined by a machine-readable
specification (under `openspec/specs/`) before it is implemented; those specs
drive the test suite. Component wiring is explicit — done by hand in
`build_machine()`, with no reflection or dependency-injection magic. The only
platform-specific dependency is pysdl2, for the display and audio frontend.

---

## Emulated hardware

Each component below is pure Python. "Implementation" points to the source
file(s); "Known limitations" lists what is not (yet) reproduced.

### CPU — Zilog Z80

Full register file (AF, BC, DE, HL, IX, IY, SP, PC, I, R and shadow registers),
all 252 documented opcodes, prefix tables CB/DD/ED/FD, the undocumented
IXH/IXL/IYH/IYL register-access opcodes, maskable (INT mode 1 and mode 2) and
non-maskable (NMI) interrupts, and T-state accurate stepping with a configurable
MSX **M1 wait state** (+1 T-state per opcode fetch, matching real MSX / openMSX;
default 0 keeps the core a pure Zilog Z80).

- Implementation: `msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`, `msx/cpu/registers.py`
- Known limitations: OTIR/INIR and similar block I/O instructions are not
  cycle-exact across page boundaries; the R register increments only on opcode
  fetch.

### VDP — TMS9918A (MSX1)

16 KB VRAM, 8 control registers, Screen modes 0–3 (Text 40-col, Graphic 1,
Graphic 2, Multicolor), sprite rendering with size/magnification, 5th-sprite and
coincidence flags, and the VBlank interrupt.

- Implementation: `msx/vdp/vdp.py`, `msx/vdp/renderer.py`
- Known limitations: mid-frame register-change timing and undocumented
  sprite-overflow behaviour are not emulated.

### VDP — Yamaha V9938 (MSX2)

128 KB VRAM, 28 control registers, a programmable 16-colour palette (9-bit
GRB333), Screen modes 0–8 (SCREEN 0 through SCREEN 8), the hardware command
engine (HMMV, HMMM, HMMC, LMMV, LMMM, LMCM, LMMC, YMMM, LINE, PSET, POINT,
SRCH), the horizontal line interrupt (R#19/R#23, IE1), and a banded renderer for
mid-frame register and palette changes.

- Implementation: `msx/vdp/v9938.py`, `msx/vdp/v9938_renderer.py`
- Known limitations: command timing is approximate, not cycle-accurate;
  beam-raced blits and double-buffered VRAM updates within a single frame are not
  reproduced faithfully.

### PSG — AY-3-8910

16 registers, 3 tone channels, a noise channel, an envelope generator with 8
waveform shapes, a quasi-logarithmic amplitude table, and 44100 Hz PCM output at
735 samples/frame. **Sub-frame software-PCM** reproduction cycle-timestamps
volume-register writes and replays them at their in-frame sample positions; the
tone generator is integrated over each output sample, so an ultrasonic period-0
PCM carrier is band-limited to its duty average instead of aliasing to a
full/zero chop.

- Implementation: `msx/psg.py`

### SCC — Konami SCC (Sound Creative Chip)

5-channel wavetable synthesiser with 4 waveform banks (32 samples each), 12-bit
frequency and 4-bit volume per channel, mixed into the audio output alongside
the PSG.

- Implementation: `msx/scc.py`
- Activation: the KonamiSCC mapper activates the SCC when 0x3F is written to
  0x9000; registers appear at 0x9800.

### FM-PAC — MSX-MUSIC cartridge (YM2413/OPLL)

Optional overlay cartridge enabled with `--fmpac`, placed in primary slot 2:
64 KB banked ROM, 8 KB battery-backed SRAM with openMSX-compatible magic-value
unlock, and a YM2413 (OPLL) FM sound chip — 9-channel 2-operator melody synthesis
(15 built-in instruments plus a user-defined tone), ADSR envelopes, and rhythm
mode (bass drum, snare, tom, top cymbal, hi-hat), mixed into the audio output
alongside PSG/SCC.

| Item | Detail |
| --- | --- |
| Implementation | `msx/fmpac.py` (cartridge device), `msx/opll.py` (YM2413/OPLL chip) |
| Activation | `--fmpac` overlays the cartridge in primary slot 2 (any base `--machine`); ROM at `roms/fmpac/fmpac.rom` |
| Memory map | 64 KB ROM in four 16 KB banks (`0x7FF7` bank register) at `0x4000-0x7FFF`; 8 KB SRAM (openMSX-exact `0x1FFE`-byte usable region, magic-value unlock at `0x5FFE`/`0x5FFF`); memory-mapped OPLL registers (`0x7FF4`/`0x7FF5`), enable register (`0x7FF6`) |
| I/O ports | `0x7C`/`0x7D`, gated by the enable register's bit 0 |
| SRAM persistence | `saves/sram/fmpac.sram`, loaded on start and saved on exit |
| OPLL synthesis | Faithful port of emu2413 v1.5.9: log-domain synthesis (log-sin + exp tables), hardware envelope-rate tables with key-scaling, AM/PM LFO, the YM2413 instrument ROM, and rhythm mode (register `0x0E`: bass drum, snare, tom, top cymbal, hi-hat) with the real short-noise / LFSR taps |
| Known limitations | Output rate conversion uses an accumulate-and-average decimator (chip clk/72 → 44100 Hz) rather than emu2413's windowed-sinc resampler; the analog-style low-pass cleans up the residual imaging. Only the YM2413 instrument set is included (no VRC7 / YMF281 banks); no channel masking / stereo pan |

### Audio output filter

An analog-style output low-pass (2-pole Butterworth) models the RC filter on real
MSX audio out, removing residual imaging/aliasing from the mixed PSG/SCC/DAC/OPLL
signal.

- Implementation: `msx/audio_filter.py`

### PPI — Intel i8255

Slot-select register (port 0xA8), the 11-row × 8-bit MSX keyboard matrix (port
0xA9), and row selection (port 0xAA).

- Implementation: `msx/ppi.py`
- Known limitations: the cassette interface (port 0xAA bits 4–7) is not
  implemented.

### Memory bus / slot system

MSX1 uses a 4-page × 4-slot dispatch: BIOS ROM in slot 0, a cartridge in slot 1,
an optional second cartridge in slot 2, and 32 KB RAM in slot 3. On MSX2, primary
slot 3 is expanded into 4 secondary slots, with the sub-ROM in sub-slot 3-0 and
the 128 KB RAM mapper in sub-slot 3-2.

| Item | Detail |
| --- | --- |
| Implementation | `msx/memory.py` |
| Address space | Flat 64 KB (0x0000–0xFFFF), four 16 KB pages |
| Slot 0 pages 0–1 | BIOS ROM (read-only, 0x0000–0x7FFF) |
| Slot 0 page 2 | Logo ROM (`cbios_logo_msx1.rom`) at 0x8000–0xBFFF; auto-loaded from same directory as BIOS; returns 0xFF if absent |
| Slot 1 | Cartridge ROM via mapper |
| Slot 2 | Second cartridge ROM via `_mapper2`; open bus (0xFF on read, writes ignored) when no slot 2 ROM is loaded |
| Slot 3 (MSX1) | 32 KB RAM at 0x8000–0xFFFF |
| Slot 3 (MSX2) | Expanded into 4 secondary slots; sub-ROM in 3-0, 128 KB RAM mapper in 3-2 |

### RAM mapper

128 KB main RAM (8 × 16 KB segments) with segment registers at ports 0xFC–0xFF.

- Implementation: `msx/ram_mapper.py`

### RTC — RP5C01

Real-time clock at ports 0xB4–0xB5.

- Implementation: `msx/rtc.py`
- Known limitations: clock reads reflect host system time; no alarm or timer
  output.

### Cartridge mappers

Flat (no bank switching), ASCII8, ASCII16, Konami, KonamiSCC, Majutsushi (DAC),
ASCII8SRAM2/8, ASCII16SRAM2/8, and R-Type, auto-detected from a SHA1-based ROM
database. Override with `--mapper`.

| Mapper | Description |
| --- | --- |
| `FlatMapper` | No bank switching; mirrors ROM across the 32 KB cartridge region |
| `Ascii8Mapper` | Four 8 KB windows; control registers at 0x6000–0x7FFF |
| `Ascii16Mapper` | Two 16 KB windows; control registers at 0x6000–0x7FFF |
| `KonamiMapper` | Three 8 KB windows; bank register written to window base address |
| `KonamiSCCMapper` | Same as Konami; activates SCC when 0x3F is written to 0x9000 |
| `MajutsushiMapper` | ASCII8 variant with DAC output on writes to 0x9000 |
| `ASCII8SRAM2`, `ASCII8SRAM8` | ASCII8 with 2 KB or 8 KB battery-backed SRAM |
| `ASCII16SRAM2`, `ASCII16SRAM8` | ASCII16 with 2 KB or 8 KB battery-backed SRAM |
| `RTypeMapper` | 8 KB windows; bank-0 fixed at ROM start |

Slot 2 uses a separate mapper controlled by `--mapper2` (auto-detected by
default). `KonamiSCC` is not a valid mapper for slot 2; if the ROM database
returns `KonamiSCC` for a slot 2 cartridge, the mapper automatically falls back
to `Konami` with a warning on stderr.

### Floppy disk drive (WD2793)

Generic FDC layer (disk image / drive / controller / connection-style interface)
with a WD2793 controller and Sony/Philips connection style, as used by the Sony
HB-F1XD. Registers are memory-mapped in slot 3 sub-slot 0; `*.dsk` images mount
via `--fdd1`. Supports Disk BASIC boot, `CALL FORMAT`, and file read/write with
write-back on exit; disks can be swapped at runtime from the debugger REPL
(`fdd1`/`fdd2`).

### ROM database

SHA1 title lookup for automatic game-title detection and mapper selection.

| Item | Detail |
| --- | --- |
| Implementation | `msx/romdb.py` |
| Source | [openMSX software database](https://github.com/openMSX/openMSX/blob/master/share/softwaredb.xml) (referenced; all entries are independently compiled factual data) |
| Fallback | If PyYAML is not installed, or the ROM is not found, the emulator continues without a title and falls back to `--mapper auto` heuristics |

### I/O bus

Range-based port registration; reads/writes dispatched to the registered handler.

- Implementation: `msx/io.py`

### Keyboard / joystick input

| Item | Detail |
| --- | --- |
| Keyboard | `msx/input.py`; 11 rows × 8 bits, active-low, per MSX Technical Handbook |
| Physical joystick | `msx/joystick.py`; SDL2 GameController (preferred) + raw joystick fallback; hot-plug/unplug |
| Keyboard emulation | WASD = Joy1 directions; Z/X or ,/. = Trigger A/B; arrow keys also mapped |

### SDL2 frontend

Fixed 768×636 window (256×212 × scale 3) for every machine and mode. The rendered
frame is always 212 lines (192-line SCREEN modes are centred with border rows) so
a 4:3 CRT aspect is kept regardless of R#9 LN, and SCREEN 6/7 (512-wide)
downscale to the same window width to preserve pixel aspect. Hardware palette,
mono audio at 44100 Hz through the analog-style low-pass filter, fullscreen
toggle, screenshot, state save/load, and automatic frame skip (VDP pixel render
suppressed on late frames; the VBlank interrupt still fires every frame).

- Implementation: `frontend/sdl2_frontend.py`

### State save/load

Complete hardware snapshot (CPU, RAM, VDP, PSG, SCC, FM-PAC/OPLL, mapper banks)
as a stdlib JSON container, a PNG screenshot alongside each save, and
`saves/states/latest.*` symlinks for quick resume.

- Implementation: `msx/state.py`

### Interactive debugger

A REPL reachable via Ctrl+C or a breakpoint hit: breakpoints/watchpoints, step
execution, register/VRAM dump, disassembly, VDP trace, mapper trace, slot
inspector, and floppy disk swap (`fdd1`/`fdd2 [FILE|-]`).

- Implementation: `msx/debugger/`

### Debug tooling

Opt-in structured logging, CPU instruction trace, I/O port trace, and a hang
detector.

- Implementation: `msx/diagnostics/`

---

## Spec-driven architecture

Every hardware component in this emulator is defined by a machine-readable
specification written before any code is produced. This project was implemented
using [Claude Code](https://claude.ai/code) and
[OpenSpec](https://openspec.dev/).

### How it works

Specs live under `openspec/specs/<component>/spec.md`. Each spec file uses a
structured prose format that interleaves natural-language requirements with
concrete WHEN/THEN scenarios:

```markdown
### Requirement: Instruction fetch and execute

`Z80.step() -> int` SHALL fetch the opcode byte at PC, advance PC, decode and
execute the instruction, and return the number of T-states consumed.

#### Scenario: NOP executes in 4 T-states

- **WHEN** opcode 0x00 (NOP) is at PC and `step()` is called
- **THEN** the return value is 4 and PC is incremented by 1

#### Scenario: LD BC, nn loads a 16-bit immediate

- **WHEN** bytes [0x01, 0x34, 0x12] are at PC and `step()` is called
- **THEN** BC is 0x1234 and PC is incremented by 3
```

The scenarios map directly to unit tests, making it straightforward to verify
that the implementation matches the specification. When a new feature is added
or an existing component is changed, the spec is updated first and the
implementation follows.

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

## Performance

Measured with the `--benchmark` CLI flag: a headless run, unthrottled (no
`FrameTimer` pacing), started from a saved mid-game scene via `--resume` (BIOS
boot time is excluded from the measurement). Every frame still runs full CPU
emulation and VDP-to-offscreen-buffer rendering, the same cost paid during
interactive play — but `--benchmark` never opens an SDL window, generates audio
samples, or performs texture upload/blit, so real interactive sessions run
somewhat slower than the raw numbers below.

For each (runtime, game) pair, 10 runs of 10000 frames each were measured, the
fastest and slowest were discarded, and the remaining 8 were averaged into the
score.

| Platform | Runtime | Game | Avg FPS (`--benchmark`) | vs. 60 fps target |
| --- | --- | --- | --- | --- |
| Apple MacBook Pro (M5 Pro) | CPython 3.12.13 | MSX1: Salamander (KonamiSCC) | 258.87 | ~4.3× |
| Apple MacBook Pro (M5 Pro) | CPython 3.12.13 | MSX2: Dragon Slayer 4 (ASCII8) | 414.04 | ~6.9× |
| Apple MacBook Pro (M5 Pro) | PyPy 7.3.19 (Python 3.10.16) | MSX1: Salamander (KonamiSCC) | 1079.28 | ~18.0× |
| Apple MacBook Pro (M5 Pro) | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer 4 (ASCII8) | 1251.85 | ~20.9× |
| Raspberry Pi 5 | CPython 3.12.13 | MSX1: Salamander (KonamiSCC) | 67.17 | ~1.1× |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: Dragon Slayer 4 (ASCII8) | 104.95 | ~1.7× |
| Raspberry Pi 5 | PyPy 7.3.19 (Python 3.10.16) | MSX1: Salamander (KonamiSCC) | 219.29 | ~3.7× |
| Raspberry Pi 5 | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer 4 (ASCII8) | 279.85 | ~4.7× |

Every combination tested clears the raw 60 fps target. The tightest margin is
Raspberry Pi 5 with CPython running Salamander (MSX1, KonamiSCC mapper — the
heaviest rendering/audio load among the target titles) at ~1.1×; PyPy raises the
same case to ~3.7×. On hardware weaker than a Raspberry Pi 5, or under a heavier
title, a run can still drop below 60 fps — in which case the game runs in slow
motion at a rate proportional to the achieved frame rate, and audio degrades
(clicks or silence) because samples are generated per-frame while the audio
device always consumes at 44100 Hz. PyPy3 is a drop-in alternative that
substantially improves throughput on slower hardware and is the recommended way
to keep headroom on constrained hosts like a Raspberry Pi.

Automatic frame skip (`--frame-skip auto`, the default) suppresses VDP pixel
rendering on frames that miss the deadline while still firing the VBlank
interrupt every frame. This improves display smoothness on hosts near but below
the 60 fps target. Audio quality is unaffected by frame skip; underruns remain
on any platform below 60 fps. Frame skip can be disabled with
`--frame-skip none`.

`--speed` scales the target frame rate (e.g. `--speed 2.0` runs the game at 2×
real time on a capable host). It does not compensate for insufficient host
throughput and does not improve audio quality on slow hardware.

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

- `cbios_main_msx2.rom`
- `cbios_logo_msx2.rom`
- `cbios_sub.rom`

The required filenames for each machine ID are listed in the corresponding YAML
under `config/machines/`.

> **Legal note:** do not use a copyrighted MSX BIOS dump extracted from a
> commercial machine. C-BIOS is the recommended free and legal alternative. The
> `roms/` directory is excluded from version control by `.gitignore`.

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

# Window scale (default 3): 2x for a small display, 4x for a large one
python . path/to/game.rom --scale 4

# Dual cartridge (slot 1 and slot 2)
python . path/to/game1.rom --slot2 path/to/game2.rom

# Sony HB-F1XD with a floppy disk mounted in drive A (boots Disk BASIC)
python . --machine hb_f1xd --fdd1 path/to/disk.dsk

# Create a blank 720 KB disk to format with CALL FORMAT
python tools/make_blank_dsk.py blank.dsk

# Force a specific mapper type
python . path/to/game.rom --mapper KonamiSCC

# Add an FM-PAC (MSX-MUSIC) cartridge in slot 2 alongside a game in slot 1
python . path/to/game.rom --fmpac

# Resume from the most recent save state
python . path/to/game.rom --resume

# Resume from a specific save file
python . path/to/game.rom --resume saves/states/game_20260605_120000.state

# Enable debug logging
python . path/to/game.rom --debug --log trace.log

# Set breakpoints at startup
python . path/to/game.rom --break-point C000,D000

# Run 300 frames headlessly and capture VDP trace (no SDL window)
python . path/to/game.rom --count-frame 300 --vdp-trace --vdp-trace-out trace.log

# Benchmark: run 10000 frames headlessly (default) and report average FPS
python . path/to/game.rom --benchmark

# Benchmark 30000 frames starting from a saved scene
python . path/to/game.rom --benchmark 30000 --resume saves/states/game_20260605_120000.state
```

### Command-line options

| Option | Default | Description |
| --- | --- | --- |
| `cartridge` | _(none)_ | Path to the cartridge ROM |
| `--machine MACHINE_ID` | _(auto)_ | Machine configuration ID (e.g. `cbios_msx2_jp`). Auto-detected from ROM database when omitted |
| `--speed FLOAT` | `1.0` | Emulation speed multiplier |
| `--scale N` | `3` | Integer window scale over the 256×212 base resolution (e.g. `2` for a small display, `4` for a large one) |
| `--mapper TYPE` | `auto` | Slot 1 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `KonamiSCC`, `Majutsushi`, `ASCII8SRAM2`, `ASCII8SRAM8`, `ASCII16SRAM2`, `ASCII16SRAM8`, `R-Type` |
| `--slot2 ROM2` | _(none)_ | Path to the slot 2 cartridge ROM |
| `--mapper2 TYPE` | `auto` | Slot 2 mapper: `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `Majutsushi` (KonamiSCC not supported in slot 2) |
| `--fdd1 DSK` | _(none)_ | Floppy `*.dsk` image mounted in drive A (machines with an FDC, e.g. `hb_f1xd`); writes flush back to the file on exit |
| `--fdd2 DSK` | _(none)_ | Floppy `*.dsk` image mounted in drive B (only on machines with two drives) |
| `--resume [FILE]` | _(none)_ | Resume from `saves/states/latest.state`, or a specific `.state` file |
| `--frame-skip MODE` | `auto` | Frame skip: `auto` skips VDP rendering on late frames; `none` disables |
| `--debug` | off | Enable structured diagnostic logging to stderr |
| `--log FILE` | _(none)_ | Write diagnostic log to a file (requires `--debug`) |
| `--vdp-trace` | off | Enable VDP register-write tracing to stdout |
| `--vdp-trace-out FILE` | stdout | Write VDP trace to FILE instead of stdout |
| `--mapper-trace` | off | Enable cartridge mapper bank-switch tracing (MAP\_BANK records) |
| `--mapper-trace-out FILE` | stdout | Write mapper trace to FILE instead of stdout |
| `--count-frame N` | _(none)_ | Run exactly N frames headlessly and exit (no SDL window) |
| `--benchmark [FRAMES]` | _(none)_ | Run headlessly, unthrottled, for FRAMES frames (default: 10000) and report average FPS. Combine with `--resume` to benchmark from a saved scene. Mutually exclusive with `--count-frame` |
| `--break-point ADDRS` | _(none)_ | Comma-separated hex breakpoint addresses, max 4 (MSX2 only) |
| `--watch-point ADDRS` | _(none)_ | Watchpoint addresses, max 4 (MSX2 only); append `,r`, `,w`, or `,rw` after each address to restrict to read, write, or both (default: `rw`). Example: `C000,rw,D000,r` |
| `--rpc` | off | Enable the embedded Unix-socket JSON-RPC control server (interactive run mode). See [Remote control](#remote-control-socket-rpc--mcp) |
| `--rpc-socket PATH` | `/tmp/py_msx_emu.sock` | Unix socket path for `--rpc` (no effect without `--rpc`) |

### Configuration file (`py_emulator.yaml`)

Frequently-used defaults can be set in an optional `py_emulator.yaml` at the
repository root instead of typing flags every run. Copy the bundled
`py_emulator.example.yaml` to `py_emulator.yaml` and edit it:

```bash
cp py_emulator.example.yaml py_emulator.yaml
```

Values resolve with the precedence **built-in default < `py_emulator.yaml` <
command-line argument** — a flag you pass always wins, and the file only fills in
options you did not pass. If the file is absent, behaviour is unchanged. The file
is git-ignored, so local settings stay out of version control.

```yaml
machine: cbios_msx2_jp   # default machine ID (auto-detected when unset)
speed: 1.0               # emulation speed multiplier
scale: 3                 # integer window scale over the 256x212 base
mapper: auto             # slot 1 mapper (see --mapper choices)
fmpac: false             # overlay an FM-PAC in slot 2

rpc:
  enabled: false
  socket: /tmp/py_msx_emu.sock

joystick:
  turbo_hz: 20           # auto-fire rate (round(60 / turbo_hz) frames)
  buttons:               # SDL GameController button per MSX function
    up: dpup
    down: dpdown
    left: dpleft
    right: dpright
    trigger_a: a
    trigger_b: b
    turbo_a: y           # auto-fire Trigger A
    turbo_b: x           # auto-fire Trigger B
```

Only `machine`, `speed`, `scale`, `mapper`, `fmpac`, and RPC/gamepad settings are
configurable; the gamepad button map applies to the SDL GameController path (both
ports share one map). See `py_emulator.example.yaml` for the full annotated list
and valid button labels.

### In-emulator key bindings

| Key | Action |
| --- | --- |
| Esc | Quit |
| F8 | Save state to `saves/states/<title>_YYYYMMDD_HHMMSS.state`* |
| F9 | Load most recent save state |
| F10 | Save screenshot to `saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png` |
| F11 | Toggle fullscreen |
| F1–F5 | Passed through to the MSX keyboard matrix |
| Ctrl + F1 | MSX HOME |
| Ctrl + F2 | MSX INS |
| Ctrl + F3 | MSX DEL |
| Ctrl + F4 | MSX STOP |
| Ctrl + F5 | MSX SELECT |
| Right Alt/Option | MSX CODE/KANA |

\* `<title>` is the game title from the ROM database, or `"py-msx-emulator"` if
the cartridge is not in the database.

**Keyboard joystick emulation (Joy 1):**

| Key | Action |
| --- | --- |
| W / ↑ | Up |
| S / ↓ | Down |
| A / ← | Left |
| D / → | Right |
| Z or , | Trigger A |
| X or . | Trigger B |

---

## Remote control (Socket RPC & MCP)

The emulator can expose a small local control surface so external tools — shell
scripts, a test harness, or an AI coding agent — can pause, inspect, and drive a
running instance. There are two layers:

- **Socket RPC** — a Unix-domain-socket JSON-RPC server embedded in the emulator
  process (`msx/rpc_server.py`). It is **off by default**; enable it with `--rpc`.
- **MCP server** — a standalone stdio server (`tools/mcp_server.py`) that wraps the
  socket RPC as [Model Context Protocol](https://modelcontextprotocol.io) tools, so a
  client like Claude Code can call emulator functions as native tools (and receive
  screenshots as inline images).

```
MCP client  ──stdio/MCP──▶  tools/mcp_server.py  ──Unix socket──▶  emulator (--rpc)
```

### Enabling the RPC server

```bash
# Start the emulator with the control socket enabled
python . path/to/cartridge.rom --rpc

# Optional: use a custom socket path (e.g. for multiple instances)
python . path/to/cartridge.rom --rpc --rpc-socket /tmp/py_msx_alt.sock
```

The RPC methods cover debugger pause/step/continue, breakpoints and watchpoints,
memory and VRAM read/write, disassembly, VDP registers, keyboard/joystick
injection, screenshot capture, save-state, and disk swap. The wire protocol and
full method reference are documented in
[`extras/msx_emulator_rpc_spec.md`](extras/msx_emulator_rpc_spec.md).

Quick manual test with the bundled client:

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py memory.read address=0xC000 length=16
```

### Registering the MCP server

The MCP server needs the optional `mcp` dependency:

```bash
pip install -e '.[mcp]'      # or: pip install 'mcp[cli]>=1.0'
```

Register it once with Claude Code (writes `.mcp.json`):

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
claude mcp list        # msx-emulator  ●  connected
```

Point the MCP server at a non-default socket via the `MSX_RPC_SOCKET` environment
variable (settable in the `.mcp.json` `env` block).

### Security notes

- The Unix socket is reachable only by local processes running as the same user.
- `memory.write` and `cpu.step` mutate machine state and are **paused-only**.
- There is no authentication; on a shared host, restrict the socket with
  `chmod 600`. The server is opt-in (`--rpc`) precisely because it is a control
  surface — no socket exists unless you ask for one.

---

## Machine configuration

Hardware topology — VDP type, RAM size, slot wiring, ROM files — is declared in
YAML files under `config/machines/`. The `--machine` flag selects a
configuration by ID; when omitted, the ROM database determines the generation
automatically (MSX1 ROM → `cbios_msx1_jp`; MSX2 ROM or no cartridge →
`cbios_msx2_jp`).

### Available machine IDs

| ID | Generation | Region | VDP |
| --- | --- | --- | --- |
| `cbios_msx1` | MSX1 | International | TMS9918A |
| `cbios_msx1_jp` | MSX1 | Japan | TMS9918A |
| `cbios_msx1_eu` | MSX1 | Europe | TMS9918A |
| `cbios_msx1_br` | MSX1 | Brazil | TMS9918A |
| `cbios_msx2` | MSX2 | International | V9938 |
| `cbios_msx2_jp` | MSX2 | Japan (default) | V9938 |
| `cbios_msx2_eu` | MSX2 | Europe | V9938 |
| `cbios_msx2_br` | MSX2 | Brazil | V9938 |
| `hb_f1xd` | MSX2 | Japan | V9938 |

`hb_f1xd` (Sony HB-F1XD) uses the real machine ROMs and adds a WD2793 floppy
disk drive; place its `hb-f1xd_basic-bios2.rom`, `hb-f1xd_msx2sub.rom`, and
`hb-f1xd_disk.rom` under `roms/hb_f1xd/` and mount a disk with `--fdd1`.

### Machine YAML structure

A machine file declares the CPU, slot wiring, and built-in devices. Device
definitions live separately under `config/devices/` and are referenced by
`ref:`.

```yaml
schema_version: 1
id: cbios_msx2
name: "Generic MSX2 (C-BIOS, International)"
generation: msx2
video_standard: ntsc
cpu:
  type: z80a
  clock_mhz: 3.579545
  m1_wait_states: 1 # MSX inserts 1 wait per M1 (opcode fetch); omit for pure Z80

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
    1: { type: cartridge }
    2: { type: cartridge }
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
    overrides: { vram_kb: 128 }
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

Key fields:

| Field | Description |
| --- | --- |
| `generation` | `msx1` or `msx2`; determines VDP class and memory model |
| `cpu.clock_mhz` | Z80A clock frequency (3.579545 MHz for NTSC MSX) |
| `cpu.m1_wait_states` | Extra T-states per M1/opcode fetch (optional; default 0 = pure Z80). MSX machines use `1` |
| `slots.primary.N` | Primary slot N: `{type: cartridge}`, `{type: ram, ...}`, or inline ROM `content` |
| `slots.primary.N.expanded` | Set `true` to expand into 4 secondary slots |
| `builtin_devices` | Devices wired directly (not slot-based): VDP, PSG, PPI, RTC, RAM mapper |
| `overrides` | Shallow merge over a device's defaults (e.g. `vram_kb: 128` for V9938) |
| `sha1` | `null` means load without hash verification |

To use a custom machine definition, add a new YAML file to `config/machines/`
and pass its `id` as `--machine`. Device entries with `implemented: false` in
their device YAML are skipped at load time with a warning.

---

## Running tests

The test suite covers all major components with 1607 tests spanning unit tests
for individual opcodes and hardware registers, integration tests that wire
multiple components together, and scenario-level tests whose conditions are
derived directly from the component specs.

```bash
# Install the development dependencies (pytest, ruff, mypy)
pip install -r requirements-dev.txt

# Run all tests
python -m pytest

# Verbose output
python -m pytest -v

# Run tests matching a keyword
python -m pytest -k "psg"
```

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
│   ├── diagnostics/       # DebugLogger, CPU/I/O trace, hang detector
│   ├── debugger/          # Interactive REPL (prompt, disassembler)
│   ├── machine.py         # Component wiring and frame loop
│   ├── machine_loader.py  # YAML-based machine configuration loader
│   ├── memory.py          # Slot-based memory bus
│   ├── mapper.py          # Cartridge mappers (Flat, ASCII8/16, Konami, SCC, ...)
│   ├── mapper_tracer.py   # Cartridge bank-switch tracer
│   ├── ram_mapper.py      # MSX2 RAM mapper (128 KB, 8 segments)
│   ├── rtc.py             # RP5C01 real-time clock
│   ├── psg.py             # AY-3-8910 PSG + audio synthesis (sub-frame software PCM)
│   ├── audio_filter.py    # Analog-style output low-pass (BiquadLowPass)
│   ├── scc.py             # Konami SCC wavetable synthesiser
│   ├── fmpac.py           # FM-PAC cartridge (banked ROM, SRAM, OPLL routing)
│   ├── opll.py            # YM2413 (OPLL) FM sound chip
│   ├── ppi.py             # i8255 PPI (slot register, keyboard)
│   ├── io.py              # I/O bus (port dispatch)
│   ├── input.py           # Keyboard matrix + joystick input state
│   ├── joystick.py        # Physical joystick manager (SDL2)
│   ├── frame_timer.py     # 60 fps pacing + FPS measurement
│   ├── romdb.py           # SHA1-based ROM title/mapper database
│   ├── screenshot.py      # RGB24→PNG writer (screenshots + save-state images)
│   └── state.py           # Save/load machine state (JSON + PNG)
├── config/
│   ├── devices/           # Device YAML definitions (VDP, PSG, PPI, RTC, ...)
│   └── machines/          # Machine YAML definitions (cbios_msx1_jp, cbios_msx2_jp, ...)
├── roms/
│   └── cbios/             # C-BIOS ROM files (not in version control)
├── saves/                 # Save states and screenshots (created at runtime)
├── openspec/
│   └── specs/             # Component specifications (not included in the public repository)
├── tests/                 # Test suite — 1607 tests
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Development dependencies
└── pyproject.toml         # Project metadata and tool configuration
```

---

## Contributing

### Spec-first rule

Every new hardware component or significant behaviour change must have a
specification added or updated in `openspec/specs/<component>/spec.md` before
any implementation code is written. The scenarios in the spec are the source of
truth for test cases. A PR that adds implementation without a corresponding spec
update will not be merged.

### Coding conventions

- **Pure Python only** — no C extensions, no Cython, no additional ctypes beyond
  what is already used in the SDL2 frontend
- **Python 3.10+** — use dataclasses, `match`/`case`, and modern type annotation
  syntax
- **Type hints everywhere** — the project is checked with mypy in strict mode
- **Linting** — ruff with `line-length = 99`; run `python -m ruff check .`
  before committing
- **Comments only for the non-obvious** — do not add comments that restate what
  the code already says

### Issues and pull requests

There is no formal CONTRIBUTING.md at present. Please open a GitHub issue to
discuss significant changes before submitting a PR. Bug reports for ROMs other
than the listed target titles are welcome; compatibility fixes will be
considered on a best-effort basis.

---

## Acknowledgements

- **[openMSX](https://openmsx.org/)** — ROM identification data references
  openMSX softwaredb.xml (https://github.com/openMSX/openMSX), but all entries
  are independently compiled factual data. openMSX is released under the GNU GPL
  v2.
- **[emu2413](https://github.com/digital-sound-antiques/emu2413)** by Mitsutaka
  Okazaki — the YM2413 (OPLL) chip in `msx/opll.py` is a Python port of emu2413
  v1.5.9. emu2413 is released under the MIT license.
- **[C-BIOS](https://cbios.sourceforge.net/)** — recommended free MSX BIOS
  replacement used for testing.

---

## License

MIT — see [LICENSE](LICENSE).

---

## History

- **v2.4.1** (2026-08-02) — Add an optional `py_emulator.yaml` startup configuration file (default machine/speed/mapper/FM-PAC, gamepad button map, turbo rate); switch `--benchmark` to a frame count.
- **v2.4.0** (2026-07-30) — Add the FM-PAC (MSX-MUSIC) cartridge with a YM2413 (OPLL) FM sound chip.
- **v2.3.6** (2026-07-23) — Unify rendered output to a constant 212-line height.
- **v2.3.5** (2026-07-20) — Fix sprite ghosting from the upper split-screen region.
- **v2.3.4** (2026-07-19) — Add per-line banding for the display-adjust register.
- **v2.3.3** (2026-07-19) — Fix handling of filenames with spaces.
- **v2.3.2** (2026-07-19) — Fix V9938 line-interrupt handling.
- **v2.3.1** (2026-07-19) — Refactor the socket RPC and MCP server.
- **v2.3.0** (2026-07-19) — Add a socket RPC interface and MCP server.
- **v2.2.1** (2026-07-15) — Add cycle-accurate CPU timing and PSG PCM playback.
- **v2.2.0** (2026-07-13) — Add support for the Sony HB-F1XD (FDD + RTC).
- **v2.1.0** (2026-07-13) — Improve MSX2 emulation performance.
- **v2.0.0** (2026-07-06) — Add MSX2 CBIOS support.
- **v1.0.0** (2026-06-07) — Initial release.
