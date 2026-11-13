# Debugger Reference

The emulator includes an interactive debug REPL that you can open at any point during a run. It gives Z80 and VDP inspection, breakpoints, watchpoints, step execution, and tracing.

The **⚙V9938** mark shows a command or an output form that needs an MSX2 / V9938 machine. On MSX1 / TMS9918A, `rp` prints an error message and does not raise an exception, and `v` and `rv` show the TMS9918A form.

Source: `host/debugger/prompt.py` (REPL), `msx/vdp/v9938.py` / `msx/vdp/v9938_renderer.py` (VDP), `host/trace.py` (VDP and mapper trace text).

---

## Entering the debugger

### Ctrl+C

Press **Ctrl+C** in the terminal that runs the emulator (or in the SDL window). In the terminal, the emulation stops before the next part of the frame, at most about one frame later. In the SDL window, it stops between frames. The debugger disassembles the current PC and shows the REPL prompt:

```
Debugger entered. Type 'c' to resume, 'q' to exit.
  PC=C010h  3E 01        LD A,01h
(msx-dbg cyc=720430 frm=12)
```

`cyc` is the cumulative Z80 T-state count. `frm` is the number of completed frames. The window shows the last rendered frame. If you press Ctrl+C in the SDL window, type the REPL commands in the terminal that started the emulator. The window does not update until you type `c`.

Type `c` to resume the emulation, or `q` to exit. A frame that stopped at a break or at Ctrl+C continues from where it stopped, so its VBlank and its remaining scanlines still run. If you press Ctrl+C again at the REPL prompt, the emulator exits immediately (`sys.exit(0)`). It does not cancel the input or return to the prompt.

With `--rpc`, Ctrl+C in the terminal also opens the REPL, and the RPC pause state does not change. While the REPL is open, the emulator serves no RPC request, so RPC calls time out until you type `c`. Breakpoints and watchpoints still pause the RPC server instead of opening the REPL.

### Line editing and history

The `readline` module is available in stock CPython and PyPy on macOS and Linux. Where it is available, the prompt gives the usual line editing and Tab completion of all command names. It also keeps a command history across runs, in `~/.msx_dbg_history_cpython` on CPython and in `~/.msx_dbg_history_pypy` on PyPy. The two interpreters write the file in different formats, so each one has its own file. The history holds the last 500 entries. The debugger reads it when the REPL opens and writes it when emulation resumes. Stock Windows CPython has no `readline`. The REPL still works there, but without editing, completion, or history.

### Breakpoints and watchpoints at launch

To set breakpoints or watchpoints before the SDL window opens, use CLI flags. Both flags work on any machine, MSX1 or MSX2:

```bash
# Execution breakpoints — up to 4, comma-separated hex addresses (no 0x prefix)
python . path/to/game.rom --machine cbios_msx2_jp --break-point C000,D000

# Watchpoints — addr[,r|w|rw] pairs, comma-separated; default mode is rw
python . path/to/game.rom --machine cbios_msx1_jp --watch-point ca4a,w,fedc,rw
```

At startup, the emulator writes the active breakpoints and watchpoints to stdout, as `break   : C000h, D000h` and `watch   : CA4Ah[w], FEDCh[rw]` lines.

---

## Command reference

**`h`**, **`?`**: print the one-line command summary. The debugger also shows this summary automatically after an unrecognized command.

The debugger splits command arguments on whitespace. Put double quotes around a path that contains a space, for example `fdd1 "MY DISK 1.dsk"` or `ld "saves/states/my checkpoint.state"`. This applies to every command that takes a filename (`fdd1`/`fdd2`, `ld`, `sv`, `dvf`). If a quote has no closing quote, the debugger reports `Parse error` and does not change the emulator state.

### Execution flow

**`c`**: resume emulation.

**`q`**: exit the emulator (the process exits with code 0).

**`reset`**: full power-on reset of the CPU, PSG, VDP, PPI, the primary/secondary slot registers, every cartridge mapper, and, when present, the SCC, FM-PAC, FDC, Kanji ROM, printer port, and RAM mapper. The memory and VRAM contents stay the same. Emulation then resumes immediately, as with `c`, because there is nothing to examine at the PC before the reset.

**`s [N]`**: step `N` Z80 instructions (decimal, default 1). After the step, the debugger prints a compact register summary and the next instruction:

```
  PC=C012  AF=0100  BC=0010  DE=0000  HL=C100
  => C012: CD 40 C0     CALL C040h
```

Each step is one instruction of the stopped frame, so the prompt's `cyc` rises by its T-states, and a step can cross a scanline, the VBlank or the end of the frame. A step does not stop at a breakpoint.

**`g ADDR`**: resume emulation and run until PC reaches `ADDR` (hex). This is a one-shot temporary breakpoint. The debugger clears it after the hit. It does not use a `ba` slot and does not change the permanent breakpoints.

**`gf N`**: resume emulation and run until the frame counter (the `frm=` value in the prompt) reaches the decimal frame number `N`. This is a one-shot check. The core checks it once at the end of each frame, also a frame that is not rendered, not per instruction. So it does not make execution slower, as an active `ba`/`g` breakpoint does. `N` must be greater than the current frame count. If it is not, `gf` prints an error and does not resume.

**`so`**: step out. Resume until SP goes above its value at the time of the `so` command (i.e. the current routine returns). See _Known limitations_ for edge cases.

---

### Breakpoints

**`ba ADDR`**: add an execution breakpoint at `ADDR` (hex, no `0x` prefix). The maximum is 4 breakpoints. When PC matches, the emulation pauses and the REPL opens.

**`br ADDR`**: remove the breakpoint at `ADDR`. If the address is not in the active set, the debugger prints an error.

**`bl`**: list all active breakpoints.

#### Crash-signature auto-break

**`bh`**: toggle break-on-HALT-with-interrupts-disabled. The debugger breaks at the moment the CPU executes `HALT` while `iff1 == 0`. This is the signature of a dead hang: no interrupt can ever wake this HALT. The break fires once per rising edge. If you resume while the condition is still true, it does not fire again.

**`bs [LOW HIGH | off]`**: break when SP goes out of valid RAM.

- `bs` (no arguments): enable, with a range that the debugger calculates from the RAM configuration of the machine. MSX1 flat RAM in slot 3 gives a top-of-memory window. RAM in slot 0 (as on the `hb_10`) gives the page that holds it. MSX2 mapper RAM gives the full address space.
- `bs LOW HIGH`: enable, with an explicit inclusive hex range. If LOW is above HIGH, the debugger swaps them.
- `bs off`: disable.

This finds stack corruption at the first instruction that moves SP out of the valid range. It fires on the rising edge only.

---

### Watchpoints

**`wa ADDR[,r|w|rw]`**: add a watchpoint at `ADDR`. The modes are `r` (break on read), `w` (break on write), and `rw` (both, the default). The maximum is 4 watchpoints. You can also give the mode after a space (`wa CA4A w`). A `wa` at an address that already has a watchpoint changes its mode.

At a hit, the debugger prints:

```
[WP] READ  CA4Ah = 02h  PC=C024h
```

or

```
[WP] WRITE CA4Ah = 05h  PC=C030h
```

Then it opens the REPL. The emulation stops after the accessing instruction completes, so a watched write is already in memory. `PC=` is the address of that instruction, and the REPL shows the next one.

When `s` runs an instruction that hits a watchpoint, the debugger prints the same line before the step summary and stays at the prompt.

**`wd ADDR`**: remove the watchpoint at `ADDR`.

**`wl`**: list all active watchpoints with their modes.

---

### CPU state

**`rc`**: dump all Z80 registers:

```
AF=5A00  BC=0010  DE=0000  HL=C100  IX=F380  IY=FC40  SP=F3E0  PC=C010
  S=0  Z=1  H=0  P/V=0  N=0  C=0
```

**`da [ADDR]`**: disassemble 10 instructions from `ADDR` (hex) or from the current PC:

```
  C010: 3E 01        LD A,01h
  C012: CD 40 C0     CALL C040h
  C015: 21 00 C1     LD HL,C100h
  ...
```

---

### Memory dump

**`dm ADDR [SIZE]`**: hex+ASCII dump of the CPU address space. Both arguments are hex. SIZE has a default of 0x80 (128 bytes):

```
  F000: 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F  ................
  F010: 41 42 43 44 45 46 47 48 49 4A 4B 4C 4D 4E 4F 50  ABCDEFGHIJKLMNOP
```

**`dv VADDR [SIZE]`**: hex+ASCII dump of VRAM. Both arguments are hex. SIZE has a default of 0x80. The address wraps within the VRAM size (0x3FFF for TMS9918A, 0x1FFFF for V9938). The width of the address prefix adjusts automatically.

**`dvf FILE`**: write the full VRAM contents to a raw binary file. Use it for offline diffs or to load the data into a reference tool.

---

### VDP state

**`v`**: VDP status summary.

On V9938 (⚙V9938):

```
  Screen : SCREEN5 (GRAPHIC4)
  VRAM   : Name=00000  Color=02000  Pattern=00800  SprAttr=07600  SprPat=07800
  Disp   : EN=ON   SPR=ON   192L  NTSC     Spr= 8x8 /mag=x1  FG=F BG=1  IE0
  CMD    : LMMC  IMP/T  [IDLE]  SRC=(0,0)  DST=(208,97)  SIZE=(8,8)  CLR=00  DIR=→↓
  Status : S#0=04 (  5S=0 C=0)  S#2=82 (TR   )
```

On TMS9918A:

```
  Screen : SCREEN2 (GRAPHIC2)
  VRAM   : Name=1800  Color=27C0  Pat=0000  SprAttr=1B00  SprPat=3800
  Disp   : EN=1  h=192  FG=F  BG=5  size=8  mag=0
  Status : S#0=04
```

**`rv`**: VDP register dump.

On V9938 (⚙V9938): R#0-R#27 (display registers), then R#32-R#46 (command registers), 8 per row:

```
  R#0=00  R#1=72  R#2=1F  R#3=FF  R#4=3F  R#5=7F  R#6=07  R#7=00
  R#8=08  R#9=80  R#10=00  R#11=00  R#12=00  R#13=00  R#14=00  R#15=00
  ...
  R#32=00  R#33=00  R#34=00  R#35=00  R#36=00  R#37=00  R#38=00  R#39=00
  ...
```

On TMS9918A: R#0-R#7 in a single row.

**`rp`** ⚙V9938: palette dump. It shows all 16 entries as `#N=XXX(R,G,B)`. XXX is the 9-bit value (R in bits 8-6, G in bits 5-3, B in bits 2-0), and R/G/B are the 3-bit channel values:

```
  #0=000(0,0,0)  #1=000(0,0,0)  #2=071(1,6,1)  #3=1FB(3,7,3)  ...
  #8=1C9(7,1,1)  #9=1DB(7,3,3)  #A=1B1(6,6,1)  #B=1B4(6,6,4)  ...
```

On TMS9918A, which has no programmable palette, it prints an error.

---

### Sprites

**`ds`**: toggle sprite rendering on/off for both VDP types. When sprite rendering is off, the renderer draws only the background layer. Use it to isolate sprite artifacts. The setting stays until you toggle it again or the emulator exits.

---

### Tracing

**`te`**: enable the VDP register-write trace, on both VDP types. During emulation, it writes one line to stdout for each register write. If `--vdp-trace-out FILE` was given at launch, the lines go to that file instead:

```
CY=0000012450 FR=000003 PC=C018 VDP_REG R#02=1Fh
CY=0000012460 FR=000003 PC=C01C VDP_REG R#05=7Fh
```

The core records the writes, and the debugger prints the lines at the end of each frame and after each `s` step. The lines of a frame therefore appear together, after the frame runs.

A write to R#46, which starts a VDP command, gives a `VDP_CMD` line in place of a `VDP_REG` line. The line shows the command, the logical operation, the written value, and the values written to R#32-R#45 since the previous R#46 write (`--` for a register not written):

```
CY=0000013000 FR=000003 PC=C030 VDP_CMD HMMV/IMP  (C0h) R32-45=['--','--','--','--','00','00','00','00','00','01','D4','00','00','00']
```

A register write through port 0x9B (indirect register access) ends with `;port 9Bh`.

**`td`**: disable VDP register tracing. If the trace is already off, `td` prints a message.

**`ce`**: enable the cartridge mapper bank-switch tracer. It writes one line for each bank-register write that changes the bank of a window:

```
CY=0000025000 FR=000005 PC=5B2A MAP_BANK win=1 03h->05h addr=6001h
```

`win` is the mapper window index. `addr` is the address of the register write. The lines go to stdout, or to the `--mapper-trace-out` file, and appear at the end of each frame and after each `s` step, like the VDP trace. If no bank-switching mapper is present in slot 1 or 2, `ce` prints a message.

**`cd`**: disable mapper bank-switch tracing.

---

### Slot inspector

**`sl`**: active slot table. It shows one row per page (P0-P3), with the address range, primary slot, secondary slot, content, and bank/segment:

```
  Page  Addr        Prim  Sec  Content                          Bank
  P0    0000-3FFF   0     -    ROM cbios_main_msx2_jp.rom       -
  P1    4000-7FFF   1     -    Cartridge Ascii8                 w0=b2@04000  w1=b0@00000
  P2    8000-BFFF   1     -    Cartridge Ascii8                 w2=b3@06000  w3=b1@02000
  P3    C000-FFFF   3     2    RAM (mapper:standard)            seg=2
```

Values in the Bank column:

- RAM mapper pages: `seg=N`
- Cartridge mapper pages: what each window of the page maps, that is, what a read through the window gets. A 16 KB window shows `bank N @XXXXX-YYYYY` (ASCII16, R-Type, FM-PAC), and an 8 KB window shows `wW=bN@XXXXX` (ASCII8, Konami, SCC, Game Master 2, Halnote). N is the page the window maps, which can differ from the value the guest wrote when that value is beyond the ROM.
- A window that maps SRAM: `SRAM@XXXX`, the offset in the SRAM, in place of the bank (`bank SRAM@0000`, `w3=SRAM@1000`). Halnote's SRAM at page 0 shows `SRAM@0000-3FFF`, and its sub-mapper adds `sub=…` to page 1.
- A window past the end of the ROM, which reads 0xFF: `open`.
- Flat ROM, BIOS, plain RAM: `-`

**`st`**: slot tree. It goes through primary slots 0-3 and shows the full sub-slot structure of expanded slots:

```
  Primary 0  ROM cbios_main_msx2_jp.rom  32KB
  Primary 1  [cartridge slot]  Cartridge Ascii8
  Primary 2  [cartridge slot]  Cartridge (empty)
  Primary 3 [EXPANDED]  secondary-select(raw)=A0h
    page-map: P0->3-0  P1->3-0  P2->3-2  P3->3-2
    3-0  ROM cbios_sub.rom  16KB
    3-1  empty
    3-2  RAM (mapper:standard)  128KB
    3-3  empty
```

On an MSX1 machine, which has no expanded slot, `st` shows slot 3 as a non-expanded primary slot.

When one page of a non-expanded slot holds something other than the slot's own content, `st` adds a line for that page under the slot. On the `hb_10`, slot 0 holds the main ROM in pages 0-1 and 16 KB of RAM in page 3:

```
  Primary 0  ROM hb-10_basic-bios1.rom  32KB
    P3  RAM  16KB
```

`sl` shows each page of slot 0 as what a read of it returns: `RAM` for that RAM page, and `empty` for a page past the end of the main ROM, which reads 0xFF.

---

### Floppy disk

**`fdd1 [FILE|-]`**, **`fdd2 [FILE|-]`**: show, swap, or eject the disk in floppy drive A / B. These commands work on machines with a floppy interface, e.g. `hb_f1xd` and `fs_a1f`.

- With no argument, the command prints the current mount (path or `empty`).
- A path mounts that `*.dsk` file. The path can be relative to the cwd or absolute, and the command expands `~`. At the next `c` (continue), the controller sees the new disk through the disk-change line. Both connection styles share the swap path, so the behavior is the same on WD2793 and TC8566AF machines.
- `-` ejects the current disk.

For errors (missing file, unreadable image, no such drive), the command reports the error and does not change the current disk. On a machine with no floppy interface, the command prints `this machine has no floppy interface`.

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

### Cassette tape

**`tape [blocks|load FILE|eject|rewind|seek SECONDS|seek #N]`**: show or change the cassette tape. These commands work on machines with a cassette port, e.g. `hb_10` and `hb_f1xd`.

- With no argument, the command prints the file, the position in seconds with the block at the position, the tape length and the motor state (or `empty`).
- `blocks` lists the blocks of the file: the number, the type (`binary`, `basic`, `ascii` for a file header, `data` for the other blocks), the file name, the start in seconds and the length in bytes.
- `load FILE` converts and mounts a `.cas` file at position 0, and prints the conversion warnings. Only CAS is supported. A WAV file is refused with a message to convert it with an external tool. [`tools/wav2cas.py`](../tools/README_tools.md#wav2caspy) does this conversion.
- `eject` removes the tape. `rewind` sets the position to 0.
- `seek SECONDS` sets the position in seconds. `seek #N` sets it to the start of block N.

The motor is not a command: only the guest starts and stops it (for example `CLOAD` or `BLOAD"CAS:"`). For errors (missing or invalid file, a position outside the tape, no tape, no cassette port), the command reports the error and does not change the tape. On a machine with no cassette port, `tape load` prints `this machine has no cassette port`.

```
(msx-dbg cyc=720000 frm=12) tape load game.cas
tape: mounted game.cas (2 blocks)
(msx-dbg cyc=720000 frm=12) tape
tape: game.cas  0.00 s (block 0) / 48.15 s  motor off
(msx-dbg cyc=720000 frm=12) tape blocks
  #0   binary  GAME        0.00 s  16 bytes
  #1   data                8.81 s  4000 bytes
(msx-dbg cyc=720000 frm=12) tape seek #1
tape: game.cas  8.81 s (block 1) / 48.15 s  motor off
(msx-dbg cyc=720000 frm=12) tape seek 99
tape: tape position 354374955 is outside 0-172343159
```

---

### Screenshot

**`ss`**: render the current VDP state and save it as `saves/screenshots/screenshot_YYYYMMDD_HHMMSS.png`. The F10 hotkey uses the same location:

```
(msx-dbg cyc=724321 frm=12) ss
screenshot saved: saves/screenshots/screenshot_20260101_120000.png
```

`ss` keeps the internal frame counter and has no side effect on the emulation state. Use it to compare a visual snapshot with the `v`/`dv`/`rv` output from the same pause.

---

### State save / load

**`sv [TITLE]`**: save the full machine state to `saves/states/<title>_YYYYMMDD_HHMMSS.state`. The socket RPC `state.save` method uses the same format and location (see [`docs/socket-rpc-mcp.md`](socket-rpc-mcp.md)). With no argument, `sv` uses a default title. An argument gives the title, not a file path. The REPL stays open.

`sv` also writes a PNG thumbnail with the same name, and points `saves/states/latest.state` and `latest.png` at the new files. Before it saves, `sv` writes the changes on a mounted disk back to its `.dsk` file, so the state and the disk file match.

**`ld FILE`**: restore the machine state from a `.state` file that you saved before. The REPL stays open and does not resume automatically. So you can examine the loaded state, or advance it precisely with `s` or `gf`. In these cases, `ld` prints an error, does not raise an exception, and changes nothing:

- The file is missing.
- The snapshot has an incompatible `format_version`.
- The snapshot comes from a different machine configuration (machine, extensions, cartridge ROMs).
- A drive does not hold the disk that it held at the save. `ld` does not swap disks, so swap them with `fdd1`/`fdd2` first.
- The deck does not hold the tape that it held at the save. `ld` does not change the tape, so mount it with `tape load` first.

```
(msx-dbg cyc=720000 frm=1999) sv before-glitch
state saved: saves/states/before-glitch_20260101_120000.state
(msx-dbg cyc=720000 frm=1999) gf 2080
  Running to frame 2080 ...

... (reopens once frame 2080 is reached) ...
(msx-dbg cyc=... frm=2080) ld saves/states/before-glitch_20260101_120000.state
state loaded: saves/states/before-glitch_20260101_120000.state
  Loaded state from saves/states/before-glitch_20260101_120000.state
(msx-dbg cyc=720000 frm=1999) gf 2001
  Running to frame 2001 ...
```

---

## Launch-time tracing (headless capture)

These options start tracing before the first frame. They work without the REPL.

### VDP register trace

```bash
# Print VDP_REG lines to stdout
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace

# Redirect to a file
python . path/to/game.rom --machine cbios_msx2_jp --vdp-trace --vdp-trace-out trace.log
```

Output format: `CY=.. FR=.. PC=.. VDP_REG R#NN=XXh`, and a `VDP_CMD` line for each R#46 write (see `te`)

`--vdp-trace` traces both VDP types, the TMS9918A of an MSX1 machine too.

### Mapper bank-switch trace

```bash
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace
python . path/to/game.rom --machine cbios_msx2_jp --mapper-trace --mapper-trace-out banks.log
```

Output format: `CY=.. FR=.. PC=.. MAP_BANK win=W OLDh->NEWh addr=XXXXh`

If the cartridge uses a flat (non-bank-switching) mapper, the emulator writes a note to stderr and writes no `MAP_BANK` lines.

### Diagnostic log

```bash
python . path/to/game.rom --machine cbios_msx2_jp --debug
python . path/to/game.rom --machine cbios_msx2_jp --debug --log debug.log
```

`--debug` writes a diagnostic log to stderr. With `--log FILE`, the emulator also appends the same lines to `FILE`. `--log` needs `--debug`: without `--debug`, the emulator ignores `--log` and writes no file. Each line starts with a category:

| Category | Line                                                                                                                                            |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `[IO]`   | `IN` or `OUT` for each I/O port access, with the port, the value and the PC of the instruction. A cassette motor change, with the tape position |
| `[BOOT]` | The VDP display turns on or off (R#1), or the screen mode changes (R#0, R#1)                                                                    |
| `[CPU]`  | An undefined opcode, or one line of the instruction history before a hang                                                                       |
| `[HANG]` | The same PC runs 1000 times in a row, or a HALT with interrupts disabled at the end of a frame                                                  |

Before each `[HANG]` line, the log shows the last 64 instructions as `[CPU]  trace` lines. Each hang location is reported only once. The emulation does not stop at a `[HANG]` line. To stop at a HALT with interrupts disabled, use `bh` in the REPL.

### Headless N-frame run

```bash
python . path/to/game.rom --machine cbios_msx2_jp --count-frame 300 --vdp-trace
```

`--count-frame N` runs exactly N frames without an SDL window, and then exits. With `--vdp-trace` or `--mapper-trace`, it gives deterministic captures that a script can run.

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

**Deferred renderer.** The CPU runs a full frame, and VDP commands write into VRAM instantly. The renderer then runs once per frame, at the start of vertical blanking. On the V9938, it draws mid-frame register and palette changes in bands, at the line where each change took effect (the TMS9918A applies the last register values to the whole frame). VRAM writes have no such record. So the emulator does not faithfully reproduce VRAM updates that follow the raster within a frame (beam-raced blits, double-buffered title screens). This limit comes from the basic architecture, and a renderer fix cannot remove it.

**Command timing.** An approximate cycle budget controls the completion of VDP commands (S#2 CE bit). There is no cycle-accurate model. Software that busy-waits on CE works, but the exact command durations differ from hardware.

**`so` and `bs` are SP heuristics.** `so` breaks when SP first goes above its value at the time of the command. A routine that switches stacks, pops more than it pushed, or runs again before it returns can make `so` fire early or late. For a precise stop, use `g ADDR`. `bs` compares SP with a static address range. It does not take into account slot switching that correctly remaps a page to RAM.
