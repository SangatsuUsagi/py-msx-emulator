# py-msx-emulator

A functionally accurate MSX1/MSX2 emulator written in pure Python 3.10+, driven
by machine-readable component specifications.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-3462%20passing-brightgreen)

[日本語版 README はこちら](README_ja.md)

---

Every hardware component is pure Python and defined by a machine-readable
specification before it is implemented, and those specs drive the test suite.
See [Spec-driven architecture](#spec-driven-architecture) for how. Component
wiring is explicit, done by hand in `build_machine()` with no reflection or
dependency-injection magic. The only platform-specific dependency is pysdl2,
for the display and audio frontend.
[`docs/technical-implementation.md`](docs/technical-implementation.md) walks
through the internals.

### Verified hardware and software

This emulator has only been verified against the following hardware and
software owned by the author. It has only been tested against physical
ROM/disk dumps the author owns, so other MSX1 or MSX2 titles are not
guaranteed to work. Bug reports for other titles are welcome, but support is
best-effort.

**Hardware:**

- [Sony HB-10](https://www.msx.org/wiki/Sony_HB-10)
- [Sony HB-F1XD](https://www.msx.org/wiki/Sony_HB-F1XD)
- [MSX Standard Japanese Cartridge HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1)
- [FM-PAC (FM Pana Amusement Cartridge)](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072)

**Software:**

- **MSX1:**
  - [Salamander (沙羅曼蛇) by KONAMI](https://generation-msx.nl/software/konami/salamander/941)
  - [Nemesis 2 (グラディウス2) by KONAMI](https://generation-msx.nl/software/konami/nemesis-2/932)
  - [Penguin Adventure (夢大陸アドベンチャー) by KONAMI](https://generation-msx.nl/software/konami/penguin-adventure/873)
  - [Game Master 2 (コナミの新10倍カートリッジ) by KONAMI](https://generation-msx.nl/software/konami/game-master-2/937)
- **MSX2:**
  - [Dragon Slayer IV - Drasle Family (ドラゴンスレイヤーIVドラスレファミリー) by Falcom](https://generation-msx.nl/software/falcom/dragon-slayer-iv-drasle-family/979)
  - [Romancia - Dragon Slayer Jr. (ロマンシア) by Falcom](https://generation-msx.nl/software/falcom/romancia-dragon-slayer-jr/881)
- **MSX2 (floppy disk):**
  - [Dragon Slayer VI - The Legend of Heroes (ドラゴンスレイヤー英雄伝説) by Falcom](https://generation-msx.nl/software/falcom/dragon-slayer-vi-the-legend-of-heroes/1387)
  - [Letter Writer: Print Like Handwriting (はがき書右衛門) by Sony](https://generation-msx.nl/software/sony-corporation/letter-writer-print-like-handwriting/1181)

---

## Contents

- [Emulated hardware](#emulated-hardware)
- [Spec-driven architecture](#spec-driven-architecture)
- [Performance](#performance)
- [Installation](#installation)
- [Usage](#usage)
- [Tools](#tools)
- [Remote control (Socket RPC & MCP)](#remote-control-socket-rpc--mcp)
- [Machine configuration](#machine-configuration)
- [Running tests](#running-tests)
- [Project layout](#project-layout)
- [Contributing](#contributing)
- [Acknowledgements](#acknowledgements)
- [License](#license)
- [History](#history)

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
- Known limitations:
  - OTIR/INIR and similar block I/O instructions are not cycle-exact across
    page boundaries.
  - The R register increments on every fetched byte (opcode and operand
    alike), not only on M1 opcode-fetch cycles as on real hardware.
  - The undocumented flag bits Y (bit 5) and X (bit 3) are not reproduced
    except by the block I/O flag routine, so `PUSH AF`/`POP AF`/`EX AF,AF'`
    do not round-trip them.

### VDP (Video Display Processor) — TMS9918A (MSX1)

16 KB VRAM, 8 control registers, Screen modes 0–3 (Text 40-col, Graphic 1,
Graphic 2, Multicolor), sprite rendering with size/magnification, 5th-sprite and
coincidence flags, and the VBlank interrupt.

- Implementation: `msx/vdp/vdp.py`, `msx/vdp/renderer.py`
- Known limitations:
  - Mid-frame register-change timing is not emulated: a game that changes
    screen mode, colours, or sprite settings mid-frame sees the new state
    applied to the whole frame rather than only from the changed scanline
    onward. Undocumented sprite-overflow behaviour is also not emulated.
  - During frame skip, sprite 5th-sprite/coincidence status is not updated
    on a skipped frame, so a ROM polling collision during turbo mode sees
    stale (last-rendered-frame) status.

### VDP — Yamaha V9938 (MSX2)

128 KB VRAM, 28 control registers, a programmable 16-colour palette (9-bit
GRB333), Screen modes 0–8 (SCREEN 0 through SCREEN 8), the hardware command
engine (HMMV, HMMM, HMMC, LMMV, LMMM, LMCM, LMMC, YMMM, LINE, PSET, POINT,
SRCH), the horizontal line interrupt (R#19/R#23, IE1), and a banded renderer for
mid-frame register and palette changes.

- Implementation: `msx/vdp/v9938.py`, `msx/vdp/v9938_renderer.py`
- Known limitations:
  - Command timing is approximate, not cycle-accurate; beam-raced blits and
    double-buffered VRAM updates within a single frame are not reproduced
    faithfully.
  - GRAPHIC 6/7 planar VRAM interleave (the real chip splits these modes
    across two VRAM ICs by even/odd byte) is not modelled — a linear
    addressing model is used instead, which diverges only when VRAM content
    written in a planar mode is read back after switching to a non-planar
    mode without an intervening clear.
  - TEXT2 (SCREEN 0 WIDTH 80)'s blink colour/rate (R#12/R#13) is not
    implemented, so blinking characters render in a single fixed colour
    instead of alternating. R#9's EO bit drives GRAPHIC4-7's automatic
    even/odd page alternation (matching real V9938 behaviour), but the
    half-scanline CRT field-fusion shift that produces true interlaced
    (double vertical resolution) output is a deliberate non-goal,
    prioritizing rendering simplicity/performance.
  - During frame skip, sprite 5th-sprite/coincidence status is not updated
    on a skipped frame, so a ROM polling collision during turbo mode sees
    stale (last-rendered-frame) status.

### PSG (Programmable Sound Generator) — AY-3-8910

16 registers, 3 tone channels, a noise channel, an envelope generator with 8
waveform shapes, a measured-silicon amplitude table (derived from ayumi's
oscilloscope-measured AY-3-8910 DAC curve, rather than an idealised geometric
one), and 44100 Hz PCM output at 735 samples/frame. **Sub-frame software-PCM**
reproduction cycle-timestamps volume-register writes and replays them at their
in-frame sample positions; the tone generator is integrated over each output
sample, so an ultrasonic period-0 PCM carrier is band-limited to its duty
average instead of aliasing to a full/zero chop.

- Implementation: `msx/psg.py`

### SCC — Konami SCC (Sound Creative Chip)

5-channel wavetable synthesiser, mixed into the audio output alongside the
PSG. Two modes:

- **Compatible mode** (default): 4 waveform banks (32 samples each; channel 5
  mirrors channel 4), 12-bit frequency and 4-bit volume per channel. The
  KonamiSCC mapper activates it when 0x3F is written to 0x9000; registers
  appear at 0x9800.
- **Plus mode**: 5 *independent* waveform banks, frequency/volume/enable
  relocated to a different register offset. Used by the SCC-I cartridge (see
  below), selected via `SCC.set_mode()`.

- Implementation: `msx/scc.py`
- Known limitations:
  - The deform/test register (frequency-multiply calibration bits, counter
    reset, and hardware waveform auto-rotation) is not implemented.

### SCC-I cartridge ("SCC+")

A bare sound cartridge (no game ROM), enabled with `--extension1`/`--extension2
scc_plus` — see
[`docs/README_extension.md`](docs/README_extension.md#scc-i-cartridge-scc) for
activation and slot mechanics. 64 KB of physical bank-switched RAM addressed
as if 128 KB (blank — no ROM/data file is ever loaded), with bank register
bit 3 ignored so block N mirrors block N+8 — reproducing a documented
real-hardware modification (["connect the two 64 KB
banks"](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html)) that lets
one physical SCC-I cartridge work with either of the two factory
RAM-population variants, each of which this project's two target titles
expects — a mode register selecting the carried SCC chip's Compatible or
Plus mode, and per-window RAM-write control. Targets FDD (floppy-disk) MSX2
titles that plug this cartridge in purely for its audio.

> **Note**: the author does not own a real SCC-I (SCC+) cartridge or
> compatible software, so this implementation is based on publicly
> available technical documentation and has not been verified against real
> hardware.

| Item | Detail |
| --- | --- |
| Implementation | `msx/mapper.py:SCCICart` |
| Activation | `--extension1`/`--extension2 scc_plus` (see [`docs/README_extension.md`](docs/README_extension.md#scc-i-cartridge-scc)) |
| Memory map | 4 × 8 KB bank-switched RAM windows at `0x4000-0xBFFF`; mode register at `0xBFFE`/`0xBFFF` |
| SCC register window | `0x9800-0x9FFF` (Compatible mode) or `0xB800-0xBFFF` (Plus mode), depending on the mode register |

### FM-PAC — MSX-MUSIC cartridge (YM2413/OPLL)

Optional overlay cartridge, enabled with `--extension1`/`--extension2 fmpac` —
see [`docs/README_extension.md`](docs/README_extension.md#fm-pac) for activation and ROM
requirements. 64 KB banked ROM, 8 KB battery-backed SRAM with
openMSX-compatible magic-value unlock, and a YM2413 (OPLL) FM sound chip —
9-channel 2-operator melody synthesis (15 built-in instruments plus a
user-defined tone), ADSR envelopes, and rhythm mode (bass drum, snare, tom,
top cymbal, hi-hat), mixed into the audio output alongside PSG/SCC.

| Item | Detail |
| --- | --- |
| Implementation | `msx/fmpac.py` (cartridge device), `msx/opll.py` (YM2413/OPLL chip) |
| Activation | `--extension1`/`--extension2 fmpac` (see [`docs/README_extension.md`](docs/README_extension.md#fm-pac)); ROM at `roms/fmpac/fmpac.rom` |
| Memory map | 64 KB ROM in four 16 KB banks (`0x7FF7` bank register) at `0x4000-0x7FFF`; 8 KB SRAM (openMSX-exact `0x1FFE`-byte usable region, magic-value unlock at `0x5FFE`/`0x5FFF`); memory-mapped OPLL registers (`0x7FF4`/`0x7FF5`), enable register (`0x7FF6`) |
| I/O ports | `0x7C`/`0x7D`, gated by the enable register's bit 0 |
| SRAM persistence | `saves/sram/fmpac.sram`, loaded on start and saved on exit |
| OPLL synthesis | Faithful port of emu2413 v1.5.9: log-domain synthesis (log-sin + exp tables), hardware envelope-rate tables with key-scaling, AM/PM LFO, the YM2413 instrument ROM, and rhythm mode (register `0x0E`: bass drum, snare, tom, top cymbal, hi-hat) with the real short-noise / LFSR taps |

- Known limitations:
  - Output rate conversion uses an accumulate-and-average decimator (chip
    clk/72 → 44100 Hz) rather than emu2413's windowed-sinc resampler; the
    analog-style low-pass cleans up the residual imaging.
  - Only the YM2413 instrument set is included (no VRC7 / YMF281 banks).
  - No channel masking / stereo pan.

### Sony HBI-J1 — Kanji-ROM + MSX-JE word processor

Optional overlay cartridge, enabled with `--extension1`/`--extension2 hbi_j1`
— see [`docs/README_extension.md`](docs/README_extension.md#sony-hbi-j1) for
activation, ROM requirements, and licensing. A JIS Kanji font ROM I/O device
(ports `0xD8-0xDB`, no slot location of its own) plus an *expanded* primary
slot (whichever slot `--extension1`/`--extension2` places it in) hosting two
sub-slot devices — a Halnote-mapped MSX-JE word-processor ROM with 16 KB
battery-backed SRAM, and a flat Kanji driver + BASIC extension ROM. It plugs
into a stock MSX2 host, so its expanded slot coexists with the host
machine's own slot 3 expansion.

The author physically owns this cartridge and dumped its ROMs (SHA1-verified
against openMSX's `Sony_HBI-J1.xml`), so this implementation targets real
hardware, not just documentation.

| Item | Detail |
| --- | --- |
| Implementation | `msx/kanji.py` (Kanji-ROM device), `msx/mapper.py:HalnoteMapper` (MSX-JE cartridge), `msx/machine_loader.py` (expanded overlay wiring) |
| Activation | `--extension1`/`--extension2 hbi_j1` (see [`docs/README_extension.md`](docs/README_extension.md#sony-hbi-j1)); ROMs at `roms/hbi_j1/` |
| Kanji-ROM I/O | Ports `0xD8-0xDB`: bit 1 selects JIS level (1/2), bit 0 selects column/row write vs. data read; a 5-bit read counter auto-increments per read, wrapping to byte 0 of the same glyph every 32 reads |
| Sub-slot 0 | `HalnoteMapper` — 1 MB ROM (128 × 8 KB banks), 16 KB SRAM at `0x0000-0x3FFF` (bank-0 register bit 7), JIS2 dictionary sub-mapper shadowing `0x7000-0x7FFF` (bank-1 register bit 7) |
| Sub-slot 1 | Flat 32 KB Kanji driver + BASIC ROM at `0x4000-0xBFFF`; `0x0000-0x3FFF`/`0xC000-0xFFFF` open bus |
| SRAM persistence | `saves/sram/hbi-j1_msx-je.sram`, loaded on start and saved on exit |
| Known limitations | Real-hardware behavior past the Kanji-ROM read counter's 32nd consecutive read without an address rewrite is unverified — this implementation follows openMSX's documented behavior (wrap to the same glyph's byte 0) as ground truth |

### Audio output filter

An analog-style output low-pass (2-pole Butterworth) models the RC filter on
real MSX audio out, removing residual imaging/aliasing from the mixed
PSG/SCC/DAC/OPLL signal.

- Implementation: `msx/audio_filter.py`

### PPI (Programmable Peripheral Interface) — Intel i8255

Slot-select register (port 0xA8), the 11-row × 8-bit MSX keyboard matrix (port
0xA9), and row selection (port 0xAA).

- Implementation: `msx/ppi.py`
- Known limitations: the cassette interface (port 0xAA bits 4–7) is not
  implemented.

### Memory bus / slot system

MSX1 uses a 4-page × 4-slot dispatch: BIOS ROM in slot 0, a cartridge in slot 1,
an optional second cartridge in slot 2, and 32 KB RAM in slot 3. On MSX2, primary
slot 3 is expanded into 4 secondary slots, with the sub-ROM in sub-slot 3-0 and
the 128 KB RAM mapper in sub-slot 3-2. Primary slot 1 and slot 2 can each
independently also be expanded via `--extension1`/`--extension2` — see
[`docs/README_extension.md`'s "Slot model" section](docs/README_extension.md#slot-model)
for what each slot can hold and which extensions expand a slot — coexisting
with slot 3's own expansion on the same machine, each with its own secondary
slot register. The same ROM/extension id cannot be selected on both
cartridge slots at once (rejected at startup).

| Item | Detail |
| --- | --- |
| Implementation | `msx/memory.py` |
| Address space | Flat 64 KB (0x0000–0xFFFF), four 16 KB pages |
| Slot 0 pages 0–1 | BIOS ROM (read-only, 0x0000–0x7FFF) |
| Slot 0 page 2 | Logo ROM (`cbios_logo_msx1.rom`) at 0x8000–0xBFFF; declared in the machine YAML as a `pages: [2]` entry next to the BIOS; returns 0xFF if absent |
| Slot 0 page 3 | 16 KB SRAM at 0xC000–0xFFFF on some machines (e.g. `hb_10`) — volatile work RAM, not battery-backed or saved like cartridge SRAM; declared in the machine YAML as a `ram: {size_kb: 16, pages: [3]}` entry next to the BIOS |
| Slot 1 | Cartridge ROM via mapper; open bus (0xFF on read, writes ignored) when no slot 1 ROM is loaded. Expanded into sub-slots under `--extension1` — see [`docs/README_extension.md`](docs/README_extension.md#slot-model) |
| Slot 2 | Second cartridge ROM via `_mapper2`; open bus (0xFF on read, writes ignored) when no slot 2 ROM is loaded. Expanded into sub-slots under `--extension2` — see [`docs/README_extension.md`](docs/README_extension.md#slot-model) |
| Slot 3 (MSX1) | 32 KB RAM at 0x8000–0xFFFF by default; the size is set per machine (64 KB across all pages on `hb_10_64k`, unpopulated open bus on `hb_10`) |
| Slot 3 (MSX2) | Expanded into 4 secondary slots; sub-ROM in 3-0, 128 KB RAM mapper in 3-2 |

### RAM mapper

128 KB main RAM (8 × 16 KB segments) with segment registers at ports 0xFC–0xFF.

- Implementation: `msx/ram_mapper.py`

### RTC — RP5C01

Real-time clock at ports 0xB4–0xB5, including its 26 nibbles of
battery-backed CMOS RAM (blocks 2/3) and 12/24-hour mode encoding
(register 10, bit 0).

- Implementation: `msx/rtc.py`
- CMOS RAM persistence: `saves/sram/rtc_<machine_id>.sram` (one file per
  machine — real hardware has one physical RTC/battery per machine, unlike
  a cartridge's SRAM), loaded on start and saved on exit
- Known limitations: clock reads reflect host system time; no alarm or timer
  output.

### Cartridge mappers

Flat (no bank switching), ASCII8, ASCII16, Konami, KonamiSCC, Majutsushi (DAC),
ASCII8SRAM2/8, ASCII16SRAM2/8, R-Type, a fixed-page mapper (Page2/0x4000/0x8000),
KoeiSRAM32, and GameMaster2, auto-detected from a SHA1-based ROM database.
Override with `--mapper`/`--mapper1` (slot 1) or `--mapper2` (slot 2); both
accept the identical type list.

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
| `FixedPageMapper` | No bank switching; ROM visible only at a fixed base address (database types `Page2`, `0x4000`, `0x8000`), rest of the cartridge region reads as open bus |
| `KoeiSRAM32Mapper` | ASCII8 with 32 KB battery-backed SRAM; extends the SRAM-selectable window set to include 0x4000 |
| `GameMaster2Mapper` | Konami-style 128 KB ROM with 8 KB battery-backed SRAM; each bank register selects a ROM page or a 4 KB SRAM half (mirrored across the 8 KB window), SRAM writable at 0xB000–0xBFFF |

Slot 1 and slot 2 each resolve their mapper independently (`--mapper`/`--mapper1`
and `--mapper2`, both auto-detected by default) and support the identical
mapper set, SRAM-backed types included — each slot's SRAM persists to its own
`saves/sram/<cartridge SHA1>.sram` path. `KonamiSCC` in either slot builds a
real Konami-SCC chip (a genuine Konami-SCC cartridge can physically occupy
either slot, e.g. behind a Konami slot-converter cartridge), but slot 1 and
slot 2 share the machine's single `SCC` chip: `MachineLoadError` is raised if
both slots resolve to `KonamiSCC` at once, or if one slot resolves to
`KonamiSCC` while the other selects `--extensionN scc_plus`.

### Floppy disk drive (WD2793)

Generic FDC (Floppy Disk Controller) layer (disk image / drive / controller /
connection-style interface) with a WD2793 controller and Sony/Philips connection style, as used by the Sony
HB-F1XD. Registers are memory-mapped in slot 3 sub-slot 0; `*.dsk` images mount
via `--fdd1`. Supports Disk BASIC boot, `CALL FORMAT`, and file read/write with
write-back on exit; disks can be swapped at runtime from the debugger REPL
(`fdd1`/`fdd2`).

- Implementation: `msx/fdc/wd2793.py`, `msx/fdc/interface.py`
- Known limitations:
  - No timing model — command execution is instantaneous (no seek/step
    rate, no revolution-based ID search, no head-load/verify-after-seek
    delay).
  - READ TRACK is a bare stub.
  - WRITE TRACK discards stream content and formats by byte count only,
    without real gap-byte/address-mark interpretation.

### Floppy disk drive (TC8566AF)

A second FDC controller/connection-style pair on the same generic FDC layer,
as used by the Panasonic FS-A1F (`--machine fs_a1f`). Registers are
memory-mapped in slot 3 sub-slot 2 (FS-A1F's real hardware layout — RAM in
sub-slot 0, SUB ROM in sub-slot 1) as Main Status Register, Data Register, and
two control registers — no directly addressable TRACK/SECTOR register, unlike
WD2793; `*.dsk` images mount the same way via `--fdd1`. Implements SPECIFY,
SENSE INTERRUPT STATUS, SENSE DEVICE STATUS, RECALIBRATE, SEEK, READ DATA,
WRITE DATA, and FORMAT — enough for the MSX DISK ROM's boot/sector-I/O path
and `CALL FORMAT`.

- Implementation: `msx/fdc/tc8566af.py`, `msx/fdc/interface.py`
- Known limitations:
  - No timing model (same functional-model approach as WD2793).
  - Non-DMA mode only — the chip's DRQ2/-DACK2/DMATC pins are not modelled.
  - READ DELETED DATA / WRITE DELETED DATA / READ DIAGNOSTIC / READ ID / SCAN
    are not implemented (not needed for the Disk BASIC boot/sector-I/O path).
  - Like WD2793's own WRITE TRACK, FORMAT discards the streamed descriptor
    bytes and just blanks every sector of the current (track, side) rather
    than writing real gap/sync/ID/CRC bytes.
  - Every shipped machine configures exactly one drive, so multi-drive
    support is untested: a runtime disk-swap abort only tracks drive 0
    correctly, and drive routing reads the target drive from each command's
    own parameter byte rather than a persistent Control Register 0 selection
    (both match single-drive behaviour, per real DISK ROM traffic).

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
| Keyboard emulation | WASD = Joy1 directions; Z/X or ,/. = Trigger A/B; arrow keys also mapped. Each function's key is overridable via `keyboard_joystick.buttons` in `py_emulator.yaml` |

- Known limitations:
  - The MSX numeric keypad (matrix rows 9-10) has no host-key binding.
  - Key ghosting (inherent to the diode-less matrix) is not modelled.
  - Joystick Type A vs Type B (pin 7 second-trigger) detection is not
    modelled — every port behaves as Type B.
  - On macOS with a JIS keyboard, SDL2 can misdetect the host layout as US
    right after launch. If symbol keys behave oddly on a JIS machine (e.g.
    `cbios_msx2_jp`), switching the input source away and back once
    (⌘+Space) usually fixes it — this is an SDL2/macOS quirk outside this
    project's control.

### Mouse

Optional MSX mouse driven by the host mouse, plugged into a joystick port in
place of the physical/keyboard joystick on that port. Reproduces the real
protocol: pin 8 (normally the PSG's always-high strobe output) is toggled by
the MSX to clock through a 4-bit nibble sequence (X high, X low, Y high, Y
low) of signed relative motion, followed by a zero-delta alternate cycle so
MSX BIOS mouse detection recognises the device as a mouse rather than a
trackball. Left/right buttons drive the port's trigger A/B lines.

| Item | Detail |
| --- | --- |
| Implementation | `msx/mouse.py` (protocol state machine), `msx/psg.py` (register 14/15 wiring) |
| Enable | `--mouse[=1\|2]` (CLI) or `mouse.enabled`/`mouse.port` in `py_emulator.yaml`; the CLI flag always overrides the config file |
| Host input | SDL relative-mouse mode (cursor hidden and locked to the window); motion is scaled to the window's integer `--scale` factor |

- Known limitations:
  - The NYYRIKKI/Prodatron extended protocol (higher resolution, wheel,
    extra buttons) is not implemented.
  - Absolute positioning is not implemented.
  - The real mouse's click-and-hold joystick-emulation mode is not
    implemented.

### SDL2 frontend

Fixed 768×636 window (256×212 × scale 3) for every machine and mode. The rendered
frame is always 212 lines (192-line SCREEN modes are centred with border rows) so
a 4:3 CRT aspect is kept regardless of R#9 LN, and SCREEN 6/7 (512-wide)
downscale to the same window width to preserve pixel aspect. Hardware palette,
mono audio at 44100 Hz through the analog-style low-pass filter, fullscreen
toggle, screenshot, state save/load, and automatic frame skip (VDP pixel render
suppressed on late frames; the VBlank interrupt still fires every frame).

- Implementation: `frontend/sdl2_frontend.py`
- Known limitations: when no audio device is open (e.g. headless /
  `--benchmark` runs), PSG/SCC/OPLL never receive a synthesis tick, so their
  internal envelope/LFO state does not advance during that time.

### State save/load

Complete hardware snapshot (CPU, RAM, VDP, PSG, SCC, FM-PAC/OPLL, mapper
banks, FDC register/phase state and mounted-disk identity) as a stdlib JSON
container, a PNG screenshot alongside each save, and `saves/states/latest.*`
symlinks for quick resume. Loading a save whose mounted `.dsk` doesn't match
the one recorded at save time (by path, size, and SHA1) raises an error
instead of silently proceeding.

- Implementation: `msx/state.py`

### Interactive debugger

A REPL reachable via Ctrl+C or a breakpoint hit: breakpoints/watchpoints, step
execution, register/VRAM dump, disassembly, VDP trace, mapper trace, slot
inspector, and floppy disk swap (`fdd1`/`fdd2 [FILE|-]`). Full command
reference: [`docs/debugger.md`](docs/debugger.md).

- Implementation: `msx/debugger/`

### Debug tooling

Opt-in structured logging, CPU instruction trace, I/O port trace, and a hang
detector.

- Implementation: `msx/diagnostics/`

---

## Spec-driven architecture

This project was implemented using [Claude Code](https://claude.ai/code) and
[OpenSpec](https://openspec.dev/).

Since v2.5.0, [Allium](https://juxt.github.io/allium/) is used alongside
OpenSpec as a second, behaviour-focused specification layer (`allium/*.allium`)
to verify that each component's implementation stays aligned with its
specification.

Specs live under `openspec/specs/<spec-id>/spec.md`, one directory per
component, the ID carrying a domain prefix (`cpu-z80`, `vdp-tms9918a`,
`audio-psg`, `fdc-wd2793`, and so on). Each file interleaves natural-language
requirements with concrete WHEN/THEN scenarios — this is how `vdp-tms9918a`
opens:

```markdown
### Requirement: VRAM
The VDP SHALL maintain 16 KB of video RAM as a `bytearray` of length 0x4000.
All VRAM addresses SHALL be masked to 14 bits (`& 0x3FFF`).

#### Scenario: VRAM initialises to zero
- **WHEN** a `VDP` instance is created
- **THEN** all 16384 bytes of VRAM are 0x00

#### Scenario: VRAM write and read back
- **WHEN** a byte is written to VRAM address 0x1234 via the data port
- **THEN** reading VRAM at 0x1234 directly returns the same byte
```

The scenarios map directly to unit tests. When a new feature is added or an
existing component is changed, the spec is updated first and the implementation
follows.

---

## Performance

Measured with the `--benchmark` CLI flag: a headless run, unthrottled (no
`FrameTimer` pacing), from a cold boot through the full frame count (BIOS
boot time is included in the measurement). Every frame still runs full CPU
emulation and VDP-to-offscreen-buffer rendering, the same cost paid during
interactive play — but `--benchmark` never opens an SDL window, generates audio
samples, or performs texture upload/blit, so real interactive sessions run
somewhat slower than the raw numbers below.

For each (runtime, game) pair, 10 runs of 10000 frames each were measured, the
fastest and slowest were discarded, and the remaining 8 were averaged into the
score.

| Platform | Runtime | Game | Avg FPS (`--benchmark`) | vs. 60 fps target |
| --- | --- | --- | --- | --- |
| Apple MacBook Pro (M5 Pro) | CPython 3.12.13 | MSX1: Salamander (CPU+TMS9918+SCC) | 285.57 | ~4.8× |
| Apple MacBook Pro (M5 Pro) | CPython 3.12.13 | MSX2: Dragon Slayer 4 (CPU+V9938+PSG) | 571.82 | ~9.5× |
| Apple MacBook Pro (M5 Pro) | CPython 3.12.13 | MSX2: Dragon Slayer VI - The Legend of Heroes (CPU+V9938+FDC+FMPAC) | 299.56 | ~5.0× |
| Apple MacBook Pro (M5 Pro) | PyPy 7.3.19 (Python 3.10.16) | MSX1: Salamander (CPU+TMS9918+SCC) | 1123.72 | ~18.7× |
| Apple MacBook Pro (M5 Pro) | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer 4 (CPU+V9938+PSG) | 1269.52 | ~21.2× |
| Apple MacBook Pro (M5 Pro) | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer VI - The Legend of Heroes (CPU+V9938+FDC+FMPAC) | 1069.07 | ~17.8× |
| Raspberry Pi 5 | CPython 3.12.13 | MSX1: Salamander (CPU+TMS9918+SCC) | 74.91 | ~1.2× |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: Dragon Slayer 4 (CPU+V9938+PSG) | 189.38 | ~3.2× |
| Raspberry Pi 5 | CPython 3.12.13 | MSX2: Dragon Slayer VI - The Legend of Heroes (CPU+V9938+FDC+FMPAC) | 84.54 | ~1.4× |
| Raspberry Pi 5 | PyPy 7.3.19 (Python 3.10.16) | MSX1: Salamander (CPU+TMS9918+SCC) | 359.38 | ~6.0× |
| Raspberry Pi 5 | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer 4 (CPU+V9938+PSG) | 472.93 | ~7.9× |
| Raspberry Pi 5 | PyPy 7.3.19 (Python 3.10.16) | MSX2: Dragon Slayer VI - The Legend of Heroes (CPU+V9938+FDC+FMPAC) | 314.82 | ~5.2× |

Every combination tested clears the raw 60 fps target. The tightest margin is
Raspberry Pi 5 with CPython running Salamander (MSX1, KonamiSCC mapper, the
heaviest rendering/audio load among the target titles) at ~1.2×; PyPy raises the
same case to ~6.0×. On hardware weaker than a Raspberry Pi 5, or under a heavier
title, a run can still drop below 60 fps — in which case the game runs in slow
motion at a rate proportional to the achieved frame rate, and audio degrades
(clicks or silence) because samples are generated per-frame while the audio
device always consumes at 44100 Hz. PyPy3 is a drop-in alternative that
substantially improves throughput on slower hardware and is the recommended way
to keep headroom on constrained hosts like a Raspberry Pi.

PyPy's numbers above are more prone to run-to-run variance than CPython's —
occasional runs land well outside the usual range for a given (platform, game)
pair, most likely due to OS/hardware scheduling behavior (e.g. core migration
or thermal throttling) rather than anything in the emulator itself. Treat
PyPy figures as broadly indicative rather than exact.

### Benchmark history

Avg FPS (`--benchmark`) from v0.1.0 through v2.7.0, per platform and runtime.
All versions were measured uniformly with the cold-boot methodology above.

![Benchmark history on Apple MacBook Pro (M5 Pro)](assets/bench-history-m5pro.png)

![Benchmark history on Raspberry Pi 5](assets/bench-history-rpi5.png)

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

## Installation

See [QUICK_START.md](QUICK_START.md) for requirements, installation steps, and
BIOS setup.

---

## Usage

### Running the emulator

```bash
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

# Same, with slot 1 given explicitly instead of positionally
python . --slot1 path/to/game1.rom --slot2 path/to/game2.rom

# Sony HB-F1XD with a floppy disk mounted in drive A (boots Disk BASIC)
python . --machine hb_f1xd --fdd1 path/to/disk.dsk

# Create a blank 720 KB disk to format with CALL FORMAT
python tools/dskblank.py blank.dsk

# Force a specific mapper type (--mapper2 accepts the identical type list)
python . path/to/game.rom --mapper KonamiSCC

# Add an FM-PAC (MSX-MUSIC) cartridge in slot 2 alongside a game in slot 1
python . path/to/game.rom --extension2 fmpac

# Connect an SCC-I (SCC+) cartridge in slot 2 (no cartridge ROM argument);
# boot from floppy instead
python . --machine hb_f1xd --extension2 scc_plus --fdd1 path/to/disk.dsk

# Attach the Sony HBI-J1 Kanji-ROM/MSX-JE cartridge (expands slot 2)
python . --extension2 hbi_j1

# --extension1/--extension2 are fully symmetric: either slot can host any
# extension id, including two different ones at once (a game in the other
# slot, or a second extension)
python . path/to/game.rom --extension1 fmpac --extension2 hbi_j1

# More --extension1/--extension2 ids (MSX-DOS2 + RAM expansion, Kanji font,
# View font) are documented in docs/README_extension.md

# Attach an MSX mouse to Joy2 (default port), driven by the host mouse
python . path/to/game.rom --mouse

# Attach an MSX mouse to Joy1 instead
python . path/to/game.rom --mouse=1

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
| `cartridge` | _(none)_ | Path to the slot 1 cartridge ROM — shorthand for `--slot1 cartridge --mapper1 auto`; conflicts with `--slot1`/`--extension1` |
| `--slot1 ROM1`, `--slot ROM1` | _(none)_ | Path to the slot 1 cartridge ROM (an alternative to the positional `cartridge` argument; conflicts with it and with `--extension1`). `--slot` is accepted as an alias for `--slot1`, mirroring `--mapper`'s alias for `--mapper1` |
| `--machine MACHINE_ID` | _(auto)_ | Machine configuration ID (e.g. `cbios_msx2_jp`). Auto-detected from ROM database when omitted |
| `--speed FLOAT` | `1.0` | Emulation speed multiplier |
| `--scale N` | `3` | Integer window scale over the 256×212 base resolution (e.g. `2` for a small display, `4` for a large one) |
| `--mapper TYPE`, `--mapper1 TYPE` | `auto` | Slot 1 mapper (`--mapper` is an alias for `--mapper1`): `auto`, `Mirrored`, `Normal`, `ASCII8`, `ASCII16`, `Konami`, `KonamiSCC`, `Majutsushi`, `ASCII8SRAM2`, `ASCII8SRAM8`, `ASCII16SRAM2`, `ASCII16SRAM8`, `R-Type`, `Page2`, `0x4000`, `0x8000`, `KoeiSRAM32`, `GameMaster2` |
| `--slot2 ROM2` | _(none)_ | Path to the slot 2 cartridge ROM; conflicts with `--extension2` |
| `--mapper2 TYPE` | `auto` | Slot 2 mapper — identical type list to `--mapper1` (KonamiSCC and every SRAM-backed type included; slot 2's SRAM persists the same way slot 1's does). KonamiSCC shares the machine's single SCC chip with slot 1: rejected if slot 1 also resolves to KonamiSCC, or if slot 1 selects `--extension1 scc_plus` |
| `--extension1 ID`, `--extension2 ID`, `--extension ID` | _(none)_ | Overlay a slot 1/slot 2 extension device — see [`docs/README_extension.md`](docs/README_extension.md) for the full list of ids and what each one is; `--extension1` conflicts with the positional `cartridge`/`--slot1`/`--mapper1`, `--extension2` conflicts with `--slot2`/`--mapper2`. The same extension id (or the same ROM, for `--slot1`/`--slot2`) cannot be selected on both slots at once — rejected at startup. `none` forces no extension overlay on that slot even when `py_emulator.yaml` sets one, freeing that slot's `--slotN`/`--mapperN` for CLI use. `--extension` is accepted as an alias for `--extension2`, matching its exact meaning before `--extension1`/`--extension2` existed |
| `--fdd1 DSK` | _(none)_ | Floppy `*.dsk` image mounted in drive A (machines with an FDC, e.g. `hb_f1xd`); writes flush back to the file on exit |
| `--fdd2 DSK` | _(none)_ | Floppy `*.dsk` image mounted in drive B (only on machines with two drives) |
| `--resume [FILE]` | _(none)_ | Resume from `saves/states/latest.state`, or a specific `.state` file |
| `--mouse [1\|2]` | _(none)_ | Attach an MSX mouse to a joystick port (bare = Joy2); overrides `py_emulator.yaml`'s `mouse` settings. Ignored in headless runs |
| `--frame-skip MODE` | `auto` | Frame skip: `auto` skips VDP rendering on late frames; `none` disables |
| `--debug` | off | Enable structured diagnostic logging to stderr |
| `--log FILE` | _(none)_ | Write diagnostic log to a file (requires `--debug`) |
| `--vdp-trace` | off | Enable VDP register-write tracing to stdout |
| `--vdp-trace-out FILE` | stdout | Write VDP trace to FILE instead of stdout |
| `--mapper-trace` | off | Enable cartridge mapper bank-switch tracing (MAP\_BANK records) |
| `--mapper-trace-out FILE` | stdout | Write mapper trace to FILE instead of stdout |
| `--count-frame N` | _(none)_ | Run exactly N frames headlessly and exit (no SDL window) |
| `--benchmark [FRAMES]` | _(none)_ | Run headlessly, unthrottled, for FRAMES frames (default: 10000) and report average FPS. Combine with `--resume` to benchmark from a saved scene. Mutually exclusive with `--count-frame` |
| `--break-point ADDRS` | _(none)_ | Comma-separated hex breakpoint addresses, max 4 |
| `--watch-point ADDRS` | _(none)_ | Watchpoint addresses, max 4; append `,r`, `,w`, or `,rw` after each address to restrict to read, write, or both (default: `rw`). Example: `C000,rw,D000,r` |
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
# slot1: roms/slot1.rom  # slot 1 cartridge ROM path (unset = use the positional cartridge argument)
# slot2: roms/slot2.rom  # slot 2 cartridge ROM path (unset = no slot 2 cartridge)
# mapper1/mapper2 are CLI-only (--mapper / --mapper1 / --mapper2); not configurable here
# extension1: fmpac        # overlay a slot 1 extension -- see docs/README_extension.md
# extension2: hbi_j1       # overlay a slot 2 extension ('extension' also still works, as a legacy alias for extension2)
frame_skip: true         # true = auto (default), false = none (disable)

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

keyboard_joystick:
  buttons: {}             # Joy1 key overrides, e.g. { trigger_a: j }
    # up: i               # (each entry replaces that function's default
    # down: k             #  key(s) entirely — no partial/alternate merge;
    # left: j             #  see py_emulator.example.yaml for the full
    # right: l            #  key-name vocabulary)
    # trigger_a: n
    # trigger_b: m

mouse:
  enabled: false         # attach an MSX mouse driven by the host mouse
  port: 2                # 1 (Joy1) or 2 (Joy2); default 2 when enabled
```

`machine`, `speed`, `scale`, `slot1`, `slot2`, `extension1`, `extension2`
(`extension` also still works, as a legacy alias for `extension2`),
`frame_skip`, `mouse`, and RPC/gamepad/keyboard-joystick settings are
configurable (`mapper1`/`mapper2` are CLI-only, see `--mapper`/`--mapper1`/
`--mapper2` above); the gamepad button map applies to the SDL GameController path
(both ports share one map), `keyboard_joystick.buttons` applies to Joy1's
keyboard emulation only, and `--mouse` on the command line always overrides
`mouse.enabled`/`mouse.port`. See `py_emulator.example.yaml` for the full
annotated list and valid button/key-name labels.

### In-emulator key bindings

| Key | Action |
| --- | --- |
| Ctrl + Q | Quit |
| F8 | Save state to `saves/states/<title>_YYYYMMDD_HHMMSS.state`* |
| F9 | Load most recent save state |
| F10 | Save screenshot to `saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png` |
| F11 | Toggle fullscreen |
| Esc, F1–F5 | Passed through to the MSX keyboard matrix |
| Ctrl + F1 | MSX HOME |
| Ctrl + F2 | MSX INS |
| Ctrl + F3 | MSX DEL |
| Ctrl + F4 | MSX STOP |
| Ctrl + F5 | MSX SELECT |
| Left Alt/Option | MSX GRAPH |
| Right Alt/Option | MSX CODE/KANA |
| Shift + 0 | MSX "_" — types what SHIFT+"\" (a JIS-only key, absent on International keyboards) would produce |

\* `<title>` is the game title from the ROM database, or `"py-msx-emulator"` if
the cartridge is not in the database.

**Note (macOS):** `^F1`–`^F5` (Ctrl+F1..F5) may be reserved by macOS as
system-wide shortcuts (System Settings → Keyboard → Keyboard Shortcuts →
Keyboard, e.g. "Move focus to the menu bar" = `^F2`). If Ctrl+F1..F5 doesn't
seem to reach the emulator, disable those shortcuts there.

**Note (JIS BIOS machines):** HB-F1XD and other Japanese MSX machines run a
Japanese BIOS ROM, and that ROM's character-decode table is always the JIS
one, regardless of the `keyboard_type` setting in the machine config —
`keyboard_type` only controls which host key maps to which MSX keyboard
matrix cell, not what character the BIOS assigns to that cell. Running the
emulator with an International host keyboard on one of these machines
therefore means the keytop and the character actually typed won't always
match. `"_"` is the only character with no key at all on an International
keyboard, hence the Shift+0 assignment above; every other key still types,
just not necessarily the character printed on its keytop.

**Keyboard joystick emulation (Joy 1):** these are the built-in defaults; each
function's key is overridable via `keyboard_joystick.buttons` in
`py_emulator.yaml` (see [Configuration file](#configuration-file-py_emulatoryaml)).

| Key | Action |
| --- | --- |
| W / ↑ | Up |
| S / ↓ | Down |
| A / ← | Left |
| D / → | Right |
| Z or , | Trigger A |
| X or . | Trigger B |

---

## Tools

Standalone command-line utilities under `tools/`: disk image management
(an interactive ftp-style shell, a blank-image creator, a raw-sector copier
for a physical USB floppy drive), plus the RPC/MCP client tools used by
[Remote control](#remote-control-socket-rpc--mcp) below.

Full usage, options, and examples for every script:
[`tools/README_tools.md`](tools/README_tools.md) /
[`tools/README_tools_ja.md`](tools/README_tools_ja.md).

---

## Remote control (Socket RPC & MCP)

The emulator can expose a small local control surface — a Unix-socket
JSON-RPC server built into the emulator (`--rpc`) plus an MCP server that
wraps it (`tools/mcp_server.py`) — so external tools, a test harness, or an
AI coding agent such as Claude Code can pause, inspect, and drive a running
instance.

Architecture, enabling the RPC server, registering the MCP server, the RPC
method reference, and security notes:
[`tools/README_tools.md`](tools/README_tools.md#remote-control-socket-rpc--mcp)
/ [`tools/README_tools_ja.md`](tools/README_tools_ja.md#リモート制御socket-rpc--mcp).

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
| `hb_10` | MSX1 | Japan | TMS9918A |
| `hb_10_64k` | MSX1 | Japan | TMS9918A |
| `hb_f1xd` | MSX2 | Japan | V9938 |
| `hb_f1xd_256k` | MSX2 | Japan | V9938 |
| `fs_a1f` | MSX2 | Japan | V9938 |

`hb_10` (Sony HB-10) uses the real machine ROM, with 16 KB RAM in slot 0
alongside the BIOS (slot 3 is unpopulated, as on real hardware); place its
`hb-10_basic-bios1.rom` under `roms/hb_10/`.

> **Note**: the HB-10's VDP is a TMS9118NL; this emulator implements it as
> the functionally equivalent TMS9918A.

`hb_10_64k` models an HB-10 with the 64 KB RAM modification described on the
[Sony HB-10 category page](https://www.msx.org/wiki/Category:Sony_HB-10):
slot 0's 16 KB RAM is removed and slot 3 holds 64 KB flat RAM across all four
pages. It reuses `hb_10`'s ROM from `roms/hb_10/`. This configuration has not
been verified against a modified unit.

`hb_f1xd` (Sony HB-F1XD) uses the real machine ROMs and adds a WD2793 floppy
disk drive; place its `hb-f1xd_basic-bios2.rom`, `hb-f1xd_msx2sub.rom`, and
`hb-f1xd_disk.rom` under `roms/hb_f1xd/` and mount a disk with `--fdd1`.

`hb_f1xd_256k` reuses the same ROMs and floppy drive as `hb_f1xd` above, but
replaces slot 3's flat 64 KB RAM with a 256 KB RAM mapper — enough for
MSX-DOS2's extended BIOS mapper support routines, which require a RAM
mapper with at least 128 KB and cannot use flat RAM regardless of its size.

> **Note**: no real HB-F1XD variant with a 256 KB RAM mapper was ever
> manufactured; real HB-F1XD hardware always has a fixed 64 KB flat RAM,
> not a RAM mapper. `hb_f1xd_256k` is not an unverified claim about real
> hardware — it is an invented configuration, built solely to give
> MSX-DOS2 the memory layout it needs, reusing every other real-hardware
> HB-F1XD declaration unchanged.

`fs_a1f` (Panasonic FS-A1F) uses the real machine ROMs and adds a TC8566AF
floppy disk drive; place its `fs-a1f_basic-bios2.rom`, `fs-a1f_msx2sub.rom`,
and `fs-a1f_disk.rom` under `roms/fs_a1f/` and mount a disk with `--fdd1`.
Real hardware ships these as one combined 128 KB mask ROM — see
`config/machines/fs_a1f.yaml`'s comment for the expected split.

> **Note**: real FS-A1F hardware also includes a bundled "Cockpit"
> application and a Kanji ROM/font device; neither is emulated. The author
> does not own a real FS-A1F, so this implementation is based on publicly
> available technical documentation and has not been verified against real
> hardware.

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

The test suite covers all major components with 3462 tests spanning unit tests
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
│   ├── fdc/               # Floppy (disk image, drive, WD2793/TC8566AF, interface)
│   ├── diagnostics/       # DebugLogger, CPU/I/O trace, hang detector
│   ├── debugger/          # Interactive REPL (prompt, disassembler)
│   ├── machine.py         # Component wiring and frame loop
│   ├── machine_loader.py  # YAML-based machine configuration loader
│   ├── memory.py          # Slot-based memory bus
│   ├── mapper.py          # Cartridge mappers (Flat, ASCII8/16, Konami, SCC, Halnote, ...)
│   ├── mapper_tracer.py   # Cartridge bank-switch tracer
│   ├── kanji.py           # JIS Kanji font ROM I/O device
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
│   ├── mouse.py           # MSX mouse protocol state machine (pin-8-clocked nibbles)
│   ├── frame_timer.py     # 60 fps pacing + FPS measurement
│   ├── app_config.py      # py_emulator.yaml startup defaults
│   ├── romdb.py           # SHA1-based ROM title/mapper database
│   ├── rpc_server.py      # Unix-socket JSON-RPC control server (--rpc)
│   ├── screenshot.py      # RGB24→PNG writer (screenshots + save-state images)
│   └── state.py           # Save/load machine state (JSON + PNG)
├── config/
│   ├── devices/           # Device YAML definitions (VDP, PSG, PPI, RTC, ...)
│   └── machines/          # Machine YAML definitions (cbios_msx1_jp, cbios_msx2_jp, ...)
├── tools/                 # Disk image tools (dskftp, dskblank, dskdd), RPC client, MCP server — see tools/README_tools.md
├── docs/                  # Debugger guide, socket RPC / MCP reference
├── assets/                # Benchmark history charts used by this README
├── roms/
│   └── cbios/             # C-BIOS ROM files (not in version control)
├── saves/                 # Save states and screenshots (created at runtime)
├── allium/                # Allium behaviour specs, verifying spec/implementation alignment (not included in the public repository)
├── openspec/
│   └── specs/             # Component specifications (not included in the public repository)
├── tests/                 # Test suite — 3462 tests
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Development dependencies
└── pyproject.toml         # Project metadata and tool configuration
```

---

## Contributing

### Spec-first rule

Every new hardware component or significant behaviour change must have a
specification added or updated in `openspec/specs/<spec-id>/spec.md` before any
implementation code is written; a new spec joins an existing domain prefix
rather than inventing one. The scenarios in the spec are the source of truth for
test cases. A PR that adds implementation without a corresponding spec update
will not be merged.

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

There is no formal CONTRIBUTING.md at present. Please open a
[GitHub issue](https://github.com/SangatsuUsagi/py-msx-emulator/issues) to
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

Full version history: [CHANGELOG.md](CHANGELOG.md).
