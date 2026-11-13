# 拡張機能

`--extension1 <id>` と `--extension2 <id>` は、選んだマシンのプライマリスロット 1 またはスロット 2 に拡張デバイスを追加します。各拡張は `config/extensions/<id>.yaml` の YAML フラグメントで定義します。

拡張は、各カートリッジスロットに 1 つまで入れられます。

- `--extension1` は必ずプライマリスロット 1 を使います。そのため、位置引数のカートリッジ、`--slot1`、`--mapper1` とは同時に指定できません。
- `--extension2` は必ずプライマリスロット 2 を使うので、`--slot2`、`--mapper2` とは同時に指定できません。

両方のスロットに拡張を 1 つずつ入れることも、片方に拡張、もう片方に普通のカートリッジを入れることもできます。ただし、一部の組み合わせは起動時にエラーになります。下記の[「制限」節](#制限)を参照してください。

`py_emulator.yaml` の `extension1:`/`extension2:` キーでも id を設定できます。従来の `extension:` キーと `--extension` フラグも、スロット 2 の別名として今も使えます。`--extension1 none`/`--extension2 none` を付けると、そのスロットについてだけ、その回の実行で設定ファイルの指定を打ち消します。

この文書では、各拡張の用途と必要な ROM ファイルを説明します。FM-PAC/SCC-I/HBI-J1 の実装の詳細（メモリマップ、I/O ポート、既知の制限）は、[`README_ja.md` の「対応ハードウェア」節](../README_ja.md#対応ハードウェア)を参照してください。

[English version here](README_extension.md)

- [スロットモデル](#スロットモデル)
- [制限](#制限)
- [FM-PAC](#fm-pac)
- [SCC-I カートリッジ (SCC+)](#scc-i-カートリッジ-scc)
- [Sony HBI-J1](#sony-hbi-j1)
- [memory_512k: 512 KB RAM 拡張](#memory_512k-512-kb-ram-拡張)
- [msxdos2_512k: 512 KB RAM + MSX-DOS2 カーネル](#msxdos2_512k-512-kb-ram--msx-dos2-カーネル)
- [msxdos2_512k_kanjirom: + 漢字フォント ROM](#msxdos2_512k_kanjirom--漢字フォント-rom)
- [msxdos2_512k_viewfont: + View フォント ROM](#msxdos2_512k_viewfont--view-フォント-rom)
- [ROM ファイル配置](#rom-ファイル配置)

---

## スロットモデル

各プライマリスロットに何を入れられるか、`--extension1`/`--extension2` がどのスロットに届くかを次の表にまとめます。

| スロット | サブスロット拡張 | 拡張手段 | 入れられるもの |
| --- | --- | --- | --- |
| 0 | なし | なし | メイン BIOS ROM。ほかに、ページ 2 のロゴ ROM（任意）と、一部の機種では 1 ページ分のフラット RAM（例：`hb_10` のページ 3 の 16 KB RAM） |
| 1 | あり、0〜3（`--extension1` 経由のみ） | `--extension1` | カートリッジ ROM + マッパー（位置引数の `cartridge`/`--slot1`/`--mapper`/`--mapper1`）、**または**拡張 |
| 2 | あり、0〜3（`--extension2` 経由のみ） | `--extension2` | カートリッジ ROM + マッパー（`--slot2`/`--mapper2`）、**または**拡張 |
| 3 | MSX2：あり、0〜3（マシン定義のみ）。MSX1：なし | なし | マシン YAML（`config/machines/*.yaml`）で固定。MSX1：機種ごとのサイズのフラット RAM（デフォルトは 32 KB、`hb_10_64k` は 64 KB）。`hb_10` では空き。MSX2：自身の SUB ROM、RAM マッパーまたはフラット RAM。`hb_f1xd`（WD2793）と `fs_a1f`（TC8566AF。公開資料を元に実装しており、実機では未検証）ではフロッピーディスクコントローラも |

スロット 1 とスロット 2 を拡張状態にするマシン YAML はありません。この 2 つをサブスロットに拡張できるのは、拡張機能だけです。拡張したスロットに入れられるのは、ROM のサブスロット（個数の制限なし）、`ram_mapper` のサブスロット（1 つまで）、`io_device`（1 つまで）です。ROM のサブスロットは、`flat_rom` か、バンク切り替え型マッパー 1 種（`ascii8`、`ascii16`、`halnote` のいずれか）です。今のところ `io_device` は `kanji_rom` だけです。`kanji_rom` は固定の I/O ポートで応答し、スロット上の位置を占めません。

スロット 3 のサブスロット拡張はベースマシンの性質で、スロット 1 や 2 に入れた拡張機能の影響は受けません。どのスロットのサブスロット拡張も共存し、セカンダリスロットレジスタはスロットごとに別々です。

## 制限

次の構成は、起動時にエラーになります。

- **両スロットに同じ id。** このプロジェクトは、同じ拡張を 2 つ同時に使う構成をサポートしません。そうした構成が実際に必要になる場面もほとんどありません。
- **MSX1 機でのサブスロット拡張型の拡張機能。** `hbi_j1`、`memory_512k`、`msxdos2_512k*` の 3 つは、スロットをサブスロットに拡張します。これらは MSX2 機専用です。`fmpac` と `scc_plus` は MSX1 でも動きます。
- **RAM マッパーが 2 つ。** `memory_512k` と `msxdos2_512k*` は、どれも RAM マッパーを含みます。スロット 3 に RAM マッパーを持つ機種（`cbios_msx2*`、`fs_a1f`、`hb_f1xd_256k`）では使えません。両スロットにこれらを 1 つずつ入れることもできません。どちらの場合も、2 つのメモリマッパーが同じ I/O ポート（0xFC〜0xFF）を共有することになり、エミュレータはそれに対応していません。同梱の機種でこれらの拡張を使えるのは `hb_f1xd` だけです。
- **漢字フォント ROM デバイスが 2 つ。** `hbi_j1` と `msxdos2_512k_kanjirom` は、どちらも I/O ポート 0xD8〜0xDB に漢字フォント ROM を置きます。この 2 つは同時に使えません。
- **SCC 音源チップが 2 つ。** `KonamiSCC` カートリッジマッパーと `scc_plus` 拡張は、どちらも SCC チップを作りますが、エミュレータが持てる SCC は 1 つだけです。そのため、片方のスロットのマッパーが `KonamiSCC` で、もう片方のスロットの拡張が `scc_plus` という組み合わせは使えません。両スロットのマッパーがともに `KonamiSCC` の場合も同じです。

## FM-PAC

`--extension1`/`--extension2 fmpac`：[FM-PAC（ＦＭパナアミューズメントカートリッジ）](https://generation-msx.nl/software/matsushita-electric-industrial/fm-pana-amusement-cartridge/1072)は、実在する MSX-MUSIC カートリッジです。YM2413（OPLL）FM 音源チップ、64 KB のバンク切り替え ROM、8 KB の電池バックアップ SRAM を搭載しています。SRAM の内容は `saves/sram/fmpac.sram` に保存されます。カートリッジの ROM はこのプロジェクトに含まれないため、各自でダンプして `roms/fmpac/fmpac.rom` に置いてください。

## SCC-I カートリッジ (SCC+)

`--extension1`/`--extension2 scc_plus`：[Konami の SCC（052539）](https://www.msx.org/wiki/Konami_052539)サウンドチップをモデルにした、音源だけのカートリッジです。64 KB の物理バンク切り替え RAM を搭載し、アドレス上は 128 KB に見えます。RAM は空の状態で始まり、エミュレータは ROM やデータファイルを読み込みません。そのため ROM ファイルは不要です。

バンクレジスタは bit 3 を無視するので、ブロック N はブロック N+8 のミラーになります。この挙動は、実機の改造として知られる[「2 つの 64 KB バンクを接続する」](http://bifi.msxnet.org/msxnet/tech/soundcartridge.html)を再現したものです。このカートリッジを音源として使う MSX2 のフロッピータイトルは、工場出荷時の 2 種類の RAM 実装のどちらか一方を前提にしています。この改造を再現することで、1 本のカートリッジでどちらにも対応できます。

> **Note**: 作者は SCC-I カートリッジも、それを使うソフトウェアも持っていません。この実装は公開情報を元にしたもので、実機では検証していません。

## Sony HBI-J1

`--extension1`/`--extension2 hbi_j1`：[Sony HBI-J1](https://www.msx.org/wiki/Sony_HBI-J1) は、実在する漢字 ROM + MSX-JE ワープロカートリッジです。MSX2 機でだけ動きます。MSX-JE の 16 KB SRAM は `saves/sram/hbi-j1_msx-je.sram` に保存されます。次の 3 つの ROM ダンプが必要です。

```
roms/hbi_j1/
├── hbi-j1_kanjibasic.rom   (32 KB  — 漢字ドライバ + BASIC 拡張)
├── hbi-j1_kanjifont.rom    (256 KB — JIS 漢字フォント ROM)
└── hbi-j1_msx-je.rom       (1 MB   — MSX-JE ワープロ ROM)
```

これらは Sony の商用 ROM で、**このプロジェクトには含まれていません**。このプロジェクトには、これらを再配布する権利がないためです。HBI-J1 カートリッジを持っている場合は、各自で ROM をダンプし、上記のパスに置いてください。それ以外に正当な入手方法はありません。

## memory_512k: 512 KB RAM 拡張

`--extension1`/`--extension2 memory_512k` は、MSX2 のメインメモリ用の 512 KB バンク切り替え RAM 拡張です。対応する実在のカートリッジはありません。主な用途は、スロット 3 に RAM マッパーを持たない `hb_f1xd` で MSX-DOS2 を動かすことです。片方のスロットに MSX-DOS2 カーネルのカートリッジ、もう片方にこの拡張を入れると、MSX-DOS2 を動かすのに十分なバンク RAM がそろいます。

ROM ファイルは不要です。RAM は実行のたびに空の状態から始まり、ファイルには保存されません。

```bash
# スロット 1 に MSX-DOS2 カーネルカートリッジ、スロット 2 にこの拡張の RAM を組み合わせる
python . path/to/msxdos2_kernel.rom --extension2 memory_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k: 512 KB RAM + MSX-DOS2 カーネル

`--extension1`/`--extension2 msxdos2_512k` は、`memory_512k` の 512 KB RAM と MSX-DOS2 カーネル ROM を 1 つのスロットにまとめた拡張で、これも実在のカートリッジには対応しません。フラグ 1 つで MSX-DOS2 を動かせるので、もう片方のスロットに別のカーネルカートリッジを用意する必要はありません。

カーネル ROM は [`msxdos2s`](https://github.com/b3rendsh/msxdos2s) の `msxd22s.rom` で、`roms/msxdos2s/msxd22s.rom` に置きます。このプロジェクトのソースの大半は、ASCII によるオリジナルの MSX-DOS2 を組み直したものです。独自のコードには CC BY-NC-SA 4.0 が適用され、それ以外は教育目的と非商用の利用に限って公開されています。**使う前にライセンスを確認してください。**

MSX-DOS2 を起動するには、`MSXDOS2.SYS` と `COMMAND2.COM` を含むフロッピーディスクイメージも必要です。この 2 つはこのプロジェクトには**含まれません**。マシンは `--machine hb_f1xd` を使ってください。`hb_f1xd` はフロッピーディスクコントローラを備え、スロット 3 に RAM マッパーがないためです。`cbios_*` マシンにはフロッピーディスクコントローラがなく、`fs_a1f` はスロット 3 の RAM マッパーのためにこの拡張を受け付けません。このマシンの条件は、下記の [msxdos2_512k_kanjirom](#msxdos2_512k_kanjirom--漢字フォント-rom) と [msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-フォント-rom) にも当てはまります。

```bash
# MSX-DOS2 を直接起動 — スロット 1 のカートリッジ不要、カーネルは拡張に内蔵済み
python . --extension2 msxdos2_512k --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_kanjirom: + 漢字フォント ROM

`--extension1`/`--extension2 msxdos2_512k_kanjirom` は、`msxdos2_512k` に MSX 標準の JIS 漢字フォント ROM の I/O デバイス（ポート `0xD8`〜`0xDB`）を加えたものです。これにより、MSX-DOS2 のアプリケーションが漢字を表示できます。デフォルトの設定では、HBI-J1 拡張のフォントダンプ `roms/hbi_j1/hbi-j1_kanjifont.rom` を使います。ライセンスについては、上記の [Sony HBI-J1](#sony-hbi-j1) 節の注意がそのまま当てはまります。別の JIS 漢字フォント ROM ダンプを使う場合は、`config/extensions/msxdos2_512k_kanjirom.yaml` の `io_device.rom` ブロックを編集してください。

この拡張が対応するのは、固定ポートの I/O デバイスだけです。MSX-View のアドオンパッケージのフォントのような、メモリマップされた ROM カートリッジの漢字フォントは、別の種類のデバイスです。その種類については、下記の [msxdos2_512k_viewfont](#msxdos2_512k_viewfont--view-フォント-rom) を参照してください。

```bash
# msxdos2_512k と同様、漢字表示にも対応
python . --extension2 msxdos2_512k_kanjirom --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## msxdos2_512k_viewfont: + View フォント ROM

`--extension1`/`--extension2 msxdos2_512k_viewfont` は、`msxdos2_512k` に ASCII8 マップ方式（8 KB バンクの MEGA ROM コントローラ）のフォント ROM を加えたものです。この ROM は、MSX-View のアドオンパッケージのフォントカートリッジと互換で、サブスロット 2 に入り、`msxdos2_512k` の RAM マッパーや MSX-DOS2 カーネルと同じスロットに同居します。

デフォルトの設定では、[MSXView 漢字カートリッジ互換ROM](https://littlelimit.net/viewfont.htm)（`k12x8shn.rom`）を使います。しののめフォントと k12x8 フォントを元に、このカートリッジ形式用に作られた無償のビットマップフォント ROM です。ライセンスは、商用・非商用を問わず、改変の有無にかかわらず、使用・複製・配布を無制限に許可しています。

このプロジェクトにはコピーを**同梱していません**。上記のリンクからダウンロードし、`roms/kanji/k12x8shn.rom` に置いてください。ほかの MSX-View 互換フォント ROM も使えます。差し替える場合は、`config/extensions/msxdos2_512k_viewfont.yaml` のサブスロット 2 の `rom` ブロックを編集してください。

```bash
# msxdos2_512k と同様、View フォント互換の漢字 ROM を使用
python . --extension2 msxdos2_512k_viewfont --machine hb_f1xd --fdd1 path/to/disk.dsk
```

## ROM ファイル配置

拡張に関係する ROM ファイルの配置です。どれもこのプロジェクトには含まれません。入手方法は、上記の各拡張の節を参照してください。

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
