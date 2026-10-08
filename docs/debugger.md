# Debugger Reference

The emulator includes an interactive debug REPL that you can open at any point
during a run. It gives Z80 and VDP inspection, breakpoints, watchpoints, step
execution, and tracing.

Commands with the **⚙V9938** mark need an MSX2 / V9938 machine. On MSX1 /
TMS9918A, they print a clear error message and do not raise an exception.

Source: `msx/debugger/prompt.py` (REPL), `msx/vdp/v9938.py` /
`msx/vdp/v9938_renderer.py` (VDP), `msx/mapper_tracer.py` (mapper trace).

---

## Entering the debugger

### Ctrl+C

Press **Ctrl+C** in the terminal that runs the emulator (or in the SDL window).
The emulation pauses immediately. The debugger disassembles the current PC and
shows the REPL prompt:

```
Debugger entered. Type 'c' to resume, 'q' to exit.
  PC=C010h  3E 01        LD A,01h
(msx-dbg cyc=720430 frm=12)
```

`cyc` is the cumulative Z80 T-state count. `frm` is the number of completed
frames. The window shows the last rendered frame. Type `c` to resume the
emulation, or `q` to exit. If you press Ctrl+C again at the REPL prompt, the
emulator exits immediately (`sys.exit(0)`). It does not cancel the input or
return to the prompt.

### Line editing and history

The `readline` module is available in stock CPython and PyPy on macOS and
Linux. Where it is available, the prompt gives the usual line editing and Tab
completion of all command names. It also keeps a command history across runs in
`~/.msx_dbg_history`. The history holds the last 500 entries. The debugger
reads it when the REPL opens and writes it when emulation resumes. Stock
Windows CPython has no `readline`. The REPL still works there, but without
editing, completion, or history.

### Breakpoints and watchpoints at launch

To set breakpoints or watchpoints before the SDL window opens, use CLI flags.
Both flags work on any machine, MSX1 or MSX2:

```bash
# Execution breakpoints — up to 4, comma-separated hex addresses (no 0x prefix)
python . path/to/game.rom --machine cbios_msx2_jp --break-point C000,D000

# Watchpoints — addr[,r|w|rw] pairs, comma-separated; default mode is rw
python . path/to/game.rom --machine cbios_msx1_jp --watch-point ca4a,w,fedc,rw
```

At startup, the emulator writes the active breakpoints and watchpoints to
stderr.

---

## Command reference

**`h`**, **`?`**: print the one-line command summary. The debugger also shows
this summary automatically after an unrecognized command.

> **Filenames with spaces:** the debugger splits command arguments on
> whitespace. Put double quotes around a path that contains a space, for
> example `fdd1 "MY DISK 1.dsk"` or `ld "saves/states/my checkpoint.state"`.
> This applies to every command that takes a filename (`fdd1`/`fdd2`, `ld`,
> `sv`, `dvf`). If a quote has no closing quote, the debugger reports
> `Parse error` and does not change the emulator state.

### Execution flow

**`c`**: resume emulation.

**`q`**: exit the emulator (the process exits with code 0).

**`reset`**: full power-on reset of the CPU, PSG, VDP, PPI, the
primary/secondary slot registers, every cartridge mapper, and, when present,
the SCC, FM-PAC, FDC, Kanji ROM, and RAM mapper. The memory and VRAM contents
stay the same. Emulation then resumes immediately, as with `c`, because there is nothing
to examine at the PC before the reset.

**`s [N]`**: step `N` Z80 instructions (decimal, default 1). After the step,
the debugger prints a compact register summary and the next instruction:

```
  PC=C012  AF=0100  BC=0010  DE=0000  HL=C100
  => C012: CD 40 C0     CALL C040h
```

**`g ADDR`**: resume emulation and run until PC reaches `ADDR` (hex). This is
a one-shot temporary breakpoint. The debugger clears it after the hit. It does
not use a `ba` slot and does not change the permanent breakpoints.

**`gf N`**: resume emulation and run until the frame counter (the `frm=` value
in the prompt) reaches the decimal frame number `N`. This is a one-shot check.
The debugger checks it once per rendered frame, not per instruction. Thus it
does not make execution slower, as an active `ba`/`g` breakpoint does. `N` must
be greater than the current frame count. If it is not, `gf` prints an error and
does not resume.

**`so`**: step out. Resume until SP goes above its value at the time of the
`so` command (i.e. the current routine returns). See _Known limitations_ for
edge cases.

---

### Breakpoints

**`ba ADDR`**: add an execution breakpoint at `ADDR` (hex, no `0x` prefix).
The maximum is 4 breakpoints. When PC matches, the emulation pauses and the
REPL opens.

**`br ADDR`**: remove the breakpoint at `ADDR`. If the address is not in the
active set, the debugger prints an error.

**`bl`**: list all active breakpoints.

#### Crash-signature auto-break

**`bh`**: toggle break-on-HALT-with-interrupts-disabled. The debugger breaks at
the moment the CPU executes `HALT` while `iff1 == 0`. This is the signature of
a dead hang: no interrupt can ever wake this HALT. The break fires once per
rising edge. If you resume while the condition is still true, it does not fire
again.

**`bs [LOW HIGH | off]`**: break when SP goes out of valid RAM.

- `bs` (no arguments): enable, with a range that the debugger calculates from
  the RAM configuration of the machine. MSX1 flat RAM gives a top-of-memory
  window. MSX2 mapper RAM gives the full address space.
- `bs LOW HIGH`: enable, with an explicit inclusive hex range.
- `bs off`: disable.

This finds stack corruption at the first instruction that moves SP out of the
valid range. It fires on the rising edge only.

---

### Watchpoints

**`wa ADDR[,r|w|rw]`**: add a watchpoint at `ADDR`. The modes are `r` (break on
read), `w` (break on write), and `rw` (both, the default). The maximum is 4
watchpoints.

At a hit, the debugger prints:

```
[WP] READ  CA4Ah = 02h  PC=C024h
```

or

```
[WP] WRITE CA4Ah = 05h  PC=C030h
```

Then it opens the REPL.

**`wd ADDR`**: remove the watchpoint at `ADDR`.

**`wl`**: list all active watchpoints with their modes.

---

### CPU state

**`rc`**: dump all Z80 registers:

```
AF=5A00  BC=0010  DE=0000  HL=C100  IX=F380  IY=FC40  SP=F3E0  PC=C010
  S=0  Z=1  H=0  P/V=0  N=0  C=0
```

**`da [ADDR]`**: disassemble 10 instructions from `ADDR` (hex) or from the
current PC:

```
  C010: 3E 01        LD A,01h
  C012: CD 40 C0     CALL C040h
  C015: 21 00 C1     LD HL,C100h
  ...
```

---

### Memory dump

**`dm ADDR [SIZE]`**: hex+ASCII dump of the CPU address space. Both arguments
are hex. SIZE has a default of 0x80 (128 bytes):

```
  F000: 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F  ................
  F010: 41 42 43 44 45 46 47 48 49 4A 4B 4C 4D 4E 4F 50  ABCDEFGHIJKLMNOP
```

**`dv VADDR [SIZE]`**: hex+ASCII dump of VRAM. Both arguments are hex. SIZE has
a default of 0x80. The address wraps within the VRAM size (0x3FFF for
TMS9918A, 0x1FFFF for V9938). The width of the address prefix adjusts
automatically.

**`dvf FILE`**: write the full VRAM contents to a raw binary file. Use it for
offline diffs or to load the data into a reference tool.

---

### VDP state

**`v`**: VDP status summary.

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

**`rv`**: VDP register dump.

On V9938 (⚙V9938): R#0-R#27 (display registers), then R#32-R#46 (command
registers), 8 per row:

```
  R#0=00  R#1=72  R#2=1F  R#3=FF  R#4=3F  R#5=7F  R#6=07  R#7=00
  R#8=08  R#9=80  R#10=00  R#11=00  R#12=00  R#13=00  R#14=00  R#15=00
  ...
  R#32=00  R#33=00  R#34=00  R#35=00  R#36=00  R#37=00  R#38=00  R#39=00
  ...
```

On TMS9918A: R#0-R#7 in a single row.

**`rp`** ⚙V9938: palette dump. It shows all 16 entries as `#N=XXX(R,G,B)`.
XXX is the 9-bit GRB333 raw value, and R/G/B are the 3-bit channel values:

```
  #0=000(0,0,0)  #1=000(0,0,0)  #2=071(1,6,1)  #3=1FB(3,7,3)  ...
  #8=1C9(7,1,1)  #9=1DB(7,3,3)  #A=1B1(6,6,1)  #B=1B4(6,6,4)  ...
```

On TMS9918A, which has no programmable palette, it prints a clear error.

---

### Sprites

**`ds`**: toggle sprite rendering on/off for both VDP types. When sprite
rendering is off, the renderer draws only the background layer. Use it to
isolate sprite artifacts. The setting stays until you toggle it again or the
emulator exits.

---

### Tracing

**`te`**: enable the VDP register-write tracer, on both VDP types. During emulation, it
writes one line to stdout for each register write:

```
CY=0000012450 FR=000003 PC=C018 VDP_REG R#02=1Fh
CY=0000012460 FR=000003 PC=C01C VDP_REG R#05=7Fh
```

On first use, `te` connects the `_get_pc` and `_get_cycle` callbacks
automatically. If a `Tracer` is already attached, `te` enables it again.

**`td`**: disable VDP register tracing.

**`ce`**: enable the cartridge mapper bank-switch tracer. It writes one line
for each bank-register write that changes the bank of a window:

```
CY=0000025000 FR=000005 PC=5B2A MAP_BANK win=1 03h->05h addr=6001h
```

`win` is the mapper window index. `addr` is the address of the register write.
If no bank-switching mapper is present, `ce` prints a message.

**`cd`**: disable mapper bank-switch tracing.

---

### Slot inspector

**`sl`**: active slot table. It shows one row per page (P0-P3), with the
address range, primary slot, secondary slot, content, and bank/segment:

```
  Page  Addr        Prim  Sec  Content                          Bank
  P0    0000-3FFF   0     -    ROM cbios_main_msx2.rom          -
  P1    4000-7FFF   1     -    Cartridge (mapped)               w0=b2@04000  w1=b0@00000
  P2    8000-BFFF   1     -    Cartridge (mapped)               w0=b3@06000  w1=b1@02000
  P3    C000-FFFF   3     2    RAM (mapper)                     seg=2
```

Values in the Bank column:

- RAM mapper pages: `seg=N`
- Cartridge ROM mapper pages: the selected bank number and the resolved ROM
  byte-offset range
- Flat ROM, BIOS, plain RAM: `-`

**`st`**: slot tree. It goes through primary slots 0-3 and shows the full
sub-slot structure of expanded slots:

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

On MSX1 (`sub_slot_enabled = False`), `st` shows slot 3 as a non-expanded
primary slot.

---

### Floppy disk

**`fdd1 [FILE|-]`**, **`fdd2 [FILE|-]`**: show, swap, or eject the disk in
floppy drive A / B. These commands work on machines with a floppy interface,
e.g. `hb_f1xd` and `fs_a1f`.

- With no argument, the command prints the current mount (path or `empty`).
- A path mounts that `*.dsk` file. The path can be relative to the cwd or
  absolute, and the command expands `~`. At the next `c` (continue), the
  controller sees the new disk through the disk-change line. Both connection
  styles share the swap path, so the behavior is the same on WD2793 and
  TC8566AF machines.
- `-` ejects the current disk.

For errors (missing file, unreadable image, no such drive), the command
reports the error and does not change the current disk. On a machine with no
floppy interface, the command prints `this machine has no floppy interface`.

```
(msx-dbg cyc=720000 frm=12) fdd1
fdd1: /path/to/game.dsk
(msx-dbg cyc=720000 frm=12) fdd1 disk2.dsk
fdd1: mounted disk2.dsk
(msx-dbg cyc=720000 frm=12) fdd1 "MY DISK 1.dsk"
fdd1: mounted MY DISK 1.dsk
(msx-dbg cyc=720000 frm=12) fdd1 -
fdd1: ejected
```

---

### Screenshot

**`ss`**: render the current VDP state and save it as
`saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png`. The F10 hotkey uses the
same location:

```
(msx-dbg cyc=724321 frm=12) ss
screenshot saved: saves/screenshots/screenshot_20260101_120000.png
```

`ss` keeps the internal frame counter and has no side effect on the emulation
state. Use it to compare a visual snapshot with the `v`/`dv`/`rv` output from
the same pause.

---

### State save / load

**`sv [TITLE]`**: save the full machine state (CPU, VDP, mappers, RAM) to
`saves/states/<title>_YYYYMMDD_HHMMSS.state`. The socket RPC `state.save`
method uses the same format and location (see
[`docs/socket-rpc-mcp.md`](socket-rpc-mcp.md)). With no argument, `sv` uses a
default title. An argument gives the title, not a file path. The REPL stays
open.

**`ld FILE`**: restore the machine state from a `.state` file that you saved
before. The REPL stays open and does not resume automatically. You can thus
examine the loaded state, or advance it precisely with `s` or `gf`. If the file
is missing, or the snapshot has an incompatible `format_version` or machine
type, `ld` prints an error and does not raise an exception.

```
(msx-dbg cyc=720000 frm=1999) sv before-glitch
state saved: saves/states/before-glitch_20260101_120000.state
(msx-dbg cyc=720000 frm=1999) gf 2080
  Running to frame 2080 ...

... (reopens once frame 2080 is reached) ...
(msx-dbg cyc=... frm=2080) ld saves/states/before-glitch_20260101_120000.state
  Loaded state from saves/states/before-glitch_20260101_120000.state
(msx-dbg cyc=720000 frm=1999) gf 2001
  Running to frame 2001 ...
```

---

## Launch-time tracing (headless capture)

These options start tracing before the first frame. They work without the
REPL.

### VDP register trace

```bash
# Print VDP_REG lines to stdout
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace

# Redirect to a file
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace --vdp-trace-out trace.log
```

Output format: `CY=.. FR=.. PC=.. VDP_REG R#NN=XXh`

### Mapper bank-switch trace

```bash
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace --mapper-trace-out banks.log
```

Output format: `CY=.. FR=.. PC=.. MAP_BANK win=W OLDh->NEWh addr=XXXXh`

If the cartridge uses a flat (non-bank-switching) mapper, the emulator writes a
note to stderr and writes no `MAP_BANK` lines.

### Headless N-frame run

```bash
python . path/to/game.rom --machine cbios_msx2_jp --count-frame 300 --vdp-trace
```

`--count-frame N` runs exactly N frames without an SDL window, and then exits.
With `--vdp-trace` or `--mapper-trace`, it gives deterministic captures that a
script can run.

---

## Worked example

Set a breakpoint, resume, step through a routine, and examine memory:

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

**Deferred renderer.** The CPU runs a full frame, and VDP commands write into
VRAM instantly. The renderer then runs once per frame, at the start of vertical
blanking. The emulator does not faithfully reproduce VRAM updates that follow
the raster within a frame (beam-raced blits, double-buffered title screens).
This limit comes from the basic architecture, and a renderer fix cannot remove
it.

**Command timing.** An approximate cycle budget controls the completion of VDP
commands (S#2 CE bit). There is no cycle-accurate model. Software that
busy-waits on CE works, but the exact command durations differ from hardware.

**`so` and `bs` are SP heuristics.** `so` breaks when SP first goes above its
value at the time of the command. A routine that switches stacks, pops more
than it pushed, or runs again before it returns can make `so` fire early or
late. For a precise stop, use `g ADDR`. `bs` compares SP with a static address
range. It does not take into account slot switching that correctly remaps a
page to RAM.
