# Extensions

`--extension1 <id>`/`--extension2 <id>` overlays a device fragment into
primary slot 1 or slot 2 (respectively) on top of an already-resolved base
machine, defined by a YAML fragment in `config/extensions/<id>.yaml`. Each
cartridge slot can independently host at most one extension: `--extension1`
unconditionally occupies primary slot 1, so it conflicts with the positional
cartridge argument/`--slot1`/`--mapper1`; `--extension2` unconditionally
occupies primary slot 2, so it conflicts with `--slot2`/`--mapper2`. The two
slots' extensions (or an extension in one slot and a plain cartridge in the
other) are otherwise independent and can be combined freely — except that the
same `id` cannot be selected on both slots at once (rejected at startup; see
[the "Slot model" section](#slot-model) below for why). The same ids can be
set via the `extension1:`/`extension2:` keys in `py_emulator.yaml` (the older
bare `extension:` key still works, as a legacy alias for `extension2:`);
`--extension1 none`/`--extension2 none` overrides that config-file setting
for a single run, on that slot only. See
[`README.md`'s "Emulated hardware" section](README.md#emulated-hardware) for
the FM-PAC/SCC-I/HBI-J1 implementation detail (memory maps, I/O ports, known
limitations) — this document focuses on what each extension is for and what
ROM files it needs.

[日本語版はこちら](README_extension_ja.md)

- [Slot model](#slot-model)
- [FM-PAC](#fm-pac)
- [SCC-I cartridge (SCC+)](#scc-i-cartridge-scc)
- [Sony HBI-J1](#sony-hbi-j1)
- [memory_512k: 512 KB RAM expansion](#memory_512k-512-kb-ram-expansion)
- [msxdos2_512k: 512 KB RAM + MSX-DOS2 kernel](#msxdos2_512k-512-kb-ram--msx-dos2-kernel)
- [msxdos2_512k_kanjirom: + Kanji font ROM](#msxdos2_512k_kanjirom-kanji-font-rom)
- [msxdos2_512k_viewfont: + View font ROM](#msxdos2_512k_viewfont-view-font-rom)
- [ROM file layout](#rom-file-layout)

---

## Slot model

What each primary slot can hold when building a machine, and which slot(s)
`--extension1`/`--extension2` can actually reach:

| Slot | Sub-slot expansion | Extensible via | What it can hold |
| --- | --- | --- | --- |
| 0 | No | No | Main BIOS ROM only (plus an optional logo ROM at page 2) |
| 1 | Yes, 0–3 (only ever via `--extension1` — no machine YAML declares slot 1 expanded on its own) | `--extension1` | Cartridge ROM + mapper (positional `cartridge`/`--slot1`/`--mapper`/`--mapper1`), **or**, once expanded: the same per-sub-slot device shapes slot 2 supports (below) |
| 2 | Yes, 0–3 (only ever via `--extension2` — no machine YAML declares slot 2 expanded on its own) | `--extension2` | Cartridge ROM + mapper (`--slot2`/`--mapper2`), **or**, once expanded: any number of ROM sub-slots (each one `flat_rom`, or one bank-switched mapper kind — `ascii8`, `ascii16`, or `halnote`), at most one `ram_mapper` sub-slot, and at most one overlay-wide `io_device` (currently only `kanji_rom`, which has no slot location of its own) |
| 3 | Yes, 0–3 (machine-defined only) | No | Fixed per machine YAML (`config/machines/*.yaml`), not selectable via `--extension1`/`--extension2`: MSX2's own SUB ROM, a RAM mapper or flat RAM, and — on the two hardware-verified machines — a floppy disk controller (`hb_f1xd`: WD2793; `fs_a1f`: TC8566AF) |

Slot 1 and slot 2 are each independently extensible, one extension per slot
(so two *different* extensions — one per slot — combine freely, e.g. FM-PAC
in slot 1 alongside HBI-J1 in slot 2, or a game cartridge in one slot
alongside an extension in the other). What is still not supported is the
*same* id on both slots at once: `--extension1`/`--extension2` share
`Machine`'s underlying per-slot cartridge-SRAM and save-state machinery, and
there is essentially no real-hardware use case for it (the one theoretical
case — stacking two FM-PAC/SCC-I cartridges for a richer multi-chip sound
mix — is out of scope). This is rejected at startup as a same-ROM/same-
extension-id check, independent of the (also still enforced) KonamiSCC
single-chip-sharing check below. Slot 3's own sub-slot expansion is a
base-machine property declared in `config/machines/*.yaml`, independent of
and unaffected by either cartridge slot's extension (all expansions coexist,
each with its own secondary slot register).

The one thing that does not fall out of per-slot independence: a KonamiSCC
cartridge mapper and an `scc_plus` extension both ultimately build a real
SCC sound chip, and `Machine` has a single `scc` field — so `KonamiSCC` in
one slot's mapper together with `scc_plus` in the other slot's extension (or
`KonamiSCC` in both slots' mappers) is rejected, for the same
one-physical-chip reason real hardware never has two SCC chips attached at
once.

## FM-PAC

`--extension1`/`--extension2 fmpac` — [FM-PAC (FM Pana Amusement Cartridge)](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072), a real MSX-MUSIC
cartridge: a YM2413 (OPLL) FM sound chip, 64 KB banked ROM, and 8 KB
battery-backed SRAM. Requires the cartridge's own ROM at
`roms/fmpac/fmpac.rom` — not distributed with this project; obtain your own
dump.

## SCC-I cartridge (SCC+)

`--extension1`/`--extension2 scc_plus` — a bare sound cartridge modeled on
[Konami's SCC (052539)](https://www.msx.org/wiki/Konami_052539) sound chip,
connected unconditionally in whichever primary slot the flag targets: 64 KB
of physical bank-switched RAM addressed as if 128 KB (blank — no ROM/data
file is ever loaded), with bank register bit 3 ignored so block N mirrors
block N+8 — reproducing a documented real-hardware modification (["connect
the two 64 KB banks"](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html))
that lets one physical SCC-I cartridge work with either of the two factory
RAM-population variants, each of which this project's two target titles
expects. No ROM file is required.

> **Note**: the author does not own a real SCC-I cartridge, or software that
> uses one, so this implementation is based on publicly available
> information and has not been verified against real hardware.

## Sony HBI-J1

`--extension1`/`--extension2 hbi_j1` — [Sony HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1), a
real Kanji-ROM + MSX-JE word-processor cartridge. Requires three ROM dumps at:

```
roms/hbi_j1/
├── hbi-j1_kanjibasic.rom   (32 KB  — Kanji driver + BASIC extension)
├── hbi-j1_kanjifont.rom    (256 KB — JIS Kanji font ROM)
└── hbi-j1_msx-je.rom       (1 MB   — MSX-JE word-processor ROM)
```

These are commercial Sony ROMs and are **not distributed with this project** —
they are not redistributable under any license this project could grant. If
you own a physical HBI-J1 cartridge, dump its ROMs yourself and place the
files at the paths above; there is no other legitimate way to obtain them.

## memory_512k: 512 KB RAM expansion

`--extension1`/`--extension2 memory_512k` does not correspond to any real
cartridge — it is a 512 KB bank-switched RAM expansion for MSX2's main
memory, placed in whichever primary slot the flag targets. Its main use is
exercising MSX-DOS2 without a machine that already has a slot-3 RAM mapper:
put an MSX-DOS2 kernel cartridge in one slot and this extension in the other,
and the resulting machine has enough banked RAM to run MSX-DOS2. No ROM file
is required — the RAM starts blank every run, with no save-file persistence.
Two `ram_mapper`-declaring extensions across both slots at once (e.g. this
one alongside `msxdos2_512k`, which also declares one) is rejected — see
[the "Slot model" section](#slot-model) above.

```bash
# A slot 1 MSX-DOS2 kernel cartridge, paired with this extension's RAM in slot 2
python . path/to/msxdos2_kernel.rom --extension2 memory_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k: 512 KB RAM + MSX-DOS2 kernel

`--extension1`/`--extension2 msxdos2_512k` is also not a real cartridge — it
combines `memory_512k`'s 512 KB RAM expansion with an MSX-DOS2 kernel ROM in
the same slot fragment, so a single `--extension1`/`--extension2` flag is
enough to run MSX-DOS2 with no separate kernel cartridge in the other slot
needed.

The kernel ROM this extension expects is
[`msxdos2s`](https://github.com/b3rendsh/msxdos2s), an open MSX-DOS2 kernel
implementation, at `roms/msxdos2s/msxd22s.rom`. **Check that project's own
license before use** — as of this writing it permits educational,
non-commercial use only, not unrestricted redistribution or commercial use.

> **Note**: booting MSX-DOS2 also needs a floppy disk image containing
> `MSXDOS2.SYS` and `COMMAND2.COM`, in addition to this extension's DOS2
> kernel ROM. The `cbios_*` machines have no floppy disk controller at all,
> so MSX-DOS2 cannot boot on them — use `--machine hb_f1xd` or `fs_a1f`
> instead, both of which have one. `MSXDOS2.SYS`/`COMMAND2.COM` are **not**
> included with this project. This applies equally to
> [msxdos2_512k_kanjirom](#msxdos2_512k_kanjirom-kanji-font-rom) and
> [msxdos2_512k_viewfont](#msxdos2_512k_viewfont-view-font-rom) below.

```bash
# Boot MSX-DOS2 directly -- no slot 1 cartridge needed, the kernel is built in
python . --extension2 msxdos2_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_kanjirom: + Kanji font ROM

`--extension1`/`--extension2 msxdos2_512k_kanjirom` adds the standard MSX
JIS Kanji font ROM I/O device (ports `0xD8`-`0xDB`) on top of `msxdos2_512k`,
so an MSX-DOS2 application can display Kanji. As configured, it reuses the HBI-J1 extension's
own font dump at `roms/hbi_j1/hbi-j1_kanjifont.rom` — see the licensing note
under [Sony HBI-J1](#sony-hbi-j1) above, which applies here identically. Any
other JIS Kanji font ROM dump compatible with the `kanji_rom` I/O device can
be substituted by editing `config/extensions/msxdos2_512k_kanjirom.yaml`'s
`io_device.rom` block.

This is the fixed-port I/O device shape only — a *mapper-based* (memory-mapped
ROM cartridge) Kanji font, such as the one bundled with the MSX-View add-on
package, is a different device kind and is not supported through this
extension. See [msxdos2_512k_viewfont](#msxdos2_512k_viewfont-view-font-rom)
below for that shape instead.

```bash
# Same as msxdos2_512k, plus Kanji display support
python . --extension2 msxdos2_512k_kanjirom --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_viewfont: + View font ROM

`--extension1`/`--extension2 msxdos2_512k_viewfont` adds an ASCII8-mapped
(8 KB-bank MEGA ROM Controller) font ROM, compatible with the font cartridge bundled with the
MSX-View add-on package, on top of `msxdos2_512k` (sub-slot 2, alongside
`msxdos2_512k`'s RAM mapper and MSX-DOS2 kernel).

As configured, this uses the
[MSXView Kanji Cartridge Compatible ROM](https://littlelimit.net/viewfont.htm)
(`k12x8shn.rom`), a free, redistributable bitmap font ROM built from the
Shinonome and k12x8 fonts specifically for this cartridge format — its
license grants "unlimited permission... to use, copy, and distribute them,
with or without modification, either commercially or noncommercially."
This project does **not** bundle a copy — download it from the link above
and place it at `roms/kanji/k12x8shn.rom`. Any other MSX-View-compatible
font ROM also works; substitute one by editing
`config/extensions/msxdos2_512k_viewfont.yaml`'s sub-slot 2 `rom` block.

```bash
# Same as msxdos2_512k, using a View-font-compatible Kanji ROM instead
python . --extension2 msxdos2_512k_viewfont --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## ROM file layout

Extension-related ROM files (not distributed with this project — see each
extension's section above for how to obtain them):

```
roms/
├── fmpac/
│   └── fmpac.rom
├── hbi_j1/
│   ├── hbi-j1_kanjibasic.rom
│   ├── hbi-j1_kanjifont.rom
│   └── hbi-j1_msx-je.rom
├── kanji/
│   └── k12x8shn.rom
└── msxdos2s/
    └── msxd22s.rom
```
