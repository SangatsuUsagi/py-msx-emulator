# Technical Implementation

[← README.md](../README.md)

This document describes the internal structure of the MSX1/MSX2 emulator: CPU
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
- [Core boundary and porting guide](#core-boundary-and-porting-guide)
- [Portability](#portability)

---

## CPU emulation

### Instruction decode

`msx/cpu/z80.py` implements the Z80 CPU. The opcode dispatch table `_DISPATCH`
is a flat list of 256 callables. `opcodes_main._build_dispatch()` in
`msx/cpu/opcodes_main.py` builds it once at import time, and `z80.py` binds it
as a module-level constant:

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

The execution of an instruction is a single indexed call, with no dictionary
lookup and no match/case:

```python
n = _DISPATCH[opcode](cpu)
```

The CB, DD, ED, and FD prefix tables use the same pattern. When the main
dispatcher finds a prefix byte, it fetches the next byte and dispatches into
the related prefix table.

### Execution loop

`Z80.step() -> int` executes one instruction. All costs below are in T-states,
the basic clock-cycle unit of the Z80:

1. If `nmi_pending` is set, push PC, jump to 0x0066, and return 11 T-states.
2. If `iff1` and `int_pending` are both true and `ei_pending` is not set,
   accept the interrupt. Mode 1: push PC, jump to 0x0038, 13 T-states.
   Mode 2: push PC, read the vector byte from `I<<8 | data_bus`, jump to the
   handler address, 19 T-states.
3. If `ei_pending` is set, clear it. EI enables interrupts only after the
   instruction that follows it. Step 2 checks `ei_pending` and this step
   clears it, so an interrupt that step 2 would accept waits for exactly one
   step.
4. If `halted` is set, advance `4 + m1_wait_states` T-states without an opcode
   fetch. The value is 4 on a pure Z80 core, and the MSX M1 wait also applies.
5. Otherwise, record the current PC in `instruction_pc`, fetch the opcode with
   `_fetch_opcode()`, and dispatch.

`_fetch()` reads one byte from `read_byte(PC)` and increments PC.
`_fetch_opcode()` does the same and also increments R, once per M1 cycle: the
primary opcode and the opcode byte after a CB/DD/ED/FD prefix. Operand and
displacement bytes, including the displacement and final opcode of
`DDCB`/`FDCB`, do not touch R. Only bits 0-6 of R count. Bit 7 is sticky and
changes only through a full register write such as `LD R,A`. A real SONY
HB-F1XD and HB-10 give the same R steps.

### Timing

`step()` returns the number of T-states that the instruction used.
`Machine.run_frame()` adds these values to a per-frame total and to
`machine.cycle_count`. The NTSC frame budget is **59,736 T-states**: 228
T-states per scanline × 262 lines per frame. The 228 T-states per scanline is
the real TMS9918A/V99x8 raster timing. The shared raster clock model of openMSX
confirms this value for both VDP chip families.

This budget is different from the Z80A clock (3.579545 MHz) divided by a flat
60 Hz. The field rate of real MSX NTSC hardware is ~59.92 Hz, not exactly
60 Hz. PAL machines use 313 lines per frame, a budget of 71,364 T-states (see
`video_standard` in [Machine YAML](#machine-yaml-configmachinesyaml)).

#### MSX M1 wait state

A real MSX adds one wait state to every Z80 M1 cycle (opcode fetch). The core
models this with a configurable `Z80.m1_wait_states` field. The default is
`0`, a pure datasheet Z80, so the core stays reusable for non-MSX systems. MSX
machine YAMLs set `cpu.m1_wait_states: 1`, and `machine_loader` passes this
value to the `Z80(...)` constructor.

The core adds the wait per M1 cycle, not per instruction byte. `step()` adds
`m1_wait_states` for the primary opcode fetch. Each prefix handler
(`_op_prefix_cb/dd/fd/ed`) adds it again for the second M1. Thus:

- An unprefixed instruction gets `+1`, whatever its operand length.
- A `CB`/`ED`/`DD`/`FD` instruction gets `+2`.
- `DDCB`/`FDCB` gets `+2`, because the displacement and the final opcode are
  operand reads, not M1 cycles.

This matches openMSX (`Z80.hh` `WAIT_CYCLES = 1`, M1 base 5 vs datasheet 4).
The frame budget stays at 59,736 T-states, so the wait decreases the number of
instructions per frame. Code with timing from the CPU loop (e.g. software-PCM
audio from a delay loop) thus runs at the correct hardware rate, not ~13% fast.
The wait has no effect on the interrupt cadence (VBlank) or the PSG pitch.

### Known limitations

OTIR/INIR and similar block I/O instructions are not cycle-exact across page
boundaries. The undocumented flag bits Y (bit 5) and X (bit 3) follow the
documented per-instruction rules, including three less common sources:
`BIT n,(HL)` takes them from the internal MEMPTR register, a repeating
`LDIR`/`LDDR`/`CPIR`/`CPDR` takes them from its own address, and `SCF`/`CCF`
depend on whether the previous instruction changed the flags. `SCF`/`CCF`
follow the Zilog NMOS rule; NEC and ST CMOS Z80s differ
([Undocumented Flags](https://github.com/hoglet67/Z80Decoder/wiki/Undocumented-Flags)).
The emulator targets a Zilog-compatible Z80. TODO: record how the NEC and
Sharp Z80-compatible CPUs used in some MSX machines (for example the HB-10
and HB-F1XD) differ, once tested on real hardware. The
repeating `INIR`/`INDR`/`OTIR`/`OTDR` keep the simpler per-iteration flags.

---

## Interrupt management

### MSX1: level-based VBlank IRQ

On MSX1, the TMS9918A VDP has a single interrupt source: VBlank, which the VDP
raises at the start of vertical blanking. Its interrupt line is the level
`F && IE0` (S#0 bit 7 and R#1 bit 5). `VDP.irq` caches this level, and the VDP
calculates it again when either input changes:

- `VDP.begin_vblank()` sets F after `Machine.run_frame()` renders the frame at
  the `display_height` scanline boundary. It does not set F for a frame that a
  break or pause cuts short.
- A read of S#0 clears F.
- A write to R#1 changes IE0.

If a program sets IE0 while F is still pending, the line thus goes high
immediately, as on real hardware. `Machine.run_frame()` samples the line at
every instruction boundary, in the same way as the MSX2 path below:

```python
cpu.int_pending = vdp.irq
```

The MSX1 path has no per-scanline interrupts.

### MSX2: level-based IRQ

On MSX2 (V9938), interrupts are level-based. The inner loop of
`Machine.run_frame()` samples the `irq` of the VDP at every instruction
boundary. It uses the same `cpu.int_pending = vdp.irq` line as the MSX1 path
above.

`V9938.irq` gives the result of `irq_pending()`:

```python
def irq_pending(self) -> bool:
    ie0 = bool(self.regs[1] & 0x20)   # IE0 = R#1 bit 5
    f   = bool(self.status & 0x80)     # F   = S#0 bit 7 (VBlank)
    ie1 = bool(self.regs[0] & 0x10)   # IE1 = R#0 bit 4
    fh  = bool(self._status1 & 0x01)  # FH  = S#1 bit 0 (H-line)
    return (ie0 and f) or (ie1 and fh)
```

The CPU sees a true interrupt level until a read of port 0x99 clears the status
flag, as on real hardware. A read of S#0 (R#15 = 0) clears F. A read of S#1
(R#15 = 1) clears FH.

### H-line interrupt

`Machine.run_frame()` divides the frame into scanlines. It calls
`vdp9938.begin_scanline(L)` once at the end of the T-state budget of each
scanline. In outline (`Machine._run_lines_fast`):

```python
for line in range(first_line, end_line):   # 262 NTSC lines per frame
    line_end = (line + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()                     # or step_halted_batch(), below
        total += n
        if vdp9938._cmd_active:
            vdp9938.tick(n)                # command engine running
        else:
            vdp9938._line_cycle += n       # horizontal-retrace timer only
    vdp9938.begin_scanline(line)
```

The CPU sees a level change from `begin_scanline` at its next instruction
boundary.

The loop runs in two segments: the lines up to and including
`vblank_start_line`, then the rest of the frame. The machine renders the
frame between them, before the VBlank interrupt handler runs (see
[VDP](#vdp)). While the CPU is halted and cannot take an interrupt, the loop
calls `Z80.step_halted_batch()` instead of `step()`. One call advances the
whole run of halted M1 cycles up to the end of the line, with the same R value
and T-state total. The loop does not batch while a V9938 command runs,
because the command engine can raise an interrupt partway through the line.

`begin_scanline(line)` calculates the effective IRQ line as
`(R#19 − R#23) & 0xFF`. It sets S#1 bit 0 (FH) when `line == effective_irq_line`
and IE1 (R#0 bit 4) is set. The compare can target any line 0-255, including
the border and vertical-blanking lines. The VDP does not latch FH while IE1 is
clear. Thus, if a program sets IE1 later, no interrupt occurs for a line that
has already passed.

FH stays set until a read of S#1. This read clears FH and evaluates `irq`
again.

`begin_scanline(vblank_start_line)` also sets the VBlank flag.
`vblank_start_line` is the active line count (192 or 212), so this is the
first line after the active display. It replaces the old per-frame finalize path for
MSX2.

---

## I/O bus

### Registration and dispatch

`IOBus` (`msx/io.py`) holds two lists of `(start, end, handler)` tuples: one for
reads and one for writes. Devices register themselves during machine
construction:

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` and `write_port` mask the incoming 16-bit Z80 port address to 8 bits
(`port &= 0xFF`) and do a linear scan. The first registered handler whose
`[start, end]` range includes the port wins. If no handler matches, reads
return 0xFF (open bus).

### Logging

When a `_logger` is attached, the bus logs a read after the handler returns,
because the value is known only then. It logs a write before the dispatch,
because the call site already sets the value. Both log entries record the port,
the value, and the current instruction PC.

### Standard port map

| Port(s)   | Device                                   |
| --------- | ---------------------------------------- |
| 0x98-0x9B | VDP (TMS9918A or V9938)                  |
| 0x9A      | V9938 palette (MSX2 only)                |
| 0xA0-0xA2 | PSG (AY-3-8910)                          |
| 0x7C-0x7D | FM-PAC OPLL (with `--extensionN fmpac`)  |
| 0xA8-0xAB | PPI (i8255)                              |
| 0xB4-0xB5 | RTC (RP5C01, MSX2 only)                  |
| 0xD8-0xDB | Kanji ROM (`hbi_j1` and similar extensions) |
| 0xFC-0xFF | RAM mapper segment registers (MSX2 only) |

---

## VDP

### TMS9918A (MSX1)

`msx/vdp/vdp.py` implements the TMS9918A, and `msx/vdp/renderer.py` holds its
renderer. It has 16 KB VRAM and 8 control registers, and it supports screen
modes 0-3 (Text, Graphic 1/2, Multicolor).

`render_frame()` renders the 256×192 active frame partway through
`Machine.run_frame()`, at the start of vertical blanking. `Machine.run_frame()`
then calls `VDP.begin_vblank()` to set the VBlank flag and update the interrupt
line (`VDP.irq`). The renderer pads the frame to a constant 256×212 output (10
border rows at the top and bottom) through the shared `msx/vdp/_geometry.py`
helper. Every frame thus keeps a stable 4:3 geometry, whatever the active line
count of the VDP is (see V9938 `display_height` below).

### V9938 (MSX2)

`msx/vdp/v9938.py` implements the V9938, and `msx/vdp/v9938_renderer.py` holds
its renderer. These are the main differences from the TMS9918A:

- 128 KB VRAM
- 28 control registers (R#0-R#27) plus 15 command-engine registers (R#32-R#46)
- Programmable 16-color palette (9-bit GRB333). The `_MSX2_DEFAULT_PALETTE`
  that the VDP loads at reset matches the power-on values in the V9938 data
  book
- Three status registers: S#0 (VBlank/sprite), S#1 (H-line FH), S#2 (command
  engine CE/TR, retrace HR/VR)
- Hardware command engine
- Level-based IRQ through `irq_pending()`

The `display_height` property returns 192 normally, or 212 when R#9 bit 7 (LN)
is set. The `display_width` property returns 512 for these modes, and 256 for all
other modes:

- The wide modes SCREEN 6/G5 and SCREEN 7/G6 (M5 set, M4 clear).
- TEXT2, SCREEN 0 WIDTH 80 (M1 and M4 set, M3 clear).

### Banded renderer

Some games change VDP registers mid-frame, for example to switch the palette
or the screen mode between display regions. The renderer must apply these
changes at the correct scanline.

`V9938.write_port()` records every write to a display-relevant register in
`_reg_write_log`:

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 18, 19, 23})
```

Each entry is one of two types (`msx/vdp/_v9938_defs.py`), and readers tell
them apart by type:

- `_RegChange(line, reg, value)` for a register write
- `_PaletteChange(line, idx, rgb)` for a palette write (port 0x9A). `rgb` is
  the 9-bit entry as the palette stores it, `r << 6 | g << 3 | b`

`begin_scanline(0)` (frame start) clears the log. `render_frame()` does not
clear it. `V9938.to_rgb24()` thus can use the log when it converts the frame
to RGB24 inside the core, to apply the correct palette per scanline.

`render_frame_v9938(vdp)` examines `_reg_write_log` to find band boundaries.
A band boundary is a line where a display-relevant value differs from the
snapshot of the previous band. The renderer renders each contiguous band
`[y0, y1)` with the register state at its start. It calls the per-band
renderers with explicit `y_start` and `y_end` parameters. Each scanline gets
exactly one render across all bands.

When the log is empty (no mid-frame writes), `render_frame_v9938` uses a
single-pass render. Its output and performance are the same as the path before
banding.

The renderer draws sprites once per sprite attribute table (SAT) region. A SAT
region is a maximal run of consecutive bands with the same SAT base
(R#5/R#11). When the SAT base stays the same across the frame (the usual
case), there is exactly one sprite pass, as in a single-pass render. When the
SAT base changes mid-frame (e.g. a sprite multiplexer), the renderer draws the
sprites of each region from the SAT of that region. Different regions thus
read different buffers, and the same sprite does not appear twice.

### Command engine

The V9938 hardware command engine supports byte-level and pixel-level VRAM
operations:

| Command | Description                                              |
| ------- | -------------------------------------------------------- |
| HMMV    | Fill a rectangle with a byte value                       |
| HMMM    | Copy a rectangle (byte granularity)                      |
| HMMC    | Transfer a block from CPU to VRAM (byte granularity)     |
| LMMV    | Fill a rectangle with a color value (pixel granularity)  |
| LMMM    | Copy a rectangle (pixel granularity)                     |
| LMCM    | Transfer a block from VRAM to CPU                        |
| LMMC    | Transfer a block from CPU to VRAM (pixel granularity)    |
| YMMM    | Copy a rectangle using Y-axis only (byte granularity)    |
| LINE    | Draw a line                                              |
| PSET    | Write a single pixel                                     |
| POINT   | Read a single pixel value (result in S#7)                |
| SRCH    | Search a scanline for a color match (result in S#8/S#9)  |
| ABRT    | Abort the active command                                 |

The engine executes each command immediately. To model the command duration,
`V9938.tick(n)` decrements an approximate cycle budget (`_cmd_remaining`).
While a command runs, the machine calls `tick(n)` after each instruction with
the T-states that the instruction used. While no command runs, the machine
only adds the T-states to `_line_cycle`, the horizontal-retrace timer that
`tick(n)` also advances (see [H-line interrupt](#h-line-interrupt)). Software
that busy-waits on S#2 bit 0 (CE) sees CE clear after the budget expires.

Each block command has its own cost per element, in T-states:

| Constant | Cost | Unit |
| --- | --- | --- |
| `_HMMV_CYCLES_PER_BYTE` | 10 | per VRAM byte |
| `_HMMM_CYCLES_PER_BYTE` | 21 | per VRAM byte |
| `_YMMM_CYCLES_PER_BYTE` | 18 | per VRAM byte |
| `_LMMV_CYCLES_PER_DOT` | 21 | per dot |
| `_LMMM_CYCLES_PER_DOT` | 30 | per dot |

These values come from timing full-page commands on a real SONY HB-F1XD with
the display and sprites on, the slowest case. The emulator does not model the
speed-up with less display or sprite VRAM traffic.

A write to R#44 (COL) sets the HMMC/LMMC transfer latch (`_cmd_transfer`). The
first pixel or byte of a transfer comes from a pending COL write. The engine
does not load it automatically when the command starts. This matches the
openMSX behavior.

`_apply_log()` in `v9938.py` applies the logical operations (IMP, AND, OR,
XOR, NOT) per pixel.

### Known limitations

- Command timing is approximate. The engine uses one measured cost per element
  (see above). It does not use the per-command cycle counts of the V9938 data
  book.
- The engine writes the VRAM results when the command starts, not
  incrementally. Software that reads VRAM while a command is nominally in
  progress can see the completed result before CE clears.
- The emulator renders each frame after the CPU runs it. The CPU runs a full
  frame, VDP commands write into VRAM instantly, and then a single render pass
  covers the whole frame. Both
  the V9938 and the TMS9918A do this render pass at the start of vertical
  blanking. The emulator does not faithfully reproduce VRAM updates that
  follow the raster within a frame (beam-raced blits, double-buffered title
  screens). The banded renderer above recovers mid-frame *register* changes,
  but not mid-frame VRAM content.

---

## Audio

Five sources render audio: the PSG (`msx/psg.py`), the SCC (`msx/scc.py`), the
OPLL of the FM-PAC (`msx/opll.py`), the Majutsushi DAC, and the PPI key click.
Each one renders signed 16-bit mono PCM at 44,100 Hz. A frame has 735 samples
on NTSC machines and 882 on PAL machines. At the end of every
`Machine.run_frame()`, `AudioMixer` (`msx/audio_mixer.py`) adds the outputs of
the chips that the machine has and clamps the result to 16 bits. This happens
with or without a frontend, so the chips advance the same way in a headless
run. The SDL2 frontend gets the buffer in `step_frame`'s `FrameOutput` (see
[Frame stepping](#frame-stepping)), passes it through the output low-pass filter, and
queues it to SDL. The machine holds
only the latest frame, so audio that nobody takes does not accumulate.

The Majutsushi DAC and the key click (PPI Port C bit 7) share one write-driven
DAC channel, `EventDac` in `msx/dac.py`. The owner converts each write to a
signed 16-bit level and passes it with the cycle of the write. A write that
does not change the level records nothing. `generate_samples` places each
change at sample `(cycle - frame_start) * n // (frame_end - frame_start)` and
holds the level until the next change. When a frame has no change and the
level is 0, it returns `None`, and the mixer mixes nothing for that
channel.

### Sub-frame software PCM

`PSG.generate_samples(n, frame_start, frame_end)` reproduces software PCM that
a program plays through rapid volume-register writes:

1. `write_port` records each register write as `(cycle, reg, value)`. It
   reads the cycle from `machine.cycle_count` through its `_machine`
   reference. The fast frame loop adds the T-states of each scanline to
   `cycle_count` at the end of the line. The position of a write thus has a
   resolution of one scanline (228 T-states, about 2.8 samples).
2. `generate_samples` goes back to the register and generator state at the
   first write of the frame.
3. It renders the buffer again as consecutive segments. Each write starts a
   new segment at the sample position
   `clamp((cycle - frame_start) * n // (frame_end - frame_start), 0, n)`.
4. The tone, noise, and envelope state carry across segments.

A frame with no recorded writes uses an unchanged single-snapshot fast path.
Its output is byte-for-byte the same as the old behavior, so normal music has
no extra cost.

### Tone integration (period-0 PCM carrier)

Software PCM keeps the tone generator enabled at tone period 0. This gives an
ultrasonic (~223 kHz) carrier, which the real analog output filters to a ~50 %
duty average. The volume register thus carries the PCM. If the emulator
point-sampled `tone_out` at 44.1 kHz, the carrier would alias into a full/zero
chop, and approximately half the samples would be zero.

The emulator thus **integrates the tone of each channel over the output
sample**. The inner loop counts the PSG ticks during which the square wave was
high (`hi0/hi1/hi2`). The mixer then scales the channel amplitude by
`hi / ticks`. For audible tones (no toggle within a sample), `hi` is `ticks` or
`0`, so the result is bit-for-bit the same as point-sampling. Only the
ultrasonic carrier changes: it becomes its band-limited duty average.

### Output low-pass filter

`frontend/audio_filter.py` (`BiquadLowPass`) is a stateful 2-pole Butterworth
low-pass filter (8 kHz, RBJ coefficients, Direct Form I). The frontend applies
it to each frame's PCM from the machine. It models the analog RC filter on the
real MSX audio output. It uses floating point, so it stays out of the core:
the core's audio output is the same on every host. The frontend owns a single
instance, which carries its `x1/x2/y1/y2` state across frames. The reset
hotkey clears it.

The filter removes the residual high-frequency imaging and aliasing that
point-sampling synthesis leaves near Nyquist. Its cost is ~1 % of the
per-frame audio-path cost. The audible band and the overall level stay almost
the same, because the energy of real PSG music is far below the cutoff.

## Memory and slot system

### MSX1 slot layout

`Memory` (`msx/memory.py`) maps the flat 64 KB address space through a 4-page ×
4-slot dispatch. The slot-select register (port 0xA8, through the PPI) selects
the slot for each 16 KB page. Bits `[2N+1 : 2N]` of the register select the
slot for page N.

Default MSX1 layout:

| Slot | Content                                   |
| ---- | ----------------------------------------- |
| 0    | BIOS ROM (read-only). Some machines add 16 KB RAM at page 3 (e.g. `hb_10`) |
| 1    | Cartridge ROM via mapper                  |
| 2    | Second cartridge or open bus (reads 0xFF) |
| 3    | 32 KB RAM at pages 2-3 (0x8000-0xFFFF). Each machine sets the size |

### MSX2 expanded sub-slot

On MSX2, slot 3 is expanded (`sub_slot_enabled = True`). A read of 0xFFFF
returns `~sub_slot_reg`, so software can detect sub-slot support. The sub-slot
register (`sub_slot_reg`) selects which of the four secondary slots occupies
each page within the expanded primary slot.

Default MSX2 layout:

| Sub-slot | Content                    |
| -------- | -------------------------- |
| 3-0      | C-BIOS Sub ROM (read-only) |
| 3-1      | Empty                      |
| 3-2      | RAM via RAM mapper (128 KB default) |
| 3-3      | Empty                      |

### RAM mapper

`RamMapper` (`msx/ram_mapper.py`) divides its RAM into 16 KB segments. The size
is a positive multiple of 16 KB, set by `size_kb` in the machine or extension
YAML. The default is 128 KB (8 segments). `hb_f1xd_256k` uses 256 KB, and the
512 KB extensions use 512 KB. Four segment registers at ports 0xFC-0xFF
independently select the visible segment in each CPU page:

| Port | Page | Address range |
| ---- | ---- | ------------- |
| 0xFC | 0    | 0x0000-0x3FFF |
| 0xFD | 1    | 0x4000-0x7FFF |
| 0xFE | 2    | 0x8000-0xBFFF |
| 0xFF | 3    | 0xC000-0xFFFF |

A write of a segment number to a register remaps that page immediately. The
mapper masks the number with `bank_mask`: the smallest power of two that covers
the segment count, minus 1 (7 for 128 KB). A read returns the segment number
with the unused upper bits set to 1.

---

## Floppy disk (FDC)

The floppy subsystem is a generic layer (`msx/fdc/`). A new controller chip or
connection style connects to this layer without changes to `Memory`. At
present, the layer has two controllers and two connection styles: the WD2793
with the Sony/Philips style, and the TC8566AF with its own style.

| Layer      | File                             | Responsibility                                                                                                                                                                                                                                                    |
| ---------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Disk image | `disk_image.py` (`DskDiskImage`) | Holds a `*.dsk` sector image that the host made from the bytes of the file. Gets the geometry from the BPB of the FAT12 filesystem (BIOS Parameter Block: bytes/sector, total sectors, sectors/track, heads). Buffers writes and marks the image dirty. `host/media.py` writes a dirty image back to its file on exit, before a save state, and when the disk leaves its drive |
| Drive      | `disk_drive.py` (`DiskDrive`)    | One physical drive. Holds the mounted image, tracks the head position, and gives sector read/write                                                                                                                                                                  |
| Controller | `wd2793.py` (`WD2793`), `tc8566af.py` (`TC8566AF`) | The FDC chip. `WD2793`: Type I-IV command decode, status register, data/track/sector registers, `abort()`. `TC8566AF` (uPD765 family): Command/Execution/Result phase model, Main Status Register, non-DMA transfers, no directly addressable track/sector register |
| Interface  | `interface.py`                   | Connection style between the controller and the memory bus. Both styles map the DISK ROM at 0x4000-0x7FFF. `SonyPhilipsInterface` (= openMSX PhilipsFDC) puts the WD2793 registers at 0x7FF8-0x7FFF and uses the disk-change bit. `TC8566AFInterface` puts two control registers, the Main Status Register, and the Data Register at 0x7FF8-0x7FFB. Both styles share `swap()`, which mounts and ejects disks at runtime |

The `fdc:` block in the machine YAML declares the controller and the connection
style of a machine. The two shipped floppy machines thus differ only in data:

- The Sony HB-F1XD (`hb_f1xd`) puts the WD2793 and its DISK ROM in slot 3
  sub-slot 0, with 64 KB of flat RAM in sub-slot 3.
- The Panasonic FS-A1F (`fs_a1f`) uses the TC8566AF and spreads the roles
  across its four secondary slots: RAM in sub-slot 0, SUB ROM in 1, and the
  FDC in 2.

`--fdd1`/`--fdd2` mount images into drives A/B. `--fdd1` can take a list of
images. Three paths swap disks at runtime. All of them call `swap_disk()` in
`host/media.py`. It first writes a dirty outgoing image back to its file, and
then calls `Machine.mount_disk()`, which calls the `swap()` of the floppy
interface:

- The F6/Shift+F6 hotkeys of the SDL2 frontend step through the `--fdd1`
  list, or eject and insert the drive A disk again.
- The `fdd1`/`fdd2` commands of the debugger.
- The `fdd.swap` method of the socket RPC server.

The implementation boots Disk BASIC, supports `CALL FORMAT`, and reads and
writes files. The host writes the changed sectors back to the `.dsk` file on
exit, before a save state, and when the disk leaves its drive.
`machine.fdc` is `None` on machines with no floppy interface.

## Machine and extension YAML schema

`host/machine_config.py` reads three YAML surfaces: device definitions
(`config/devices/*.yaml`), machine specifications (`config/machines/*.yaml`),
and extension overlays (`config/extensions/*.yaml`). All three are `TypedDict`
shapes that the loader validates by hand (`DeviceEntryYaml`,
`MachineEntryYaml`, `ExtensionOverlayYaml`, and their nested shapes). No
schema library validates them. The closed sets of names (mappers, FDC
controllers and connection styles, extension, sub-slot and I/O devices) are
defined in `msx/machine_config.py`. The YAML loader imports them under its
`_KNOWN_*`/`_SUPPORTED_*` names, and the core checks the same sets again when
it parses the MachineConfig document.

Each field in this section matches an explicit `.get()` call in the loader. A
key that the loader does not read is documentation only, whatever the YAML
comment next to it says. For the user-facing summary of what each primary slot
can hold, see
[the "Slot model" section of README_extension.md](README_extension.md#slot-model).
This section documents the YAML syntax that gives that result.

### Device YAML (`config/devices/*.yaml`)

| Field | Type | Required | Read by |
| --- | --- | --- | --- |
| `id` | string | yes | Must equal the filename stem, or `load_device_registry` raises `MachineLoadError` |
| `type` | string | yes | The loader checks that the key is present, but never checks the value (`io_device`, by convention) |
| `implemented` | bool | no (default `true`) | `false` skips the device during `builtin_devices` resolution, with a stderr warning and no hard failure. A device definition can thus land before its emulation |
| `io_ports` | list of int | no | The first and last elements become the `(start, end)` I/O range of the device. If the key is absent, the loader uses `_DEFAULT_IO_PORTS` |

`_DeviceDef.raw` stores every other key (`name`, `chip`, `controls`, `vram_kb`,
`segment_size_kb`, `keyboard_type`, ...), but generic code does *not* read
them. In the whole codebase, only three keys go through `dev.raw.get(...)`:
`io_ports` above, and `keyboard_type` and `control_read` below (`ppi8255`
only).

Nothing reads `vram_kb: 128` in `vdp_v9938.yaml`. The VRAM size of the V9938
is the hardcoded `_VRAM_SIZE = 131072` constant in `msx/vdp/v9938.py`. That key
and its per-machine copy `overrides: {vram_kb: 128}` (below) thus document
intent but have no effect on the running emulator.

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
| `schema_version` | int | (none) | Must be exactly `1` |
| `id` | string | (none) | Must equal the filename stem |
| `generation` | string | (none) | `msx1` or `msx2`. Any other value raises an error |
| `name` | string | `id` | Display name only |
| `rom_base` | string | `roms/cbios` | Directory (relative to the project root) for the `file` of every `rom:` block |
| `video_standard` | string | `ntsc` | `ntsc` → 59,736 T-states / 262 lines per frame. `pal` → 71,364 / 313 (used by `cbios_msx1_eu` and `cbios_msx2_eu`). Also selects the real-time pacing target of the SDL2 frontend (60 fps NTSC / 50 fps PAL) |
| `cpu.m1_wait_states` | int | `0` | Extra T-states per Z80 M1 cycle. See [CPU emulation](#cpu-emulation) above |
| `slots.primary` | mapping | (none) | Keys `0`-`3`. The loader reads only `0` and `3` (below) |
| `builtin_devices` | list | `[]` | See below |
| `io_device` | mapping | (none) | Optional machine-level global I/O device, with the same shape as the `io_device` of an extension overlay (below) |

#### Slot 0 (fixed)

```yaml
0:
  content:
    - rom: {file: cbios_main_msx2.rom, size_kb: 32, pages: [0, 1], sha1: null}
    - rom: {file: cbios_logo_msx2.rom, size_kb: 16, pages: [2], sha1: null}
```

`content` is a list of `{rom: {...}}` entries. `_parse_slot0` scans it for two
entries:

- The entry whose `pages` includes `0` or `1`: the main BIOS ROM. This entry is
  necessary. If it is absent, the loader raises `MachineLoadError`.
- The entry whose `pages` includes `2`: an optional logo ROM at
  0x8000-0xBFFF.

The `sha1` of a `rom:` block only records the expected dump. The loader does
not compare it with the file that it reads, whether the value is `null` or a
hash. The core checks a different SHA1. The host calculates it from the
bytes that it read and writes it into the MachineConfig document (see
[load_machine_config() and build_machine()](#load_machine_config-and-build_machine)).

`content` can also hold at most one `{ram: {size_kb, pages}}` entry. This is
flat RAM in slot 0 next to the ROMs. An example is the 16 KB SRAM of the Sony
HB-10 at page 3, a volatile work RAM with no battery backup:

```yaml
    - ram: {size_kb: 16, pages: [3]}
```

`size_kb` must be `16` and `pages` must name exactly one page, or the loader
raises `MachineLoadError`. The loader rejects a second `ram` entry in the same
way.

#### Slots 1 and 2: not read from the machine YAML

Every machine YAML declares `1: {type: cartridge}` and `2: {type: cartridge}`
under `slots.primary`. But `load_machine_spec` never reads `primary[1]` or
`primary[2]`. It reads only `primary[0]` and `primary[3]`. These two entries
thus declare intent only.

The contents of slots 1 and 2 come entirely from the parameters of
`load_machine_config()`, which writes them into the `cartridges` and
`extensions` of the MachineConfig document. For slot 1, these are the
cartridge and mapper (the positional CLI argument or `--slot1`, plus
`--mapper1`) or the overlay (`--extension1`). For slot 2, these are the
cartridge and mapper (`--slot2`/`--mapper2`) or the overlay (`--extension2`).
The CLI sets these values. Nothing under `slots.primary.1`/`.2` in the YAML
affects them.

#### Slot 3: MSX1

```yaml
3:
  size_kb: 32   # optional, defaults to 32
```

`_parse_slot3_msx1` reads only `size_kb` (default `32`). This is flat RAM that
ends at 0xFFFF, with no further structure:

- 32 KB covers 0x8000-0xFFFF.
- 64 KB (`hb_10_64k`) covers all four pages.
- `size_kb: 0` (`hb_10`) leaves slot 3 as open bus.

If `3:` is absent, the loader uses the 32 KB default, not open bus.

#### Slot 3: MSX2

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

`expanded: true` changes slot 3 into four secondary slots (`secondary.0` to
`.3`, with the same keys as `slots.primary`). `_parse_slot3_msx2` scans them
independently for these items:

- The first sub-slot with `content` whose `rom.pages` includes `0` or `1` →
  the SUB ROM. This is optional. The loader records its sub-slot index, which
  is not fixed to 0.
- The first sub-slot with `mapper: standard` → a `RamMapper`, with the size
  from its `size_kb` (default `128`). The size must be a positive multiple of
  `16`. `_check_ram_mapper_size_kb` checks this, or raises `MachineLoadError`.
- The first sub-slot with `type: ram` (and no `mapper: standard`) → flat
  (non-mapper) RAM, with the size from `size_kb` (default `64`). `hb_f1xd` uses
  this for its real fixed 64 KB.
- The first sub-slot with an `fdc:` block → see
  [Floppy disk (FDC)](#floppy-disk-fdc).

A `mapper: standard` sub-slot and a `type: ram` sub-slot are mutually exclusive
across the whole `secondary` mapping. The reason is that `Memory` cannot hold
both a RAM mapper and flat RAM. If both are present, the loader raises
`MachineLoadError`. The loader also rejects flat RAM in the same sub-slot index
as the SUB ROM or the FDC. The reason is that the write path of `Memory` does
not stop a stray write to either of them from going into the flat RAM. The
loader checks every sub-slot index against `0`-`3` (`_check_subslot_index`),
whatever its role is.

`fdc:` block fields (only meaningful in a slot-3 sub-slot):

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `rom` | `rom:` block | (none) | Necessary (the DISK ROM) |
| `controller` | string | `wd2793` | `wd2793` or `tc8566af` |
| `connection_style` | string | `sony` | `sony` or `tc8566af` |
| `drives` | int | `1` | `1`-`4`. The YAML loader checks only that the value is positive. The core rejects a document with more than 4 drives |

`(controller, connection_style)` must be `(wd2793, sony)` or
`(tc8566af, tc8566af)`. The loader raises an error for any other pair, even if
each value is valid alone (e.g. it rejects `wd2793` + `tc8566af`).

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

The `ref` of each entry must resolve against the device registry (the output
of `load_device_registry`), or the loader raises `MachineLoadError`.
`overrides` is a free-form mapping. But `_parse_builtin_devices` reads only two
keys from it, and only when `ref: ppi8255`:

- `keyboard_type`: `int` or `jp`.
- `control_read`: `control`, `open_bus` or `bus_latch`, the value that a read
  of port 0xAB returns.
 The former key `control_readback`, in the device file or in
`overrides`, raises `MachineLoadError`. The loader accepts and ignores every
other `overrides` key, for every other `ref`.

The presence of `ref: vdp_v9938`/`rtc_rp5c01`/`ppi8255` sets the
`has_v9938`/`has_rtc`/keyboard-layout flags of `MachineSpec`. Every other `ref`
only adds its `io_ports` range.

#### Machine-level `io_device`

```yaml
io_device:
  device: kanji_rom
  rom: {file: some_kanji_font.rom, size_kb: 256, sha1: null}
```

This field has the same shape as the `io_device` of an extension overlay
(below). It is a slot-independent global I/O device that the machine itself
supplies. The loader validates it against the same `_KNOWN_IO_DEVICES` set,
which holds only `kanji_rom`. At present, no shipped machine YAML declares
one. At present, only two extensions supply a `kanji_rom` device: `hbi_j1` and
`msxdos2_512k_kanjirom` (`--extension1`/`--extension2`). Both do it through the
`io_device` of the extension overlay (below).

### Extension overlay YAML (`config/extensions/*.yaml`)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | (none) | Must be `1` |
| `id` | string | (none) | Documentation only. `load_extension_overlay` never reads it. The loader finds the file by its filename stem alone (`config/extensions/<--extension1/--extension2 value>.yaml`), unlike the `id` of machine and device YAML |
| `slot` | int | `2` | Must be `1` or `2`. Documentation only: it records the slot that the author of the YAML targeted. The CLI flag (`--extension1` or `--extension2`) selects the actual slot |
| `shape` | string | `flat` | `flat` or `expanded` |
| `rom_base` | string | `""` (project root) | Base directory for every `rom:` block below |

#### Flat shape (`shape: flat`, or omitted)

```yaml
device: fmpac
rom: {file: fmpac.rom, size_kb: 64}
sram: {size_kb: 8, save_file: saves/sram/fmpac.sram}
```

`device` must be `fmpac` or `scc_i_cart` (`_KNOWN_EXTENSION_DEVICES`). This is
a different namespace from the sub-slot devices of the expanded shape below,
although both use the key `device`. `fmpac` needs a `rom:` block. `scc_i_cart`
does not need one, because its RAM starts blank and the loader loads no file.
The device of a flat overlay always replaces the complete mapper of its target
primary slot (slot 1 or slot 2).

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

`subslots` is a necessary, non-empty mapping with keys `0`-`3`. It uses the
same index range and the same `_check_subslot_index` as an MSX2 slot-3
sub-slot. The `device` of each entry must be one of `halnote`, `flat_rom`,
`ram_mapper`, `ascii8`, or `ascii16` (`_KNOWN_EXPANDED_SUBSLOT_DEVICES`):

| `device` | Requires | Constructs |
| --- | --- | --- |
| `halnote` | `rom:` | `HalnoteMapper` (1 MB ROM, optional `sram:`) |
| `flat_rom` | `rom:` | `FixedPageMapper(base=0x4000)`, visible only at 0x4000-0xBFFF |
| `ascii8` | `rom:` | `Ascii8Mapper` |
| `ascii16` | `rom:` | `Ascii16Mapper` |
| `ram_mapper` | `size_kb:` (directly on the entry, no `rom:`) | `RamMapper`. `size_kb` must be a positive multiple of `16` |

An overlay can have at most one `ram_mapper` sub-slot. A second one would
register the standard memory-mapper I/O ports (0xFC-0xFF) two times. The
loader always raises `MachineLoadError` for an expanded overlay on an MSX1
machine. No MSX1-standard hardware matches a memory-mapper sub-slot, and every
current expanded overlay models MSX2-era hardware. A `ram_mapper` sub-slot also
conflicts with a machine that already has its own slot-3 RAM mapper
(`has_ram_mapper=True`). The two mappers would use the same ports.

The loader validates the optional top-level `io_device` against a *third*
device namespace, `_KNOWN_IO_DEVICES = {kanji_rom}`. This namespace has no
overlap with the other two. `device: kanji_rom` is not valid in `subslots`, and
`device: halnote` (or any other sub-slot device) is not valid in `io_device`.
If a machine-level `io_device` and the `io_device` of an overlay both resolve
to `kanji_rom`, `build_machine` raises an error. The reason is that ports
0xD8-0xDB cannot serve two Kanji-ROM devices.

#### `rom:` and `sram:` blocks

Both shapes use the same nested block schemas. `_parse_rom_entry` parses them.
The loader also uses this function for the slot 0 ROMs, the MSX2 SUB ROM, and
the DISK ROM of the FDC. It is the one shared implementation for every
ROM-bearing field in the loader.

| `rom:` field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | string | yes | Resolved against `rom_base` |
| `size_kb` | int | no (default `0`) | Documentation only. The loader does not check it against the actual file size |
| `pages` | list of int | no (default `[]`) | Only meaningful for slot 0 and the SUB ROM, where it selects the main, logo, or SUB ROM entry |
| `sha1` | string | no | Documentation only. The loader does not check it against the file (see [Slot 0](#slot-0-fixed)) |

| `sram:` field | Type | Notes |
| --- | --- | --- |
| `size_kb` | int | Documentation only. The loader ignores it. The SRAM size of each device is a Python-side constant (`HALNOTE_SRAM_SIZE`, `FMPAC_SRAM_SIZE`, ...) |
| `save_file` | string | Path (relative to the project root) of the SRAM save file. Omit it for no persistence |

## Machine YAML loader

### Two-pass resolution

`host/machine_config.py` resolves the hardware topology in two passes:

1. **Device registry pass.** `load_device_registry(config_dir)` reads every
   `*.yaml` file in `config/devices/` into a dict with the device `id` as key.
   Each file describes one hardware item (VDP type, PSG, PPI, RTC, RAM mapper)
   alone, with no machine context.

2. **Machine spec pass.**
   `load_machine_spec(machine_id, config_dir, device_registry, project_root)`
   loads `config/machines/<machine_id>.yaml` and validates the necessary
   fields. It resolves every `builtin_devices` `ref` against the registry and
   returns a `MachineSpec` dataclass. For an unresolved `ref`, an unknown
   `schema_version`, or an absent necessary ROM entry, it raises a
   `MachineLoadError` that gives the file and the field.

### load_machine_config() and build_machine()

The host and the core share the work of building a machine.
`load_machine_config(spec, cartridge1, mapper1, ...)` in `host/machine_config.py`
reads every file the machine needs into a `MachineConfig` value
(`msx/boundary.py`). It writes the resolved machine, the cartridges with their
mappers and the extensions into one canonical JSON document
(`msx/machine_config.py`, `msx/machine_config.schema.json`). For an `auto`
mapper, it looks up the mapper in the ROM database first. In the document,
every ROM is a reference `{name, size, sha1, pages}`. The ROM images travel
beside the document, keyed by SHA1. The run inputs also travel beside it: the
saved SRAM images, the disk images and the RTC start time.

`build_machine(config)` in `msx/machine_loader.py` parses the document
strictly and checks the size and SHA1 of every referenced image. It then
constructs the `Machine` from those bytes only. It opens no file and consults
no database, so the same config always builds the same machine. The `Machine`
keeps the SHA-256 of the document, which a save state records (see
[MachineConfig document](#machineconfig-document)). The `spec.generation`
field selects the concrete types:

- `"msx1"`: `VDP` (TMS9918A), flat slot 3 RAM, no RTC or RAM mapper
- `"msx2"`: `V9938`, expanded slot 3 with sub-ROM and `RamMapper`, `RTC`

At load time, the loader skips device YAML entries with `implemented: false`
and shows a stderr warning. The rest of the machine continues normally. A
device definition can thus go into a commit before its emulation exists, and
boots do not fail. Every device in `config/devices/` is currently
`implemented: true`.

---

## Core boundary and porting guide

The emulator core (`msx/`) is separate from everything that embeds it. You
can thus drive the core from a different frontend. You can also port only the
core to another language (Zig, C++, Rust) first, and keep the Python host and
frontend for comparison. This section describes the boundary from the point
of view of such a reuse. The language-neutral contract is
`allium/core-frontend.allium`. The requirements are in the `machine-core`,
`machine-loader` and `machine-state` specs under `openspec/specs/`. Neither
`allium/` nor `openspec/` is published yet.

### Three parties

| Party | Code | Responsibility |
| --- | --- | --- |
| Core | `msx/` | The emulated machine: CPU, VDP, sound chips, memory and slots, FDC, RTC, PPI and input lines. A pure function of its state and of the data handed to it |
| Host | `__main__.py`, `host/` | Process-level services: YAML and ROM file loading, the ROM database, SRAM, disk image, save-state and screenshot files, the debugger, the socket RPC |
| Frontend | `frontend/` | Presentation and devices: the SDL2 window, audio output and its low-pass filter, host input devices, frame pacing and frame skipping, hotkeys |

The core opens no file, never reads the host clock, and imports neither
`host/`, `frontend/` nor `sdl2`. `tests/test_core_frontend_boundary.py`
enforces this. It imports every `msx` module in an interpreter that cannot
import `sdl2`, and it rejects any import of the three names. Everything
that the core needs thus enters as a value, and everything it produces leaves
as a value.

### Values that cross the boundary

All of them are in `msx/boundary.py` and `msx/input.py`:

| Value | Direction | Content |
| --- | --- | --- |
| `MachineConfig` | in, once | `document` (the canonical MachineConfig JSON, below), `roms` (the ROM images by lowercase hex SHA1), `media` (saved SRAM and CMOS images by `MediaId`), `disks` (`DiskMedium` images to mount by drive id), `rtc_initial_time` |
| `InputFrame` | in, per frame | 11 active-low keyboard matrix rows, two active-low 6-bit joystick ports (bit 0 up, 1 down, 2 left, 3 right, 4 trigger A, 5 trigger B), the mouse motion of the frame in MSX counts, and the two mouse buttons. Immutable. Its constructor checks every value |
| `FrameOutput` | out, per frame | `video` (an `RgbFrame`, or `None` when the frame was not rendered), `audio`, `frame_number` |
| `RgbFrame` | out | Packed RGB24, row-major. `width` is 256 or 512, `height` is always 212 (the core pads 192-line modes with the border color) |
| audio | out | Signed 16-bit little-endian mono PCM at 44,100 Hz, before the output filter: 735 samples per NTSC frame, 882 per PAL frame |
| snapshot | in/out | `bytes` from `msx.state.save_state()` (below) |
| media image | in/out | `bytes`, named by a `MediaId` |
| `DiskMedium` | in | `data`, `write_protected`, and `path`: the host's name for the file, which the core records but never opens |

`MediaId` is a closed set: `slot1.sram`, `slot2.sram`, `slot<n>.<sub>.sram`
(an expanded-slot device, slots 1-2, sub-slots 0-3), `fmpac.sram`,
`rtc.cmos`, and `drive_a` to `drive_d` (FDC drives 0-3). The string values are
the wire spelling. Python code formats a `MediaId` with `.value`.

### Core API

`build_machine(config)` in `msx/machine_loader.py` returns a `Machine`. The
methods below are the boundary. Everything else on `Machine` is internal or
belongs to the debugger.

| Group | Operation | Notes |
| --- | --- | --- |
| Construction | `build_machine(config)` | Raises `MachineLoadError` (`msx/machine_spec.py`) for a malformed document or a missing or mismatched ROM image, before it builds anything. The optional `logger`/`tracer` arguments are diagnostics |
| Frame stepping | `step_frame(frame_input, render_video) -> FrameOutput` | Runs exactly one frame |
| | `apply_input(frame_input)` | Replaces the input lines and mouse buttons at once, without running a frame. RPC key injection uses it |
| | `samples_per_frame()`, `video_standard` | 735 or 882. NTSC or PAL (`video_standard.target_fps` is 60 or 50) |
| Control | `reset()` | Power-on reset. RAM, VRAM and mounted media stay as they are |
| | `msx.state.save_state(machine) -> bytes` | The snapshot |
| | `msx.state.load_state(machine, data, source)` | Raises `ValueError` (`StateLoadError`, `SnapshotMismatchError`) and changes nothing when the snapshot does not fit |
| Media | `dirty_media()` | The ids written since they were last persisted |
| | `read_media(media_id) -> bytes` | The current bytes of a medium |
| | `mark_persisted(media_id)` | Call it after the host has written the bytes |
| | `mount_disk(drive, medium) -> bytes \| None` | `None` ejects. Returns the outgoing image when it holds unpersisted writes. Raises `ValueError` for a drive the machine lacks or an image whose size is not a positive multiple of 512 |
| | `disk_mounted(drive)`, `disk_path(drive)` | Drive state and the recorded host name |
| Devices | `attach_mouse(MouseDevice(), JoystickPort(n))` | Connects an MSX mouse to joystick port 1 or 2 |

The other members do not cross the boundary, and a port does not need them
to run software:

- `run_frame()` and `step()`, the internal frame loop and single-step
- breakpoints and watchpoints
- `set_pause_hook()`, the seam that the RPC server uses to pause
- `attach_debugger()`
- the VDP and mapper tracers

### MachineConfig document

The machine definition reaches the core in two layers:

1. **Authored YAML** (`config/machines`, `config/devices`,
   `config/extensions`). It keeps its comments about the hardware, and only
   the host reads it (see
   [Machine and extension YAML schema](#machine-and-extension-yaml-schema)).
2. **The MachineConfig document**, which `load_machine_config()` makes from
   the YAML and the command-line choices. It is RFC 8785 (JCS) canonical JSON:
   UTF-8, sorted keys, no whitespace, integers only. `format_version` is `1`.
   It holds the resolved machine, the cartridges per slot with their mapper
   (never `auto`), and the extension overlays. `msx/machine_config.py`
   encodes and parses it. `msx/machine_config.schema.json` (JSON Schema
   draft 2020-12) describes it. The tests read the schema, and the core never
   does.

Every ROM in the document is a reference `{name, size, sha1, pages}`. `name`
is a bare file name, used only as a label. `sha1` is the SHA1 of the bytes
that the host read. The images travel in `MachineConfig.roms` by that SHA1.
The parser is strict. It rejects:

- an unknown or missing key, a wrong type or range, a float, and a duplicate
  key
- another `format_version`
- a name outside a closed set (mapper, FDC controller, connection style and
  their pair, flat-extension device, sub-slot device, I/O device)
- a device that needs a ROM and has none, and a `ram_mapper` without
  `size_kb`
- a ROM image that is absent or whose size or SHA1 differs from its
  reference

The SRAM and CMOS images, the disks, the RTC start time and every host path
are run inputs. They are outside the document, so they do not change its
identity. The identity of a config is the SHA-256 of the canonical form that
the core encodes again after it parses the document. A document that differs
only in whitespace or key order thus has the same identity. The `Machine`
keeps this value as `machine_config_sha256`.

A port that has no YAML loader can write the document directly. It can also
take one from the Python host: encode it with `load_machine_config()` and save
`config.document` and `config.roms`.

### Snapshots

`save_state()` returns the whole machine state as canonical JSON (format
version 19). The layout is `msx/snapshot.schema.json`: one object per
component (`cpu`, `memory`, `slots`, `vdp`, `psg`, `ppi`, `scc`, `fdc`, `rtc`,
`mouse`), every key always present, and `null` for a part that the machine
does not have. Byte arrays are padded RFC 4648 base64 strings, and every
integer is within ±(2^53 − 1).

- `load_state()` accepts any whitespace and key order. It rejects floats,
  NaN or Infinity, duplicate keys, and another format version.
- It checks the identity of the machine first: the machine type, the mapper
  kinds, the slot wiring, the FDC, and the mounted disks. Then it compares
  `machine_config_sha256` with the hash of the running machine, which also
  covers the cartridge ROM bytes. A snapshot thus loads only into a core
  built from the same MachineConfig document.
- A snapshot that fails any check changes nothing.
- `save_state()`, then `load_state()`, then `save_state()` again gives the
  same bytes.

The snapshot does not hold two kinds of state. One is the address and read
counter of the Kanji font ROM, by design (see the `machine-state` spec). The
other is anything outside the core, such as the state of the audio filter.

### Frame stepping

`Machine.step_frame(frame_input, render_video)` is the per-frame call of the
core. It applies `frame_input` to the input lines that the PPI and PSG read
(`InputLines`). The input thus stays the same for the whole frame, and the
mouse gets the motion of the frame once. It then runs the frame and returns
the `FrameOutput`. `render_video` decides only whether the core produces
video. A skipped frame still runs the sprite pass. The VDP status, the audio
and the next snapshot are thus the same either way.

`InputState` in `host/input_state.py` builds the input outside the core.
The keyboard, the gamepads, the mouse and the RPC input methods write to it.
It keeps the keyboard-joystick and gamepad sources of each port apart until
it merges them. Each frame, the frontend passes `take_frame()` to
`step_frame`, which also zeroes the mouse motion of the builder. RPC
injection calls `Machine.apply_input`, which sets the lines and the mouse
buttons at once without running a frame. A paused machine thus sees the input
at its next instruction. Headless runs (`--count-frame`, `--benchmark`) pass
an empty `InputFrame()`.

### Guarantees

- **Determinism.** Build two cores from the same MachineConfig and give them
  the same sequence of `InputFrame` values. They then produce the same video,
  audio and snapshot bytes. This is true whatever drives them and whatever
  `render_video` values they get. The RTC advances from the emulated cycle
  count, never from the host clock.
- **One frame per step.** In normal run mode, one `step_frame` call advances
  exactly one video frame and returns one frame of audio. A frame is 59,736
  T-states on NTSC and 71,364 on PAL, to within one instruction. A debugger
  break can end a frame early. That mode is outside the contract.
- **RGB inside the core.** The core converts every pixel to RGB with integer
  tables only. The frontend never needs the palette or the VDP registers.
- **Pre-filter audio.** The audio is the clamped sum of every present sound
  source (PSG, SCC, Majutsushi DAC, key click, OPLL) before any filter.
  Filtering, resampling and buffering are the frontend's.
- **Output lifetime.** `FrameOutput.video` and `.audio` stay valid only
  until the next `step_frame`. In Python they are fresh immutable `bytes`, so
  the SDL2 frontend keeps the last frame without a copy. A native core may
  reuse its buffers, and a frontend for it copies a frame that it keeps
  longer.
- **Media.** A medium is dirty from the first write by the emulated machine
  until `mark_persisted`. A write of the value already stored does not count,
  and a write-protected medium is never dirty. The core decides the set of
  SRAM ids at build time, and the set does not change. To swap a disk, write
  the dirty outgoing image back first and then call `mount_disk`. `host/media.py`'s `swap_disk` does this, so a failed
  write-back leaves the drive as it was.

### Embedding example

This example uses the Python host for loading, and runs the core headless. It
is the minimum for another frontend: replace the loop body with your own
presentation, audio output and input.

```python
from pathlib import Path

from host.machine_config import (
    load_device_registry, load_machine_config, load_machine_spec, sram_paths,
)
from host.media import persist_dirty_media
from msx.input import InputFrame
from msx.machine_loader import build_machine
from msx.state import load_state, save_state

root = Path(".")                                   # the repository root
registry = load_device_registry(root / "config")
spec = load_machine_spec("cbios_msx2", root / "config", registry, root)
config = load_machine_config(spec)                 # host: YAML + files -> MachineConfig
machine = build_machine(config)                    # core: values only

for _ in range(120):
    out = machine.step_frame(InputFrame(), render_video=True)
    # out.video.pixels: width * 212 * 3 bytes of RGB24
    # out.audio: samples_per_frame() signed 16-bit LE mono samples

snapshot = save_state(machine)                     # bytes; store them anywhere
load_state(machine, snapshot)
assert save_state(machine) == snapshot

persist_dirty_media(machine, sram_paths(config))   # write SRAM/CMOS/disk changes back
```

The SDL2 frontend (`frontend/sdl2_frontend.py`) follows the same pattern.
Each frame, it passes the frame of its input builder to `step_frame`. It
uploads `out.video` when the core rendered the frame. It passes `out.audio`
through `BiquadLowPass` (`frontend/audio_filter.py`) and queues it to SDL. On
exit, `__main__.py` calls `persist_dirty_media`. A frontend that has no host
side of its own can keep `config.document` and `config.roms` from one run,
and build the `MachineConfig` from them directly.

### Porting the core

These are the differences between the Python code and the language-neutral
contract in `allium/core-frontend.allium` (its *Status* comment lists them):

- The control operations return `None`, not a status record. `save_state`
  and `load_state` are functions in `msx/state.py` that take the machine.
- `eject_disk` is `mount_disk(drive, None)`. `read_media` returns the bytes
  alone. The write-protect flag stays with the mounted image.
- The frame rate is `VideoStandard` data, not a method.
- The frontend attaches the mouse at run time (`attach_mouse`). The contract
  has it as a config field. Which form to keep is an open question.
- `load_machine_config()` returns a `LoadedMachineConfig`, which adds the
  SRAM file paths of the host. The core reads only the `MachineConfig` part.

These questions are open, and a port must decide them:

- **OPLL tables.** `msx/opll.py` builds its log-sin, exponent and key-scale
  tables at import with floating-point math. A port that calculates them
  again can differ in the last bit, and its FM-PAC audio then does not match
  byte for byte. Ship the tables as integer data that the Python build
  generated.
- **Mouse attachment**, as above.
- **Debug control.** Breakpoints, watchpoints, single-step and the RPC pause
  are outside the contract. A port can leave them out at first.

Some Python patterns have no direct equivalent in a statically typed
language: bus hooks, register properties, the PSG envelope underflow, the
renderer lookup tables and callable hooks. See [Portability](#portability).

A suggested order for a port:

1. **The CPU, memory and slots, the VDP and the PSG** for one MSX1 C-BIOS
   machine (`cbios_msx1`). Take the MachineConfig document and the ROM
   images from the Python host. Run N frames headless with an empty input on
   both sides, as `--count-frame` does. Then compare the `save_state()`
   bytes. A mismatch shows the component and the field.
2. **The snapshot format**, so that a state saved by one implementation
   loads in the other. After this step, either side can produce the start
   state for the other, and a test can start at any point in a program.
3. **MSX2**: the V9938 and its command engine, the RAM mapper, the RTC and
   the expanded slots (`cbios_msx2`).
4. **The other sound sources and the cartridges**: the SCC, the DAC and key
   click, the OPLL with shipped tables, the SRAM mappers.
5. **The FDC** (WD2793 and TC8566AF) and the media API.

At each step, compare the bytes with the Python core. The same config and
the same `InputFrame` sequence must give the same `RgbFrame.pixels`, audio
and snapshot bytes.

## Portability

The implementation is pure Python 3.10+. It has no C extensions or native
bindings, except in the SDL2 frontend. Several design decisions keep the core
logic simple to port to a statically typed systems language (Rust, C++). A
_Portability note_ in the related source file documents each decision. This
section gives a summary of them.

### Intentionally portable patterns

These patterns translate directly to any statically typed target:

- **Opcode dispatch** (`msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`):
  `_DISPATCH` is a flat 256-element list of callables, built once at import.
  Each entry is a plain function. A Rust port maps this to an array of
  function pointers or a `match` on the opcode byte.
- **Register file** (`msx/cpu/registers.py`): the primary registers are
  Python `int` fields with explicit `& 0xFF` / `& 0xFFFF` masks at every write
  site. They do not rely on arbitrary-precision values. The shadow registers
  are discrete named fields (`A_`, `BC_`, etc.) with no reflection.
- **VRAM and RAM**: `bytearray` objects of fixed size (`_VRAM_SIZE = 131072`
  for V9938, `16384` for TMS9918A, segment arrays for the RAM mapper). All
  access is direct slice-and-index access. There is no list-of-ints or
  dictionary-backed storage.
- **I/O bus dispatch** (`msx/io.py`): a linear scan over a
  `list[tuple[int, int, Callable]]`, with no dictionary lookup or reflection.
  The number of registered handlers is small and fixed per machine
  configuration.
- **Component structs**: every hardware component is a `@dataclass`, with all
  fields explicitly declared and typed. There is no `__getattr__`/
  `__setattr__` magic anywhere in `msx/`. The two structs on the hottest path,
  `Z80` and `Registers`, also use `@dataclass(slots=True)` to remove the
  per-instance `__dict__`. `Memory` and a few small V9938 value types also
  use it: the `_RegChange`/`_PaletteChange` log entries, `_RenderStatus` and
  `_SatSegment`. The other components use a plain `@dataclass`, because the
  field-layout guarantee is less important off the hot path.
- **Timing constants**: `CYCLES_PER_LINE`, `NTSC_LINES_PER_FRAME` /
  `PAL_LINES_PER_FRAME` (`msx/vdp/_geometry.py`), `_HBLANK_START`,
  `_TR_REASSERT_CYCLES`, etc. are module-level integer constants. The per-frame
  budget comes from the properties of the `VideoStandard` enum. A port writes
  these as a `match` / `const fn`.

### Python-specific patterns a port must adapt

The patterns below use Python language features that have no direct
equivalent in a statically typed language. For each pattern, a note in the
source tells what a Rust/C++ port uses instead.

#### Bus hooks as reassignable bound methods

`Z80` stores `read_byte` / `write_byte` / `read_port` / `write_port` as
`Callable` fields. `Machine.__post_init__` connects them by assigning bound
methods at runtime. When a program enables watchpoints later,
`Machine.set_watchpoints` swaps `cpu.read_byte` / `cpu.write_byte` again. It
changes them between the plain memory handler and a variant that traps
watchpoints (`Machine._read_with_watch` / `_write_with_watch`).

Python allows this because a `Callable` field is only a slot that holds any
object with `__call__`. Rust and C++ have no runtime method swap on a struct
field. A port expresses the bus as a `trait MemoryBus` (or an
`enum { Normal, Watchpoint }`). A flag selects the concrete implementation
once, so the dispatch on each access has no branch.

#### Register 8-bit halves as computed properties

`Registers` stores `BC`, `DE`, `HL` as 16-bit `int` fields. It gives access to
`B`/`C`, `D`/`E`, `H`/`L` through `@property` getter/setter pairs
(`Registers.B`/`C` and the related properties). Each pair shifts and masks the
16-bit value. Each
opcode that reads or writes a single 8-bit half thus goes through the
descriptor protocol. This adds a call frame on the hot instruction path.

A Rust/C++ port stores the 8-bit halves as plain `u8` fields (or as explicit
inline getter/setter methods) and calculates the 16-bit pairs when necessary.
This removes the property-call overhead completely.

#### Arbitrary-precision integers in the PSG envelope

Python integers have arbitrary precision: `-1 & 0x20 == 0x20`. The PSG
envelope generator uses this when `_env_step` becomes negative. Bit 5 of a
negative Python int still works as an underflow flag (`PSG._env_step`).

Rust/C++ unsigned types wrap on underflow and do not extend the sign. The same
expression thus gives 0, not `0x20`. A port must use a signed type (`i32`) or
an explicit `wrapping_sub` / bitfield overlay to keep the flag.

#### `bytes.translate()` LUT cache in the renderer

The V9938 G4/G6 and G5 per-scanline pixel expanders memoize
`bytes.translate()` tables in `_G46_LUT_CACHE` / `_G5_LUT_CACHE`. These are
dicts with `(tp: bool, border: int)` as key. `bytes.translate()` is a CPython
built-in that applies a full 256-byte lookup table in C. It is thus the
fastest path in Python for this transformation. The dict cache prevents the
allocation of identical tables on every scanline.

`bytes.translate()` and a per-call dict hash are not natural constructs in a
systems language. A Rust/C++ port keeps fixed `[u8; 256]` arrays, precomputed
at init, and indexes them directly. Only 32 combinations exist across all
`(tp, border)` pairs.

#### Callable tracer hooks

`VDP` (TMS9918A) and `V9938` both store `tracer`, `_get_pc`, and `_get_cycle`
as nullable `Callable` fields. The machine assigns them at wiring time, and the
VDP calls them on each related event. Python types these fields as
`Callable[[], None] | None`, but they have no direct static equivalent.

A Rust/C++ port models them as `Option<Box<dyn Fn()>>` trait objects (or an
equivalent). Another option is feature-flagged compile-time generics, which
remove the per-call dispatch in release builds.

#### Spin loop in the frame timer

`FrameTimer.tick()` uses `time.perf_counter()` in a busy-wait loop for the
final sub-millisecond period before the frame deadline. Python has no way to
tell the scheduler that this loop is a spin.

A Rust port adds `std::hint::spin_loop()` in the same loop. This gives the CPU
pipeline a hint without a sleep. It can decrease power consumption and improve
timing jitter on SMT cores.

During fast-forward, the frontend calls `FrameTimer.tick_unpaced()` instead of
`tick()`. It counts the frame for the fps measurement and restarts the
deadline, but it does not wait. A port keeps the same two entry points.
