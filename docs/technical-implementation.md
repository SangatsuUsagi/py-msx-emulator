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
documented per-instruction rules. Three sources of Y and X are not modelled:
the internal MEMPTR register (`BIT n,(HL)` leaves them clear), the PC of a
repeating block instruction, and the previous instruction (`SCF`/`CCF`).

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
scanline:

```python
for L in range(lpf):               # lpf = 262 NTSC lines
    line_end = (L + 1) * cpf // lpf
    while total < line_end:
        cpu.int_pending = vdp9938.irq
        n = cpu_step()
        ...
    vdp9938.begin_scanline(L)
```

The CPU sees a level change from `begin_scanline` at its next instruction
boundary.

`begin_scanline(line)` calculates the effective IRQ line as
`(R#19 − R#23) & 0xFF`. It sets S#1 bit 0 (FH) when `line == effective_irq_line`
and IE1 (R#0 bit 4) is set. The compare can target any line 0-255, including
the border and vertical-blanking lines. The VDP does not latch FH while IE1 is
clear. Thus, if a program sets IE1 later, no interrupt occurs for a line that
has already passed.

FH stays set until a read of S#1. This read clears FH and evaluates `irq`
again.

`begin_scanline(display_height)` also sets the VBlank flag. This is the first
line after the active display. It replaces the old per-frame finalize path for
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
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 18, 19, 23})
```

Each entry has one of these forms:

- `(display_line, reg, value)` for a register write
- `(display_line, -1, (palette_index, grb_value))` for a palette write (port
  0x9A), with the sentinel `reg = -1`

`begin_scanline(0)` (frame start) clears the log. `render_frame()` does not
clear it, so the SDL2 frontend can use it during RGB conversion to apply the
correct palette per scanline.

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
The machine calls `tick(n)` after each instruction with the T-states that the
instruction used. Software that busy-waits on S#2 bit 0 (CE) sees CE clear
after the budget expires.

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
does not load it automatically at dispatch. This matches the openMSX behavior.

`_apply_log()` in `v9938.py` applies the logical operations (IMP, AND, OR,
XOR, NOT) per pixel.

### Known limitations

- Command timing is approximate. The engine uses one measured cost per element
  (see above). It does not use the per-command cycle counts of the V9938 data
  book.
- The engine writes the VRAM results at dispatch, not incrementally. Software
  that reads VRAM while a command is nominally in progress can see the
  completed result before CE clears.
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
[Frame API](#frame-api)), passes it through the output low-pass filter, and
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

1. `write_port` records each register write as `(cycle, reg, value)`. It gets
   the cycle from a `_get_cycle` callback that reads `machine.cycle_count`.
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
| Disk image | `disk_image.py` (`DskDiskImage`) | Holds a `*.dsk` sector image built from the file's bytes. Gets the geometry from the BPB of the FAT12 filesystem (BIOS Parameter Block: bytes/sector, total sectors, sectors/track, heads). Buffers writes and marks the image dirty; `host/media.py` writes a dirty image back to its file on exit, before a save state, and when the disk is swapped out |
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
images. Three paths swap disks at runtime, and all of them call
`FloppyDisk.swap()`:

- The F6/Shift+F6 hotkeys of the SDL2 frontend step through the `--fdd1`
  list, or eject and insert the drive A disk again.
- The `fdd1`/`fdd2` commands of the debugger.
- The `fdd.swap` method of the socket RPC server.

The implementation boots Disk BASIC, supports `CALL FORMAT`, and reads and
writes files; written sectors go back to the `.dsk` file on exit, before a
save state, and when the disk is swapped out.
`machine.fdc` is `None` on machines with no floppy interface.

## Machine and extension YAML schema

`msx/machine_loader.py` is the single source of truth for three YAML surfaces:
device definitions (`config/devices/*.yaml`), machine specifications
(`config/machines/*.yaml`), and extension overlays
(`config/extensions/*.yaml`). All three are `TypedDict` shapes that the loader
validates by hand (`DeviceEntryYaml`, `MachineEntryYaml`,
`ExtensionOverlayYaml`, and their nested shapes). No schema library validates
them.

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

`sha1: null` disables hash verification for that ROM. The loader checks any
other string value against the loaded file.

`content` can also hold at most one `{ram: {size_kb, pages}}` entry. This is
flat RAM in slot 0 next to the ROMs. An example is the 16 KB SRAM of the Sony
HB-10 at page 3, a volatile work RAM with no battery backup:

```yaml
    - ram: {size_kb: 16, pages: [3]}
```

`pages` must name exactly one page, or the loader raises `MachineLoadError`.
The loader rejects a second `ram` entry in the same way.

#### Slots 1 and 2: not read from the machine YAML

Every machine YAML declares `1: {type: cartridge}` and `2: {type: cartridge}`
under `slots.primary`. But `load_machine_spec` never reads `primary[1]` or
`primary[2]`. It reads only `primary[0]` and `primary[3]`. These two entries
thus declare intent only.

`build_machine()` gets the contents of slots 1 and 2 entirely from its own
parameters. For slot 1, these are the cartridge and mapper (the positional CLI
argument or `--slot1`, plus `--mapper1`) or the overlay (`--extension1`). For
slot 2, these are the cartridge and mapper (`--slot2`/`--mapper2`) or the
overlay (`--extension2`). The CLI sets these values. Nothing under
`slots.primary.1`/`.2` in the YAML affects them.

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
| `drives` | int | `1` | Must be positive |

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
| `sha1` | string | no | `null` or an absent key disables verification |

| `sram:` field | Type | Notes |
| --- | --- | --- |
| `size_kb` | int | Documentation only. The loader ignores it. The SRAM size of each device is a Python-side constant (`HALNOTE_SRAM_SIZE`, `FMPAC_SRAM_SIZE`, ...) |
| `save_file` | string | Path (relative to the project root) of the SRAM save file. Omit it for no persistence |

## Machine YAML loader

### Two-pass resolution

`msx/machine_loader.py` resolves the hardware topology in two passes:

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

### build_machine()

`build_machine(spec, cartridge, mapper, ...)` constructs a `Machine` from a
resolved `MachineSpec`. The `spec.generation` field selects the concrete types:

- `"msx1"`: `VDP` (TMS9918A), flat slot 3 RAM, no RTC or RAM mapper
- `"msx2"`: `V9938`, expanded slot 3 with sub-ROM and `RamMapper`, `RTC`

At load time, the loader skips device YAML entries with `implemented: false`
and shows a stderr warning. The rest of the machine continues normally. A
device definition can thus go into a commit before its emulation exists, and
boots do not fail. Every device in `config/devices/` is currently
`implemented: true`.

---

## Frame API

`Machine.step_frame(input, render_video)` is the core's per-frame call
(`allium/core-frontend.allium`). It takes one `InputState` value: the 11
keyboard matrix rows, both joystick ports, and the frame's mouse motion (in
MSX counts) and buttons. It copies the value into the machine's own input
lines, so the input stays the same for the whole frame, and feeds the mouse
once. It then runs the frame and returns a `FrameOutput` (`msx/boundary.py`):
the RGB24 video when `render_video` is true, the frame's audio before the
output filter, and the frame number. `render_video` decides only whether
video is produced: a skipped frame still runs the sprite pass, so the VDP
status is the same.

The SDL2 frontend keeps its own `InputState` as an accumulator. The keyboard,
the gamepads, the mouse and the RPC input methods write to it, and each frame
it passes the accumulator to `step_frame` and clears the mouse motion.
Headless runs (`--count-frame`, `--benchmark`) pass an empty `InputState`.
`Machine.run_frame()` remains as the internal step that the debugger and the
break handling use.

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
  per-instance `__dict__`. The other components use a plain `@dataclass`,
  because the field-layout guarantee is less important off the hot path.
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
