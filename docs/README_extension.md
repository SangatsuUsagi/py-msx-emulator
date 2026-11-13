# Extensions

`--extension1 <id>` and `--extension2 <id>` add an extension device to primary slot 1 or slot 2 of the selected machine. A YAML fragment in `config/extensions/<id>.yaml` defines each extension.

Each cartridge slot can hold at most one extension:

- `--extension1` always occupies primary slot 1. It thus conflicts with the positional cartridge argument, `--slot1`, and `--mapper1`.
- `--extension2` always occupies primary slot 2. It thus conflicts with `--slot2` and `--mapper2`.

You can put an extension in each slot, or an extension in one slot and a plain cartridge in the other. Some combinations are rejected at startup. See [Restrictions](#restrictions) below.

You can also set the ids with the `extension1:`/`extension2:` keys in `py_emulator.yaml`. The older `extension:` key and the `--extension` flag still work, as aliases for slot 2. `--extension1 none`/`--extension2 none` overrides the config-file setting for one run, on that slot only.

This document describes what each extension is for and which ROM files it needs. For the FM-PAC/SCC-I/HBI-J1 implementation details (memory maps, I/O ports, known limitations), see [the "Emulated hardware" section of `README.md`](../README.md#emulated-hardware).

[日本語版はこちら](README_extension_ja.md)

- [Slot model](#slot-model)
- [Restrictions](#restrictions)
- [FM-PAC](#fm-pac)
- [SCC-I cartridge (SCC+)](#scc-i-cartridge-scc)
- [Sony HBI-J1](#sony-hbi-j1)
- [memory_512k: 512 KB RAM expansion](#memory_512k-512-kb-ram-expansion)
- [msxdos2_512k: 512 KB RAM + MSX-DOS2 kernel](#msxdos2_512k-512-kb-ram--msx-dos2-kernel)
- [msxdos2_512k_kanjirom: + Kanji font ROM](#msxdos2_512k_kanjirom--kanji-font-rom)
- [msxdos2_512k_viewfont: + View font ROM](#msxdos2_512k_viewfont--view-font-rom)
- [ROM file layout](#rom-file-layout)

---

## Slot model

The table shows what each primary slot can hold, and which slots `--extension1`/`--extension2` can reach:

| Slot | Sub-slot expansion | Extensible via | What it can hold |
| --- | --- | --- | --- |
| 0 | No | No | Main BIOS ROM, plus an optional logo ROM at page 2 and, on some machines, flat RAM at one page (e.g. the 16 KB RAM of `hb_10` at page 3) |
| 1 | Yes, 0-3, only through `--extension1` | `--extension1` | Cartridge ROM + mapper (positional `cartridge`/`--slot1`/`--mapper`/`--mapper1`), **or** an extension |
| 2 | Yes, 0-3, only through `--extension2` | `--extension2` | Cartridge ROM + mapper (`--slot2`/`--mapper2`), **or** an extension |
| 3 | MSX2: yes, 0-3 (machine-defined only). MSX1: no | No | Fixed by the machine YAML (`config/machines/*.yaml`). MSX1: flat RAM with a size per machine (32 KB default, 64 KB on `hb_10_64k`), or nothing on `hb_10`. MSX2: its own SUB ROM, a RAM mapper or flat RAM, and a floppy disk controller on `hb_f1xd` (WD2793) and `fs_a1f` (TC8566AF, implemented from public documentation and not verified against real hardware) |

No machine YAML declares slot 1 or slot 2 as expanded. Only an extension can expand them. An expanded extension slot can hold any number of ROM sub-slots, at most one `ram_mapper` sub-slot, and at most one `io_device`. A ROM sub-slot is `flat_rom` or one bank-switched mapper kind (`ascii8`, `ascii16`, or `halnote`). At present, the only `io_device` is `kanji_rom`, which answers on fixed I/O ports and has no slot location of its own.

The sub-slot expansion of slot 3 is a property of the base machine. An extension in slot 1 or 2 has no effect on it. All expansions coexist, and each has its own secondary slot register.

## Restrictions

The emulator rejects these configurations at startup:

- **The same id on both slots.** This project does not support two copies of one extension, and there is almost no real use case for it.
- **An expanded extension on an MSX1 machine.** `hbi_j1`, `memory_512k` and the three `msxdos2_512k*` extensions expand the slot into sub-slots. They work only on MSX2 machines. `fmpac` and `scc_plus` also work on MSX1.
- **Two RAM mappers.** `memory_512k` and the `msxdos2_512k*` extensions each contain a RAM mapper. The emulator rejects them on a machine that already has a slot-3 RAM mapper (`cbios_msx2*`, `fs_a1f`, `hb_f1xd_256k`). It also rejects two of them together, one in each slot. Two memory mappers would share the same I/O ports (0xFC-0xFF), and the emulator does not support that. Of the bundled machines, only `hb_f1xd` can run these extensions.
- **Two Kanji font ROM devices.** `hbi_j1` and `msxdos2_512k_kanjirom` both put a Kanji font ROM on I/O ports 0xD8-0xDB. You cannot use them together.
- **Two SCC sound chips.** A KonamiSCC cartridge mapper and the `scc_plus` extension both build an SCC chip, but the emulator has only one SCC. It thus rejects `KonamiSCC` in the mapper of one slot together with `scc_plus` in the other slot. It also rejects `KonamiSCC` in the mappers of both slots.

## FM-PAC

`--extension1`/`--extension2 fmpac`: [FM-PAC (FM Pana Amusement Cartridge)](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072), a real MSX-MUSIC cartridge. It has a YM2413 (OPLL) FM sound chip, a 64 KB banked ROM, and 8 KB of battery-backed SRAM. The emulator saves the SRAM to `saves/sram/fmpac.sram`. It needs the ROM of the cartridge at `roms/fmpac/fmpac.rom`. This project does not distribute this ROM. Get your own dump.

## SCC-I cartridge (SCC+)

`--extension1`/`--extension2 scc_plus`: a bare sound cartridge modeled on the [Konami SCC (052539)](https://www.msx.org/wiki/Konami_052539) sound chip. It has 64 KB of physical bank-switched RAM, addressed as if it were 128 KB. The RAM is blank: the emulator never loads a ROM or data file into it. No ROM file is necessary.

The bank register ignores bit 3, so block N mirrors block N+8. This reproduces a documented real-hardware modification (["connect the two 64 KB banks"](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html)). With it, one cartridge works with both factory RAM-population variants. The MSX2 floppy titles that use this cartridge as a sound source expect one variant or the other.

> **Note**: the author does not own a real SCC-I cartridge, or software that uses one. This implementation thus uses publicly available information, and the author has not verified it against real hardware.

## Sony HBI-J1

`--extension1`/`--extension2 hbi_j1`: [Sony HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1), a real Kanji-ROM + MSX-JE word-processor cartridge. It works only on MSX2 machines. The emulator saves the 16 KB SRAM of MSX-JE to `saves/sram/hbi-j1_msx-je.sram`. It needs three ROM dumps at these paths:

```
roms/hbi_j1/
├── hbi-j1_kanjibasic.rom   (32 KB  — Kanji driver + BASIC extension)
├── hbi-j1_kanjifont.rom    (256 KB — JIS Kanji font ROM)
└── hbi-j1_msx-je.rom       (1 MB   — MSX-JE word-processor ROM)
```

These are commercial Sony ROMs. This project does **not distribute them**, because no license that this project can grant allows their redistribution. If you own a physical HBI-J1 cartridge, dump its ROMs yourself and put the files at the paths above. There is no other legitimate way to get them.

## memory_512k: 512 KB RAM expansion

`--extension1`/`--extension2 memory_512k` is a 512 KB bank-switched RAM expansion for the MSX2 main memory, with no real-cartridge equivalent. Its main use is to run MSX-DOS2 on `hb_f1xd`, which has no slot-3 RAM mapper. Put an MSX-DOS2 kernel cartridge in one slot and this extension in the other. The machine then has enough banked RAM to run MSX-DOS2.

No ROM file is necessary. The RAM is blank at the start of each run, and the emulator does not save it to a file.

```bash
# A slot 1 MSX-DOS2 kernel cartridge, paired with this extension's RAM in slot 2
python . path/to/msxdos2_kernel.rom --extension2 memory_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k: 512 KB RAM + MSX-DOS2 kernel

`--extension1`/`--extension2 msxdos2_512k` also has no real-cartridge equivalent. It combines the 512 KB RAM of `memory_512k` with an MSX-DOS2 kernel ROM in the same slot. One flag is thus sufficient to run MSX-DOS2. You do not need a separate kernel cartridge in the other slot.

The kernel ROM is `msxd22s.rom` from [`msxdos2s`](https://github.com/b3rendsh/msxdos2s), at `roms/msxdos2s/msxd22s.rom`. Most of that project's sources are a re-composition of the original MSX-DOS2 by ASCII. Its own code is under CC BY-NC-SA 4.0, and the project shares the rest for educational and non-commercial use only. **Check its license before use.**

To boot MSX-DOS2, you also need a floppy disk image with `MSXDOS2.SYS` and `COMMAND2.COM`. This project does **not** include them. Use `--machine hb_f1xd`: it has a floppy disk controller and no slot-3 RAM mapper. The `cbios_*` machines have no floppy disk controller, and `fs_a1f` has a slot-3 RAM mapper that rejects this extension. The same applies to [msxdos2_512k_kanjirom](#msxdos2_512k_kanjirom--kanji-font-rom) and [msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-font-rom) below.

```bash
# Boot MSX-DOS2 directly -- no slot 1 cartridge needed, the kernel is built in
python . --extension2 msxdos2_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_kanjirom: + Kanji font ROM

`--extension1`/`--extension2 msxdos2_512k_kanjirom` adds the standard MSX JIS Kanji font ROM I/O device (ports `0xD8`-`0xDB`) to `msxdos2_512k`. With it, an MSX-DOS2 application can show Kanji. The default configuration uses the font dump of the HBI-J1 extension at `roms/hbi_j1/hbi-j1_kanjifont.rom`. The licensing note under [Sony HBI-J1](#sony-hbi-j1) above applies here in the same way. To use a different JIS Kanji font ROM dump, edit the `io_device.rom` block in `config/extensions/msxdos2_512k_kanjirom.yaml`.

This extension supports only the fixed-port I/O device. A Kanji font in a memory-mapped ROM cartridge, such as the font of the MSX-View add-on package, is a different device kind. For that kind, see [msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-font-rom) below.

```bash
# Same as msxdos2_512k, plus Kanji display support
python . --extension2 msxdos2_512k_kanjirom --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_viewfont: + View font ROM

`--extension1`/`--extension2 msxdos2_512k_viewfont` adds an ASCII8-mapped (8 KB-bank MEGA ROM Controller) font ROM to `msxdos2_512k`. This ROM is compatible with the font cartridge in the MSX-View add-on package. It is in sub-slot 2, next to the RAM mapper and the MSX-DOS2 kernel of `msxdos2_512k`.

The default configuration uses the [MSXView Kanji Cartridge Compatible ROM](https://littlelimit.net/viewfont.htm) (`k12x8shn.rom`). This is a free bitmap font ROM, made from the Shinonome and k12x8 fonts for this cartridge format. Its license grants "unlimited permission... to use, copy, and distribute them, with or without modification, either commercially or noncommercially."

This project does **not** bundle a copy. Download it from the link above and put it at `roms/kanji/k12x8shn.rom`. Any other MSX-View-compatible font ROM also works. To use a different one, edit the sub-slot 2 `rom` block in `config/extensions/msxdos2_512k_viewfont.yaml`.

```bash
# Same as msxdos2_512k, using a View-font-compatible Kanji ROM instead
python . --extension2 msxdos2_512k_viewfont --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## ROM file layout

These are the extension-related ROM files. This project does not distribute them. See the section of each extension above for how to get them:

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
