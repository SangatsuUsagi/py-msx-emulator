# Technical Implementation

This document describes the internal structure of the MSX1/MSX2 emulator — CPU execution, interrupt handling, I/O dispatch, VDP rendering, and the memory subsystem.

---

## CPU emulation

### Instruction decode

The Z80 CPU is implemented in `msx/cpu/z80.py`. The opcode dispatch table `_DISPATCH` is a flat list of 256 callables, built once at import time by `opcodes_main._build_dispatch()` in `msx/cpu/opcodes_main.py` and bound as a module-level constant in `z80.py`:

```python
_DISPATCH: list[Callable[[Z80], int]] = _opcodes_main._DISPATCH
```

Executing an instruction is a single indexed call — no dictionary lookup, no match/case:

```python
n = _DISPATCH[opcode](cpu)
```

CB, DD, ED, and FD prefix tables use the same pattern. When the main dispatcher encounters a prefix byte, it fetches the next byte and dispatches into the corresponding prefix table.

### Execution loop

`Z80.step() -> int` drives one instruction:

1. If `nmi_pending` is set: push PC, jump to 0x0066, return 11 T-states.
2. If `ei_pending` is set: clear it. (EI enables interrupts only after the instruction that follows it, so an accepted interrupt is suppressed for exactly one step.)
3. If `iff1` and `int_pending` are both true: accept the interrupt. Mode 1 — push PC, jump to 0x0038, 13 T-states. Mode 2 — push PC, read the vector byte from `I<<8 | data_bus`, jump to the handler address, 19 T-states.
4. If `halted`: advance 4 T-states without fetching an opcode.
5. Otherwise: record the current PC in `instruction_pc`, fetch the opcode via `_fetch()`, dispatch.

`_fetch()` reads one byte from `read_byte(PC)`, increments PC, and increments R (lower 7 bits only, wrapping at 0x7F).

### Timing

`step()` returns the number of T-states consumed. `Machine.run_frame()` accumulates these into a per-frame total and into `machine.cycle_count`. The NTSC frame budget is **59,659 T-states**, derived from the Z80A clock of 3.579545 MHz divided by 60 Hz.

### Known limitations

OTIR/INIR and similar block I/O instructions are not cycle-exact across page boundaries. The R register increments only on opcode fetches, not on data-bus accesses.

---

## Interrupt management

### MSX1: frame-based VBlank

On MSX1, the TMS9918A VDP has a single interrupt source: VBlank at the end of each frame. `Machine.__post_init__()` wires this as a callback:

```python
if not isinstance(self.vdp, V9938):
    self.vdp.on_interrupt = self._vblank_interrupt
```

`_vblank_interrupt` sets `cpu.int_pending = True`. The interrupt fires once per frame when `render_frame()` calls `vdp._finalize()`, which sets the VBlank status flag and invokes the callback. There are no per-scanline interrupts in the MSX1 path.

### MSX2: level-based IRQ

On MSX2 (V9938), interrupts are level-based. The inner loop of `Machine.run_frame()` samples `vdp9938.irq` at every instruction boundary:

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

The CPU sees a true interrupt level until the status flag is cleared by a port 0x99 read — matching hardware behaviour. A read of S#0 (R#15 = 0) clears F; a read of S#1 (R#15 = 1) clears FH.

### H-line interrupt

`Machine.run_frame()` divides the frame into scanlines and calls `vdp9938.begin_scanline(L)` once at the end of each scanline's T-state budget:

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

`begin_scanline(line)` computes the effective IRQ line as `(R#19 − R#23) & 0xFF` and sets S#1 bit 0 (FH) when `line == effective_irq_line` and `0 <= line < display_height`. IE1 (R#0 bit 4) gates whether a matching FH asserts `irq`; FH is set regardless of IE1, so software can poll S#1 without enabling the interrupt.

FH persists until S#1 is read; reading it clears FH and re-evaluates `irq`.

The VBlank flag is also set in `begin_scanline(display_height)` — the first line past the active display — replacing the old per-frame finalize path for MSX2.

---

## I/O bus

### Registration and dispatch

`IOBus` (`msx/io.py`) holds two lists of `(start, end, handler)` tuples, one for reads and one for writes. Devices register themselves during machine construction:

```python
io.register_read(0x98, 0x99, vdp.read_port)
io.register_write(0x98, 0x9B, vdp.write_port)
```

`read_port` and `write_port` mask the incoming 16-bit Z80 port address to 8 bits (`port &= 0xFF`) and do a linear scan. The first registered handler whose `[start, end]` range covers the port wins. If no handler matches, reads return 0xFF (open bus).

### Logging

When `_logger` is attached, reads are logged after the handler returns (the value is then known) and writes are logged before dispatching (the value is fixed at the call site). Both log entries record the port, value, and the current instruction PC.

### Standard port map

| Port(s) | Device |
|---------|--------|
| 0x98–0x9B | VDP (TMS9918A or V9938) |
| 0x9A | V9938 palette (MSX2 only) |
| 0xA0–0xA2 | PSG (AY-3-8910) |
| 0xA8–0xAB | PPI (i8255) |
| 0xB4–0xB5 | RTC (RP5C01, MSX2 only) |
| 0xFC–0xFF | RAM mapper segment registers (MSX2 only) |

---

## VDP

### TMS9918A (MSX1)

Implemented in `msx/vdp/vdp.py` with the renderer in `msx/vdp/renderer.py`. It has 16 KB VRAM, 8 control registers, and supports screen modes 0–3 (Text, Graphic 1/2, Multicolor). The full 256×192 frame is rendered at the end of `Machine.run_frame()` by `render_frame()`, which also calls `_finalize()` to set the VBlank flag and invoke `on_interrupt`.

### V9938 (MSX2)

Implemented in `msx/vdp/v9938.py` with the renderer in `msx/vdp/v9938_renderer.py`. Key differences from TMS9918A:

- 128 KB VRAM
- 28 control registers (R#0–R#27) plus 15 command-engine registers (R#32–R#46)
- Programmable 16-colour palette (9-bit GRB333); `_MSX2_DEFAULT_PALETTE` loaded at reset matches the V9938 data book power-on values
- Three status registers: S#0 (VBlank/sprite), S#1 (H-line FH), S#2 (command engine CE/TR, retrace HR/VR)
- Hardware command engine
- Level-based IRQ via `irq_pending()`

The `display_height` property returns 192 normally, or 212 when R#9 bit 7 (LN) is set. The `display_width` property returns 512 for SCREEN 6/G5 (M5 set, M4 clear) and 256 for all other modes.

### Banded renderer

Games that change VDP registers mid-frame — switching palette or screen mode between display regions — require the renderer to honour those changes at the correct scanline.

`V9938.write_port()` records every write to a display-relevant register into `_reg_write_log`:

```python
_DISPLAY_REGS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 19, 23})
```

Each entry is either:
- `(display_line, reg, value)` for a register write
- `(display_line, -1, (palette_index, grb_value))` for a palette write (port 0x9A), using sentinel `reg = -1`

The log is cleared at `begin_scanline(0)` (frame start) and is not cleared by `render_frame()`, so the SDL2 frontend can use it during RGB conversion to apply the correct palette per scanline.

`render_frame_v9938(vdp)` inspects `_reg_write_log` to find band boundaries — lines at which a display-relevant value differs from the previous band's snapshot. Each contiguous band `[y0, y1)` is rendered using the register state active at its start. Per-band renderers are called with explicit `y_start` and `y_end` parameters; each scanline is rendered exactly once across all bands.

When the log is empty (no mid-frame writes), `render_frame_v9938` falls back to a single-pass render, identical in output and performance to the pre-banding path.

Sprites are rendered once per sprite attribute table (SAT) region — a maximal run of consecutive bands sharing the same SAT base (R#5/R#11). When the SAT base is constant across the frame (the usual case), there is exactly one sprite pass, matching a single-pass render. When the SAT base changes mid-frame (e.g. a sprite multiplexer), each region's sprites are drawn from its own SAT, so distinct regions read distinct buffers and same-sprite duplicates are avoided.

### Command engine

The V9938 hardware command engine supports byte- and pixel-level VRAM operations:

| Command | Description |
|---------|-------------|
| HMMV | Fill a rectangle with a byte value |
| HMMM | Copy a rectangle (byte granularity) |
| HMMC | Transfer a block from CPU to VRAM (byte granularity) |
| LMMV | Fill a rectangle with a colour value (pixel granularity) |
| LMMM | Copy a rectangle (pixel granularity) |
| LMCM | Transfer a block from VRAM to CPU |
| LMMC | Transfer a block from CPU to VRAM (pixel granularity) |
| YMMM | Copy a rectangle using Y-axis only (byte granularity) |
| LINE | Draw a line |
| PSET | Write a single pixel |
| POINT | Read a single pixel value (result in S#7) |
| SRCH | Search a scanline for a colour match (result in S#8/S#9) |
| ABRT | Abort the active command |

Commands are dispatched immediately — the VRAM result is committed at dispatch time. An approximate cycle budget (`_cmd_remaining`) is decremented by `V9938.tick(n)` (called each instruction with the consumed T-states) to model command duration. The budget is calibrated at `_CYCLES_PER_BYTE = 8` T-states per VRAM byte, derived from openMSX golden-log comparisons (230K T-states for a 128×212 fill). Software that busy-waits on S#2 bit 0 (CE) will see CE clear after the budget expires.

The HMMC/LMMC transfer latch (`_cmd_transfer`) is set by writes to R#44 (COL). The first pixel/byte of a transfer comes from a pending COL write rather than being auto-loaded at dispatch, matching openMSX behaviour.

Logical operations (IMP, AND, OR, XOR, NOT) are applied per pixel via `_apply_log()` in `v9938.py`.

### Known limitations

- Command timing is approximate. Exact command durations differ from hardware; the V9938 data book per-command cycle counts are not reproduced.
- VRAM results are written at dispatch, not incrementally. Software reading VRAM while a command is nominally in progress may see the completed result before CE clears.
- The renderer is deferred: the CPU runs a full frame, VDP commands execute instantly into VRAM, then one render pass occurs at end-of-frame. VRAM updates that are synchronised to the raster within a frame (beam-raced blits, double-buffered title screens) are not reproduced faithfully.

---

## Memory and slot system

### MSX1 slot layout

`Memory` (`msx/memory.py`) maps the flat 64 KB address space through a 4-page × 4-slot dispatch. The slot-select register (port 0xA8, via PPI) determines which slot occupies each 16 KB page. Bits `[2N+1 : 2N]` of the register select the slot for page N.

Default MSX1 layout:

| Slot | Content |
|------|---------|
| 0 | BIOS ROM (read-only) |
| 1 | Cartridge ROM via mapper |
| 2 | Second cartridge or open bus (reads 0xFF) |
| 3 | 32 KB RAM at pages 2–3 (0x8000–0xFFFF) |

### MSX2 expanded sub-slot

For MSX2, slot 3 is expanded (`sub_slot_enabled = True`). Reading 0xFFFF returns `~sub_slot_reg` so software can detect sub-slot support. The sub-slot register (`sub_slot_reg`) selects which of the four secondary slots occupies each page within the expanded primary.

Default MSX2 layout:

| Sub-slot | Content |
|----------|---------|
| 3-0 | C-BIOS Sub ROM (read-only) |
| 3-1 | Empty |
| 3-2 | 128 KB RAM via RAM mapper |
| 3-3 | Empty |

### RAM mapper

`RamMapper` (`msx/ram_mapper.py`) divides 128 KB into 8 segments of 16 KB each. Four segment registers at ports 0xFC–0xFF independently control which segment is visible in each CPU page:

| Port | Page | Address range |
|------|------|---------------|
| 0xFC | 0 | 0x0000–0x3FFF |
| 0xFD | 1 | 0x4000–0x7FFF |
| 0xFE | 2 | 0x8000–0xBFFF |
| 0xFF | 3 | 0xC000–0xFFFF |

Writing the segment number (0–7) to the register remaps that page immediately.

---

## Machine YAML loader

### Two-pass resolution

`msx/machine_loader.py` resolves hardware topology in two passes:

1. **Device registry pass.** `load_device_registry(config_dir)` reads every `*.yaml` file under `config/devices/` into a dict keyed by device `id`. Each file describes one piece of hardware (VDP type, PSG, PPI, RTC, RAM mapper) in isolation — no machine context.

2. **Machine spec pass.** `load_machine_spec(machine_id, config_dir, device_registry, project_root)` loads `config/machines/<machine_id>.yaml`, validates required fields, resolves every `builtin_devices` `ref` against the registry, and returns a `MachineSpec` dataclass. An unresolved `ref`, unknown `schema_version`, or missing required ROM entry raises `MachineLoadError` naming the file and field.

### build_machine()

`build_machine(spec, cartridge, mapper, ...)` constructs a `Machine` from a resolved `MachineSpec`. The `spec.generation` field determines the concrete types:

- `"msx1"` — `VDP` (TMS9918A), flat slot 3 RAM, no RTC or RAM mapper
- `"msx2"` — `V9938`, expanded slot 3 with sub-ROM and `RamMapper`, `RTC`

Device YAML entries with `implemented: false` are skipped at load time with a stderr warning; the rest of the machine proceeds normally. This allows device definitions for unimplemented hardware (e.g. FDC, FM-PAC) to exist in the registry without breaking boots.

---

## Portability

The implementation is pure Python 3.10+ and carries no C extensions or native bindings beyond the SDL2 frontend. Several design decisions were made to keep the core logic straightforward to port to a statically-typed systems language (Rust, C++). Each decision is documented inline as a *Portability note* in the relevant source file; this section summarises them.

### Intentionally portable patterns

These patterns translate cleanly to any statically-typed target:

- **Opcode dispatch** (`msx/cpu/z80.py`, `msx/cpu/opcodes_main.py`) — `_DISPATCH` is a flat 256-element list of callables built once at import. Each entry is a plain function. A Rust port maps this to an array of function pointers or a `match` over the opcode byte.
- **Register file** (`msx/cpu/registers.py`) — primary registers are stored as Python `int` fields with explicit `& 0xFF` / `& 0xFFFF` masking at every write site, not as arbitrary-precision values. Shadow registers are discrete named fields (`A_`, `BC_`, etc.) with no reflection.
- **VRAM and RAM** — `bytearray` objects of fixed size (`_VRAM_SIZE = 131072` for V9938, `16384` for TMS9918A, segment arrays for the RAM mapper). Direct slice-and-index access throughout; no list-of-ints or dictionary-backed storage.
- **I/O bus dispatch** (`msx/io.py`) — linear scan over a `list[tuple[int, int, Callable]]`. No dictionary lookup or reflection. The number of registered handlers is small and fixed per machine configuration.
- **Component structs** — every hardware component is a `@dataclass(slots=True)`. All fields are explicitly declared with types; no `__getattr__`/`__setattr__` magic. `slots=True` avoids the `__dict__` per instance and makes the field layout explicit.
- **Timing constants** — `CYCLES_PER_FRAME`, `_TSTATES_PER_LINE`, `_HBLANK_START`, `_CYCLES_PER_BYTE` etc. are module-level integer constants, not derived at runtime.

### Python-specific patterns a port must adapt

The following patterns rely on Python language features that have no direct static-typed analogue. Each is documented in the source with an explicit note on what a Rust/C++ port would use instead.

#### Bus hooks as reassignable bound methods

`Z80` stores `read_byte` / `write_byte` / `read_port` / `write_port` as `Callable` fields (`z80.py:32`). `Machine.__post_init__` wires them by assigning bound methods at runtime. Enabling watchpoints later re-swaps `cpu.read_byte` / `cpu.write_byte` between the plain memory handler and a watchpoint-trapping variant (`machine.py:64`, `machine.py:159`).

Python allows this because a `Callable` field is just a slot that holds any object with `__call__`. In Rust/C++ there is no runtime method swap on a struct field; a port expresses the bus as a `trait MemoryBus` (or an `enum { Normal, Watchpoint }`) whose concrete implementation is selected once behind a flag, so the per-access dispatch stays branch-free.

#### Register 8-bit halves as computed properties

`Registers` stores `BC`, `DE`, `HL` as 16-bit `int` fields but exposes `B`/`C`, `D`/`E`, `H`/`L` as `@property` getter/setter pairs that shift and mask over the 16-bit pair (`registers.py:63`). Each opcode that reads or writes a single 8-bit half goes through the descriptor protocol, adding a call frame on the hot instruction path.

A Rust/C++ port stores the 8-bit halves as plain `u8` fields (or as explicit inline getter/setter methods) and derives the 16-bit pairs on demand, avoiding the property-call overhead entirely.

#### Arbitrary-precision integers in the PSG envelope

Python integers are arbitrary precision: `-1 & 0x20 == 0x20`. The PSG envelope generator uses this when `_env_step` goes negative — bit 5 of a negative Python int still acts as an underflow flag (`psg.py:155`).

Rust/C++ unsigned types wrap on underflow rather than extending sign, so the same expression produces 0 instead of `0x20`. A port must use a signed type (`i32`) or an explicit `wrapping_sub` / bitfield overlay to preserve the flag.

#### `bytes.translate()` LUT cache in the renderer

The V9938 G4/G6 and G5 per-scanline pixel expanders memoize `bytes.translate()` tables in a dict keyed on `(tp: bool, border: int)` (`v9938_renderer.py:54`). `bytes.translate()` is a CPython built-in that unpacks a full 256-byte lookup table in C, making it the fastest available path in Python for this transformation. The dict cache avoids allocating identical tables on every scanline.

Neither `bytes.translate()` nor a per-call dict hash are natural constructs in a systems language. A Rust/C++ port keeps fixed `[u8; 256]` arrays precomputed at init — only 32 combinations exist across all `(tp, border)` pairs — and indexes them directly.

#### Callable interrupt and tracer hooks

Both `VDP` (TMS9918A) and `V9938` store `on_interrupt`, `tracer`, `_get_pc`, and `_get_cycle` as nullable `Callable` fields (`vdp.py:22`, `v9938.py:126`). These are assigned at wiring time and invoked on each relevant event. The fields are typed as `Callable[[], None] | None` in Python but have no direct static analogue.

A Rust/C++ port models them as `Option<Box<dyn Fn()>>` trait objects (or equivalent), or as feature-flagged compile-time generics so the per-call dispatch disappears in release builds.

#### Spin loop in the frame timer

`FrameTimer.tick()` uses `time.perf_counter()` in a busy-wait loop for the final sub-millisecond stretch before the frame deadline (`frame_timer.py:51`). In Python there is no way to hint to the scheduler that this is a spin.

A Rust port inserts `std::hint::spin_loop()` inside the same loop to yield the CPU pipeline hint without sleeping, which can reduce power consumption and improve timing jitter on SMT cores.
