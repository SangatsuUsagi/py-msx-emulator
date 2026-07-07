# Debugger Reference

The emulator includes an interactive debug REPL accessible at any point during a run. It provides Z80 and VDP inspection, breakpoints, watchpoints, step execution, and tracing without requiring a separate debug build.

Commands marked **⚙V9938** require an MSX2 / V9938 machine. On MSX1 / TMS9918A they print a clear error message rather than raising an exception.

Source: `msx/debugger/prompt.py` (REPL), `msx/vdp/v9938.py` / `msx/vdp/v9938_renderer.py` (VDP), `msx/mapper_tracer.py` (mapper trace).

---

## Entering the debugger

### Ctrl+C

Press **Ctrl+C** in the terminal running the emulator (or in the SDL window). Emulation pauses immediately, the current PC is disassembled, and the REPL prompt appears:

```
Debugger entered. Type 'c' to resume, 'q' to exit.
  PC=C010h  3E 01        LD A,01h
(msx-dbg cyc=720430 frm=12)
```

`cyc` is the cumulative Z80 T-state count; `frm` is the completed-frame count. The displayed image is the last rendered frame. Type `c` to resume emulation, `q` to exit. Pressing Ctrl+C again while at the REPL prompt exits the emulator immediately (`sys.exit(0)`) — it does not cancel input or return to the prompt.

### Breakpoints and watchpoints at launch

Set breakpoints or watchpoints before the SDL window opens using CLI flags:

```bash
# Execution breakpoints — up to 4, comma-separated hex addresses (no 0x prefix)
python . path/to/game.rom --machine cbios_msx2_jp --break-point C000,D000

# Watchpoints — addr[,r|w|rw] pairs, comma-separated; default mode is rw
python . path/to/game.rom --machine cbios_msx2_jp --watch-point ca4a,w,fedc,rw
```

Active breakpoints and watchpoints are reported to stderr on startup.

---

## Command reference

### Execution flow

**`c`** — resume emulation.

**`q`** — exit the emulator (process exits with code 0).

**`s [N]`** — step `N` Z80 instructions (decimal; default 1). After stepping, prints a compact register summary and the next instruction:

```
  PC=C012  AF=0100  BC=0010  DE=0000  HL=C100
  => C012: CD 40 C0     CALL C040h
```

**`g ADDR`** — resume emulation and run until PC reaches `ADDR` (hex). One-shot temporary breakpoint — cleared once hit, does not consume a `ba` slot, and does not alter permanent breakpoints.

**`so`** — step out: resume until SP rises above its value at the time `so` was issued (i.e. the current routine returns). See *Known limitations* for edge cases.

---

### Breakpoints

**`ba ADDR`** — add an execution breakpoint at `ADDR` (hex, no `0x` prefix). Maximum 4 breakpoints. When PC matches, emulation pauses and the REPL opens.

**`br ADDR`** — remove the breakpoint at `ADDR`. Prints an error if the address is not in the active set.

**`bl`** — list all active breakpoints.

#### Crash-signature auto-break

**`bh`** — toggle break-on-HALT-with-interrupts-disabled. Breaks the instant the CPU executes `HALT` while `iff1 == 0` — the dead-hang signature: a HALT that can never be woken by an interrupt. Fires once per rising edge; resuming from a still-true condition does not re-trigger.

**`bs [LOW HIGH | off]`** — break when SP leaves valid RAM.

- `bs` (no arguments): enable with the range auto-derived from the machine RAM configuration (MSX1 flat RAM → top-of-memory window; MSX2 mapper RAM → full address space).
- `bs LOW HIGH`: enable with an explicit inclusive hex range.
- `bs off`: disable.

Catches stack corruption at the instruction that first drives SP out of the valid range. Fires on the rising edge only.

---

### Watchpoints

**`wa ADDR[,r|w|rw]`** — add a watchpoint at `ADDR`. Mode: `r` = break on read, `w` = break on write, `rw` = both (default). Maximum 4 watchpoints.

When hit, prints:
```
[WP] READ  CA4Ah = 02h  PC=C024h
```
or
```
[WP] WRITE CA4Ah = 05h  PC=C030h
```
then enters the REPL.

**`wd ADDR`** — remove the watchpoint at `ADDR`.

**`wl`** — list all active watchpoints with their modes.

---

### CPU state

**`rc`** — dump all Z80 registers:

```
AF=5A00  BC=0010  DE=0000  HL=C100  IX=F380  IY=FC40  SP=F3E0  PC=C010
  S=0  Z=1  H=0  P/V=0  N=0  C=0
```

**`da [ADDR]`** — disassemble 10 instructions from `ADDR` (hex) or the current PC:

```
  C010: 3E 01        LD A,01h
  C012: CD 40 C0     CALL C040h
  C015: 21 00 C1     LD HL,C100h
  ...
```

---

### Memory dump

**`dm ADDR [SIZE]`** — hex+ASCII dump of CPU address space. Both arguments are hex; SIZE defaults to 0x80 (128 bytes):

```
  F000: 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F  ................
  F010: 41 42 43 44 45 46 47 48 49 4A 4B 4C 4D 4E 4F 50  ABCDEFGHIJKLMNOP
```

**`dv VADDR [SIZE]`** — hex+ASCII dump of VRAM. Both arguments are hex; SIZE defaults to 0x80. Address wraps within the VRAM size (0x3FFF for TMS9918A; 0x1FFFF for V9938). The address prefix width adjusts automatically.

**`dvf FILE`** — write the entire VRAM contents as a raw binary file. Useful for offline diffing or loading into a reference tool.

---

### VDP state

**`v`** — VDP status summary.

On V9938 (⚙V9938):
```
  Screen : SCREEN5 (GRAPHIC4)
  VRAM   : Name=10000  Color=00000  Pat=00000  SprAttr=1FE00  SprPat=00000
  Disp   : EN=1 SPR=1 H=192 NTSC IL=0 SZ=0 MAG=0 FG=0F BG=00 IE0=1 IE1=0
  CMD    : ABRT LOG=IMP CE=0 TR=0  DX=0 DY=0 NX=0 NY=0
  Status : S#0=00 S#2=0C
```

On TMS9918A:
```
  Screen : SCREEN2 (GRAPHIC2)
  VRAM   : Name=1800  Color=2000  Pat=0000  SprAttr=1B00  SprPat=3800
  Disp   : EN=1 H=192 FG=0F BG=00 SZ=0 MAG=0
  Status : S#0=00
```

**`rv`** — VDP register dump.

On V9938 (⚙V9938): R#0–R#27 (display registers) followed by R#32–R#46 (command registers), 8 per row:
```
  R#0=00  R#1=72  R#2=1F  R#3=FF  R#4=3F  R#5=7F  R#6=07  R#7=00
  R#8=08  R#9=80  R#10=00  R#11=00  R#12=00  R#13=00  R#14=00  R#15=00
  ...
  R#32=00  R#33=00  R#34=00  R#35=00  R#36=00  R#37=00  R#38=00  R#39=00
  ...
```

On TMS9918A: R#0–R#7 in a single row.

**`rp`** ⚙V9938 — palette dump: all 16 entries as `#N=XXX(R,G,B)` where XXX is the 9-bit GRB333 raw value and R/G/B are the 3-bit channel values:

```
  #0=000(0,0,0)  #1=000(0,0,0)  #2=071(1,6,1)  #3=1FB(3,7,3)  ...
  #8=1C9(7,1,1)  #9=1DB(7,3,3)  #A=1B1(6,6,1)  #B=1B4(6,6,4)  ...
```

Prints a clear error on TMS9918A (no programmable palette).

---

### Sprites

**`ds`** — toggle sprite rendering on/off for both VDP types. When off, only the background layer is rendered. Useful for isolating sprite artefacts. State persists until toggled again or the emulator exits.

---

### Tracing

**`te`** ⚙V9938 — enable the VDP register-write tracer. Emits a line to stdout for every register write during emulation:

```
CY=12450 FR=3 PC=C018  VDP_REG R#2=1F
CY=12460 FR=3 PC=C01C  VDP_REG R#5=7F
```

`te` wires the `_get_pc` and `_get_cycle` callbacks automatically on first use. If a `Tracer` is already attached, it re-enables it.

**`td`** — disable VDP register tracing.

**`ce`** — enable the cartridge mapper bank-switch tracer. Emits a line on every bank-register write that changes a window's bank:

```
CY=25000 FR=5 PC=5B2A  MAP_BANK win=1 03h->05h addr=6001h
```

`win` is the mapper window index; `addr` is the address of the register write. Prints a message if no bank-switching mapper is present.

**`cd`** — disable mapper bank-switch tracing.

---

### Slot inspector

**`sl`** — active slot table: one row per page (P0–P3) with address range, primary slot, secondary slot, content, and bank/segment:

```
  Page  Addr        Prim  Sec  Content                          Bank
  P0    0000-3FFF   0     -    ROM cbios_main_msx2.rom          -
  P1    4000-7FFF   1     -    Cartridge (mapped)               w0=b2@04000  w1=b0@00000
  P2    8000-BFFF   1     -    Cartridge (mapped)               w0=b3@06000  w1=b1@02000
  P3    C000-FFFF   3     2    RAM (mapper)                     seg=2
```

Bank column values:
- RAM mapper pages: `seg=N`
- Cartridge ROM mapper pages: selected bank number and resolved ROM byte-offset range
- Flat ROM, BIOS, plain RAM: `-`

**`st`** — slot tree: walks primary slots 0–3 and shows the full sub-slot structure for expanded slots:

```
  Primary 0  ROM cbios_main_msx2.rom  64 KB
  Primary 1  [cartridge slot]  Cartridge (mapped)  128 KB
  Primary 2  [cartridge slot]  (empty)
  Primary 3 [EXPANDED]  secondary-select(raw)=08h
    page-map: P0->3-0  P1->3-0  P2->3-2  P3->3-2
    3-0  ROM cbios_sub.rom  32 KB
    3-1  (empty)
    3-2  RAM (mapper)  128 KB
    3-3  (empty)
```

On MSX1 (`sub_slot_enabled = False`), slot 3 is shown as a non-expanded primary.

---

### Screenshot

**`ss`** — render the current VDP state and save `screenshot_YYYYMMDD_HHMMSS.png` to the working directory. The internal frame counter is preserved; `ss` has no side-effect on emulation state. Useful for correlating a visual snapshot with `v`/`dv`/`rv` output taken at the same pause.

---

## Launch-time tracing (headless capture)

These options set up tracing before the first frame and work without entering the REPL.

### VDP register trace

```bash
# Print VDP_REG lines to stdout
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace

# Redirect to a file
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace --vdp-trace-out trace.log
```

Output format: `CY=.. FR=.. PC=..  VDP_REG R#N=XX`

### Mapper bank-switch trace

```bash
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace --mapper-trace-out banks.log
```

Output format: `CY=.. FR=.. PC=..  MAP_BANK win=W OLDh->NEWh addr=XXXXh`

If the cartridge uses a flat (non-bank-switching) mapper, a note is printed to stderr and no `MAP_BANK` lines are emitted.

### Headless N-frame run

```bash
python . path/to/game.rom --machine cbios_msx2_jp --count-frame 300 --vdp-trace
```

`--count-frame N` runs exactly N frames without opening an SDL window, then exits. Combined with `--vdp-trace` or `--mapper-trace`, it produces deterministic, scriptable captures. The mapper trace can also be enabled this way.

---

## Worked example

Set a breakpoint, resume, step through a routine, and inspect memory:

```
$ python . path/to/game.rom --machine cbios_msx2_jp
... (game boots) ...
^C
Debugger entered. Type 'c' to resume, 'q' to exit.
  PC=C010h  3E 01        LD A,01h
(msx-dbg cyc=720000 frm=12) ba C080
  Breakpoint set at C080h (1/4)
(msx-dbg cyc=720000 frm=12) c

... (breakpoint hit at C080) ...
  PC=C080h  CD 40 C0     CALL C040h
(msx-dbg cyc=724310 frm=12) rc
AF=0100  BC=0010  DE=0000  HL=C100  IX=F380  IY=FC40  SP=F3E0  PC=C080
  S=0  Z=1  H=0  P/V=0  N=0  C=0
(msx-dbg cyc=724310 frm=12) da
  C080: CD 40 C0     CALL C040h
  C083: 3A 00 C1     LD A,(C100h)
  C086: FE 02        CP 02h
  C088: 20 03        JR NZ,C08Dh
  ...
(msx-dbg cyc=724310 frm=12) s 1
  PC=C083  AF=0200  BC=0010  DE=0000  HL=C100
  => C083: 3A 00 C1     LD A,(C100h)
(msx-dbg cyc=724321 frm=12) dm C100 20
  C100: 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
  C110: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
(msx-dbg cyc=724321 frm=12) br C080
  Breakpoint C080h removed (0/4)
(msx-dbg cyc=724321 frm=12) c
```

---

## Known limitations

**Deferred renderer.** The CPU runs a full frame; VDP commands execute instantly into VRAM; the renderer runs once at end-of-frame. VRAM updates synchronised to the raster within a frame (beam-raced blits, double-buffered title screens) are not reproduced faithfully. This is a fundamental architectural constraint, not a renderer bug.

**Command timing.** VDP command completion (S#2 CE bit) is driven by an approximate cycle budget, not a cycle-accurate model. Software that busy-waits on CE works, but exact command durations differ from hardware.

**`so` and `bs` are SP heuristics.** `so` breaks when SP first rises above its value at issue time; a routine that switches stacks, pops more than it pushed, or is re-entered can fire early or late. Use `g ADDR` when you need a precise stop. `bs` checks SP against a static address range and cannot account for slot switching that legitimately remaps a page to RAM.
