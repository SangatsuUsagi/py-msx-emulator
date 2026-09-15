# Technical Implementation

[← README.md](../README.md)

This document describes the internal structure of the MSX1/MSX2 emulator — CPU
execution, interrupt handling, I/O dispatch, VDP rendering, and the memory
subsystem.

- [CPU emulation](#cpu-emulation)
- [Interrupt management](#interrupt-management)
- [I/O bus](#io-bus)
- [VDP](#vdp)
- [Audio](#audio)
- [Memory and slot system](#memory-and-slot-system)
- [Floppy disk (FDC)](#floppy-disk-fdc)
- [Machine and extension YAML schema](#machine-and-extension-yaml-schema)
- [Machine YAML loader](#machine-yaml-loader)
- [Portability](#portability)

---

## CPU emulation

### Instruction decode

The Z80 CPU is implemented in `msx/cpu/z80.py`. The opcode dispatch table
`_DISPATCH` is a flat list of 256 callables, built once at import time by
`opcodes_main._build_dispatch()` in `msx/cpu/opcodes_main.py` and bound as a
module-level constant in `z80.py`:

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

Executing an instruction is a single indexed call — no dictionary lookup, no
match/case:

```python
n = _DISPATCH[opcode](cpu)
```

CB, DD, ED, and FD prefix tables use the same pattern. When the main dispatcher
encounters a prefix byte, it fetches the next byte and dispatches into the
corresponding prefix table.

### Execution loop

`Z80.step() -> int` drives one instruction:

1. If `nmi_pending` is set: push PC, jump to 0x0066, return 11 T-states.
2. If `ei_pending` is set: clear it. (EI enables interrupts only after the
   instruction that follows it, so an accepted interrupt is suppressed for
   exactly one step.)
3. If `iff1` and `int_pending` are both true: accept the interrupt. Mode 1 —
   push PC, jump to 0x0038, 13 T-states. Mode 2 — push PC, read the vector byte
   from `I<<8 | data_bus`, jump to the handler address, 19 T-states.
4. If `halted`: advance 4 T-states without fetching an opcode.
5. Otherwise: record the current PC in `instruction_pc`, fetch the opcode via
   `_fetch()`, dispatch.

`_fetch()` reads one byte from `read_byte(PC)`, increments PC, and increments R.
Only bits 0–6 of R count; bit 7 is sticky and changes only through a full
register write such as `LD R,A`.

### Timing

`step()` returns the number of T-states consumed. `Machine.run_frame()`
accumulates these into a per-frame total and into `machine.cycle_count`. The
NTSC frame budget is **59,659 T-states**, derived from the Z80A clock of
3.579545 MHz divided by 60 Hz.

#### MSX M1 wait state

Real MSX inserts one wait state on every Z80 M1 cycle (opcode fetch). The core
models this with a configurable `Z80.m1_wait_states` field (default `0` = pure
datasheet Z80, so the core stays reusable for non-MSX systems). MSX machine
YAMLs set `cpu.m1_wait_states: 1`, which `machine_loader` passes to the
`Z80(...)` constructor.

The wait is added per M1 cycle, not per instruction byte: `step()` adds
`m1_wait_states` for the primary opcode fetch, and each prefix handler
(`_op_prefix_cb/dd/fd/ed`) adds it again for the second M1. So an unprefixed
instruction gets `+1` regardless of its operand length, a `CB`/`ED`/`DD`/`FD`
instruction gets `+2`, and `DDCB`/`FDCB` gets `+2` (the displacement and final
opcode are operand reads, not M1s). This matches openMSX (`Z80.hh`
`WAIT_CYCLES = 1`, M1-base 5 vs datasheet 4). Because the frame budget stays
59,659 T-states, enabling the wait means fewer instructions execute per frame —
so CPU-loop-paced code (e.g. software-PCM audio driven by a delay loop) runs at
the correct hardware rate rather than ~13% fast. Interrupt cadence (VBlank) and
PSG pitch are unaffected.

### Known limitations

OTIR/INIR and similar block I/O instructions are not cycle-exact across page
boundaries. R increments on every byte `_fetch()` reads — operands included,
since `_fetch_word()` calls it twice — whereas real hardware refreshes only on
M1 opcode-fetch cycles, so code that reads R as an entropy source sees a
different sequence.

---

## Interrupt management

### MSX1: frame-based VBlank

On MSX1, the TMS9918A VDP has a single interrupt source: VBlank at the end of
each frame. `Machine.__post_init__()` wires this as a callback:

```python
if not isinstance(self.vdp, V9938):
    self.vdp.on_interrupt = self._vblank_interrupt
```

`_vblank_interrupt` sets `cpu.int_pending = True`. The interrupt fires once per
frame when `render_frame()` calls `vdp._finalize()`, which sets the VBlank
status flag and invokes the callback. There are no per-scanline interrupts in
the MSX1 path.

### MSX2: level-based IRQ

On MSX2 (V9938), interrupts are level-based. The inner loop of
`Machine.run_frame()` samples `vdp9938.irq` at every instruction boundary:

```python
cpu.int_pending = vdp9938.irq
```

`V9938.irq` reflects `irq_pending()`:

```python
def irq_pending(self) -> bool:
    ie0 = bool(self.regs[1] & 0x20)   # IE0 = R#1 bit 5
    f   = bool(self.status & 0x80)     # F   = S#0 bit 7 (VBlank)
    ie1 = bool(self.regs[0] & 0x10)   # IE1 = R#0 bit 4
    fh  = bool(self._status1 & 0x01)  # FH  = S#1 bit 0 (H-line)
    return (ie0 and f) or (ie1 and fh)
```

The CPU sees a true interrupt level until the status flag is cleared by a port
0x99 read — matching hardware behaviour. A read of S#0 (R#15 = 0) clears F; a
read of S#1 (R#15 = 1) clears FH.

### H-line interrupt

`Machine.run_frame()` divides the frame into scanlines and calls
`vdp9938.begin_scanline(L)` once at the end of each scanline's T-state budget:

```python
for L in range(lpf):               # lpf = 262 NTSC lines
    line_end = (L + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()
        ...
    vdp9938.begin_scanline(L)
    cpu.int_pending = vdp9938.irq
```

`begin_scanline(line)` computes the effective IRQ line as `(R#19 − R#23) & 0xFF`
and sets S#1 bit 0 (FH) when `line == effective_irq_line` and
`0 <= line < display_height`. IE1 (R#0 bit 4) gates whether a matching FH
asserts `irq`; FH is set regardless of IE1, so software can poll S#1 without
enabling the interrupt.

FH persists until S#1 is read; reading it clears FH and re-evaluates `irq`.

The VBlank flag is also set in `begin_scanline(display_height)` — the first line
past the active display — replacing the old per-frame finalize path for MSX2.

---

## I/O bus

### Registration and dispatch

`IOBus` (`msx/io.py`) holds two lists of `(start, end, handler)` tuples, one for
reads and one for writes. Devices register themselves during machine
construction:

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` and `write_port` mask the incoming 16-bit Z80 port address to 8 bits
(`port &= 0xFF`) and do a linear scan. The first registered handler whose
`[start, end]` range covers the port wins. If no handler matches, reads return
0xFF (open bus).

### Logging

When `_logger` is attached, reads are logged after the handler returns (the
value is then known) and writes are logged before dispatching (the value is
fixed at the call site). Both log entries record the port, value, and the
current instruction PC.

### Standard port map

| Port(s)   | Device                                   |
| --------- | ---------------------------------------- |
| 0x98–0x9B | VDP (TMS9918A or V9938)                  |
| 0x9A      | V9938 palette (MSX2 only)                |
| 0xA0–0xA2 | PSG (AY-3-8910)                          |
| 0xA8–0xAB | PPI (i8255)                              |
| 0xB4–0xB5 | RTC (RP5C01, MSX2 only)                  |
| 0xFC–0xFF | RAM mapper segment registers (MSX2 only) |

---

## VDP

### TMS9918A (MSX1)

Implemented in `msx/vdp/vdp.py` with the renderer in `msx/vdp/renderer.py`. It
has 16 KB VRAM, 8 control registers, and supports screen modes 0–3 (Text,
Graphic 1/2, Multicolor). The 256×192 active frame is rendered at the end of
`Machine.run_frame()` by `render_frame()`, which also calls `_finalize()` to set
the VBlank flag and invoke `on_interrupt`. The renderer then pads that frame to
a constant 256×212 output (10 border rows top and bottom) via the shared
`msx/vdp/_geometry.py` helper, so every frame keeps a stable 4:3 geometry
regardless of the VDP's active line count (see V9938 `display_height` below).

### V9938 (MSX2)

Implemented in `msx/vdp/v9938.py` with the renderer in
`msx/vdp/v9938_renderer.py`. Key differences from TMS9918A:

- 128 KB VRAM
- 28 control registers (R#0–R#27) plus 15 command-engine registers (R#32–R#46)
- Programmable 16-colour palette (9-bit GRB333); `_MSX2_DEFAULT_PALETTE` loaded
  at reset matches the V9938 data book power-on values
- Three status registers: S#0 (VBlank/sprite), S#1 (H-line FH), S#2 (command
  engine CE/TR, retrace HR/VR)
- Hardware command engine
- Level-based IRQ via `irq_pending()`

The `display_height` property returns 192 normally, or 212 when R#9 bit 7 (LN)
is set. The `display_width` property returns 512 for the wide modes (SCREEN
6/G5 and SCREEN 7/G6, M5 set and M4 clear) and for TEXT2 (SCREEN 0 WIDTH 80,
M1 and M4 set with M3 clear), and 256 for all other modes.

### Banded renderer

Games that change VDP registers mid-frame — switching palette or screen mode
between display regions — require the renderer to honour those changes at the
correct scanline.

`V9938.write_port()` records every write to a display-relevant register into
`_reg_write_log`:

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 18, 19, 23})
```

Each entry is either:

- `(display_line, reg, value)` for a register write
- `(display_line, -1, (palette_index, grb_value))` for a palette write (port
  0x9A), using sentinel `reg = -1`

The log is cleared at `begin_scanline(0)` (frame start) and is not cleared by
`render_frame()`, so the SDL2 frontend can use it during RGB conversion to apply
the correct palette per scanline.

`render_frame_v9938(vdp)` inspects `_reg_write_log` to find band boundaries —
lines at which a display-relevant value differs from the previous band's
snapshot. Each contiguous band `[y0, y1)` is rendered using the register state
active at its start. Per-band renderers are called with explicit `y_start` and
`y_end` parameters; each scanline is rendered exactly once across all bands.

When the log is empty (no mid-frame writes), `render_frame_v9938` falls back to
a single-pass render, identical in output and performance to the pre-banding
path.

Sprites are rendered once per sprite attribute table (SAT) region — a maximal
run of consecutive bands sharing the same SAT base (R#5/R#11). When the SAT base
is constant across the frame (the usual case), there is exactly one sprite pass,
matching a single-pass render. When the SAT base changes mid-frame (e.g. a
sprite multiplexer), each region's sprites are drawn from its own SAT, so
distinct regions read distinct buffers and same-sprite duplicates are avoided.

### Command engine

The V9938 hardware command engine supports byte- and pixel-level VRAM
operations:

| Command | Description                                              |
| ------- | -------------------------------------------------------- |
| HMMV    | Fill a rectangle with a byte value                       |
| HMMM    | Copy a rectangle (byte granularity)                      |
| HMMC    | Transfer a block from CPU to VRAM (byte granularity)     |
| LMMV    | Fill a rectangle with a colour value (pixel granularity) |
| LMMM    | Copy a rectangle (pixel granularity)                     |
| LMCM    | Transfer a block from VRAM to CPU                        |
| LMMC    | Transfer a block from CPU to VRAM (pixel granularity)    |
| YMMM    | Copy a rectangle using Y-axis only (byte granularity)    |
| LINE    | Draw a line                                              |
| PSET    | Write a single pixel                                     |
| POINT   | Read a single pixel value (result in S#7)                |
| SRCH    | Search a scanline for a colour match (result in S#8/S#9) |
| ABRT    | Abort the active command                                 |

Commands are dispatched immediately. An approximate cycle budget
(`_cmd_remaining`) is decremented by `V9938.tick(n)` (called each instruction
with the consumed T-states) to model command duration. The budget is calibrated
at `_CYCLES_PER_BYTE = 8` T-states per VRAM byte, derived from openMSX
golden-log comparisons (230K T-states for a 128×212 fill). Software that
busy-waits on S#2 bit 0 (CE) will see CE clear after the budget expires.

The HMMC/LMMC transfer latch (`_cmd_transfer`) is set by writes to R#44 (COL).
The first pixel/byte of a transfer comes from a pending COL write rather than
being auto-loaded at dispatch, matching openMSX behaviour.

Logical operations (IMP, AND, OR, XOR, NOT) are applied per pixel via
`_apply_log()` in `v9938.py`.

### Known limitations

- Command timing is approximate. Exact command durations differ from hardware;
  the V9938 data book per-command cycle counts are not reproduced.
- VRAM results are written at dispatch, not incrementally. Software reading VRAM
  while a command is nominally in progress may see the completed result before
  CE clears.
- The renderer is deferred: the CPU runs a full frame, VDP commands execute
  instantly into VRAM, then a single render pass covers the whole frame — at the
  start of vertical blanking on V9938, at end-of-frame on TMS9918A. VRAM updates
  that are synchronised to the raster within a frame (beam-raced blits,
  double-buffered title screens) are not reproduced faithfully. The banded
  renderer above recovers mid-frame *register* changes, but not mid-frame VRAM
  content.

---

## Audio

The PSG (`msx/psg.py`), SCC (`msx/scc.py`), the FM-PAC's OPLL (`msx/opll.py`),
and the Majutsushi DAC each render signed-16-bit mono PCM at 44,100 Hz, 735
samples per frame. The SDL2 frontend's `_mix_audio()` sums whichever of them the
machine has, clamps to 16-bit, and queues the buffer to SDL.

### Sub-frame software PCM

`PSG.generate_samples(n, frame_start, frame_end)` reproduces software PCM played
by rapid volume-register writes. `write_port` timestamps each register write as
`(cycle, reg, value)` using a `_get_cycle` callback wired to
`machine.cycle_count`. `generate_samples` rewinds to the register/generator
state at the frame's first write and re-renders the buffer as consecutive
segments split at each write's sample position
`clamp((cycle - frame_start) * n // (frame_end - frame_start), 0, n)`, carrying
tone/noise/envelope state across segments. A frame with no recorded writes takes
an unchanged single-snapshot fast path (byte-for-byte identical to the old
behaviour), so normal music pays nothing.

### Tone integration (period-0 PCM carrier)

Software PCM leaves the tone generator enabled at tone period 0 — an ultrasonic
(~223 kHz) carrier that the real analog output filters to a ~50 % duty average,
so the volume register carries the PCM. Point-sampling `tone_out` at 44.1 kHz
would alias that carrier into a full/zero chop, zeroing ~half the samples.
Instead, each channel's tone is **integrated over the output sample**: the inner
loop counts the PSG ticks the square wave was high (`hi0/hi1/hi2`) and the mixer
scales the channel amplitude by `hi / ticks`. For audible tones (no toggle
within a sample) `hi` is `ticks` or `0`, so the result is bit-for-bit identical
to point-sampling; only the ultrasonic carrier is band-limited to its duty
average.

### Output low-pass filter

`msx/audio_filter.py` (`BiquadLowPass`) is a stateful 2-pole Butterworth
low-pass (8 kHz, RBJ coefficients, Direct Form I) applied to the final mixed
buffer in the frontend before `SDL_QueueAudio`, modelling the analog RC filter
on real MSX audio out. A single instance carries its `x1/x2/y1/y2` state across
frames (reset when the audio device opens). It removes the residual
high-frequency imaging/aliasing that point-sampling synthesis leaves near
Nyquist at ~1 % of the per-frame audio-path cost; the audible band and overall
level are essentially unchanged because real PSG music energy sits well below
the cutoff.

## Memory and slot system

### MSX1 slot layout

`Memory` (`msx/memory.py`) maps the flat 64 KB address space through a 4-page ×
4-slot dispatch. The slot-select register (port 0xA8, via PPI) determines which
slot occupies each 16 KB page. Bits `[2N+1 : 2N]` of the register select the
slot for page N.

Default MSX1 layout:

| Slot | Content                                   |
| ---- | ----------------------------------------- |
| 0    | BIOS ROM (read-only)                      |
| 1    | Cartridge ROM via mapper                  |
| 2    | Second cartridge or open bus (reads 0xFF) |
| 3    | 32 KB RAM at pages 2–3 (0x8000–0xFFFF)    |

### MSX2 expanded sub-slot

For MSX2, slot 3 is expanded (`sub_slot_enabled = True`). Reading 0xFFFF returns
`~sub_slot_reg` so software can detect sub-slot support. The sub-slot register
(`sub_slot_reg`) selects which of the four secondary slots occupies each page
within the expanded primary.

Default MSX2 layout:

| Sub-slot | Content                    |
| -------- | -------------------------- |
| 3-0      | C-BIOS Sub ROM (read-only) |
| 3-1      | Empty                      |
| 3-2      | 128 KB RAM via RAM mapper  |
| 3-3      | Empty                      |

### RAM mapper

`RamMapper` (`msx/ram_mapper.py`) divides 128 KB into 8 segments of 16 KB each.
Four segment registers at ports 0xFC–0xFF independently control which segment is
visible in each CPU page:

| Port | Page | Address range |
| ---- | ---- | ------------- |
| 0xFC | 0    | 0x0000–0x3FFF |
| 0xFD | 1    | 0x4000–0x7FFF |
| 0xFE | 2    | 0x8000–0xBFFF |
| 0xFF | 3    | 0xC000–0xFFFF |

Writing the segment number (0–7) to the register remaps that page immediately.

---

## Floppy disk (FDC)

The floppy subsystem is a generic layer (`msx/fdc/`) so a new controller chip or
connection style plugs in without touching `Memory`. Two of each are wired up
today — WD2793 with the Sony/Philips style, and TC8566AF with its own:

| Layer      | File                             | Responsibility                                                                                                                                                                                                                                                    |
| ---------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Disk image | `disk_image.py` (`DskDiskImage`) | Reads a `*.dsk` sector image; derives geometry from the FAT12 BPB (bytes/sector, total sectors, sectors/track, heads); buffers writes and flushes back to the file on exit                                                                                        |
| Drive      | `disk_drive.py` (`DiskDrive`)    | One physical drive: holds the mounted image, tracks the head position, exposes sector read/write                                                                                                                                                                  |
| Controller | `wd2793.py` (`WD2793`), `tc8566af.py` (`TC8566AF`) | The FDC chip. `WD2793`: Type I–IV command decode, status register, data/track/sector registers, `abort()`. `TC8566AF` (uPD765 family): Command/Execution/Result phase model, Main Status Register, non-DMA transfers, no directly addressable track/sector register |
| Interface  | `interface.py`                   | Connection style between the controller and the memory bus, both mapping the DISK ROM at 0x4000–0x7FFF. `SonyPhilipsInterface` (= openMSX PhilipsFDC) puts the WD2793 registers at 0x7FF8–0x7FFF and consumes the disk-change bit; `TC8566AFInterface` puts two control registers, the Main Status Register and the Data Register at 0x7FF8–0x7FFB. `swap()` mounts/ejects at runtime and is shared by both |

Which controller and connection style a machine gets is declared in its YAML
`fdc:` block, so the two shipped floppy machines differ only in data. The Sony
HB-F1XD (`hb_f1xd`) puts the WD2793 and its DISK ROM in slot 3 sub-slot 0,
alongside 64 KB of flat RAM in sub-slot 3. The Panasonic FS-A1F (`fs_a1f`) uses
the TC8566AF and spreads the roles across its four secondary slots — RAM in
sub-slot 0, SUB ROM in 1, the FDC in 2. `--fdd1`/`--fdd2` mount images into
drives A/B; the debugger's `fdd1`/`fdd2` commands swap them at runtime. The implementation boots Disk BASIC, supports
`CALL FORMAT`, and reads/writes files with write-back on exit. `machine.fdc` is
`None` on machines with no floppy interface.

## Machine and extension YAML schema

`msx/machine_loader.py` is the single source of truth for three YAML
surfaces: device definitions (`config/devices/*.yaml`), machine
specifications (`config/machines/*.yaml`), and extension overlays
(`config/extensions/*.yaml`). All three are hand-validated `TypedDict`
shapes (`DeviceEntryYaml`, `MachineEntryYaml`, `ExtensionOverlayYaml`, and
their nested shapes), not schema-validated by a library — every field this
section documents corresponds to an explicit `.get()` call somewhere in the
loader; a key not read there is pure documentation, whatever the YAML
comment beside it claims. See
[README_extension.md's "Slot model" section](../README_extension.md#slot-model)
for the user-facing summary of what each primary slot can hold; this
section documents the YAML syntax that produces it.

### Device YAML (`config/devices/*.yaml`)

| Field | Type | Required | Read by |
| --- | --- | --- | --- |
| `id` | string | yes | must equal the filename stem, or `load_device_registry` raises `MachineLoadError` |
| `type` | string | yes | presence is validated; the value itself is never checked (`io_device`, by convention) |
| `implemented` | bool | no (default `true`) | `false` skips the device at `builtin_devices` resolution time with a stderr warning rather than a hard failure — lets a device definition land before its emulation does |
| `io_ports` | list of int | no | first and last elements become the device's `(start, end)` I/O range, falling back to `_DEFAULT_IO_PORTS` when the key is absent |

Every other key (`name`, `chip`, `controls`, `vram_kb`, `segment_size_kb`,
`keyboard_type`, ...) is stored in `_DeviceDef.raw` but is *not* read by
generic code. `dev.raw.get(...)` is called from exactly two places in the
whole codebase: `io_ports` above, and `keyboard_type` below (`ppi8255`
only). `vdp_v9938.yaml`'s `vram_kb: 128` is never read anywhere — V9938's
VRAM size is the hardcoded `_VRAM_SIZE = 131072` constant in
`msx/vdp/v9938.py`, so that key (and its per-machine
`overrides: {vram_kb: 128}` echo, below) documents intent without having
any effect on the running emulator.

```yaml
id: vdp_v9938
type: io_device
implemented: true
name: Yamaha V9938 Video Display Processor (MSX2)
chip: v9938
io_ports: [0x98, 0x99, 0x9A, 0x9B]
controls: video_display_processor
vram_kb: 128    # documentation only -- see note above
```

### Machine YAML (`config/machines/*.yaml`)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | — | must be exactly `1` |
| `id` | string | — | must equal the filename stem |
| `generation` | string | — | `msx1` or `msx2`; anything else raises |
| `name` | string | `id` | display name only |
| `rom_base` | string | `roms/cbios` | directory (relative to the project root) every `rom:` block's `file` resolves against |
| `video_standard` | string | `ntsc` | `ntsc` → 59,659 T-states / 262 lines per frame; `pal` → 71,364 / 313 (used by `cbios_msx1_eu`) |
| `cpu.m1_wait_states` | int | `0` | extra T-states per Z80 M1 cycle — see [CPU emulation](#cpu-emulation) above |
| `slots.primary` | mapping | — | keyed `0`–`3`; only `0` and `3` are read (below) |
| `builtin_devices` | list | `[]` | see below |
| `io_device` | mapping | — | optional machine-level global I/O device, same shape as an extension overlay's `io_device` (below) |

#### Slot 0 (fixed)

```yaml
0:
  content:
    - rom: {file: cbios_main_msx2.rom, size_kb: 32, pages: [0, 1], sha1: null}
    - rom: {file: cbios_logo_msx2.rom, size_kb: 16, pages: [2], sha1: null}
```

`content` is a list of `{rom: {...}}` entries. `_parse_slot0` scans it for
the entry whose `pages` includes `0` or `1` (the main BIOS ROM, required —
its absence raises `MachineLoadError`) and the entry whose `pages` includes
`2` (an optional logo ROM at 0x8000–0xBFFF). `sha1: null` disables hash
verification for that ROM; any other string value is checked against the
loaded file.

#### Slots 1 and 2: not read from the machine YAML

Every machine YAML declares `1: {type: cartridge}` and `2: {type: cartridge}`
under `slots.primary` — but `load_machine_spec` never reads `primary[1]` or
`primary[2]`, only `primary[0]` and `primary[3]`. These two entries are
declared intent only. Slot 1's cartridge (the positional CLI argument plus
`--mapper`) and slot 2's cartridge/mapper (`--slot2`/`--mapper2`) or overlay
(`--extension`) are resolved entirely from `build_machine()`'s own
parameters — a consequence of the CLI, not of anything under
`slots.primary.1`/`.2` in the YAML.

#### Slot 3 — MSX1

```yaml
3:
  size_kb: 32   # optional, defaults to 32
```

`_parse_slot3_msx1` reads only `size_kb` (default `32`) — flat RAM at
0x8000–0xFFFF, no further structure.

#### Slot 3 — MSX2

```yaml
3:
  expanded: true
  secondary:
    0:
      content:
        - rom: {file: cbios_sub.rom, size_kb: 32, pages: [0, 1], sha1: null}
    2:
      type: ram
      mapper: standard
      size_kb: 128
```

`expanded: true` switches slot 3 into four secondary slots (`secondary.0`–
`.3`, keyed the same way as `slots.primary`). `_parse_slot3_msx2` scans them,
independently, for:

- the first sub-slot with `content` whose `rom.pages` includes `0` or `1` →
  the SUB ROM (optional; its sub-slot index is recorded, not fixed to 0)
- the first sub-slot with `mapper: standard` → a `RamMapper`, sized by its
  `size_kb` (default `128`; must be a positive multiple of `16`, checked by
  `_check_ram_mapper_size_kb`, or `MachineLoadError`)
- the first sub-slot with `type: ram` (and no `mapper: standard`) → flat
  (non-mapper) RAM, sized by `size_kb` (default `64`) — used by `hb_f1xd`'s
  real fixed 64 KB
- the first sub-slot with an `fdc:` block → see [Floppy disk (FDC)](#floppy-disk-fdc)

A `mapper: standard` sub-slot and a `type: ram` sub-slot are mutually
exclusive across the whole `secondary` mapping (`Memory` cannot host both a
RAM mapper and flat RAM at once) — `MachineLoadError` if both are declared.
Flat RAM sharing a sub-slot index with the SUB ROM or the FDC is also
rejected, since `Memory`'s write path has no guard against a stray write to
either landing in flat RAM instead. Every sub-slot index is checked against
`0`–`3` (`_check_subslot_index`) independently of which role it carries.

`fdc:` block fields (only meaningful inside a slot-3 sub-slot):

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `rom` | `rom:` block | — | required (the DISK ROM) |
| `controller` | string | `wd2793` | `wd2793` or `tc8566af` |
| `connection_style` | string | `sony` | `sony` or `tc8566af` |
| `drives` | int | `1` | must be positive |

`(controller, connection_style)` must be one of `(wd2793, sony)` or
`(tc8566af, tc8566af)` — any other pairing raises, even if each value is
individually valid (e.g. `wd2793` + `tc8566af` is rejected).

#### `builtin_devices`

```yaml
builtin_devices:
  - ref: ppi8255
    overrides: {keyboard_type: jp}
  - ref: vdp_v9938
    overrides: {vram_kb: 128}   # accepted, never read (see Device YAML above)
  - ref: psg_ay8910
  - ref: rtc_rp5c01
  - ref: memory_mapper_standard
```

Each entry's `ref` must resolve against the device registry
(`load_device_registry`'s output), or `MachineLoadError`. `overrides` is a
free-form mapping, but `_parse_builtin_devices` only ever reads one key from
it: `keyboard_type` (`int` or `jp`), and only when `ref: ppi8255` — every
other `overrides` key, for every other `ref`, is accepted and ignored.
`ref: vdp_v9938`/`rtc_rp5c01`/`ppi8255` presence sets the `has_v9938`/
`has_rtc`/keyboard-layout flags `MachineSpec` carries; every other `ref`
only contributes its `io_ports` range.

#### Machine-level `io_device`

```yaml
io_device:
  device: kanji_rom
  rom: {file: some_kanji_font.rom, size_kb: 256, sha1: null}
```

Same shape as an extension overlay's `io_device` (below) — a slot-independent
global I/O device the machine itself provides, validated against the same
`kanji_rom`-only `_KNOWN_IO_DEVICES` set. No shipped machine YAML declares
one today; `--extension hbi_j1`/`msxdos2_512k_kanjirom` are the only current
sources of a `kanji_rom` device, via the extension overlay's own
`io_device` (below) instead.

### Extension overlay YAML (`config/extensions/*.yaml`)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | — | must be `1` |
| `id` | string | — | documentation only — `load_extension_overlay` never reads it; the file is located by filename stem alone (`config/extensions/<--extension value>.yaml`), unlike machine/device YAML's `id` |
| `slot` | int | `2` | must be `2` — the only slot `--extension` can reach |
| `shape` | string | `flat` | `flat` or `expanded` |
| `rom_base` | string | `""` (project root) | base directory for every `rom:` block below |

#### Flat shape (`shape: flat`, or omitted)

```yaml
device: fmpac
rom: {file: fmpac.rom, size_kb: 64}
sram: {size_kb: 8, save_file: saves/sram/fmpac.sram}
```

`device` must be `fmpac` or `scc_i_cart` (`_KNOWN_EXTENSION_DEVICES`) — a
separate namespace from the expanded shape's sub-slot devices below, even
though both are spelled `device`. `fmpac` requires a `rom:` block;
`scc_i_cart` does not (its RAM starts blank, no file loaded). A flat
overlay's device unconditionally replaces primary slot 2's mapper wholesale.

#### Expanded shape (`shape: expanded`)

```yaml
shape: expanded
slot: 2
rom_base: roms/hbi_j1
subslots:
  0:
    device: halnote
    rom: {file: hbi-j1_msx-je.rom, size_kb: 1024, sha1: null}
    sram: {size_kb: 16, save_file: saves/sram/hbi-j1_msx-je.sram}
  1:
    device: flat_rom
    rom: {file: hbi-j1_kanjibasic.rom, size_kb: 32, sha1: null}
io_device:
  device: kanji_rom
  rom: {file: hbi-j1_kanjifont.rom, size_kb: 256, sha1: null}
```

`subslots` is a required, non-empty mapping keyed `0`–`3` (same index range
and `_check_subslot_index` as an MSX2 slot-3 sub-slot). Each entry's
`device` must be one of `halnote`, `flat_rom`, `ram_mapper`, `ascii8`,
`ascii16` (`_KNOWN_EXPANDED_SUBSLOT_DEVICES`):

| `device` | Requires | Constructs |
| --- | --- | --- |
| `halnote` | `rom:` | `HalnoteMapper` (1 MB ROM, optional `sram:`) |
| `flat_rom` | `rom:` | `FixedPageMapper(base=0x4000)` — visible only at 0x4000–0xBFFF |
| `ascii8` | `rom:` | `Ascii8Mapper` |
| `ascii16` | `rom:` | `Ascii16Mapper` |
| `ram_mapper` | `size_kb:` (directly on the entry, no `rom:`) | `RamMapper`; `size_kb` must be a positive multiple of `16` |

At most one `ram_mapper` sub-slot is allowed per overlay — a second one
would double-register the standard memory-mapper I/O ports (0xFC–0xFF).
Applying an expanded overlay to an MSX1 machine always raises
`MachineLoadError` (no MSX1-standard hardware matches a memory-mapper
sub-slot, and every current expanded overlay models MSX2-era hardware). A
`ram_mapper` sub-slot also conflicts with a machine that already has its
own slot-3 RAM mapper (`has_ram_mapper=True`) — two mappers would fight
over the same ports.

The optional top-level `io_device` is validated against a *third*, disjoint
device namespace, `_KNOWN_IO_DEVICES = {kanji_rom}` — `device: kanji_rom`
is invalid inside `subslots`, and `device: halnote` (or any sub-slot
device) is invalid inside `io_device`. If both a machine-level `io_device`
and an overlay's `io_device` resolve to `kanji_rom` at once, `build_machine`
raises (ports 0xD8–0xDB cannot serve two Kanji-ROM devices).

#### `rom:` and `sram:` blocks

Both shapes share the same nested block schemas, parsed by
`_parse_rom_entry` (also used for slot 0's ROMs, the MSX2 SUB ROM, and the
FDC's DISK ROM — one shared implementation for every ROM-bearing field in
the loader):

| `rom:` field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | string | yes | resolved against `rom_base` |
| `size_kb` | int | no (default `0`) | documentation only — not checked against the actual file size |
| `pages` | list of int | no (default `[]`) | only meaningful for slot 0 and the SUB ROM, where it selects which entry is the main/logo/SUB ROM |
| `sha1` | string | no | `null`/omitted disables verification |

| `sram:` field | Type | Notes |
| --- | --- | --- |
| `size_kb` | int | documentation only — ignored; each device's SRAM size is a Python-side constant (`HALNOTE_SRAM_SIZE`, `FMPAC_SRAM_SIZE`, ...) |
| `save_file` | string | path (relative to the project root) the SRAM is persisted to; omit for no persistence |

## Machine YAML loader

### Two-pass resolution

`msx/machine_loader.py` resolves hardware topology in two passes:

1. **Device registry pass.** `load_device_registry(config_dir)` reads every
   `*.yaml` file under `config/devices/` into a dict keyed by device `id`. Each
   file describes one piece of hardware (VDP type, PSG, PPI, RTC, RAM mapper) in
   isolation — no machine context.

2. **Machine spec pass.**
   `load_machine_spec(machine_id, config_dir, device_registry, project_root)`
   loads `config/machines/<machine_id>.yaml`, validates required fields,
   resolves every `builtin_devices` `ref` against the registry, and returns a
   `MachineSpec` dataclass. An unresolved `ref`, unknown `schema_version`, or
   missing required ROM entry raises `MachineLoadError` naming the file and
   field.

### build_machine()

`build_machine(spec, cartridge, mapper, ...)` constructs a `Machine` from a
resolved `MachineSpec`. The `spec.generation` field determines the concrete
types:

- `"msx1"` — `VDP` (TMS9918A), flat slot 3 RAM, no RTC or RAM mapper
- `"msx2"` — `V9938`, expanded slot 3 with sub-ROM and `RamMapper`, `RTC`

Device YAML entries with `implemented: false` are skipped at load time with a
stderr warning; the rest of the machine proceeds normally, so a device
definition can be committed before its emulation exists without breaking boots.
Every device currently under `config/devices/` is `implemented: true`.

---

## Portability

The implementation is pure Python 3.10+ and carries no C extensions or native
bindings beyond the SDL2 frontend. Several design decisions were made to keep
the core logic straightforward to port to a statically-typed systems language
(Rust, C++). Each decision is documented inline as a _Portability note_ in the
relevant source file; this section summarises them.

### Intentionally portable patterns

These patterns translate cleanly to any statically-typed target:

- **Opcode dispatch** (`msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`) —
  `_DISPATCH` is a flat 256-element list of callables built once at import. Each
  entry is a plain function. A Rust port maps this to an array of function
  pointers or a `match` over the opcode byte.
- **Register file** (`msx/cpu/registers.py`) — primary registers are stored as
  Python `int` fields with explicit `& 0xFF` / `& 0xFFFF` masking at every write
  site, not as arbitrary-precision values. Shadow registers are discrete named
  fields (`A_`, `BC_`, etc.) with no reflection.
- **VRAM and RAM** — `bytearray` objects of fixed size (`_VRAM_SIZE = 131072`
  for V9938, `16384` for TMS9918A, segment arrays for the RAM mapper). Direct
  slice-and-index access throughout; no list-of-ints or dictionary-backed
  storage.
- **I/O bus dispatch** (`msx/io.py`) — linear scan over a
  `list[tuple[int, int, Callable]]`. No dictionary lookup or reflection. The
  number of registered handlers is small and fixed per machine configuration.
- **Component structs** — every hardware component is a
  `@dataclass(slots=True)`. All fields are explicitly declared with types; no
  `__getattr__`/`__setattr__` magic. `slots=True` avoids the `__dict__` per
  instance and makes the field layout explicit.
- **Timing constants** — `CYCLES_PER_FRAME`, `_TSTATES_PER_LINE`,
  `_HBLANK_START`, `_CYCLES_PER_BYTE` etc. are module-level integer constants,
  not derived at runtime.

### Python-specific patterns a port must adapt

The following patterns rely on Python language features that have no direct
static-typed analogue. Each is documented in the source with an explicit note on
what a Rust/C++ port would use instead.

#### Bus hooks as reassignable bound methods

`Z80` stores `read_byte` / `write_byte` / `read_port` / `write_port` as
`Callable` fields. `Machine.__post_init__` wires them by assigning bound methods
at runtime. Enabling watchpoints later re-swaps `cpu.read_byte` /
`cpu.write_byte` between the plain memory handler and a watchpoint-trapping
variant `Machine._read_with_watch` / `_write_with_watch`, in
`Machine.set_watchpoints`.

Python allows this because a `Callable` field is just a slot that holds any
object with `__call__`. In Rust/C++ there is no runtime method swap on a struct
field; a port expresses the bus as a `trait MemoryBus` (or an
`enum { Normal, Watchpoint }`) whose concrete implementation is selected once
behind a flag, so the per-access dispatch stays branch-free.

#### Register 8-bit halves as computed properties

`Registers` stores `BC`, `DE`, `HL` as 16-bit `int` fields but exposes `B`/`C`,
`D`/`E`, `H`/`L` as `@property` getter/setter pairs that shift and mask over the
16-bit pair (`Registers.B`/`C` and their siblings). Each opcode that reads or writes a single 8-bit
half goes through the descriptor protocol, adding a call frame on the hot
instruction path.

A Rust/C++ port stores the 8-bit halves as plain `u8` fields (or as explicit
inline getter/setter methods) and derives the 16-bit pairs on demand, avoiding
the property-call overhead entirely.

#### Arbitrary-precision integers in the PSG envelope

Python integers are arbitrary precision: `-1 & 0x20 == 0x20`. The PSG envelope
generator uses this when `_env_step` goes negative — bit 5 of a negative Python
int still acts as an underflow flag (`PSG._env_step`).

Rust/C++ unsigned types wrap on underflow rather than extending sign, so the
same expression produces 0 instead of `0x20`. A port must use a signed type
(`i32`) or an explicit `wrapping_sub` / bitfield overlay to preserve the flag.

#### `bytes.translate()` LUT cache in the renderer

The V9938 G4/G6 and G5 per-scanline pixel expanders memoize `bytes.translate()`
tables in `_G46_LUT_CACHE` / `_G5_LUT_CACHE`, dicts keyed on
`(tp: bool, border: int)`.
`bytes.translate()` is a CPython built-in that unpacks a full 256-byte lookup
table in C, making it the fastest available path in Python for this
transformation. The dict cache avoids allocating identical tables on every
scanline.

Neither `bytes.translate()` nor a per-call dict hash are natural constructs in a
systems language. A Rust/C++ port keeps fixed `[u8; 256]` arrays precomputed at
init — only 32 combinations exist across all `(tp, border)` pairs — and indexes
them directly.

#### Callable interrupt and tracer hooks

Both `VDP` (TMS9918A) and `V9938` store `on_interrupt`, `tracer`, `_get_pc`, and
`_get_cycle` as nullable `Callable` fields. These
are assigned at wiring time and invoked on each relevant event. The fields are
typed as `Callable[[], None] | None` in Python but have no direct static
analogue.

A Rust/C++ port models them as `Option<Box<dyn Fn()>>` trait objects (or
equivalent), or as feature-flagged compile-time generics so the per-call
dispatch disappears in release builds.

#### Spin loop in the frame timer

`FrameTimer.tick()` uses `time.perf_counter()` in a busy-wait loop for the final
sub-millisecond stretch before the frame deadline. In
Python there is no way to hint to the scheduler that this is a spin.

A Rust port inserts `std::hint::spin_loop()` inside the same loop to yield the
CPU pipeline hint without sleeping, which can reduce power consumption and
improve timing jitter on SMT cores.
