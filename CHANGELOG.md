# Changelog

[日本語版はこちら](CHANGELOG_ja.md)

- **v2.7.9** (2026-10-20): Emulate the printer port and a Sony HBP-F1/HBP-F1C printer that prints to PNG pages.
  - New: the MSX printer port (I/O 0x90/0x91). Software sends a byte by latching it on 0x91 and pulsing STROBE (bit 0
    of 0x90). With no printer attached, the status port reads busy (0xFF), as on a machine with an empty connector.
  - New: `--printer-log FILE` attaches a logger that writes every byte the printer receives to FILE, unmodified.
  - New: `--printer-out DIR` attaches an emulated HBP-F1/HBP-F1C. It renders each page as a 1586 x 1980-dot, 180 dpi
    colour PNG (`page-001.png`, ...). It is a feature-level implementation of the control codes in the HBP-F1C manual,
    not a complete emulation of the printer:
    - pica, elite and condensed ANK; kanji mode with JIS kanji, `00 xx` half-width pairs and kanji pitch;
    - CR, LF, FF, reverse feed, line pitch and margins; a page that runs past the bottom continues on the next page;
    - double width and double height, outline/italic/bold decoration, underline;
    - seven colours (ESC C), with overprinted inks mixing (e.g. yellow over purple gives black). The mixing and the ink
      colours are approximations.
  - Fonts: the real printer has its own fonts. The emulation uses the MSX main ROM font for ANK and the Kanji ROM
    (e.g. `--extension2 hbi_j1`) for kanji, scaled to the printer's cells with a fixed-width pen so that strokes keep
    one width. Without a Kanji ROM, kanji print blank.
  - Not implemented: font selection, vertical writing (ESC t prints horizontally), hagaki mode, gaiji, bit images,
    emphasis, tabs, page length and other codes. Each is skipped with its documented parameter length and listed in
    `DIR/unsupported.log`, with a count per code at the end, so you can see which missing features software uses.
  - Docs: README has a Printer section. The verified hardware now includes the Sony HBP-F1C, and the verified software
    includes Mahjong Friend (雀フレンド).
- **v2.7.8** (2026-10-17): Fix keyboard, V9938 and machine-loader behaviour that did not match the specs.
  - Keyboard: SHIFT+"0" reaches the "_" cell on the Japanese (JIS) layout only. On the International layout it now
    types ")" as on real hardware.
  - V9938: a register or palette write that takes effect only after the last visible line no longer changes the frame
    being drawn. S#9 reads its unused bits 7-1 as 1, also before any SRCH command has run.
  - Machine loader: a slot-0 `ram` entry must declare one page (0-3) and 16 KB, and must not share page 2 with the
    logo ROM; other shapes stop loading with an error. MSX2 machines now wire a slot-0 RAM entry too (before, only
    MSX1 machines did).
- **v2.7.7** (2026-10-17): SDL2 frontend hotkeys for disk swap, pause, fast-forward, mute and reset.
  - New: `--fdd1` and the new `py_emulator.yaml` key `fdd1` take a list of disk images for drive A. F6 mounts the next
    image (it wraps to the first), and Shift+F6 ejects the disk or inserts it again. Both use the same disk swap as the
    RPC `fdd.swap` method and the debugger, and do nothing on a machine without an FDC.
  - Shift+hotkey combos do not reach the MSX keyboard matrix: the MSX SHIFT key is released while the combo is held.
    Shift+F1..F5 still give MSX F6-F10.
  - New: F7 fast-forwards while held and Shift+F7 latches it. Fast-forward runs without frame pacing, presents at
    most at the machine frame rate, and plays no audio. F12 mutes (the sound chips keep running), and Shift+F12 resets
    the machine.
  - New: F11 pauses and resumes. This pause is separate from the RPC pause: neither one resumes the other, and
    emulation runs only when neither is set. The audio device is paused while either is set.
  - **Changed:** fullscreen moves from F11 to Shift+F11.
  - New: the `py_emulator.yaml` section `hotkeys` assigns other function keys (`f1`..`f12`, optionally `shift+`) to
    these hotkeys. A key used twice, an MSX key (F1-F5, Shift+F1..F5) or a fixed hotkey (F8-F10) stops startup with an
    error.
  - The window title shows `[PAUSED]`, `[FF]`, `[MUTE]` and the drive A disk position, e.g.
    `py-msx-emulator — <title> [60 fps] [MUTE] disk 2/3: b.dsk`.
  - Docs: README and `docs/technical-implementation.md` (and their Japanese versions) are revised. They now cover PAL
    timing, the configurable RAM mapper size, the FM-PAC and Kanji ROM ports, and the new hotkeys, and state that the
    `openspec/` and `allium/` specs are not published yet.
- **v2.7.6** (2026-10-17): Emulate the key click on PPI Port C bit 7.
  - New: each change of PPI Port C bit 7 (port 0xAA write or 0xAB bit set/reset) drives a 1-bit DAC, so the BIOS key
    click is audible and software that toggles the bit plays 1-bit PCM. The level relative to the PSG follows openMSX's
    machine configurations. The mix has no DC-blocking stage, so a bit held at 1 stays as a constant offset instead of
    decaying.
  - The key click and the Majutsushi cartridge DAC share one write-driven DAC channel. A DAC that is silent for a frame
    is not mixed, so an idle machine keeps the PSG-only audio path. The Majutsushi DAC level is now restored from a save
    state and saved even when the write is not yet rendered; the save-state format does not change.
- **v2.7.5** (2026-10-08): Fix the MSX1 VDP and the PPI behaviour found by tests on the author's real Sony HB-10.
  - MSX1 VDP (TMS9918A): a port 0x98 write also refills the read-ahead buffer, so an IN right after an OUT returns the
    written byte, as on the V9938.
  - PPI: a port 0xAB read on the HB-10 (Yamaha S3527) returns a byte latched by the last PPI access: the byte a read of
    0xA8-0xAA returned, or the inverse of the byte a write to 0xA8-0xAB carried. The `ppi8255` device setting
    `control_readback` (true/false) is replaced by `control_read`: `control` (the last mode word, the default),
    `open_bus` (0xFF, the S1985 machines) or `bus_latch` (`hb_10`, `hb_10_64k`). A machine file that still sets
    `control_readback` is rejected with a message naming `control_read`.
  - The HB-10 also confirmed the R register count, the undocumented Y/X flags, the absorbed DD prefix timing, the ED
    mirrors of NEG/RETN and the half-written control pair on the MSX1 VDP; no change was needed there.
  - Documentation matches the current behaviour: R counting, the per-machine PPI 0xAB read, the loader's `ppi8255`
    keys, the full scope of the debugger `reset`, `te` on both VDP types and the trace output format. Benchmark table
    and history charts updated to v2.7.5.
- **v2.7.4** (2026-10-07): Emulate the V9938 R#13 blink, and fix V9938, Z80 and PPI behaviour found by tests on the
  author's real Sony HB-F1XD.
  - New: R#13 blink. In TEXT2 (SCREEN 0, WIDTH 80), characters marked in the blink table use the R#12 colours during
    the ON period. In GRAPHIC 4-7 the display alternates between the page R#2 selects and the even page. Every R#13
    write restarts the timer; when one time nibble is 0, the state set by the write holds. A blink text colour 0 shows
    the blink background (as on the HB-F1XD; MAME draws colour 0). The timer is in the save state (format version still
    15; re-save older version-15 files).
  - V9938 status and addressing:
    - S#6 bit 1 is the display field (EO), as the status-register summary of the data book says, not collision Y bit 9.
    - The TEXT1 name-table index wraps at 1 KB (rows 25 and 26 with R#9 LN=1).
    - The VRAM address counter wraps inside the 16 KB bank in GRAPHIC 1/2, MULTICOLOR and TEXT1, and carries into R#14
      (which increments) in the M4/M5 modes.
    - A port 0x98 write also refills the read-ahead buffer, so an IN right after an OUT returns the written byte.
  - V9938 command engine:
    - LMCM completes when it fetches its last dot. The read that takes that dot then clears TR.
    - The last HMMC/LMMC byte leaves TR set; an R#44 write with no command running clears it.
    - With MXS set (no expansion RAM), POINT and LMCM give the all-ones dot value (0x0F in SCREEN 5).
    - Leaving the bitmap modes through R#0 no longer aborts a running command.
    - Block commands take their own time per byte or dot (HMMV 10, HMMM 21, YMMM 18 T-states per byte; LMMV 21, LMMM 30
      per dot), measured with the display and sprites on. Before, every command used 8.
  - Z80:
    - R counts M1 cycles only. Operand, address and displacement bytes no longer increment it.
    - The undocumented F bits 5 (Y) and 3 (X) now follow the documented per-instruction rules. BIT n,(HL) leaves them
      clear (MEMPTR is not emulated).
  - PPI: port 0xAB reads 0xFF on the S1985 machines (`hb_f1xd`, `hb_f1xd_256k`, `fs_a1f`), as on the HB-F1XD. A new
    `control_readback` setting of the `ppi8255` device selects this per machine. `fs_a1f` uses the same S1985 and is
    not verified on a real FS-A1F.
  - Comments now cite a second source (data books, data sheets, MSX Wiki, real-hardware results) where the behaviour
    came only from openMSX. The documentation was rewritten in plainer technical English and matches the current
    behaviour.
- **v2.7.3** (2026-10-03): Fix the V9938 display page in GRAPHIC 6/7 (SCREEN 7/8).
  - In these modes, R#2 is `0 0 A16 1 1 1 1 1`, so the 64 KB page bit is bit 5. The renderer read bit 6 (the
    GRAPHIC 4/5 layout). `SET PAGE 1` (R#2 = 0x3F) thus still showed page 0. A SCREEN 7 disk title that draws its menu
    on page 1 stayed on its title screen. The page base is now `(R#2 & 0x20) << 11`.
  - Also fix the R#9 EO (even/odd page alternation) field mapping. The first field (S#2 EO = 0) now shows the even
    page, and the second field shows the page that R#2 selects. This matches the V9938 data book (Special Functions
    2.2). Before this fix, the renderer XOR-toggled the page bit on odd frames. This inverted the mapping and made an
    even-page R#2 alternate upward to the next page.
- **v2.7.2** (2026-10-02): Primary slot 0 can now hold a flat RAM region next to the main ROM.
  - Declare it as a `ram: {size_kb, pages}` entry in the `content:` list of slot 0 (exactly one page, at most one
    entry).
  - Add the Sony HB-10 (`--machine hb_10`). This MSX1 has 16 KB of RAM at slot 0 page 3, next to the 32 KB BIOS, and
    its slot 3 is unpopulated (`size_kb: 0`, open bus). Without an explicit slot 3 entry, the loader would use a 32 KB
    default RAM. The memory-size probe of the BIOS would find that RAM and prefer it to the real 16 KB. The author owns
    a real HB-10, and the owned-hardware list now includes it.
  - Add `hb_10_64k`, an HB-10 with the 64 KB RAM modification that msx.org describes. This configuration removes the
    slot 0 RAM and has 64 KB of flat RAM across all four pages of slot 3. The author has not verified it against a
    modified unit.
  - The save-state format is now version 15, to carry the optional slot 0 RAM buffer. When slot 3 has no RAM, the
    `bs` auto-range of the debugger uses the slot 0 RAM window.
  - Documentation updates: the memory bus tables, the machine YAML schema chapter, and the extension slot model now
    cover slot 0 RAM and the per-machine MSX1 slot 3 RAM size. The documentation no longer describes `fs_a1f` as
    hardware-verified. The SCC+ example now uses `--machine hb_f1xd`, because C-BIOS has no floppy support.
- **v2.7.1** (2026-09-29): Reset now reaches every cartridge mapper and the PPI.
  - Before this change, most bank-switching ROM mapper classes had a deliberate no-op `reset()`:
    `Ascii8Mapper`/`Ascii16Mapper` and their SRAM variants, `KonamiMapper`/`MajutsushiMapper`/`KonamiSCCMapper`,
    `GameMaster2Mapper`, `RTypeMapper`, and the SCC-I cartridge. The reason was that on real hardware, the response of
    the bank-select logic of a cartridge to the slot reset line depends on the design of that cartridge. Also,
    `Machine.reset()` never called `reset()` on the two top-level cartridge-slot mappers.
  - This release reverses that, as an explicit emulation-policy choice. On reset, the bank registers of every
    cartridge mapper now go back to their power-on values, whatever the wiring of a given real cartridge is. For the
    SCC-I cartridge and `KonamiSCCMapper`, the SCC mode also goes back. SRAM/RAM contents do not change.
  - The Intel 8255 PPI had no `reset()`, and `Machine` did not refer to it. It now also resets to its post-reset
    defaults (control word 0x9B, Port C cleared) in the same pass.
  - A follow-up Allium weeding pass over this area found and fixed one more real gap. `MajutsushiMapper` inherited its
    bank-register reset, but it did not reset its DAC output level and its pending DAC-write queue. openMSX's
    `RomMajutsushi::reset()` resets its DAC together with the inherited Konami reset. The DAC now correctly returns to
    silence and drops all queued writes that it did not consume.
  - No other observable behavior change.
- **v2.7.0** (2026-09-28): Fix two VDP control-port bugs that allium weeding found and that real hardware confirms.
  - A real MSX1 (TMS9918A) VDP has no separate latch for the VRAM address pointer. The first byte to the control port
    thus now also writes directly into the low 8 bits of the pointer. Before this fix, only a side effect of a
    register-write second byte changed it. A register-write second byte now also rewrites the high 6 bits of the
    pointer. This matches openMSX and an independent real-hardware source (Grauw's VDP tutorial, an MSX Resource
    Center hardware thread).
  - On the V9938, a control-port second byte with bits 7 and 6 both set (0xC0-0xFF) no longer does a register write.
    Unlike an MSX1 VDP, a real V9938 only completes the latch pair for that pattern. Real-hardware tests by two
    openMSX contributors confirm this.
  - Also fix the performance regression from the v2.6.1 correctness fix for the R-register refresh during HALT. The
    hot scanline loop of the CPU now advances a full run of consecutive HALT-internal-NOP M1 cycles in one call.
    Before, it used one Python call per 4 T-states. This recovers the lost throughput, and more, on titles that wait
    for VBlank in HALT. On a representative MSX2 title, `--benchmark` measured ~43% faster than the baseline before
    the regression. Titles that spend little time in HALT change by at most 1-2%.
  - No other observable behavior change.
- **v2.6.6** (2026-09-27): Fix PAL audio underrun.
  - The SDL2 frontend generated a fixed 735 samples per emulated frame (the NTSC rate, 44100 / 60) for every machine.
    A PAL machine runs at 50 fps, so it sent only 36,750 samples/s to the 44,100 Hz audio device, not the ~44,100/s
    that it needs. The queue became empty, and periodic dropouts occurred.
  - The per-frame sample count now comes from the video standard of the machine
    (`audio_sample_rate_hz // target_fps`: 735 at 60 fps NTSC, 882 at 50 fps PAL). It applies equally to PSG, SCC,
    the Majutsushi DAC, and OPLL, so all four sources stay the same length in the combined mix.
- **v2.6.5** (2026-09-27): Internal refactor of save-state loading. There is no observable behavior change for
  well-formed saves. The on-disk format is unchanged, and files from before this change still load.
  - Loading now rejects a corrupted or hand-edited save file earlier and more precisely. `parse_snapshot` checks the
    type, range, and (where the snapshot fixes it) length of every top-level field before it constructs a
    `MachineSnapshot`. It reports a bad value as a `StateLoadError` that gives the exact field path
    (e.g. `cpu_regs.PC`).
  - The loader fully validates every save-state producer (each cartridge mapper, PSG, SCC, FM-PAC/OPLL, the FDC)
    before it restores any producer.
  - Before this change, a short VRAM/register list, a string CPU register, or an out-of-range PC could load silently
    and corrupt the machine. A bad producer object later in the snapshot could also fail after the loader had
    restored other producers. Loading is now atomic across the whole machine, not only per producer.
- **v2.6.4** (2026-09-27): Fix two V9938 rendering bugs.
  - A banded (split-screen) frame can change its display width mid-frame, for example between a 512-dot mode
    (GRAPHIC 5/6/TEXT2) and a 256-dot mode. Such a frame now renders at the width of its widest band, not with the
    live end-of-frame registers. Before this fix, a switch from 512 to 256 crashed the emulator on a length
    assertion. The reverse switch wrote the upper band at the wrong stride and garbled the picture. The renderer now
    pixel-doubles the rows of a narrower band into the wider buffer. It merges sprite passes within a run, so no
    sprite pass spans a width change.
  - GRAPHIC 5 (SCREEN 6) now splits its border color and its color-0 pixels (with the transparent-color bit clear) by
    dot parity: R#7 bits 3:2 for even dots, bits 1:0 for odd dots. Before, it used the register as one flat color.
    This matches the hardware tiling function in the V9938 datasheet and the description of the border behavior in
    the MSX2 Technical Handbook.
- **v2.6.3** (2026-09-27): Internal structural refactor of the V9938 renderer and save state. There is no observable
  behavior change: the framebuffers are identical on every tested cartridge, and the save-state format is unchanged.
  - The render pipeline no longer changes the chip mid-render. The S#0 status, the sprite collision coordinates, and
    the raw S#2 byte go through the whole render as one returned value. `render_frame` commits this value to the chip
    once at the end, and `render_preview` discards it. A screenshot or a save-state thumbnail thus can no longer
    change what the paused program reads next. The passes of a banded (split-screen) render can no longer race each
    other over the same live status field.
  - A save state now carries the V9938-only half as one `vdp9938` object. The loader validates this object as a whole
    before it restores anything: missing or unknown keys, out-of-range values, and the agreement between the live
    port latch and the saved latch. Before, the loader validated and restored these fields one by one, together with
    the fields that both VDP types share.
  - A banded render no longer swaps the register file and palette of the chip in and out for each band. Every
    renderer below the top level now takes its registers as an explicit argument. The RGB conversion applies the
    palette of each band from the register-write log, not from a temporary chip-wide swap.
  - Sprite-heavy frames render 4-22% faster, because of a shared mode-1/mode-2 sprite-row helper.
- **v2.6.2** (2026-09-27): Fix several rendering and register-decode asymmetries between the TMS9918A and the V9938.
  An audit found them. It compared the shared behavior of the two VDP variants with each other, with openMSX, and
  with the chip data manuals.
  - The V9938 renderer now gates the second and third pattern and color table thirds of GRAPHIC2/3 in the same way as
    the TMS9918A. R#4 bits 1:0 / R#3 bits 6:5 decide whether these thirds mirror the first third or are independent.
    Before this fix, a title screen that defined only one third rendered blank on tile rows 8-23.
  - The pattern and name table addressing of GRAPHIC1/MULTICOLOR/TEXT1 now uses the full V9938 128 KB range, as
    GRAPHIC2/3 do, not the narrow TMS9918A form (16 KB).
  - The per-sprite color-table address of sprite mode 2 now correctly gates its low bits on R#5. This fixes an
    aliasing bug between sprite indices 8-31 and index (N mod 8).
  - Both VDPs now store only the implemented bits of their register file, not the raw written byte. This release also
    removes the unreachable, TMS9918A-only GRAPHIC4 decode path of R#0. It is not a real chip mode, and it could
    cause an out-of-bounds VRAM access. The V9938 also starts R#21/R#22 at their real power-on color-burst preset
    values.
  - If a breakpoint or Ctrl-C cuts a TMS9918A frame short, the VDP no longer raises VBlank or the interrupt for that
    frame. This matches the V9938.
  - Text color code 0 is now transparent (it shows the backdrop), not always opaque. This applies to TEXT1 (both
    VDPs), and to TEXT2 and GRAPHIC1/2/3/MULTICOLOR (V9938, gated by R#8 TP).
  - On V9938 machines, the `--debug` VDP `[BOOT]` milestones now also go to the log, from both the direct and the
    indirect register-write paths.
- **v2.6.1** (2026-09-27): Fix several VDP and CPU accuracy bugs. An investigation of VRAM/logo corruption on a real
  MSX1 cartridge found them. The author cross-checked each fix against openMSX and, where available, primary hardware
  documentation.
  - VDP: the TMS9918A now raises VBlank and its interrupt at the start of vertical blanking, not after the full frame.
    This matches the V9938 path and real hardware.
  - VDP: the TMS9918A interrupt line is now the level `F && IE0`, not a latch at VBlank. If a program sets IE0 again
    while F is still pending, the interrupt occurs immediately.
  - VDP: on both VDPs, every data-port access now resets the control-port latch. An ISR that occurs during an address
    setup thus no longer shifts all later register writes by one byte.
  - `Machine`: the NTSC `cycles_per_frame` and the PAL frame-pacing rate are now correct.
  - The screenshot capture of the debugger and RPC (`render_current_rgb24`) no longer changes the VDP status or the
    interrupt level. On the V9938, it also no longer changes the collision coordinates and the S#2 byte. A screenshot
    or a save-state thumbnail thus can no longer change what the paused program reads next.
  - CPU: the R register now refreshes on NMI and maskable-interrupt acceptance, and on each halted internal NOP.
    Before, it refreshed only on instruction fetch.
  - CPU: the 18 undocumented ED-prefixed mirror opcodes for IM/NEG/RETN now execute as their canonical instruction,
    not as a NOP.
  - CPU: an absorbed DD/FD prefix now uses its own full 4-T-state M1 cycle.
  - Every V9938 command dispatch now clears a pending TR re-assert delay from an overridden HMMC/LMMC transfer.
- **v2.6.0** (2026-09-21): Slot 1 and slot 2 are now fully symmetric.
  - Add `--slot1`/`--mapper1`/`--extension1` next to `--slot2`/`--mapper2`/`--extension2`. The positional `cartridge`
    argument stays a shorthand for `--slot1 ... --mapper1 auto`. `--mapper`/`--slot`/`--extension` stay as aliases,
    with their exact meaning before the symmetrization: `--mapper`/`--slot` for `--mapper1`/`--slot1`, and
    `--extension` for `--extension2`, because that is what it always meant. No existing command line changes
    behavior.
  - Both cartridge slots now accept the same mapper type list, including every SRAM-backed type, with independent
    SRAM persistence per slot. They also accept the same `--extensionN` id set. This includes the expanded-shape ids
    (`hbi_j1`, `memory_512k`, `msxdos2_512k*`), which before worked only on slot 2. Two different extensions, one per
    slot, now combine freely (e.g. FM-PAC in slot 1 with HBI-J1 in slot 2).
  - New restrictions: at startup, the emulator rejects the same ROM or extension id on both slots at once, because
    there is almost no real use case for it. The KonamiSCC single-chip-sharing check now covers all four possible
    sources: the cartridge mapper of either slot, and the `scc_plus` extension of either slot. A new check rejects two
    expanded overlays that both declare a `ram_mapper` or `halnote` sub-slot.
  - A large refactor moves the slot-1/slot-2 resolution in `Memory`/`Machine`/`machine_loader.py` into shared code
    with a per-slot parameter.
  - This work found and fixed three real bugs:
    - `Machine.reset()` never reset the sub-slot register or the sub-slot mappers of slot 1 (only those of slot 2).
    - An FM-PAC cartridge in slot 1 built and ran, but silently never saved its SRAM.
    - A second expanded overlay with no `halnote` sub-slot could erase the already-resolved Halnote device of the
      other slot.
  - A follow-up Allium weed pass over `machine_loader.py` found four more:
    - `_wire_extension_overlay` could silently erase an already-resolved FM-PAC cartridge on one slot when the flat
      overlay of the other slot was `scc_plus`. The audio went silent, and the SRAM was no longer saved.
    - A Majutsushi cartridge in slot 2 built and ran, but its DAC never reached `machine.dac`. Its audio was thus
      silently lost (unlike slot 1).
    - FM-PAC on both cartridge slots at once had no explicit rejection. This is defense in depth: the CLI cannot reach
      this case today, because the identical-extension-id check already catches it.
    - The `kanji_rom` conflict error named only two of its three possible sources.
  - `scripts/run_benchmarks.py` now uses a cold boot through the full frame count, not a resume from a saved mid-game
    scene. This removes the PyPy drift between versions that the old runs from save states caused. The full README
    benchmark history (v0.1.0 through v2.6.0) is measured again with this method.
- **v2.5.17** (2026-09-19): Extend `--mapper2` (slot 2 cartridge mapper) to also accept `R-Type`, `Page2`, `0x4000`,
  and `0x8000`. These mapper types read only ROM through a bank register. The SRAM dependency that keeps
  `ASCII8SRAM2/8`, `ASCII16SRAM2/8`, `KoeiSRAM32`, and `GameMaster2` slot-1-only thus does not apply to them.
  - Speed up several hot paths: the `LD r,r'` dispatch of the Z80 (precomputed cycle count), two follow-on YM2413
    (OPLL) optimizations (inlined envelope/phase generation, fused channel mix/noise/linearization), an unrolled SCC
    5-channel sample loop, and `dskftp`, which now keeps its write handle open across a batch of flushes.
  - Large internal refactor and typing pass across `machine_loader.py` (slot-1 setup and post-construction wiring
    extracted from `build_machine`), `__main__.py` (option resolution extracted from `main()`, and `_resolve_paths`
    now returns a `NamedTuple`), the debugger, and the VDP renderer (a shared sprite-scanline scan, a shared TMS9918A
    sprite-plot closure). No observable behavior change.
  - Add a third benchmarked title, MSX2: HB-F1XD + FDD (Dragon Slayer VI - The Legend of Heroes). The benchmark
    history in the README tracks it from v2.4.1 onward. Add the Sony HBI-J1 to the owned-hardware list.
- **v2.5.16** (2026-09-17): Fix a JIS keyboard regression from v2.5.15. The scancode interception for the dead `=`
  key of an International/US host sent a real JIS `^`/`~` key incorrectly to `_`.
  - `_` now comes from a dedicated JIS `\`/`_` scancode sentinel and a new SHIFT+`0` escape hatch (both layouts). The
    backquote-to-`^` fallback stays, for International keyboards that a macOS/SDL2 keyboard-layout misdetection
    affects.
  - Fix `dskftp.py`, which wrote a zero-filled boot sector loader stub. A formatted disk thus ran a NOP sled and did
    not fall back to BASIC.
  - Rename `make_blank_dsk.py` to `dskblank.py`, to match the naming of `dskftp`/`dskdd`.
  - Move the Socket RPC/MCP and tools reference out of `README.md` into `tools/README_tools.md`/`_ja.md`. Move
    `README_extension.md`/`_ja.md` into `docs/`.
- **v2.5.15** (2026-09-15): Add `--extension msxdos2_512k_viewfont`: an ASCII8-mapped MSX-View-compatible font ROM,
  together with the RAM mapper + MSX-DOS2 kernel of `msxdos2_512k`. Add `ascii8`/`ascii16` as supported sub-slot
  device kinds for expanded overlays. Rename `--extension msxdos2_512k_kanji` to `msxdos2_512k_kanjirom`, so that the
  two names stay clearly different.
  - Expanded extension overlays (`hbi_j1`, `memory_512k`, `msxdos2_512k*`) now reject an MSX1 base machine with
    `MachineLoadError`. Before, they silently built a machine that has no real-hardware counterpart.
  - Fix five JIS symbol keys (`=`, apostrophe, `^`/`~`, `:`/`*`, `_`) that an International/US host keyboard could not
    type under `keyboard_type: jp`.
  - Documentation: move the extension documentation out of `README.md` into `README_extension.md`/`_ja.md`, and move
    the version history into this file. Add a "Machine and extension YAML schema" chapter to
    `docs/technical-implementation.md`/`_ja.md`. Change every owned-software citation from Wikipedia/msx.org to
    [Generation MSX](https://generation-msx.nl/).
- **v2.5.14** (2026-09-12): Replace `--fmpac`/`--scc-plus` with a unified `--extension {none,fmpac,scc_plus,hbi_j1}`
  flag (`extension` config key, with `config/extensions/<id>.yaml` overlay files). Each extension always occupies
  primary slot 2. **BREAKING**: SCC-I now occupies primary slot 2, not slot 1. `--mapper2` also gets `KonamiSCC`
  support, and shares the single SCC chip of the machine with slot 1.
- **v2.5.13** (2026-09-12): Add Sony HBI-J1 support through `--extension hbi_j1`. The cartridge has a JIS Kanji font
  ROM I/O device, a Halnote-mapped MSX-JE word-processor ROM + 16 KB SRAM, and a flat Kanji driver + BASIC ROM. It
  expands slot 2 into two sub-slots. The author physically owns this cartridge and dumped its ROMs, and their SHA1
  values match openMSX. Also add the V9938 interlace mode (R#9 IL/EO bits). EO drives the automatic even/odd page
  alternation of GRAPHIC4-7, as on a real V9938.
- **v2.5.12** (2026-09-09): The size of `RamMapper` is now configurable in whole 16 KB banks. Before, it was fixed at
  128 KB. The legacy (RAM-mapper) dispatch branch of slot 3 can now also hold an FDC next to the RAM mapper. Before,
  an FDC needed the data-driven flat-RAM layout.
  - Add `hb_f1xd_256`, a hypothetical 256 KB RAM-mapper variant of the Sony HB-F1XD. No real hardware matches this
    configuration. Its purpose is the extended BIOS mapper support routines of MSX-DOS2, which need a RAM mapper of at
    least 128 KB. The author confirmed that MSX-DOS2 boots on it.
  - Implement the TEXT2 mode of the V9938 (SCREEN 0 WIDTH 80, `MODE 80` of MSX-DOS). Before, the emulator dispatched
    it incorrectly as a GRAPHIC2 background with sprite mode 2 sprites, which corrupted the screen. The fix corrects
    the mode-bit dispatch priority, adds a dedicated 80-column/512-wide renderer, and extends `display_width` and the
    screen-mode display of the debugger. The author confirmed the fix on MSX-DOS.
- **v2.5.11** (2026-09-06): Remove the `mapper`/`mapper2` config keys from `py_emulator.yaml`. The cartridge mapper
  selection is now CLI-only (`--mapper` / `--mapper2`). This fixes a config-file default that silently and incorrectly
  triggered the `--scc-plus`/`--mapper` mutual-exclusivity check.
- **v2.5.10** (2026-09-01): Add Allium specs for the remaining uncovered components: RTC, floppy disk image/drive,
  plain/fixed-page mappers, I/O bus, and the Z80 ED/CB/DD/FD prefix groups. This work found and fixed several real
  bugs:
  - RTC CMOS RAM now persists per machine to `saves/sram/rtc_<machine_id>.sram`. Before, a single shared file let the
    settings of one machine leak into another machine. This release also includes a 12/24-hour encoding fix.
  - The emulator now implements an undocumented Z80 DDCB/FDCB register-echo behavior.
  - Also add the full floppy disk state (WD2793/TC8566AF registers, drive position, mounted-disk identity) to
    save/load, with mismatch checks for the FDC kind and the disk identity.
- **v2.5.9** (2026-08-25): Full OpenSpec/Allium inventory pass across every component. The pass verified each
  specification again against openMSX and the implementation. It also fixed several accuracy bugs (V9938 sprite
  rendering, ASCII8/ASCII16 mapper bank arithmetic, SCC-I mode sync, mouse protocol timing, and others).
- **v2.5.8** (2026-08-22): Add the TC8566AF FDC controller and a Panasonic FS-A1F machine configuration
  (`--machine fs_a1f`). This is a second MSX2 with floppy disk support, in addition to the Sony HB-F1XD (WD2793). The
  FS-A1F now uses its real 4-sub-slot hardware layout, with RAM, SUB ROM, and the FDC each in its own sub-slot.
- **v2.5.7** (2026-08-20): Large internal refactor in preparation for a future Rust/C++ port. Mapper save state now
  uses a tagged `MapperKind` enum instead of untyped dicts. The cache invalidation of `Memory` moved to explicit
  setter methods. Explicit interface methods replace the reflection-based mapper/slot introspection of the debugger.
  No observable behavior change (verified with a multi-angle code review and an Allium spec-alignment check).
- **v2.5.6** (2026-08-19): Fix the JIS ¥ key, which never reached the MSX keyboard matrix on macOS. SDL2 reports this
  key with a consistent scancode but an inconsistent keysym. `key_down`/`key_up` thus now resolve it from the scancode
  alone.
- **v2.5.5** (2026-08-19): Fix a write-side open-bus leak in `Ascii16Sram2Mapper`: writes at or above 0xC000 could
  corrupt SRAM while a window was SRAM-mapped. Give `RTypeMapper` a flat read mirror. It was the last mapper class
  that still resolved its window on every read.
- **v2.5.4** (2026-08-19): Fix three bugs in the Konami-family mappers (`KonamiMapper`/`KonamiSCCMapper`/
  `MajutsushiMapper`). A cross-check against the openMSX source found them: address mirroring outside the ROM
  windows, bank-select page arithmetic, and SCC-enable writes that did not update the bank register. Add a new Allium
  spec for this mapper family, and fix an unrelated test-isolation bug in `test_cli_scc_plus.py`.
- **v2.5.3** (2026-08-17): Fix the `--break-point`/`--watch-point` CLI flags, which MSX1 machines silently ignored.
  The commands of the interactive debugger already worked there. Only the CLI startup path was limited to MSX2.
- **v2.5.2** (2026-08-16): Add SCC-I ("SCC+") cartridge support through `--scc-plus`. This is a bare sound cartridge
  in slot 1 with 64 KB of bank-switched RAM and a Plus-mode SCC chip, for floppy-disk MSX2 titles that use it only for
  audio.
- **v2.5.1** (2026-08-16): Close most open Allium questions from v2.5.0. This work fixed two PPI (i8255) bugs and
  changed the quit key to Ctrl+Q. Change the PSG amplitude table to measured-silicon data. This change is audible.
- **v2.5.0** (2026-08-15): Introduce Allium as a second, behavior-focused spec layer, and distill one spec for every
  major component. This work fixed several accuracy bugs (V9938 sprite collision, FDC). Also add save-state schemas, a
  memory dispatch cache, and JIS keyboard bindings.
- **v2.4.8** (2026-08-08): Render V9938 frames at the start of vertical blanking, not after the scanline loop. This
  fixes a one-frame tear on titles that update VRAM and a display register in the same VBlank ISR. Also make the
  per-instruction loop of the debugger ~9% faster.
- **v2.4.7** (2026-08-05): Extend `py_emulator.yaml` with the `slot2`/`mapper2` and `frame_skip` config keys, and a
  `keyboard_joystick.buttons` section to change the keyboard-emulation keys of Joy1.
- **v2.4.6** (2026-08-05): Add support for the `GameMaster2` ROM database mapper type (128 KB ROM + 8 KB
  battery-backed SRAM). Make the names that `--mapper` accepts agree with the set that the loader supports.
- **v2.4.5** (2026-08-04): Add support for four ROM database mapper types that were not supported before:
  `Page2`/`0x4000`/`0x8000` (a new `FixedPageMapper`) and `KoeiSRAM32`.
- **v2.4.4** (2026-08-04): Add MSX mouse emulation (`--mouse[=1|2]`). It reproduces the real pin-8-clocked nibble
  protocol with the host mouse.
- **v2.4.3** (2026-08-03): Make cartridge ROM reads faster on bank-switching mappers. A flat mirror, rebuilt only on
  a bank switch, replaces the resolution of the active window on every read.
- **v2.4.2** (2026-08-02): Add Ctrl+F1..F5 (MSX HOME/INS/DEL/STOP/SELECT) and Right Alt (MSX CODE/KANA) key bindings
  to the SDL2 frontend.
- **v2.4.1** (2026-08-02): Add an optional `py_emulator.yaml` startup configuration file. Change `--benchmark` to a
  frame count.
- **v2.4.0** (2026-07-30): Add the FM-PAC (MSX-MUSIC) cartridge with a YM2413 (OPLL) FM sound chip.
- **v2.3.6** (2026-07-23): Unify the rendered output to a constant 212-line height.
- **v2.3.5** (2026-07-20): Fix sprite ghosting from the upper split-screen region.
- **v2.3.4** (2026-07-19): Add per-line banding for the display-adjust register.
- **v2.3.3** (2026-07-19): Fix the handling of filenames with spaces.
- **v2.3.2** (2026-07-19): Fix V9938 line-interrupt handling.
- **v2.3.1** (2026-07-19): Refactor the socket RPC and MCP server.
- **v2.3.0** (2026-07-19): Add a socket RPC interface and MCP server.
- **v2.2.1** (2026-07-15): Add cycle-accurate CPU timing and PSG PCM playback.
- **v2.2.0** (2026-07-13): Add support for the Sony HB-F1XD (FDD + RTC).
- **v2.1.0** (2026-07-13): Improve MSX2 emulation performance.
- **v2.0.0** (2026-07-06): Add MSX2 CBIOS support.
- **v1.0.0** (2026-06-07): Initial release.
